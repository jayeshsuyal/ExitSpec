"""Durable ingestion ledger for authenticated meeting transcript events.

The inbox is deliberately not a workflow engine. It accepts only PR108
provider-neutral events whose transport envelope has already been verified,
keeps immutable content-free receipts, and temporarily stores a private payload
annex for restart recovery. It cannot create a source, confirm or freeze a
contract, start measurement, or assign a verdict.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterator, Literal, Sequence

from pydantic import Field, field_validator, model_validator

from .canonical import CanonicalizationError, canonical_json_bytes
from .meeting_connector import (
    MEETING_CONNECTOR_AUTHORITY,
    MEETING_CONNECTOR_REVIEW_STATE,
    MeetingCaptureAuthorization,
    MeetingConnectorDenied,
    MeetingConnectorFailureCode,
    MeetingEventKind,
    MeetingTranscriptEvent,
    MeetingTransportBinding,
    PrivateMeetingConnectorValidationError,
    validate_meeting_event_envelope,
    validate_meeting_transport_binding,
)
from .models import FrozenExitSpecModel, SHA256_PATTERN


MEETING_EVENT_INBOX_SCHEMA_NAME = "exitspec.meeting-event-inbox"
MEETING_EVENT_INBOX_SCHEMA_VERSION = 1
MEETING_EVENT_INBOX_VERSION = "exitspec-meeting-event-inbox/1.0"

_EVENT_IDENTITY_DOMAIN = b"exitspec-meeting-inbox-event-identity-v1\x00"
_INGRESS_KEY_DOMAIN = b"exitspec-meeting-inbox-ingress-key-v1\x00"
_INGRESS_INPUT_DOMAIN = b"exitspec-meeting-inbox-ingress-input-v1\x00"
_MARKER_IDENTITY_DOMAIN = b"exitspec-meeting-inbox-marker-v1\x00"
_STREAM_BINDING_DOMAIN = b"exitspec-meeting-inbox-stream-binding-v1\x00"

_CANONICAL_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T"
    r"\d{2}:\d{2}:\d{2}\.\d{6}Z\Z"
)
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_DEFAULT_BUSY_TIMEOUT_SECONDS = 10.0
_MAX_RAW_PAYLOAD_RETENTION_SECONDS = 24 * 60 * 60
_MAX_INGRESS_RECEIPTS = 20_000


class MeetingInboxDisposition(StrEnum):
    """Durable outcomes that still release only content-free receipts."""

    ACCEPTED = "ACCEPTED"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"


class MeetingInboxTaintKind(StrEnum):
    """Permanent stream disqualifiers; neither value is an acceptance verdict."""

    CONFLICT = "TAINTED_CONFLICT"
    CAPACITY = "TAINTED_CAPACITY"


class MeetingEventInboxError(RuntimeError):
    """Base class for sanitized durable-inbox failures."""


class MeetingEventInboxConflict(MeetingEventInboxError):
    """A changed identity or sequence permanently tainted one stream."""

    def __init__(self) -> None:
        super().__init__("Meeting event stream is durably tainted by conflict.")


class MeetingEventInboxCapacityError(MeetingEventInboxError):
    """A bounded store or frozen stream limit permanently tainted one stream."""

    def __init__(self) -> None:
        super().__init__("Meeting event stream is durably tainted by capacity.")


class MeetingEventInboxIntegrityError(MeetingEventInboxError):
    """Persisted inbox state is corrupt, contradictory, or unsupported."""


class MeetingEventInboxStorageError(MeetingEventInboxError):
    """The SQLite inbox could not be opened or updated safely."""


class MeetingEventInboxPayloadExpired(MeetingEventInboxError):
    """Receipts remain, but private replay material has expired."""

    def __init__(self) -> None:
        super().__init__(
            "Private meeting payload retention expired before source handoff."
        )


class PrivateMeetingEventInboxSerializationError(RuntimeError):
    """Refusal to serialize a recovered private stream."""

    code = "private_meeting_event_inbox_serialization_forbidden"

    def __init__(self) -> None:
        super().__init__(self.code)


class MeetingEventInboxReceipt(FrozenExitSpecModel):
    """Content-free result for one ingress attempt or exact API replay."""

    schema_version: Literal[MEETING_EVENT_INBOX_VERSION] = (
        MEETING_EVENT_INBOX_VERSION
    )
    ingress_key_sha256: str = Field(pattern=SHA256_PATTERN)
    ingress_input_sha256: str = Field(pattern=SHA256_PATTERN)
    stream_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    event_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    event_fingerprint_sha256: str = Field(pattern=SHA256_PATTERN)
    sequence: int = Field(gt=0, le=_MAX_INGRESS_RECEIPTS)
    kind: MeetingEventKind
    disposition: MeetingInboxDisposition
    event_persisted_at: datetime
    ingress_recorded_at: datetime
    raw_payload_expires_at: datetime
    idempotent_replay: bool
    transcript_authority: Literal[MEETING_CONNECTOR_AUTHORITY] = (
        MEETING_CONNECTOR_AUTHORITY
    )
    review_state: Literal[MEETING_CONNECTOR_REVIEW_STATE] = (
        MEETING_CONNECTOR_REVIEW_STATE
    )
    may_create_source: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    raw_audio_received: Literal[False] = False
    synthetic_only: Literal[True] = True

    @field_validator(
        "event_persisted_at",
        "ingress_recorded_at",
        "raw_payload_expires_at",
    )
    @classmethod
    def validate_time(cls, value: datetime, info: object) -> datetime:
        return _require_aware_datetime(value, getattr(info, "field_name", "time"))

    @model_validator(mode="after")
    def require_consistent_times(self) -> "MeetingEventInboxReceipt":
        if (
            self.ingress_recorded_at < self.event_persisted_at
            or self.raw_payload_expires_at <= self.event_persisted_at
        ):
            raise ValueError("meeting inbox receipt times are contradictory.")
        return self


class MeetingEventInboxStreamReceipt(FrozenExitSpecModel):
    """Content-free summary of a private restart recovery."""

    schema_version: Literal[MEETING_EVENT_INBOX_VERSION] = (
        MEETING_EVENT_INBOX_VERSION
    )
    stream_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    unique_event_count: int = Field(gt=0, le=_MAX_INGRESS_RECEIPTS)
    exact_duplicate_count: int = Field(ge=0, le=_MAX_INGRESS_RECEIPTS)
    first_sequence: int = Field(gt=0, le=_MAX_INGRESS_RECEIPTS)
    last_sequence: int = Field(gt=0, le=_MAX_INGRESS_RECEIPTS)
    sequence_contiguous: bool
    raw_payload_complete: Literal[True] = True
    transcript_authority: Literal[MEETING_CONNECTOR_AUTHORITY] = (
        MEETING_CONNECTOR_AUTHORITY
    )
    review_state: Literal[MEETING_CONNECTOR_REVIEW_STATE] = (
        MEETING_CONNECTOR_REVIEW_STATE
    )
    may_create_source: Literal[False] = False
    may_confirm_contract: Literal[False] = False
    may_freeze_contract: Literal[False] = False
    may_start_measurement: Literal[False] = False
    may_assign_verdict: Literal[False] = False
    raw_audio_received: Literal[False] = False
    synthetic_only: Literal[True] = True

    @model_validator(mode="after")
    def require_ordered_range(self) -> "MeetingEventInboxStreamReceipt":
        if self.last_sequence < self.first_sequence:
            raise ValueError("last_sequence cannot precede first_sequence.")
        return self


class RecoveredMeetingEventStream:
    """Private events released only to PR108 sealing or later source handoff."""

    __slots__ = ("_events", "receipt")

    def __init__(
        self,
        receipt: MeetingEventInboxStreamReceipt,
        events: Sequence[MeetingTranscriptEvent],
    ) -> None:
        material = tuple(events)
        if (
            type(receipt) is not MeetingEventInboxStreamReceipt
            or not material
            or len(material)
            != receipt.unique_event_count + receipt.exact_duplicate_count
            or any(type(event) is not MeetingTranscriptEvent for event in material)
        ):
            raise MeetingEventInboxIntegrityError(
                "Recovered meeting event stream is contradictory."
            )
        self.receipt = receipt
        self._events = material

    def __repr__(self) -> str:
        return "RecoveredMeetingEventStream(<private>)"

    __str__ = __repr__

    def events_for_sealing(self) -> tuple[MeetingTranscriptEvent, ...]:
        """Return the private delivery population to the existing sealer."""

        return self._events

    def model_dump(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise PrivateMeetingEventInboxSerializationError()

    def model_dump_json(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise PrivateMeetingEventInboxSerializationError()

    def __copy__(self) -> None:
        raise PrivateMeetingEventInboxSerializationError()

    def __deepcopy__(self, memo: object) -> None:
        del memo
        raise PrivateMeetingEventInboxSerializationError()


class SQLiteMeetingEventInbox:
    """Process-safe, append-only receipt ledger with a purgeable private annex."""

    __slots__ = (
        "_busy_timeout_seconds",
        "_clock",
        "_database_path",
        "_max_ingress_receipts",
        "_raw_payload_retention_seconds",
    )

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        raw_payload_retention_seconds: int = 60 * 60,
        max_ingress_receipts: int = _MAX_INGRESS_RECEIPTS,
        clock: Callable[[], datetime] | None = None,
        busy_timeout_seconds: float = _DEFAULT_BUSY_TIMEOUT_SECONDS,
    ) -> None:
        self._database_path = _require_safe_database_path(database_path)
        if (
            isinstance(raw_payload_retention_seconds, bool)
            or not isinstance(raw_payload_retention_seconds, int)
            or raw_payload_retention_seconds < 60
            or raw_payload_retention_seconds
            > _MAX_RAW_PAYLOAD_RETENTION_SECONDS
        ):
            raise ValueError(
                "raw_payload_retention_seconds must be an integer from 60 "
                "through 86400."
            )
        if (
            isinstance(max_ingress_receipts, bool)
            or not isinstance(max_ingress_receipts, int)
            or max_ingress_receipts < 3
            or max_ingress_receipts > _MAX_INGRESS_RECEIPTS
        ):
            raise ValueError(
                "max_ingress_receipts must be an integer from 3 through 20000."
            )
        if (
            isinstance(busy_timeout_seconds, bool)
            or not isinstance(busy_timeout_seconds, (int, float))
            or busy_timeout_seconds <= 0
            or busy_timeout_seconds > 60
        ):
            raise ValueError(
                "busy_timeout_seconds must be greater than 0 and at most 60."
            )
        self._raw_payload_retention_seconds = raw_payload_retention_seconds
        self._max_ingress_receipts = max_ingress_receipts
        self._clock = clock or _utc_now
        self._busy_timeout_seconds = float(busy_timeout_seconds)
        if not callable(self._clock):
            raise TypeError("clock must be callable.")
        self._initialize()

    @property
    def database_path(self) -> Path:
        return self._database_path

    def __repr__(self) -> str:
        return "SQLiteMeetingEventInbox(database_path={0!r})".format(
            str(self._database_path)
        )

    def append(
        self,
        *,
        ingress_idempotency_key: str,
        authorization: MeetingCaptureAuthorization,
        binding: MeetingTransportBinding,
        event: MeetingTranscriptEvent,
    ) -> MeetingEventInboxReceipt:
        """Append one delivery, replay one API attempt, or taint the stream."""

        # Validate authority before consulting idempotency state so forged input
        # cannot probe or poison a legitimate stream.
        validate_meeting_event_envelope(authorization, binding, event)
        _require_ingress_key(ingress_idempotency_key)
        recorded_at = _require_aware_datetime(self._clock(), "clock result")
        if recorded_at < event.received_at:
            raise MeetingConnectorDenied(
                MeetingConnectorFailureCode.TIMELINE_INVALID
            )

        payload = _event_payload_bytes(event)
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        event_fingerprint = event.fingerprint_sha256()
        stream_binding = _stream_binding_sha256(authorization, binding)
        event_identity = _event_identity_sha256(
            stream_binding_sha256=stream_binding,
            event_id=event.event_id,
        )
        ingress_key_sha256 = _domain_sha256(
            _INGRESS_KEY_DOMAIN,
            {"ingress_idempotency_key": ingress_idempotency_key},
        )
        ingress_input_sha256 = _domain_sha256(
            _INGRESS_INPUT_DOMAIN,
            {
                "event_fingerprint_sha256": event_fingerprint,
                "event_identity_sha256": event_identity,
                "payload_sha256": payload_sha256,
                "stream_binding_sha256": stream_binding,
            },
        )
        canonical_recorded_at = _canonical_timestamp(recorded_at)
        raw_expires_at = recorded_at + timedelta(
            seconds=self._raw_payload_retention_seconds
        )
        canonical_raw_expires_at = _canonical_timestamp(raw_expires_at)

        # Retention is completed in its own transaction so secure deletion and
        # WAL truncation finish before new private bytes are accepted.
        self.purge_expired_payloads(now=recorded_at)

        connection = self._connect()
        result: MeetingEventInboxReceipt | None = None
        pending_error: MeetingEventInboxError | MeetingConnectorDenied | None = None
        try:
            with _immediate_transaction(connection):
                persisted_stream_binding = self._ensure_stream_locked(
                    connection,
                    authorization=authorization,
                    binding=binding,
                    stream_binding_sha256=stream_binding,
                    created_at=canonical_recorded_at,
                )
                if persisted_stream_binding != stream_binding:
                    self._taint_stream_locked(
                        connection,
                        stream_binding_sha256=persisted_stream_binding,
                        taint_kind=MeetingInboxTaintKind.CONFLICT,
                        ingress_key_sha256=ingress_key_sha256,
                        ingress_input_sha256=ingress_input_sha256,
                        event_identity_sha256=event_identity,
                        sequence=event.sequence,
                        detected_at=canonical_recorded_at,
                    )
                    pending_error = MeetingEventInboxConflict()
                else:
                    prior_taint = self._load_taint_locked(
                        connection,
                        stream_binding,
                    )
                    if prior_taint is not None:
                        pending_error = _error_for_taint(prior_taint)

                existing_ingress = None
                if pending_error is None:
                    existing_ingress = connection.execute(
                        _INGRESS_RECEIPT_SELECT
                        + " WHERE i.ingress_key_sha256 = ?",
                        (ingress_key_sha256,),
                    ).fetchone()
                if existing_ingress is not None:
                    if not hmac.compare_digest(
                        existing_ingress["ingress_input_sha256"],
                        ingress_input_sha256,
                    ):
                        self._taint_stream_locked(
                            connection,
                            stream_binding_sha256=stream_binding,
                            taint_kind=MeetingInboxTaintKind.CONFLICT,
                            ingress_key_sha256=ingress_key_sha256,
                            ingress_input_sha256=ingress_input_sha256,
                            event_identity_sha256=event_identity,
                            sequence=event.sequence,
                            detected_at=canonical_recorded_at,
                        )
                        pending_error = MeetingEventInboxConflict()
                    else:
                        result = _receipt_from_joined_row(
                            existing_ingress,
                            idempotent_replay=True,
                        )
                        if (
                            result.stream_binding_sha256 != stream_binding
                            or result.event_identity_sha256 != event_identity
                            or result.event_fingerprint_sha256
                            != event_fingerprint
                            or result.sequence != event.sequence
                            or result.kind is not event.kind
                            or result.ingress_input_sha256
                            != ingress_input_sha256
                        ):
                            raise MeetingEventInboxIntegrityError(
                                "Meeting API replay contradicts durable history."
                            )

                if pending_error is None and result is None:
                    stream_time_row = connection.execute(
                        """
                        SELECT created_at
                          FROM meeting_event_inbox_streams
                         WHERE stream_binding_sha256 = ?
                        """,
                        (stream_binding,),
                    ).fetchone()
                    if stream_time_row is None:
                        raise MeetingEventInboxIntegrityError(
                            "Meeting stream binding disappeared."
                        )
                    if recorded_at < _parse_canonical_timestamp(
                        stream_time_row["created_at"]
                    ):
                        pending_error = MeetingConnectorDenied(
                            MeetingConnectorFailureCode.TIMELINE_INVALID
                        )

                existing_event = None
                if pending_error is None and result is None:
                    existing_event = connection.execute(
                        """
                        SELECT event_identity_sha256,
                               event_fingerprint_sha256,
                               stream_binding_sha256,
                               sequence,
                               kind,
                               received_at,
                               persisted_at,
                               raw_payload_expires_at,
                               payload_sha256,
                               transcript_character_count
                          FROM meeting_event_inbox_events
                         WHERE event_identity_sha256 = ?
                        """,
                        (event_identity,),
                    ).fetchone()
                if existing_event is not None:
                    try:
                        _require_exact_existing_event(
                            existing_event,
                            stream_binding_sha256=stream_binding,
                            event=event,
                            event_fingerprint_sha256=event_fingerprint,
                            payload_sha256=payload_sha256,
                            ingress_recorded_at=recorded_at,
                        )
                    except MeetingEventInboxConflict:
                        self._taint_stream_locked(
                            connection,
                            stream_binding_sha256=stream_binding,
                            taint_kind=MeetingInboxTaintKind.CONFLICT,
                            ingress_key_sha256=ingress_key_sha256,
                            ingress_input_sha256=ingress_input_sha256,
                            event_identity_sha256=event_identity,
                            sequence=event.sequence,
                            detected_at=canonical_recorded_at,
                        )
                        pending_error = MeetingEventInboxConflict()
                    else:
                        pending_error = self._require_capacity_locked(
                            connection,
                            authorization=authorization,
                            stream_binding_sha256=stream_binding,
                            ingress_key_sha256=ingress_key_sha256,
                            ingress_input_sha256=ingress_input_sha256,
                            event_identity_sha256=event_identity,
                            sequence=event.sequence,
                            detected_at=canonical_recorded_at,
                        )
                        if pending_error is None:
                            self._insert_ingress_receipt_locked(
                                connection,
                                ingress_key_sha256=ingress_key_sha256,
                                ingress_input_sha256=ingress_input_sha256,
                                stream_binding_sha256=stream_binding,
                                event_identity_sha256=event_identity,
                                event_fingerprint_sha256=event_fingerprint,
                                sequence=event.sequence,
                                kind=event.kind,
                                disposition=(
                                    MeetingInboxDisposition.EXACT_DUPLICATE
                                ),
                                recorded_at=canonical_recorded_at,
                            )
                            joined = connection.execute(
                                _INGRESS_RECEIPT_SELECT
                                + " WHERE i.ingress_key_sha256 = ?",
                                (ingress_key_sha256,),
                            ).fetchone()
                            if joined is None:
                                raise MeetingEventInboxIntegrityError(
                                    "Meeting ingress receipt disappeared."
                                )
                            result = _receipt_from_joined_row(
                                joined,
                                idempotent_replay=False,
                            )

                if pending_error is None and result is None:
                    sequence_collision = connection.execute(
                        """
                        SELECT event_identity_sha256
                          FROM meeting_event_inbox_events
                         WHERE stream_binding_sha256 = ?
                           AND sequence = ?
                        """,
                        (stream_binding, event.sequence),
                    ).fetchone()
                    if sequence_collision is not None:
                        self._taint_stream_locked(
                            connection,
                            stream_binding_sha256=stream_binding,
                            taint_kind=MeetingInboxTaintKind.CONFLICT,
                            ingress_key_sha256=ingress_key_sha256,
                            ingress_input_sha256=ingress_input_sha256,
                            event_identity_sha256=event_identity,
                            sequence=event.sequence,
                            detected_at=canonical_recorded_at,
                        )
                        pending_error = MeetingEventInboxConflict()

                if pending_error is None and result is None:
                    pending_error = self._require_capacity_locked(
                        connection,
                        authorization=authorization,
                        stream_binding_sha256=stream_binding,
                        ingress_key_sha256=ingress_key_sha256,
                        ingress_input_sha256=ingress_input_sha256,
                        event_identity_sha256=event_identity,
                        sequence=event.sequence,
                        detected_at=canonical_recorded_at,
                    )

                if pending_error is None and result is None:
                    transcript_character_count = len(event.transcript_text or "")
                    current_characters = int(
                        connection.execute(
                            """
                            SELECT COALESCE(SUM(transcript_character_count), 0)
                              FROM meeting_event_inbox_events
                             WHERE stream_binding_sha256 = ?
                            """,
                            (stream_binding,),
                        ).fetchone()[0]
                    )
                    if (
                        current_characters + transcript_character_count
                        > authorization.max_transcript_characters
                    ):
                        self._taint_stream_locked(
                            connection,
                            stream_binding_sha256=stream_binding,
                            taint_kind=MeetingInboxTaintKind.CAPACITY,
                            ingress_key_sha256=ingress_key_sha256,
                            ingress_input_sha256=ingress_input_sha256,
                            event_identity_sha256=event_identity,
                            sequence=event.sequence,
                            detected_at=canonical_recorded_at,
                        )
                        pending_error = MeetingConnectorDenied(
                            MeetingConnectorFailureCode.LIMIT_EXCEEDED
                        )
                    else:
                        connection.execute(
                            """
                            INSERT INTO meeting_event_inbox_events (
                                event_identity_sha256,
                                event_fingerprint_sha256,
                                stream_binding_sha256,
                                sequence,
                                kind,
                                received_at,
                                persisted_at,
                                raw_payload_expires_at,
                                payload_sha256,
                                transcript_character_count
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                event_identity,
                                event_fingerprint,
                                stream_binding,
                                event.sequence,
                                event.kind.value,
                                _canonical_timestamp(event.received_at),
                                canonical_recorded_at,
                                canonical_raw_expires_at,
                                payload_sha256,
                                transcript_character_count,
                            ),
                        )
                        connection.execute(
                            """
                            INSERT INTO meeting_event_inbox_payloads (
                                event_identity_sha256,
                                payload,
                                payload_sha256,
                                raw_payload_expires_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (
                                event_identity,
                                sqlite3.Binary(payload),
                                payload_sha256,
                                canonical_raw_expires_at,
                            ),
                        )
                        self._insert_ingress_receipt_locked(
                            connection,
                            ingress_key_sha256=ingress_key_sha256,
                            ingress_input_sha256=ingress_input_sha256,
                            stream_binding_sha256=stream_binding,
                            event_identity_sha256=event_identity,
                            event_fingerprint_sha256=event_fingerprint,
                            sequence=event.sequence,
                            kind=event.kind,
                            disposition=MeetingInboxDisposition.ACCEPTED,
                            recorded_at=canonical_recorded_at,
                        )
                        joined = connection.execute(
                            _INGRESS_RECEIPT_SELECT
                            + " WHERE i.ingress_key_sha256 = ?",
                            (ingress_key_sha256,),
                        ).fetchone()
                        if joined is None:
                            raise MeetingEventInboxIntegrityError(
                                "Meeting ingress receipt disappeared."
                            )
                        result = _receipt_from_joined_row(
                            joined,
                            idempotent_replay=False,
                        )

            if pending_error is not None:
                raise pending_error
            if result is None:
                raise MeetingEventInboxIntegrityError(
                    "Meeting inbox append produced no durable outcome."
                )
            return result
        except (
            MeetingConnectorDenied,
            MeetingEventInboxError,
        ):
            raise
        except sqlite3.IntegrityError as exc:
            raise MeetingEventInboxIntegrityError(
                "Meeting inbox atomic constraints were violated."
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise MeetingEventInboxStorageError(
                "Could not append to the durable meeting event inbox."
            ) from exc
        finally:
            connection.close()
            _tighten_sqlite_files(self._database_path)

    def recover_stream(
        self,
        *,
        authorization: MeetingCaptureAuthorization,
        binding: MeetingTransportBinding,
    ) -> RecoveredMeetingEventStream:
        """Recover, re-hash, and revalidate private events after restart."""

        validate_meeting_transport_binding(authorization, binding)
        now = _require_aware_datetime(self._clock(), "clock result")
        self.purge_expired_payloads(now=now)
        stream_binding = _stream_binding_sha256(authorization, binding)
        connection = self._connect()
        try:
            taint = self._load_taint_locked(connection, stream_binding)
            if taint is not None:
                raise _error_for_taint(taint)
            stream_row = connection.execute(
                """
                SELECT stream_binding_sha256,
                       authorization_id,
                       transport_binding_id,
                       stream_identity_sha256,
                       authorization_sha256,
                       transport_binding_sha256,
                       max_event_count,
                       max_transcript_characters,
                       created_at
                  FROM meeting_event_inbox_streams
                 WHERE stream_binding_sha256 = ?
                """,
                (stream_binding,),
            ).fetchone()
            if stream_row is None:
                raise KeyError("Meeting event stream was not found.")
            _validate_stream_row(
                stream_row,
                authorization=authorization,
                binding=binding,
                expected_stream_binding_sha256=stream_binding,
            )

            event_rows = connection.execute(
                """
                SELECT event_identity_sha256,
                       event_fingerprint_sha256,
                       stream_binding_sha256,
                       sequence,
                       kind,
                       received_at,
                       persisted_at,
                       raw_payload_expires_at,
                       payload_sha256,
                       transcript_character_count
                  FROM meeting_event_inbox_events
                 WHERE stream_binding_sha256 = ?
                 ORDER BY sequence ASC
                """,
                (stream_binding,),
            ).fetchall()
            if not event_rows:
                raise KeyError("Meeting event stream has no accepted events.")
            duplicate_counts = _validate_ingress_history(
                connection,
                stream_binding_sha256=stream_binding,
                event_rows=event_rows,
            )

            events: list[MeetingTranscriptEvent] = []
            duplicate_count = 0
            sequences: list[int] = []
            for event_row in event_rows:
                event_identity = event_row["event_identity_sha256"]
                payload_row = connection.execute(
                    """
                    SELECT payload, payload_sha256, raw_payload_expires_at
                      FROM meeting_event_inbox_payloads
                     WHERE event_identity_sha256 = ?
                    """,
                    (event_identity,),
                ).fetchone()
                if payload_row is None:
                    raise MeetingEventInboxPayloadExpired()
                event = _event_from_rows(event_row, payload_row)
                validate_meeting_event_envelope(authorization, binding, event)
                expected_identity = _event_identity_sha256(
                    stream_binding_sha256=stream_binding,
                    event_id=event.event_id,
                )
                if (
                    expected_identity != event_identity
                    or event.fingerprint_sha256()
                    != event_row["event_fingerprint_sha256"]
                    or event.sequence != event_row["sequence"]
                    or event.kind.value != event_row["kind"]
                ):
                    raise MeetingEventInboxIntegrityError(
                        "Recovered meeting event contradicts its receipt."
                    )
                events.append(event)
                sequences.append(event.sequence)

                exact_duplicates = duplicate_counts[event_identity]
                events.extend(event for _ in range(exact_duplicates))
                duplicate_count += exact_duplicates

            if len(events) > authorization.max_event_count:
                raise MeetingEventInboxIntegrityError(
                    "Recovered delivery population exceeds its frozen limit."
                )
            contiguous = sequences == list(
                range(sequences[0], sequences[-1] + 1)
            )
            receipt = MeetingEventInboxStreamReceipt(
                stream_binding_sha256=stream_binding,
                unique_event_count=len(event_rows),
                exact_duplicate_count=duplicate_count,
                first_sequence=sequences[0],
                last_sequence=sequences[-1],
                sequence_contiguous=contiguous,
            )
            return RecoveredMeetingEventStream(receipt, events)
        except (MeetingConnectorDenied, MeetingEventInboxError, KeyError):
            raise
        except sqlite3.DatabaseError as exc:
            raise MeetingEventInboxStorageError(
                "Could not recover the durable meeting event stream."
            ) from exc
        finally:
            connection.close()
            _tighten_sqlite_files(self._database_path)

    def purge_expired_payloads(self, *, now: datetime | None = None) -> int:
        """Secure-delete expired private payloads while retaining receipts."""

        resolved_now = _require_aware_datetime(
            now if now is not None else self._clock(),
            "now",
        )
        connection = self._connect(allow_retention_delete=True)
        try:
            with _immediate_transaction(connection):
                cursor = connection.execute(
                    """
                    DELETE FROM meeting_event_inbox_payloads
                     WHERE raw_payload_expires_at <= ?
                    """,
                    (_canonical_timestamp(resolved_now),),
                )
                deleted = int(cursor.rowcount)
            if deleted:
                checkpoint = connection.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
                if checkpoint is None or int(checkpoint[0]) != 0:
                    raise MeetingEventInboxStorageError(
                        "Could not complete private meeting payload erasure."
                    )
            _tighten_sqlite_files(self._database_path)
            return deleted
        except MeetingEventInboxError:
            raise
        except sqlite3.DatabaseError as exc:
            raise MeetingEventInboxStorageError(
                "Could not apply meeting payload retention."
            ) from exc
        finally:
            connection.close()

    def counts(self) -> tuple[int, int, int, int, int]:
        """Return stream, event, payload, ingress, and taint counts."""

        connection = self._connect()
        try:
            tables = (
                "meeting_event_inbox_streams",
                "meeting_event_inbox_events",
                "meeting_event_inbox_payloads",
                "meeting_event_inbox_ingress_receipts",
                "meeting_event_inbox_markers",
            )
            return tuple(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM {0}".format(table)
                    ).fetchone()[0]
                )
                for table in tables
            )  # type: ignore[return-value]
        except sqlite3.DatabaseError as exc:
            raise MeetingEventInboxStorageError(
                "Could not inspect the durable meeting event inbox."
            ) from exc
        finally:
            connection.close()

    def _ensure_stream_locked(
        self,
        connection: sqlite3.Connection,
        *,
        authorization: MeetingCaptureAuthorization,
        binding: MeetingTransportBinding,
        stream_binding_sha256: str,
        created_at: str,
    ) -> str:
        by_identity = connection.execute(
            """
            SELECT stream_binding_sha256,
                   authorization_id,
                   transport_binding_id,
                   stream_identity_sha256,
                   authorization_sha256,
                   transport_binding_sha256,
                   max_event_count,
                   max_transcript_characters,
                   created_at
              FROM meeting_event_inbox_streams
             WHERE authorization_id = ?
               AND transport_binding_id = ?
               AND stream_identity_sha256 = ?
            """,
            (
                authorization.authorization_id,
                binding.binding_id,
                binding.stream_identity_sha256,
            ),
        ).fetchone()
        if by_identity is not None:
            try:
                _validate_stream_row(
                    by_identity,
                    authorization=authorization,
                    binding=binding,
                    expected_stream_binding_sha256=(
                        by_identity["stream_binding_sha256"]
                    ),
                )
            except MeetingEventInboxIntegrityError:
                if (
                    by_identity["stream_binding_sha256"]
                    == stream_binding_sha256
                ):
                    raise
                return str(by_identity["stream_binding_sha256"])
            return str(by_identity["stream_binding_sha256"])

        connection.execute(
            """
            INSERT INTO meeting_event_inbox_streams (
                stream_binding_sha256,
                authorization_id,
                transport_binding_id,
                stream_identity_sha256,
                authorization_sha256,
                transport_binding_sha256,
                max_event_count,
                max_transcript_characters,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stream_binding_sha256,
                authorization.authorization_id,
                binding.binding_id,
                binding.stream_identity_sha256,
                _model_sha256(authorization),
                _model_sha256(binding),
                authorization.max_event_count,
                authorization.max_transcript_characters,
                created_at,
            ),
        )
        return stream_binding_sha256

    def _load_taint_locked(
        self,
        connection: sqlite3.Connection,
        stream_binding_sha256: str,
    ) -> MeetingInboxTaintKind | None:
        row = connection.execute(
            """
            SELECT taint_kind
              FROM meeting_event_inbox_markers
             WHERE stream_binding_sha256 = ?
            """,
            (stream_binding_sha256,),
        ).fetchone()
        if row is None:
            return None
        try:
            return MeetingInboxTaintKind(row["taint_kind"])
        except (TypeError, ValueError) as exc:
            raise MeetingEventInboxIntegrityError(
                "Meeting inbox taint marker is corrupt."
            ) from exc

    def _taint_stream_locked(
        self,
        connection: sqlite3.Connection,
        *,
        stream_binding_sha256: str,
        taint_kind: MeetingInboxTaintKind,
        ingress_key_sha256: str,
        ingress_input_sha256: str,
        event_identity_sha256: str,
        sequence: int,
        detected_at: str,
    ) -> None:
        existing = self._load_taint_locked(connection, stream_binding_sha256)
        if existing is not None:
            return
        marker_identity = _domain_sha256(
            _MARKER_IDENTITY_DOMAIN,
            {
                "event_identity_sha256": event_identity_sha256,
                "ingress_input_sha256": ingress_input_sha256,
                "ingress_key_sha256": ingress_key_sha256,
                "sequence": sequence,
                "stream_binding_sha256": stream_binding_sha256,
                "taint_kind": taint_kind.value,
            },
        )
        connection.execute(
            """
            INSERT INTO meeting_event_inbox_markers (
                marker_identity_sha256,
                stream_binding_sha256,
                taint_kind,
                ingress_key_sha256,
                ingress_input_sha256,
                event_identity_sha256,
                sequence,
                detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                marker_identity,
                stream_binding_sha256,
                taint_kind.value,
                ingress_key_sha256,
                ingress_input_sha256,
                event_identity_sha256,
                sequence,
                detected_at,
            ),
        )

    def _require_capacity_locked(
        self,
        connection: sqlite3.Connection,
        *,
        authorization: MeetingCaptureAuthorization,
        stream_binding_sha256: str,
        ingress_key_sha256: str,
        ingress_input_sha256: str,
        event_identity_sha256: str,
        sequence: int,
        detected_at: str,
    ) -> MeetingEventInboxError | MeetingConnectorDenied | None:
        total_ingress = int(
            connection.execute(
                "SELECT COUNT(*) FROM meeting_event_inbox_ingress_receipts"
            ).fetchone()[0]
        )
        stream_ingress = int(
            connection.execute(
                """
                SELECT COUNT(*)
                  FROM meeting_event_inbox_ingress_receipts
                 WHERE stream_binding_sha256 = ?
                """,
                (stream_binding_sha256,),
            ).fetchone()[0]
        )
        if total_ingress >= self._max_ingress_receipts:
            self._taint_stream_locked(
                connection,
                stream_binding_sha256=stream_binding_sha256,
                taint_kind=MeetingInboxTaintKind.CAPACITY,
                ingress_key_sha256=ingress_key_sha256,
                ingress_input_sha256=ingress_input_sha256,
                event_identity_sha256=event_identity_sha256,
                sequence=sequence,
                detected_at=detected_at,
            )
            return MeetingEventInboxCapacityError()
        if stream_ingress >= authorization.max_event_count:
            self._taint_stream_locked(
                connection,
                stream_binding_sha256=stream_binding_sha256,
                taint_kind=MeetingInboxTaintKind.CAPACITY,
                ingress_key_sha256=ingress_key_sha256,
                ingress_input_sha256=ingress_input_sha256,
                event_identity_sha256=event_identity_sha256,
                sequence=sequence,
                detected_at=detected_at,
            )
            return MeetingConnectorDenied(
                MeetingConnectorFailureCode.LIMIT_EXCEEDED
            )
        return None

    def _insert_ingress_receipt_locked(
        self,
        connection: sqlite3.Connection,
        *,
        ingress_key_sha256: str,
        ingress_input_sha256: str,
        stream_binding_sha256: str,
        event_identity_sha256: str,
        event_fingerprint_sha256: str,
        sequence: int,
        kind: MeetingEventKind,
        disposition: MeetingInboxDisposition,
        recorded_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO meeting_event_inbox_ingress_receipts (
                ingress_key_sha256,
                ingress_input_sha256,
                stream_binding_sha256,
                event_identity_sha256,
                event_fingerprint_sha256,
                sequence,
                kind,
                disposition,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ingress_key_sha256,
                ingress_input_sha256,
                stream_binding_sha256,
                event_identity_sha256,
                event_fingerprint_sha256,
                sequence,
                kind.value,
                disposition.value,
                recorded_at,
            ),
        )

    def _initialize(self) -> None:
        with _database_initialization_lock(self._database_path):
            is_new = not self._database_path.exists()
            connection = self._connect()
            try:
                journal_mode = connection.execute(
                    "PRAGMA journal_mode = WAL"
                ).fetchone()
                if (
                    journal_mode is None
                    or str(journal_mode[0]).lower() != "wal"
                ):
                    raise MeetingEventInboxIntegrityError(
                        "Meeting event inbox requires WAL journal mode."
                    )
                with _immediate_transaction(connection):
                    if is_new:
                        _create_schema(
                            connection,
                            raw_payload_retention_seconds=(
                                self._raw_payload_retention_seconds
                            ),
                            max_ingress_receipts=self._max_ingress_receipts,
                        )
                    _verify_schema(
                        connection,
                        raw_payload_retention_seconds=(
                            self._raw_payload_retention_seconds
                        ),
                        max_ingress_receipts=self._max_ingress_receipts,
                    )
                    quick_check = connection.execute(
                        "PRAGMA quick_check"
                    ).fetchone()
                    if quick_check is None or quick_check[0] != "ok":
                        raise MeetingEventInboxIntegrityError(
                            "Meeting event inbox integrity check failed."
                        )
                os.chmod(self._database_path, 0o600)
                _tighten_sqlite_files(self._database_path)
            except MeetingEventInboxError:
                raise
            except (OSError, sqlite3.DatabaseError) as exc:
                if is_new:
                    raise MeetingEventInboxStorageError(
                        "Could not initialize the durable meeting event inbox."
                    ) from exc
                raise MeetingEventInboxIntegrityError(
                    "Existing meeting event inbox failed schema validation."
                ) from exc
            finally:
                connection.close()

    def _connect(
        self,
        *,
        allow_retention_delete: bool = False,
    ) -> sqlite3.Connection:
        if self._database_path.is_symlink():
            raise MeetingEventInboxStorageError(
                "Meeting event inbox path cannot become a symbolic link."
            )
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=self._busy_timeout_seconds,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.create_function(
                "exitspec_meeting_retention_delete_allowed",
                0,
                lambda: 1 if allow_retention_delete else 0,
            )
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA recursive_triggers = ON")
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                "PRAGMA busy_timeout = {0}".format(
                    int(self._busy_timeout_seconds * 1_000)
                )
            )
            secure_delete = connection.execute(
                "PRAGMA secure_delete"
            ).fetchone()
            synchronous = connection.execute(
                "PRAGMA synchronous"
            ).fetchone()
            if (
                secure_delete is None
                or int(secure_delete[0]) != 1
                or synchronous is None
                or int(synchronous[0]) != 2
            ):
                raise sqlite3.OperationalError
            _tighten_sqlite_files(self._database_path)
            return connection
        except (OSError, sqlite3.DatabaseError) as exc:
            raise MeetingEventInboxStorageError(
                "Could not open the durable meeting event inbox."
            ) from exc


_INGRESS_RECEIPT_SELECT = """
SELECT i.ingress_key_sha256 AS ingress_key_sha256,
       i.ingress_input_sha256 AS ingress_input_sha256,
       i.stream_binding_sha256 AS stream_binding_sha256,
       i.event_identity_sha256 AS event_identity_sha256,
       i.event_fingerprint_sha256 AS event_fingerprint_sha256,
       i.sequence AS sequence,
       i.kind AS kind,
       i.disposition AS disposition,
       i.recorded_at AS ingress_recorded_at,
       e.persisted_at AS event_persisted_at,
       e.raw_payload_expires_at AS raw_payload_expires_at,
       e.payload_sha256 AS payload_sha256
  FROM meeting_event_inbox_ingress_receipts AS i
  JOIN meeting_event_inbox_events AS e
    ON e.event_identity_sha256 = i.event_identity_sha256
"""

_EXPECTED_TABLE_COLUMNS = {
    "meeting_event_inbox_metadata": (
        "singleton",
        "schema_name",
        "schema_version",
        "raw_payload_retention_seconds",
        "max_ingress_receipts",
    ),
    "meeting_event_inbox_streams": (
        "stream_binding_sha256",
        "authorization_id",
        "transport_binding_id",
        "stream_identity_sha256",
        "authorization_sha256",
        "transport_binding_sha256",
        "max_event_count",
        "max_transcript_characters",
        "created_at",
    ),
    "meeting_event_inbox_events": (
        "event_identity_sha256",
        "event_fingerprint_sha256",
        "stream_binding_sha256",
        "sequence",
        "kind",
        "received_at",
        "persisted_at",
        "raw_payload_expires_at",
        "payload_sha256",
        "transcript_character_count",
    ),
    "meeting_event_inbox_payloads": (
        "event_identity_sha256",
        "payload",
        "payload_sha256",
        "raw_payload_expires_at",
    ),
    "meeting_event_inbox_ingress_receipts": (
        "ingress_key_sha256",
        "ingress_input_sha256",
        "stream_binding_sha256",
        "event_identity_sha256",
        "event_fingerprint_sha256",
        "sequence",
        "kind",
        "disposition",
        "recorded_at",
    ),
    "meeting_event_inbox_markers": (
        "marker_identity_sha256",
        "stream_binding_sha256",
        "taint_kind",
        "ingress_key_sha256",
        "ingress_input_sha256",
        "event_identity_sha256",
        "sequence",
        "detected_at",
    ),
}

_EXPECTED_TRIGGER_TABLES = {
    "meeting_inbox_metadata_delete_guard": "meeting_event_inbox_metadata",
    "meeting_inbox_metadata_update_guard": "meeting_event_inbox_metadata",
    "meeting_inbox_streams_delete_guard": "meeting_event_inbox_streams",
    "meeting_inbox_streams_update_guard": "meeting_event_inbox_streams",
    "meeting_inbox_events_delete_guard": "meeting_event_inbox_events",
    "meeting_inbox_events_update_guard": "meeting_event_inbox_events",
    "meeting_inbox_payloads_delete_guard": "meeting_event_inbox_payloads",
    "meeting_inbox_payloads_update_guard": "meeting_event_inbox_payloads",
    "meeting_inbox_ingress_delete_guard": (
        "meeting_event_inbox_ingress_receipts"
    ),
    "meeting_inbox_ingress_update_guard": (
        "meeting_event_inbox_ingress_receipts"
    ),
    "meeting_inbox_markers_delete_guard": "meeting_event_inbox_markers",
    "meeting_inbox_markers_update_guard": "meeting_event_inbox_markers",
}


def _create_schema(
    connection: sqlite3.Connection,
    *,
    raw_payload_retention_seconds: int,
    max_ingress_receipts: int,
) -> None:
    connection.execute(
        """
        CREATE TABLE meeting_event_inbox_metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_name TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            raw_payload_retention_seconds INTEGER NOT NULL,
            max_ingress_receipts INTEGER NOT NULL
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE TABLE meeting_event_inbox_streams (
            stream_binding_sha256 TEXT PRIMARY KEY
                CHECK (length(stream_binding_sha256) = 64),
            authorization_id TEXT NOT NULL,
            transport_binding_id TEXT NOT NULL,
            stream_identity_sha256 TEXT NOT NULL
                CHECK (length(stream_identity_sha256) = 64),
            authorization_sha256 TEXT NOT NULL
                CHECK (length(authorization_sha256) = 64),
            transport_binding_sha256 TEXT NOT NULL
                CHECK (length(transport_binding_sha256) = 64),
            max_event_count INTEGER NOT NULL
                CHECK (max_event_count > 2 AND max_event_count <= 20000),
            max_transcript_characters INTEGER NOT NULL
                CHECK (
                    max_transcript_characters > 0
                    AND max_transcript_characters <= 200000
                ),
            created_at TEXT NOT NULL,
            UNIQUE (
                authorization_id,
                transport_binding_id,
                stream_identity_sha256
            )
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE TABLE meeting_event_inbox_events (
            event_identity_sha256 TEXT PRIMARY KEY
                CHECK (length(event_identity_sha256) = 64),
            event_fingerprint_sha256 TEXT NOT NULL
                CHECK (length(event_fingerprint_sha256) = 64),
            stream_binding_sha256 TEXT NOT NULL
                REFERENCES meeting_event_inbox_streams(stream_binding_sha256),
            sequence INTEGER NOT NULL CHECK (sequence > 0 AND sequence <= 20000),
            kind TEXT NOT NULL CHECK (
                kind IN (
                    'STREAM_STARTED',
                    'PARTICIPANT_JOINED',
                    'PARTICIPANT_LEFT',
                    'TRANSCRIPT_SEGMENT',
                    'STREAM_STOPPED'
                )
            ),
            received_at TEXT NOT NULL,
            persisted_at TEXT NOT NULL,
            raw_payload_expires_at TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            transcript_character_count INTEGER NOT NULL
                CHECK (transcript_character_count >= 0),
            UNIQUE (stream_binding_sha256, sequence)
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE TABLE meeting_event_inbox_payloads (
            event_identity_sha256 TEXT PRIMARY KEY
                REFERENCES meeting_event_inbox_events(event_identity_sha256),
            payload BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            raw_payload_expires_at TEXT NOT NULL
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE TABLE meeting_event_inbox_ingress_receipts (
            ingress_key_sha256 TEXT PRIMARY KEY
                CHECK (length(ingress_key_sha256) = 64),
            ingress_input_sha256 TEXT NOT NULL
                CHECK (length(ingress_input_sha256) = 64),
            stream_binding_sha256 TEXT NOT NULL
                REFERENCES meeting_event_inbox_streams(stream_binding_sha256),
            event_identity_sha256 TEXT NOT NULL
                REFERENCES meeting_event_inbox_events(event_identity_sha256),
            event_fingerprint_sha256 TEXT NOT NULL
                CHECK (length(event_fingerprint_sha256) = 64),
            sequence INTEGER NOT NULL CHECK (sequence > 0 AND sequence <= 20000),
            kind TEXT NOT NULL CHECK (
                kind IN (
                    'STREAM_STARTED',
                    'PARTICIPANT_JOINED',
                    'PARTICIPANT_LEFT',
                    'TRANSCRIPT_SEGMENT',
                    'STREAM_STOPPED'
                )
            ),
            disposition TEXT NOT NULL
                CHECK (disposition IN ('ACCEPTED', 'EXACT_DUPLICATE')),
            recorded_at TEXT NOT NULL
        ) STRICT
        """
    )
    connection.execute(
        """
        CREATE TABLE meeting_event_inbox_markers (
            marker_identity_sha256 TEXT PRIMARY KEY
                CHECK (length(marker_identity_sha256) = 64),
            stream_binding_sha256 TEXT NOT NULL UNIQUE
                REFERENCES meeting_event_inbox_streams(stream_binding_sha256),
            taint_kind TEXT NOT NULL CHECK (
                taint_kind IN ('TAINTED_CONFLICT', 'TAINTED_CAPACITY')
            ),
            ingress_key_sha256 TEXT NOT NULL
                CHECK (length(ingress_key_sha256) = 64),
            ingress_input_sha256 TEXT NOT NULL
                CHECK (length(ingress_input_sha256) = 64),
            event_identity_sha256 TEXT NOT NULL
                CHECK (length(event_identity_sha256) = 64),
            sequence INTEGER NOT NULL CHECK (sequence > 0 AND sequence <= 20000),
            detected_at TEXT NOT NULL
        ) STRICT
        """
    )

    guarded_tables = {
        "metadata": "meeting_event_inbox_metadata",
        "streams": "meeting_event_inbox_streams",
        "events": "meeting_event_inbox_events",
        "ingress": "meeting_event_inbox_ingress_receipts",
        "markers": "meeting_event_inbox_markers",
    }
    for label, table in guarded_tables.items():
        connection.execute(
            """
            CREATE TRIGGER meeting_inbox_{0}_update_guard
            BEFORE UPDATE ON {1}
            BEGIN
                SELECT RAISE(ABORT, 'meeting inbox history is append-only');
            END
            """.format(label, table)
        )
        connection.execute(
            """
            CREATE TRIGGER meeting_inbox_{0}_delete_guard
            BEFORE DELETE ON {1}
            BEGIN
                SELECT RAISE(ABORT, 'meeting inbox history is append-only');
            END
            """.format(label, table)
        )
    connection.execute(
        """
        CREATE TRIGGER meeting_inbox_payloads_update_guard
        BEFORE UPDATE ON meeting_event_inbox_payloads
        BEGIN
            SELECT RAISE(ABORT, 'meeting inbox payloads are immutable');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER meeting_inbox_payloads_delete_guard
        BEFORE DELETE ON meeting_event_inbox_payloads
        WHEN exitspec_meeting_retention_delete_allowed() != 1
        BEGIN
            SELECT RAISE(ABORT, 'meeting inbox payload deletion is retention-only');
        END
        """
    )
    connection.execute(
        """
        INSERT INTO meeting_event_inbox_metadata (
            singleton,
            schema_name,
            schema_version,
            raw_payload_retention_seconds,
            max_ingress_receipts
        ) VALUES (1, ?, ?, ?, ?)
        """,
        (
            MEETING_EVENT_INBOX_SCHEMA_NAME,
            MEETING_EVENT_INBOX_SCHEMA_VERSION,
            raw_payload_retention_seconds,
            max_ingress_receipts,
        ),
    )
    connection.execute(
        "PRAGMA user_version = {0}".format(
            MEETING_EVENT_INBOX_SCHEMA_VERSION
        )
    )


def _verify_schema(
    connection: sqlite3.Connection,
    *,
    raw_payload_retention_seconds: int,
    max_ingress_receipts: int,
) -> None:
    tables = frozenset(
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
              FROM sqlite_schema
             WHERE type = 'table'
               AND name NOT LIKE 'sqlite_%'
            """
        )
    )
    if tables != frozenset(_EXPECTED_TABLE_COLUMNS):
        raise MeetingEventInboxIntegrityError(
            "Meeting event inbox contains an unsupported table layout."
        )
    for table_name, expected_columns in _EXPECTED_TABLE_COLUMNS.items():
        actual_columns = tuple(
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info({0})".format(table_name)
            )
        )
        if actual_columns != expected_columns:
            raise MeetingEventInboxIntegrityError(
                "Meeting event inbox schema is corrupt."
            )

    trigger_rows = connection.execute(
        """
        SELECT name, tbl_name, sql
          FROM sqlite_schema
         WHERE type = 'trigger'
        """
    ).fetchall()
    triggers = {str(row["name"]): row for row in trigger_rows}
    if frozenset(triggers) != frozenset(_EXPECTED_TRIGGER_TABLES):
        raise MeetingEventInboxIntegrityError(
            "Meeting event inbox mutation guards are missing or unsupported."
        )
    for trigger_name, expected_table in _EXPECTED_TRIGGER_TABLES.items():
        row = triggers[trigger_name]
        sql = str(row["sql"] or "")
        if row["tbl_name"] != expected_table or "RAISE(ABORT" not in sql:
            raise MeetingEventInboxIntegrityError(
                "Meeting event inbox mutation guard is corrupt."
            )
        if trigger_name == "meeting_inbox_payloads_delete_guard" and (
            "exitspec_meeting_retention_delete_allowed() != 1" not in sql
        ):
            raise MeetingEventInboxIntegrityError(
                "Meeting payload retention guard is corrupt."
            )

    metadata_rows = connection.execute(
        """
        SELECT singleton,
               schema_name,
               schema_version,
               raw_payload_retention_seconds,
               max_ingress_receipts
          FROM meeting_event_inbox_metadata
        """
    ).fetchall()
    if len(metadata_rows) != 1:
        raise MeetingEventInboxIntegrityError(
            "Meeting event inbox metadata is corrupt."
        )
    metadata = metadata_rows[0]
    if (
        metadata["singleton"] != 1
        or metadata["schema_name"] != MEETING_EVENT_INBOX_SCHEMA_NAME
        or metadata["schema_version"] != MEETING_EVENT_INBOX_SCHEMA_VERSION
        or metadata["raw_payload_retention_seconds"]
        != raw_payload_retention_seconds
        or metadata["max_ingress_receipts"] != max_ingress_receipts
    ):
        raise MeetingEventInboxIntegrityError(
            "Meeting event inbox configuration does not match durable metadata."
        )
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if user_version != MEETING_EVENT_INBOX_SCHEMA_VERSION:
        raise MeetingEventInboxIntegrityError(
            "Meeting event inbox schema version is unsupported."
        )


def _event_payload_bytes(event: MeetingTranscriptEvent) -> bytes:
    """Module-private codec; private models remain non-serializable publicly."""

    payload = {
        "adapter_id": event.adapter_id,
        "adapter_version": event.adapter_version,
        "authority": event.authority,
        "event_id": event.event_id,
        "kind": event.kind.value,
        "meeting_id": event.meeting_id,
        "participant_id": event.participant_id,
        "participant_ids": list(event.participant_ids),
        "participant_label": event.participant_label,
        "provider_timestamp_ms": event.provider_timestamp_ms,
        "received_at": _canonical_timestamp(event.received_at),
        "review_state": event.review_state,
        "schema_version": event.schema_version,
        "segment_end_ms": event.segment_end_ms,
        "segment_start_ms": event.segment_start_ms,
        "sequence": event.sequence,
        "stop_reason": event.stop_reason,
        "stream_id": event.stream_id,
        "synthetic_only": event.synthetic_only,
        "transcript_text": event.transcript_text,
        "transport_binding_id": event.transport_binding_id,
    }
    try:
        return canonical_json_bytes(payload)
    except CanonicalizationError as exc:
        raise MeetingEventInboxIntegrityError(
            "Private meeting event could not be canonicalized."
        ) from exc


def _event_from_rows(
    event_row: sqlite3.Row,
    payload_row: sqlite3.Row,
) -> MeetingTranscriptEvent:
    try:
        event_identity = event_row["event_identity_sha256"]
        event_fingerprint = event_row["event_fingerprint_sha256"]
        stream_binding = event_row["stream_binding_sha256"]
        sequence = event_row["sequence"]
        kind = MeetingEventKind(event_row["kind"])
        received_at = _parse_canonical_timestamp(event_row["received_at"])
        persisted_at = _parse_canonical_timestamp(event_row["persisted_at"])
        expires_at = _parse_canonical_timestamp(
            event_row["raw_payload_expires_at"]
        )
        event_payload_sha256 = event_row["payload_sha256"]
        transcript_character_count = event_row[
            "transcript_character_count"
        ]
        payload = payload_row["payload"]
        payload_sha256 = payload_row["payload_sha256"]
        payload_expires_at = _parse_canonical_timestamp(
            payload_row["raw_payload_expires_at"]
        )
        _require_sha256(event_identity, "event_identity_sha256")
        _require_sha256(event_fingerprint, "event_fingerprint_sha256")
        _require_sha256(stream_binding, "stream_binding_sha256")
        _require_sha256(event_payload_sha256, "payload_sha256")
        _require_sha256(payload_sha256, "payload_sha256")
        if (
            not isinstance(payload, bytes)
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence <= 0
            or not isinstance(transcript_character_count, int)
            or isinstance(transcript_character_count, bool)
            or transcript_character_count < 0
            or persisted_at < received_at
            or expires_at <= persisted_at
            or payload_expires_at != expires_at
        ):
            raise ValueError
        actual_payload_sha256 = hashlib.sha256(payload).hexdigest()
        if (
            not hmac.compare_digest(payload_sha256, actual_payload_sha256)
            or not hmac.compare_digest(
                event_payload_sha256,
                actual_payload_sha256,
            )
        ):
            raise ValueError
        decoded = json.loads(payload.decode("utf-8"))
        if canonical_json_bytes(decoded) != payload:
            raise ValueError
        event = MeetingTranscriptEvent.model_validate(decoded)
        if (
            event.sequence != sequence
            or event.kind is not kind
            or event.received_at != received_at
            or len(event.transcript_text or "")
            != transcript_character_count
        ):
            raise ValueError
        return event
    except (
        CanonicalizationError,
        json.JSONDecodeError,
        KeyError,
        PrivateMeetingConnectorValidationError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise MeetingEventInboxIntegrityError(
            "Persisted private meeting payload is invalid."
        ) from exc


def _receipt_from_joined_row(
    row: sqlite3.Row,
    *,
    idempotent_replay: bool,
) -> MeetingEventInboxReceipt:
    try:
        values = {
            "ingress_key_sha256": row["ingress_key_sha256"],
            "ingress_input_sha256": row["ingress_input_sha256"],
            "stream_binding_sha256": row["stream_binding_sha256"],
            "event_identity_sha256": row["event_identity_sha256"],
            "event_fingerprint_sha256": row[
                "event_fingerprint_sha256"
            ],
        }
        for name, value in values.items():
            _require_sha256(value, name)
        sequence = row["sequence"]
        kind = MeetingEventKind(row["kind"])
        disposition = MeetingInboxDisposition(row["disposition"])
        event_persisted_at = _parse_canonical_timestamp(
            row["event_persisted_at"]
        )
        ingress_recorded_at = _parse_canonical_timestamp(
            row["ingress_recorded_at"]
        )
        raw_payload_expires_at = _parse_canonical_timestamp(
            row["raw_payload_expires_at"]
        )
        _require_sha256(row["payload_sha256"], "payload_sha256")
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence <= 0
            or sequence > _MAX_INGRESS_RECEIPTS
        ):
            raise ValueError
        return MeetingEventInboxReceipt(
            **values,
            sequence=sequence,
            kind=kind,
            disposition=disposition,
            event_persisted_at=event_persisted_at,
            ingress_recorded_at=ingress_recorded_at,
            raw_payload_expires_at=raw_payload_expires_at,
            idempotent_replay=idempotent_replay,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MeetingEventInboxIntegrityError(
            "Persisted meeting ingress receipt is corrupt."
        ) from exc


def _validate_ingress_history(
    connection: sqlite3.Connection,
    *,
    stream_binding_sha256: str,
    event_rows: Sequence[sqlite3.Row],
) -> dict[str, int]:
    events_by_identity = {
        str(row["event_identity_sha256"]): row for row in event_rows
    }
    accepted_counts = {identity: 0 for identity in events_by_identity}
    duplicate_counts = {identity: 0 for identity in events_by_identity}
    ingress_rows = connection.execute(
        """
        SELECT ingress_key_sha256,
               ingress_input_sha256,
               stream_binding_sha256,
               event_identity_sha256,
               event_fingerprint_sha256,
               sequence,
               kind,
               disposition,
               recorded_at
          FROM meeting_event_inbox_ingress_receipts
         WHERE stream_binding_sha256 = ?
        """,
        (stream_binding_sha256,),
    ).fetchall()
    try:
        for row in ingress_rows:
            ingress_key = row["ingress_key_sha256"]
            ingress_input = row["ingress_input_sha256"]
            stored_stream = row["stream_binding_sha256"]
            event_identity = row["event_identity_sha256"]
            fingerprint = row["event_fingerprint_sha256"]
            for name, value in (
                ("ingress_key_sha256", ingress_key),
                ("ingress_input_sha256", ingress_input),
                ("stream_binding_sha256", stored_stream),
                ("event_identity_sha256", event_identity),
                ("event_fingerprint_sha256", fingerprint),
            ):
                _require_sha256(value, name)
            event_row = events_by_identity.get(event_identity)
            if event_row is None:
                raise ValueError
            disposition = MeetingInboxDisposition(row["disposition"])
            recorded_at = _parse_canonical_timestamp(row["recorded_at"])
            event_persisted_at = _parse_canonical_timestamp(
                event_row["persisted_at"]
            )
            expected_input = _domain_sha256(
                _INGRESS_INPUT_DOMAIN,
                {
                    "event_fingerprint_sha256": event_row[
                        "event_fingerprint_sha256"
                    ],
                    "event_identity_sha256": event_identity,
                    "payload_sha256": event_row["payload_sha256"],
                    "stream_binding_sha256": stream_binding_sha256,
                },
            )
            if (
                stored_stream != stream_binding_sha256
                or ingress_input != expected_input
                or fingerprint != event_row["event_fingerprint_sha256"]
                or row["sequence"] != event_row["sequence"]
                or row["kind"] != event_row["kind"]
                or recorded_at < event_persisted_at
            ):
                raise ValueError
            if disposition is MeetingInboxDisposition.ACCEPTED:
                accepted_counts[event_identity] += 1
            else:
                duplicate_counts[event_identity] += 1
        if any(count != 1 for count in accepted_counts.values()):
            raise ValueError
        return duplicate_counts
    except (KeyError, TypeError, ValueError) as exc:
        raise MeetingEventInboxIntegrityError(
            "Persisted meeting ingress history is corrupt."
        ) from exc


def _validate_stream_row(
    row: sqlite3.Row,
    *,
    authorization: MeetingCaptureAuthorization,
    binding: MeetingTransportBinding,
    expected_stream_binding_sha256: str,
) -> None:
    try:
        stored_stream_binding = row["stream_binding_sha256"]
        authorization_sha256 = row["authorization_sha256"]
        transport_binding_sha256 = row["transport_binding_sha256"]
        created_at = _parse_canonical_timestamp(row["created_at"])
        _require_sha256(stored_stream_binding, "stream_binding_sha256")
        _require_sha256(authorization_sha256, "authorization_sha256")
        _require_sha256(
            transport_binding_sha256,
            "transport_binding_sha256",
        )
        if (
            stored_stream_binding != expected_stream_binding_sha256
            or row["authorization_id"] != authorization.authorization_id
            or row["transport_binding_id"] != binding.binding_id
            or row["stream_identity_sha256"]
            != binding.stream_identity_sha256
            or authorization_sha256 != _model_sha256(authorization)
            or transport_binding_sha256 != _model_sha256(binding)
            or row["max_event_count"] != authorization.max_event_count
            or row["max_transcript_characters"]
            != authorization.max_transcript_characters
            or created_at < binding.established_at
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise MeetingEventInboxIntegrityError(
            "Persisted meeting stream binding is corrupt or contradictory."
        ) from exc


def _require_exact_existing_event(
    row: sqlite3.Row,
    *,
    stream_binding_sha256: str,
    event: MeetingTranscriptEvent,
    event_fingerprint_sha256: str,
    payload_sha256: str,
    ingress_recorded_at: datetime,
) -> None:
    try:
        _require_sha256(row["event_identity_sha256"], "event_identity_sha256")
        _require_sha256(
            row["event_fingerprint_sha256"],
            "event_fingerprint_sha256",
        )
        _require_sha256(row["stream_binding_sha256"], "stream_binding_sha256")
        _require_sha256(row["payload_sha256"], "payload_sha256")
        received_at = _parse_canonical_timestamp(row["received_at"])
        persisted_at = _parse_canonical_timestamp(row["persisted_at"])
        expires_at = _parse_canonical_timestamp(row["raw_payload_expires_at"])
        if persisted_at < received_at or expires_at <= persisted_at:
            raise MeetingEventInboxIntegrityError(
                "Persisted meeting event times are contradictory."
            )
        if ingress_recorded_at < persisted_at:
            raise MeetingConnectorDenied(
                MeetingConnectorFailureCode.TIMELINE_INVALID
            )
        changed = (
            row["event_fingerprint_sha256"] != event_fingerprint_sha256
            or row["stream_binding_sha256"] != stream_binding_sha256
            or row["sequence"] != event.sequence
            or row["kind"] != event.kind.value
            or received_at != event.received_at
            or row["payload_sha256"] != payload_sha256
            or row["transcript_character_count"]
            != len(event.transcript_text or "")
        )
        if changed:
            raise MeetingEventInboxConflict()
    except (MeetingConnectorDenied, MeetingEventInboxError):
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise MeetingEventInboxIntegrityError(
            "Persisted meeting event receipt is corrupt."
        ) from exc


def _stream_binding_sha256(
    authorization: MeetingCaptureAuthorization,
    binding: MeetingTransportBinding,
) -> str:
    validate_meeting_transport_binding(authorization, binding)
    return _domain_sha256(
        _STREAM_BINDING_DOMAIN,
        {
            "authorization_sha256": _model_sha256(authorization),
            "transport_binding_sha256": _model_sha256(binding),
        },
    )


def _event_identity_sha256(
    *,
    stream_binding_sha256: str,
    event_id: str,
) -> str:
    _require_sha256(stream_binding_sha256, "stream_binding_sha256")
    return _domain_sha256(
        _EVENT_IDENTITY_DOMAIN,
        {
            "event_id": event_id,
            "stream_binding_sha256": stream_binding_sha256,
        },
    )


def _model_sha256(
    value: MeetingCaptureAuthorization | MeetingTransportBinding,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value.model_dump(mode="json"))
    ).hexdigest()


def _error_for_taint(
    taint: MeetingInboxTaintKind,
) -> MeetingEventInboxError:
    if taint is MeetingInboxTaintKind.CONFLICT:
        return MeetingEventInboxConflict()
    if taint is MeetingInboxTaintKind.CAPACITY:
        return MeetingEventInboxCapacityError()
    raise MeetingEventInboxIntegrityError(
        "Meeting inbox taint marker is unsupported."
    )


def _domain_sha256(domain: bytes, payload: object) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _parse_canonical_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _CANONICAL_TIMESTAMP.fullmatch(value):
        raise ValueError("timestamp is not canonical UTC.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if _canonical_timestamp(parsed) != value:
        raise ValueError("timestamp is not canonical UTC.")
    return parsed.astimezone(timezone.utc)


def _require_aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("{0} must be a datetime.".format(name))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("{0} must be timezone-aware.".format(name))
    return value.astimezone(timezone.utc)


def _require_sha256(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(
            "{0} must be 64 lowercase hexadecimal characters.".format(name)
        )


def _require_ingress_key(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or value != value.strip()
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise ValueError(
            "ingress_idempotency_key must be 1-200 printable characters "
            "with no surrounding whitespace."
        )


def _require_safe_database_path(
    database_path: str | os.PathLike[str],
) -> Path:
    try:
        raw_path = os.fspath(database_path)
    except TypeError as exc:
        raise TypeError("database_path must be path-like.") from exc
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise ValueError("database_path must be a non-empty filesystem path.")
    if raw_path == ":memory:" or raw_path.startswith("file:"):
        raise ValueError("database_path must identify a durable local file.")
    unexpanded = Path(raw_path)
    if not unexpanded.is_absolute():
        raise ValueError("database_path must be absolute.")
    if any(part in (".", "..") for part in unexpanded.parts):
        raise ValueError("database_path cannot contain traversal components.")
    if unexpanded.name in ("", ".", ".."):
        raise ValueError("database_path must identify a file.")
    parent = unexpanded.parent
    if not parent.exists() or not parent.is_dir():
        raise ValueError("database_path parent must be an existing directory.")
    if unexpanded.exists():
        if unexpanded.is_symlink():
            raise ValueError("database_path cannot be a symbolic link.")
        if not unexpanded.is_file():
            raise ValueError("database_path must identify a regular file.")
    resolved_parent = parent.resolve(strict=True)
    return resolved_parent / unexpanded.name


def _tighten_sqlite_files(database_path: Path) -> None:
    try:
        for candidate in (
            database_path,
            Path(str(database_path) + "-wal"),
            Path(str(database_path) + "-shm"),
        ):
            if candidate.exists():
                if candidate.is_symlink() or not candidate.is_file():
                    raise OSError
                os.chmod(candidate, 0o600)
    except OSError as exc:
        raise MeetingEventInboxStorageError(
            "Could not enforce private meeting inbox file permissions."
        ) from exc


@contextmanager
def _database_initialization_lock(database_path: Path) -> Iterator[None]:
    lock_path = database_path.parent / (
        ".{0}.initialize.lock".format(database_path.name)
    )
    if lock_path.is_symlink():
        raise MeetingEventInboxIntegrityError(
            "Meeting event inbox initialization lock cannot be a symlink."
        )
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise MeetingEventInboxStorageError(
            "Could not acquire the meeting inbox initialization lock."
        ) from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _immediate_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "MEETING_EVENT_INBOX_SCHEMA_NAME",
    "MEETING_EVENT_INBOX_SCHEMA_VERSION",
    "MEETING_EVENT_INBOX_VERSION",
    "MeetingEventInboxCapacityError",
    "MeetingEventInboxConflict",
    "MeetingEventInboxError",
    "MeetingEventInboxIntegrityError",
    "MeetingEventInboxPayloadExpired",
    "MeetingEventInboxReceipt",
    "MeetingEventInboxStorageError",
    "MeetingEventInboxStreamReceipt",
    "MeetingInboxDisposition",
    "MeetingInboxTaintKind",
    "PrivateMeetingEventInboxSerializationError",
    "RecoveredMeetingEventStream",
    "SQLiteMeetingEventInbox",
]
