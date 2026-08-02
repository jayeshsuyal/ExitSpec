"""Bounded human closure authority for terminal POC evidence.

Workspace projections remain read-only. This service owns the separate,
process-local mutation that records a human handoff/closure decision bound to
one exact contract, run, verdict, and Evidence Pack digest.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Callable, Literal, Optional, Tuple
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN, VerdictStatus


POC_ID_PATTERN = r"^poc_[a-z0-9][a-z0-9_-]{2,63}$"
_MAX_IDEMPOTENCY_KEY_LENGTH = 200
_MAX_CONFIGURABLE_RECORDS = 10_000


class HumanClosureDecision(str, Enum):
    """Bounded POC-lifecycle outcomes; neither value authorizes shipping."""

    HANDOFF_COMPLETED = "HANDOFF_COMPLETED"
    POC_STOPPED = "POC_STOPPED"


class TerminalEvidenceBinding(FrozenExitSpecModel):
    """Exact terminal evidence identity a human reviewed before closure."""

    poc_id: str = Field(pattern=POC_ID_PATTERN)
    contract_id: str = Field(min_length=1, max_length=160)
    contract_version: str = Field(min_length=1, max_length=100)
    contract_hash: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(min_length=1, max_length=200)
    verdict: VerdictStatus
    evidence_pack_url: str = Field(min_length=1, max_length=500)
    evidence_pack_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_local_evidence_pack_boundary(self) -> "TerminalEvidenceBinding":
        url = self.evidence_pack_url
        if (
            not url.startswith("/artifacts/")
            or not url.endswith("/decision-packet.html")
            or "\\" in url
            or "?" in url
            or "#" in url
            or any(part in {"", ".", ".."} for part in url.split("/")[2:])
        ):
            raise ValueError(
                "Evidence Pack URL must identify one local decision packet."
            )
        return self


class HumanPOCClosureRequest(FrozenExitSpecModel):
    """One explicit human terminal action over an exact evidence binding."""

    decision: HumanClosureDecision
    decided_by: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=2_000)
    evidence_binding: TerminalEvidenceBinding

    @field_validator("decided_by", "rationale", mode="before")
    @classmethod
    def normalize_human_text(cls, value: str) -> str:
        if type(value) is not str:
            raise ValueError("Human decision metadata must be text.")
        normalized = value.strip()
        if not normalized:
            raise ValueError("Human decision metadata cannot be blank.")
        return normalized


class HumanPOCClosureRecord(FrozenExitSpecModel):
    """Immutable process-local receipt for one completed POC lifecycle."""

    closure_id: str = Field(pattern=r"^poccl_[a-f0-9]{32}$")
    poc_id: str = Field(pattern=POC_ID_PATTERN)
    decision: HumanClosureDecision
    decided_by: str
    rationale: str
    recorded_at: datetime
    evidence_binding: TerminalEvidenceBinding
    evidence_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    authorization_scope: Literal["POC_LIFECYCLE_ONLY"] = "POC_LIFECYCLE_ONLY"
    shipping_authorized: Literal[False] = False

    @field_validator("recorded_at")
    @classmethod
    def require_timezone_aware_recorded_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Closure timestamps must be timezone-aware.")
        return value


class HumanPOCClosureResult(FrozenExitSpecModel):
    closure: HumanPOCClosureRecord
    idempotent_replay: bool


class POCClosureError(RuntimeError):
    """Base error for the process-local closure boundary."""


class POCClosureEvidenceUnavailable(POCClosureError):
    """No currently verified terminal Evidence Pack can authorize closure."""


class POCClosureBindingMismatch(POCClosureError):
    """The requested evidence identity is stale, incomplete, or forged."""


class POCClosureConflict(POCClosureError):
    """A different terminal decision already owns this POC."""


class POCClosureIdempotencyConflict(POCClosureError):
    """An idempotency key was reused for a different request."""


class POCClosureCapacityExceeded(POCClosureError):
    """The bounded process-local closure store has reached capacity."""


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    request_sha256: str
    poc_id: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _closure_id() -> str:
    return "poccl_{0}".format(uuid4().hex)


def _binding_digest(binding: TerminalEvidenceBinding) -> str:
    return hashlib.sha256(
        b"exitspec-terminal-evidence-binding-v1\x00"
        + canonical_json_bytes(binding.model_dump(mode="json"))
    ).hexdigest()


def _request_digest(poc_id: str, request: HumanPOCClosureRequest) -> str:
    return hashlib.sha256(
        b"exitspec-human-poc-closure-request-v1\x00"
        + canonical_json_bytes(
            {
                "poc_id": poc_id,
                "request": request.model_dump(mode="json"),
            }
        )
    ).hexdigest()


def _idempotency_digest(idempotency_key: str) -> str:
    if (
        type(idempotency_key) is not str
        or not idempotency_key
        or idempotency_key != idempotency_key.strip()
        or len(idempotency_key) > _MAX_IDEMPOTENCY_KEY_LENGTH
    ):
        raise ValueError(
            "idempotency_key must be exact and contain 1 to {0} characters.".format(
                _MAX_IDEMPOTENCY_KEY_LENGTH
            )
        )
    return hashlib.sha256(
        b"exitspec-human-poc-closure-idempotency-v1\x00"
        + idempotency_key.encode("utf-8")
    ).hexdigest()


class ProcessLocalPOCClosureService:
    """Thread-safe, bounded, non-durable human closure service."""

    def __init__(
        self,
        *,
        evidence_resolver: Callable[[str], Optional[TerminalEvidenceBinding]],
        clock: Callable[[], datetime] = _utc_now,
        closure_id_factory: Callable[[], str] = _closure_id,
        max_records: int = 1_024,
    ) -> None:
        if not callable(evidence_resolver):
            raise TypeError("evidence_resolver must be callable.")
        if not callable(clock) or not callable(closure_id_factory):
            raise TypeError("Closure clock and ID factory must be callable.")
        if (
            type(max_records) is not int
            or not 1 <= max_records <= _MAX_CONFIGURABLE_RECORDS
        ):
            raise ValueError(
                "max_records must be between 1 and {0}.".format(
                    _MAX_CONFIGURABLE_RECORDS
                )
            )
        self._evidence_resolver = evidence_resolver
        self._clock = clock
        self._closure_id_factory = closure_id_factory
        self._max_records = max_records
        self._closures: dict[str, HumanPOCClosureRecord] = {}
        self._idempotency: dict[str, _IdempotencyRecord] = {}
        self._lock = RLock()

    def record(
        self,
        poc_id: str,
        request: HumanPOCClosureRequest,
        *,
        idempotency_key: str,
    ) -> HumanPOCClosureResult:
        if type(request) is not HumanPOCClosureRequest:
            raise TypeError("request must be HumanPOCClosureRequest.")
        if request.evidence_binding.poc_id != poc_id:
            raise POCClosureBindingMismatch(
                "Evidence binding does not belong to the requested POC."
            )
        key_digest = _idempotency_digest(idempotency_key)
        request_sha256 = _request_digest(poc_id, request)

        with self._lock:
            prior_key = self._idempotency.get(key_digest)
            if prior_key is not None:
                if (
                    prior_key.poc_id != poc_id
                    or not hmac.compare_digest(
                        prior_key.request_sha256,
                        request_sha256,
                    )
                ):
                    raise POCClosureIdempotencyConflict(
                        "Idempotency key was reused with a different closure request."
                    )
                return HumanPOCClosureResult(
                    closure=self._closures[poc_id],
                    idempotent_replay=True,
                )

            prior_closure = self._closures.get(poc_id)
            if prior_closure is not None:
                raise POCClosureConflict(
                    "This POC already has a terminal human decision. Replay "
                    "the original idempotency key."
                )

            if len(self._closures) >= self._max_records:
                raise POCClosureCapacityExceeded(
                    "Process-local POC closure capacity has been reached."
                )

            authoritative = self._evidence_resolver(poc_id)
            if authoritative is None:
                raise POCClosureEvidenceUnavailable(
                    "A verified terminal verdict and Evidence Pack are required."
                )
            if type(authoritative) is not TerminalEvidenceBinding:
                raise POCClosureEvidenceUnavailable(
                    "Terminal evidence authority returned an invalid binding."
                )
            if not hmac.compare_digest(
                _binding_digest(authoritative),
                _binding_digest(request.evidence_binding),
            ):
                raise POCClosureBindingMismatch(
                    "Evidence binding does not match the current terminal Evidence Pack."
                )

            recorded_at = self._clock()
            closure = HumanPOCClosureRecord(
                closure_id=self._closure_id_factory(),
                poc_id=poc_id,
                decision=request.decision,
                decided_by=request.decided_by,
                rationale=request.rationale,
                recorded_at=recorded_at,
                evidence_binding=authoritative,
                evidence_binding_sha256=_binding_digest(authoritative),
            )
            self._closures[poc_id] = closure
            self._idempotency[key_digest] = _IdempotencyRecord(
                request_sha256=request_sha256,
                poc_id=poc_id,
            )
            return HumanPOCClosureResult(
                closure=closure,
                idempotent_replay=False,
            )

    def get(self, poc_id: str) -> Optional[HumanPOCClosureRecord]:
        with self._lock:
            return self._closures.get(poc_id)

    def records(self) -> Tuple[HumanPOCClosureRecord, ...]:
        with self._lock:
            return tuple(
                self._closures[poc_id] for poc_id in sorted(self._closures)
            )


__all__ = [
    "HumanClosureDecision",
    "HumanPOCClosureRecord",
    "HumanPOCClosureRequest",
    "HumanPOCClosureResult",
    "POCClosureBindingMismatch",
    "POCClosureCapacityExceeded",
    "POCClosureConflict",
    "POCClosureError",
    "POCClosureEvidenceUnavailable",
    "POCClosureIdempotencyConflict",
    "ProcessLocalPOCClosureService",
    "TerminalEvidenceBinding",
]
