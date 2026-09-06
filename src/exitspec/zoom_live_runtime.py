"""Operator-paired, process-local native Zoom capture to existing source intake."""

from __future__ import annotations

import base64
import hashlib
import hmac
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass, field

from .canonical import canonical_json_bytes
from .zoom_live_ipc import ZoomPipeChild
from .zoom_native_transcript import (
    ZoomNativeStreamBinding,
    decode_zoom_native_transcript,
)


class ZoomLiveError(ValueError):
    def __init__(self):
        super().__init__(
            "The live Zoom operation was refused. Review the local operator setup."
        )


@dataclass(frozen=True, repr=False)
class ZoomLiveSettings:
    client_id: str
    client_secret: str
    webhook_secret: str
    meeting_uuid: str
    participant_ids: tuple[int, ...]
    callback_port: int
    callback_host: str
    callback_path: str
    owner_receipt_id: str
    code_revision: str
    budget_ceiling_usd: str
    maximum_capture_seconds: int
    live_network_authorized: bool
    credential_rotation_confirmed: bool
    synthetic_requirements_consent: bool
    credits_and_budget_confirmed: bool

    def validate(self):
        for value, minimum, maximum in (
            (self.client_id, 8, 512),
            (self.client_secret, 16, 1024),
            (self.webhook_secret, 16, 1024),
            (self.meeting_uuid, 1, 256),
        ):
            if (
                type(value) is not str
                or not minimum <= len(value) <= maximum
                or any(ord(c) < 32 for c in value)
            ):
                raise ZoomLiveError()
        if (
            type(self.participant_ids) is not tuple
            or not 1 <= len(self.participant_ids) <= 2
        ):
            raise ZoomLiveError()
        if any(type(i) is not int or not 1 <= i < 2**32 for i in self.participant_ids):
            raise ZoomLiveError()
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ZoomLiveError()
        if (
            type(self.callback_port) is not int
            or not 1024 <= self.callback_port <= 65535
        ):
            raise ZoomLiveError()
        if (
            type(self.callback_host) is not str
            or not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]{1,5})?", self.callback_host)
            or len(self.callback_host) > 255
        ):
            raise ZoomLiveError()
        if type(self.callback_path) is not str or not re.fullmatch(
            r"/zoom-webhook/[a-z0-9_-]{24,96}", self.callback_path
        ):
            raise ZoomLiveError()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", self.owner_receipt_id):
            raise ZoomLiveError()
        if not re.fullmatch(r"[a-f0-9]{40}", self.code_revision):
            raise ZoomLiveError()
        if (
            type(self.budget_ceiling_usd) is not str
            or not re.fullmatch(
                r"(?:0|[1-9][0-9]{0,3})\.[0-9]{2}", self.budget_ceiling_usd
            )
            or self.budget_ceiling_usd == "0.00"
        ):
            raise ZoomLiveError()
        if (
            type(self.maximum_capture_seconds) is not int
            or not 60 <= self.maximum_capture_seconds <= 900
        ):
            raise ZoomLiveError()
        if any(
            value is not True
            for value in (
                self.live_network_authorized,
                self.credential_rotation_confirmed,
                self.synthetic_requirements_consent,
                self.credits_and_budget_confirmed,
            )
        ):
            raise ZoomLiveError()


@dataclass(repr=False)
class _Capture:
    poc_id: str
    settings: ZoomLiveSettings | None
    generation: str
    session_id: str
    expires: float
    state: str = "PAIRED"
    connected: bool = False
    binding: ZoomNativeStreamBinding | None = None
    stream_id: str | None = None
    child: object = None
    seq: int = 0
    packets: dict = field(default_factory=dict)
    total_bytes: int = 0
    total_text: int = 0
    segment_count: int = 0
    operations: dict = field(default_factory=dict)
    receipt: object = None
    provenance: dict | None = None
    failure: str | None = None
    consent_digest: str | None = None
    stop_deadline: float | None = None
    authorization_summary: dict = field(default_factory=dict)


class ZoomLiveRuntime:
    """Parent is the only native decoder/attachment authority; pipes are private.

    Local operator calls pair() directly. Browser APIs cannot construct settings
    or pair a meeting. The same OS user and installed app code are trusted.
    """

    def __init__(
        self,
        *,
        drafts,
        intake,
        run_if_open,
        child_factory=ZoomPipeChild,
        clock=time.monotonic,
        fake_network=False,
    ):
        self._drafts, self._intake, self._run_if_open = drafts, intake, run_if_open
        self._factory, self._clock = child_factory, clock
        self._mode = "FAKE_ZOOM_RTMS" if fake_network else "LIVE_ZOOM_RTMS"
        if fake_network and child_factory is ZoomPipeChild:
            raise ZoomLiveError()
        self._lock = threading.RLock()
        self._key = secrets.token_bytes(32)
        self._record = None
        self._closed = threading.Event()
        threading.Thread(target=self._watch, daemon=True).start()

    def _digest(self, value):
        return hmac.new(
            self._key, canonical_json_bytes(value), hashlib.sha256
        ).hexdigest()

    def _draft(self, poc_id):
        try:
            draft = self._drafts.get(poc_id)
            if (
                draft.archive_state.value == "ACTIVE"
                and draft.first_source_choice.value == "MEETING"
            ):
                return draft
        except Exception:  # noqa: BLE001 - private boundary always fails closed without error detail
            draft = None
        raise ZoomLiveError()

    def pair(self, poc_id, settings):
        """Operator-local bootstrap, deliberately absent from the HTTP router."""
        if self._closed.is_set():
            raise ZoomLiveError()
        if type(settings) is not ZoomLiveSettings:
            raise ZoomLiveError()
        settings.validate()

        def paired():
            self._draft(poc_id)
            with self._lock:
                self._revoke_locked("REPLACED")
                generation = secrets.token_hex(32)
                self._record = _Capture(
                    poc_id,
                    settings,
                    generation,
                    "zoomsess_" + self._digest(generation),
                    self._clock() + settings.maximum_capture_seconds,
                )
                self._record.authorization_summary = {
                    "approved_spending_ceiling_usd": settings.budget_ceiling_usd,
                    "maximum_capture_seconds": settings.maximum_capture_seconds,
                }
                return self._snapshot(poc_id)

        return self._run_if_open(poc_id, paired)

    def _snapshot(self, poc_id):
        r = self._record
        matched = r is not None and r.poc_id == poc_id
        receipt = r.receipt if matched else None
        return {
            "schema_version": "exitspec.zoom-live/1.0",
            "poc_id": poc_id,
            "session_id": r.session_id if matched else None,
            "state": r.state if matched else "UNPAIRED",
            "transport_mode": self._mode if matched else "DISABLED",
            "provider_connected": r.connected if matched else False,
            "source_content_classification": "SYNTHETIC_REQUIREMENTS_ONLY",
            "capture_scope": "BOUNDED_WINDOW_NOT_COMPLETE_MEETING",
            "segment_count": r.segment_count if matched else 0,
            "proposal_count": receipt.proposal_count if receipt else 0,
            "source_receipt_id": receipt.source_receipt_id if receipt else None,
            "review_url": f"/app/pocs/{poc_id}/review" if receipt else None,
            "failure_code": r.failure if matched else None,
        }

    def current(self, poc_id):
        self._draft(poc_id)
        self.tick()
        with self._lock:
            return self._snapshot(poc_id)

    def action(self, poc_id, payload):
        if type(payload) is not dict:
            raise ZoomLiveError()
        action = payload.get("action")
        required = {"action", "session_id", "idempotency_key"}
        if action == "start":
            required.add("consent_acknowledged")
        if set(payload) != required or action not in {
            "start",
            "stop",
            "process",
            "reset",
        }:
            raise ZoomLiveError()
        key = payload["idempotency_key"]
        if type(key) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
            raise ZoomLiveError()

        def mutate():
            draft = self._draft(poc_id)
            with self._lock:
                r = self._record
                if (
                    r is None
                    or r.poc_id != poc_id
                    or payload["session_id"] != r.session_id
                ):
                    raise ZoomLiveError()
                if self._clock() >= r.expires:
                    self._revoke_locked("EXPIRED")
                    raise ZoomLiveError()
                digest = self._digest(payload)
                prior = r.operations.get(key)
                if prior is not None:
                    if prior != digest:
                        raise ZoomLiveError()
                    return self._snapshot(poc_id)
                if len(r.operations) >= 64:
                    self._fail_locked("OPERATION_LIMIT")
                    raise ZoomLiveError()
                if action == "start":
                    if (
                        r.state != "PAIRED"
                        or payload["consent_acknowledged"] is not True
                    ):
                        raise ZoomLiveError()
                    r.consent_digest = self._digest(
                        {
                            "owner": r.settings.owner_receipt_id,
                            "session": r.session_id,
                            "poc": poc_id,
                            "disclosure": "zoom-live-synthetic-v1",
                            "ceiling": r.settings.budget_ceiling_usd,
                            "seconds": r.settings.maximum_capture_seconds,
                        }
                    )
                    r.state = "WAITING"
                    threading.Thread(
                        target=self._launch, args=(r,), daemon=True
                    ).start()
                elif action == "stop":
                    if r.state not in {"LISTENING", "INTERRUPTED", "RECONNECTING"}:
                        raise ZoomLiveError()
                    r.state, r.stop_deadline = "STOP_REQUESTED", self._clock() + 20
                    self._send_locked(r, "stop")
                elif action == "reset":
                    self._revoke_locked("RESET")
                else:
                    if r.state != "CAPTURE_READY" or not r.packets:
                        raise ZoomLiveError()
                    # Decoded objects are private parent-owned state, never IPC input.
                    text = "\n".join(
                        f"Speaker {s.speaker_pseudonym[-1]}: {s.text}"
                        for s in r.packets.values()
                    )
                    digest = self._digest(
                        {
                            "binding": r.binding.model_dump(),
                            "packets": [
                                s.normalized_sha256 for s in r.packets.values()
                            ],
                        }
                    )
                    with (
                        self._intake.native_attachment_guard(poc_id),
                        self._drafts.authoring_commit_guard(poc_id, draft),
                    ):
                        receipt = self._intake.capture_zoom_native_transcript(
                            poc_id=poc_id,
                            transcript_text=text,
                            source_binding_sha256=digest,
                            idempotency_key="zoom-native-" + r.generation,
                        )
                        source = self._intake.source_snapshot(
                            poc_id, receipt.source_receipt_id
                        )
                    r.receipt = receipt
                    r.provenance = {
                        "schema_version": "exitspec.zoom-live-receipt/1.0",
                        "poc_id": poc_id,
                        "session_id": r.session_id,
                        "source_receipt_id": receipt.source_receipt_id,
                        "transport_mode": self._mode,
                        "source_content_classification": "SYNTHETIC_REQUIREMENTS_ONLY",
                        "code_revision": r.binding.code_revision,
                        "source_binding_sha256": digest,
                        "redacted_content_sha256": source.content_sha256,
                        "capture_scope": "BOUNDED_WINDOW_NOT_COMPLETE_MEETING",
                        "provider_stop_acknowledged": True,
                        "normal_media_close_observed": True,
                        "measurement_validity": "NOT_ASSERTED",
                        **r.authorization_summary,
                    }
                    r.packets.clear()
                    r.state = "DRAFT_READY"
                r.operations[key] = self._digest(payload)
                return self._snapshot(poc_id)

        try:
            return self._run_if_open(poc_id, mutate)
        except Exception:  # noqa: BLE001 - private boundary always fails closed without error detail
            refused = True
        if refused:
            raise ZoomLiveError()

    def receipt(self, poc_id):
        self._draft(poc_id)
        with self._lock:
            r = self._record
            if r is None or r.poc_id != poc_id or r.provenance is None:
                raise ZoomLiveError()
            return dict(r.provenance)

    def _launch(self, r):
        with self._lock:
            if self._record is not r or r.state != "WAITING":
                return
            s = r.settings
            init = {
                "command": "init",
                "generation": r.generation,
                "clientId": s.client_id,
                "clientSecret": s.client_secret,
                "webhookSecret": s.webhook_secret,
                "expectedMeetingUuid": s.meeting_uuid,
                "callbackPort": s.callback_port,
                "callbackHost": s.callback_host,
                "callbackPath": s.callback_path,
                "_code_revision": s.code_revision,
            }
        # A fast child can respond before the constructor returns its handle.
        startup_lock = threading.Lock()
        startup_events = []
        ready = False

        def receive(event):
            nonlocal ready
            with startup_lock:
                if not ready:
                    if len(startup_events) >= 8:
                        self.failed(r.generation)
                    else:
                        startup_events.append(event)
                    return
            self.receive(r.generation, event)

        try:
            child = self._factory(init, receive, lambda: self.failed(r.generation))
            with self._lock:
                if self._record is not r or r.state in {"FAILED", "REVOKED"}:
                    child.close()
                else:
                    r.child = child
            with startup_lock:
                for event in startup_events:
                    self.receive(r.generation, event)
                startup_events.clear()
                ready = True
        except Exception:  # noqa: BLE001 - private boundary always fails closed without error detail
            self.failed(r.generation)

    def _send_locked(self, r, command):
        try:
            if r.child is None:
                raise ZoomLiveError()
            r.child.send(
                {
                    "command": command,
                    "generation": r.generation,
                    "stream_id": r.stream_id,
                }
            )
        except (ValueError, queue.Full):
            self._fail_locked("IPC_UNAVAILABLE")

    def receive(self, generation, event):
        """Only the callback captured by the private parent-owned child calls this."""
        with self._lock:
            r = self._record
            if (
                r is None
                or r.generation != generation
                or r.state in {"FAILED", "REVOKED", "DRAFT_READY"}
            ):
                return
            poc_id = r.poc_id

        def accept():
            self._draft(poc_id)
            with self._lock:
                if self._record is not r or r.state in {
                    "FAILED",
                    "REVOKED",
                    "DRAFT_READY",
                }:
                    return
                if type(event) is not dict or event.get("generation") != generation:
                    raise ZoomLiveError()
                if self._clock() >= r.expires:
                    raise ZoomLiveError()
                seq = event.get("seq")
                if type(seq) is not int or seq != r.seq + 1 or seq > 4096:
                    raise ZoomLiveError()
                kind = event.get("event")
                extras = {
                    "offer": set(),
                    "participant": {"user_id"},
                    "participant_left": {"user_id"},
                    "transcript": {"packet_base64"},
                    "failed": {"code"},
                    "listening": set(),
                    "interrupted": set(),
                    "reconnecting": set(),
                    "stop_ack": set(),
                    "drained": set(),
                }
                if (
                    kind not in extras
                    or set(event)
                    != {"event", "seq", "generation", "stream_id"} | extras[kind]
                ):
                    raise ZoomLiveError()
                r.seq = seq
                if (
                    kind == "failed"
                    and r.state == "WAITING"
                    and event["stream_id"] is None
                ):
                    self._fail_locked("TRANSPORT_FAILED")
                    return
                if kind == "offer":
                    if r.state != "WAITING" or r.stream_id is not None:
                        raise ZoomLiveError()
                    stream = event["stream_id"]
                    if type(stream) is not str or not 1 <= len(stream) <= 256:
                        raise ZoomLiveError()
                    r.stream_id = stream
                    s = r.settings
                    r.binding = ZoomNativeStreamBinding(
                        process_generation_sha256=self._digest(generation),
                        session_id=r.session_id,
                        poc_id=poc_id,
                        consent_receipt_sha256=r.consent_digest,
                        meeting_binding_sha256=self._digest(s.meeting_uuid),
                        stream_binding_sha256=self._digest(stream),
                        code_revision=s.code_revision,
                    )
                    self._send_locked(r, "bind")
                    return
                if event["stream_id"] != r.stream_id or r.binding is None:
                    raise ZoomLiveError()
                if kind == "failed":
                    self._fail_locked("TRANSPORT_FAILED")
                elif kind == "participant":
                    if (
                        type(event["user_id"]) is not int
                        or event["user_id"] not in r.settings.participant_ids
                    ):
                        raise ZoomLiveError()
                elif kind == "participant_left":
                    raise ZoomLiveError()
                elif kind == "listening":
                    if r.state not in {"WAITING", "RECONNECTING", "INTERRUPTED"}:
                        raise ZoomLiveError()
                    r.state, r.connected = "LISTENING", True
                elif kind in {"interrupted", "reconnecting"}:
                    if r.state not in {"LISTENING", "INTERRUPTED", "RECONNECTING"}:
                        raise ZoomLiveError()
                    r.state = kind.upper()
                    r.connected = False
                elif kind == "transcript":
                    if r.state not in {"LISTENING", "STOP_REQUESTED", "DRAINING"}:
                        raise ZoomLiveError()
                    encoded = event["packet_base64"]
                    if type(encoded) is not str or len(encoded) > 87384:
                        raise ZoomLiveError()
                    packet = base64.b64decode(encoded, validate=True)
                    packet_digest = hashlib.sha256(packet).hexdigest()
                    if packet_digest in r.packets:
                        return
                    if (
                        r.total_bytes + len(packet) > 1024 * 1024
                        or len(r.packets) >= 256
                    ):
                        raise ZoomLiveError()
                    segment = decode_zoom_native_transcript(
                        packet,
                        binding=r.binding,
                        speaker_pseudonyms={
                            p: f"SPEAKER_{i + 1}"
                            for i, p in enumerate(r.settings.participant_ids)
                        },
                        arrival_index=len(r.packets) + 1,
                    )
                    if r.total_text + len(segment.text) > 16000:
                        raise ZoomLiveError()
                    r.packets[packet_digest] = segment
                    r.segment_count = len(r.packets)
                    r.total_bytes += len(packet)
                    r.total_text += len(segment.text)
                elif kind == "stop_ack":
                    if r.state != "STOP_REQUESTED":
                        raise ZoomLiveError()
                    r.state = "DRAINING"
                elif kind == "drained":
                    if r.state != "DRAINING" or not r.packets:
                        raise ZoomLiveError()
                    r.state, r.connected = "CAPTURE_READY", False
                    r.stop_deadline = None
                    r.settings = None
                    if r.child:
                        r.child.close()

        try:
            self._run_if_open(poc_id, accept)
        except Exception:  # noqa: BLE001 - private boundary always fails closed without error detail
            with self._lock:
                if self._record is r and r.state not in {"REVOKED", "DRAFT_READY"}:
                    self._fail_locked("INVALID_EVENT")

    def failed(self, generation):
        with self._lock:
            if (
                self._record
                and self._record.generation == generation
                and self._record.state
                not in {"CAPTURE_READY", "DRAFT_READY", "REVOKED"}
            ):
                self._fail_locked("CAPTURE_FAILED")

    def _fail_locked(self, code):
        r = self._record
        if r:
            r.state, r.failure, r.connected = "FAILED", code, False
            if r.child:
                r.child.close()
            r.packets.clear()
            r.settings = None

    def _revoke_locked(self, reason):
        r = self._record
        if r:
            self._fail_locked(reason)
            r.state = "REVOKED"

    def revoke(self, poc_id, reason="CLOSED"):
        with self._lock:
            if self._record and self._record.poc_id == poc_id:
                self._revoke_locked(reason)

    def tick(self):
        with self._lock:
            r = self._record
            if not r or r.state in {"FAILED", "REVOKED", "DRAFT_READY"}:
                return
            if self._clock() >= r.expires or (
                r.stop_deadline is not None and self._clock() >= r.stop_deadline
            ):
                self._revoke_locked("TIMEOUT")
                return
            poc_id = r.poc_id
        try:
            self._run_if_open(poc_id, lambda: self._draft(poc_id))
        except Exception:  # noqa: BLE001 - private boundary always fails closed without error detail
            self.revoke(poc_id)

    def _watch(self):
        while not self._closed.wait(0.25):
            self.tick()

    def close(self):
        self._closed.set()
        with self._lock:
            self._revoke_locked("SERVER_CLOSED")
