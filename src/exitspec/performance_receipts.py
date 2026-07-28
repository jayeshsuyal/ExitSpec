"""Immutable execution receipts for inference-performance evidence.

This module owns only the content-free identity boundary between a completed
probe execution and later performance-verdict work. It deliberately has no
filesystem, network, measurement, verdict, or reporting behavior.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Literal

from pydantic import Field, field_validator, model_validator

from .canonical import canonical_json_bytes
from .models import FrozenExitSpecModel, SHA256_PATTERN


PERFORMANCE_RECEIPT_SCHEMA_VERSION = (
    "exitspec.performance-execution-receipt.v1"
)

_IDEMPOTENCY_KEY_DOMAIN = (
    b"exitspec-performance-receipt-idempotency-key-v1\x00"
)
_REQUEST_DOMAIN = b"exitspec-performance-receipt-request-v1\x00"
_OPERATION_DOMAIN = b"exitspec-performance-receipt-operation-v1\x00"
_RECEIPT_DOMAIN = b"exitspec-performance-execution-receipt-v1\x00"

_SHA256 = re.compile(SHA256_PATTERN)
_RECEIPT_ID = re.compile(r"prc_[a-f0-9]{64}\Z")
_OPERATION_ID = re.compile(r"op_[a-f0-9]{64}\Z")
_CONTRACT_ID = re.compile(r"[a-z][a-z0-9-]{2,63}\Z")
_CRITERION_ID = re.compile(r"[A-Z][A-Z0-9-]{2,63}\Z")
_EXECUTION_ID = re.compile(r"run_[a-f0-9]{32}\Z")


class PerformanceIdempotencyConflict(ValueError):
    """An idempotency key is already bound to another execution identity."""

    def __init__(self) -> None:
        super().__init__(
            "Performance idempotency key is already bound to another request."
        )


class PerformanceReceiptIntegrityError(ValueError):
    """A receipt's derived identity does not match its bound fields."""


class PerformanceExecutionReceipt(FrozenExitSpecModel):
    """An immutable, secret-free binding for one completed probe execution."""

    schema_version: Literal[
        "exitspec.performance-execution-receipt.v1"
    ] = PERFORMANCE_RECEIPT_SCHEMA_VERSION
    receipt_id: str = Field(pattern=r"^prc_[a-f0-9]{64}$")
    operation_id: str = Field(pattern=r"^op_[a-f0-9]{64}$")
    request_digest: str = Field(pattern=SHA256_PATTERN)
    contract_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    contract_version: str = Field(min_length=1, max_length=64)
    frozen_contract_hash: str = Field(pattern=SHA256_PATTERN)
    criterion_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    expected_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    execution_id: str = Field(pattern=r"^run_[a-f0-9]{32}$")
    records_sha256: str = Field(pattern=SHA256_PATTERN)
    idempotency_key_digest: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def require_valid_derived_identity(self) -> "PerformanceExecutionReceipt":
        expected_request_digest = performance_request_digest(
            contract_id=self.contract_id,
            contract_version=self.contract_version,
            frozen_contract_hash=self.frozen_contract_hash,
            criterion_id=self.criterion_id,
            expected_manifest_sha256=self.expected_manifest_sha256,
            execution_id=self.execution_id,
            records_sha256=self.records_sha256,
        )
        if not hmac.compare_digest(
            self.request_digest,
            expected_request_digest,
        ):
            raise ValueError("request_digest does not bind the receipt fields.")

        expected_operation_id = performance_operation_id(
            idempotency_key_digest=self.idempotency_key_digest,
            request_digest=self.request_digest,
        )
        if not hmac.compare_digest(self.operation_id, expected_operation_id):
            raise ValueError("operation_id does not bind the request.")

        expected_receipt_id = performance_receipt_id(
            schema_version=self.schema_version,
            operation_id=self.operation_id,
            request_digest=self.request_digest,
            contract_id=self.contract_id,
            contract_version=self.contract_version,
            frozen_contract_hash=self.frozen_contract_hash,
            criterion_id=self.criterion_id,
            expected_manifest_sha256=self.expected_manifest_sha256,
            execution_id=self.execution_id,
            records_sha256=self.records_sha256,
            idempotency_key_digest=self.idempotency_key_digest,
            created_at=self.created_at,
        )
        if not hmac.compare_digest(self.receipt_id, expected_receipt_id):
            raise ValueError("receipt_id does not bind the receipt content.")
        return self


def performance_idempotency_key_digest(idempotency_key: str) -> str:
    """Digest one ephemeral idempotency key without retaining the raw value."""

    _require_idempotency_key(idempotency_key)
    return _domain_sha256(
        _IDEMPOTENCY_KEY_DOMAIN,
        {"idempotency_key": idempotency_key},
    )


def performance_request_digest(
    *,
    contract_id: str,
    contract_version: str,
    frozen_contract_hash: str,
    criterion_id: str,
    expected_manifest_sha256: str,
    execution_id: str,
    records_sha256: str,
) -> str:
    """Digest every non-secret input that identifies completed evidence."""

    _require_contract_id(contract_id)
    _require_contract_version(contract_version)
    _require_sha256(frozen_contract_hash, "frozen_contract_hash")
    _require_criterion_id(criterion_id)
    _require_sha256(
        expected_manifest_sha256,
        "expected_manifest_sha256",
    )
    _require_execution_id(execution_id)
    _require_sha256(records_sha256, "records_sha256")
    return _domain_sha256(
        _REQUEST_DOMAIN,
        {
            "contract_id": contract_id,
            "contract_version": contract_version,
            "criterion_id": criterion_id,
            "execution_id": execution_id,
            "expected_manifest_sha256": expected_manifest_sha256,
            "frozen_contract_hash": frozen_contract_hash,
            "records_sha256": records_sha256,
            "schema_version": PERFORMANCE_RECEIPT_SCHEMA_VERSION,
        },
    )


def performance_operation_id(
    *,
    idempotency_key_digest: str,
    request_digest: str,
) -> str:
    """Create the stable operation identity for one key and exact request."""

    _require_sha256(
        idempotency_key_digest,
        "idempotency_key_digest",
    )
    _require_sha256(request_digest, "request_digest")
    digest = _domain_sha256(
        _OPERATION_DOMAIN,
        {
            "idempotency_key_digest": idempotency_key_digest,
            "request_digest": request_digest,
            "schema_version": PERFORMANCE_RECEIPT_SCHEMA_VERSION,
        },
    )
    return "op_{0}".format(digest)


def performance_receipt_id(
    *,
    schema_version: str,
    operation_id: str,
    request_digest: str,
    contract_id: str,
    contract_version: str,
    frozen_contract_hash: str,
    criterion_id: str,
    expected_manifest_sha256: str,
    execution_id: str,
    records_sha256: str,
    idempotency_key_digest: str,
    created_at: datetime,
) -> str:
    """Create the immutable receipt identity over every persisted field."""

    if schema_version != PERFORMANCE_RECEIPT_SCHEMA_VERSION:
        raise ValueError("Unsupported performance receipt schema version.")
    _require_operation_id(operation_id)
    _require_sha256(request_digest, "request_digest")
    _require_contract_id(contract_id)
    _require_contract_version(contract_version)
    _require_sha256(frozen_contract_hash, "frozen_contract_hash")
    _require_criterion_id(criterion_id)
    _require_sha256(
        expected_manifest_sha256,
        "expected_manifest_sha256",
    )
    _require_execution_id(execution_id)
    _require_sha256(records_sha256, "records_sha256")
    _require_sha256(
        idempotency_key_digest,
        "idempotency_key_digest",
    )
    normalized_created_at = _require_aware_datetime(created_at)
    digest = _domain_sha256(
        _RECEIPT_DOMAIN,
        {
            "contract_id": contract_id,
            "contract_version": contract_version,
            "created_at": _canonical_timestamp(normalized_created_at),
            "criterion_id": criterion_id,
            "execution_id": execution_id,
            "expected_manifest_sha256": expected_manifest_sha256,
            "frozen_contract_hash": frozen_contract_hash,
            "idempotency_key_digest": idempotency_key_digest,
            "operation_id": operation_id,
            "records_sha256": records_sha256,
            "request_digest": request_digest,
            "schema_version": schema_version,
        },
    )
    return "prc_{0}".format(digest)


def validate_performance_receipt(
    receipt: PerformanceExecutionReceipt,
) -> PerformanceExecutionReceipt:
    """Reparse and independently verify a possibly copied receipt object."""

    if not isinstance(receipt, PerformanceExecutionReceipt):
        raise TypeError("receipt must be a PerformanceExecutionReceipt.")
    try:
        return PerformanceExecutionReceipt.model_validate(
            receipt.model_dump(mode="python")
        )
    except (TypeError, ValueError) as exc:
        raise PerformanceReceiptIntegrityError(
            "Performance execution receipt integrity validation failed."
        ) from exc


class InMemoryPerformanceReceiptStore:
    """Thread-safe reference store with exact replay/conflict semantics."""

    __slots__ = (
        "_clock",
        "_lock",
        "_receipts_by_id",
        "_receipts_by_key_digest",
        "_receipts_by_operation_id",
    )

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or _utc_now
        if not callable(self._clock):
            raise TypeError("clock must be callable.")
        self._lock = RLock()
        self._receipts_by_id: dict[str, PerformanceExecutionReceipt] = {}
        self._receipts_by_key_digest: dict[
            str,
            PerformanceExecutionReceipt,
        ] = {}
        self._receipts_by_operation_id: dict[
            str,
            PerformanceExecutionReceipt,
        ] = {}

    def __repr__(self) -> str:
        with self._lock:
            count = len(self._receipts_by_id)
        return "InMemoryPerformanceReceiptStore(receipt_count={0})".format(
            count
        )

    def record_receipt(
        self,
        *,
        idempotency_key: str,
        contract_id: str,
        contract_version: str,
        frozen_contract_hash: str,
        criterion_id: str,
        expected_manifest_sha256: str,
        execution_id: str,
        records_sha256: str,
        created_at: datetime | None = None,
    ) -> PerformanceExecutionReceipt:
        """Record, replay, or reject one completed execution atomically."""

        key_digest = performance_idempotency_key_digest(idempotency_key)
        request_digest = performance_request_digest(
            contract_id=contract_id,
            contract_version=contract_version,
            frozen_contract_hash=frozen_contract_hash,
            criterion_id=criterion_id,
            expected_manifest_sha256=expected_manifest_sha256,
            execution_id=execution_id,
            records_sha256=records_sha256,
        )
        operation_id = performance_operation_id(
            idempotency_key_digest=key_digest,
            request_digest=request_digest,
        )

        with self._lock:
            existing = self._receipts_by_key_digest.get(key_digest)
            if existing is not None:
                verified = validate_performance_receipt(existing)
                if not (
                    hmac.compare_digest(
                        verified.request_digest,
                        request_digest,
                    )
                    and hmac.compare_digest(
                        verified.operation_id,
                        operation_id,
                    )
                ):
                    raise PerformanceIdempotencyConflict()
                return existing

            resolved_created_at = _require_aware_datetime(
                created_at if created_at is not None else self._clock()
            )
            receipt_id = performance_receipt_id(
                schema_version=PERFORMANCE_RECEIPT_SCHEMA_VERSION,
                operation_id=operation_id,
                request_digest=request_digest,
                contract_id=contract_id,
                contract_version=contract_version,
                frozen_contract_hash=frozen_contract_hash,
                criterion_id=criterion_id,
                expected_manifest_sha256=expected_manifest_sha256,
                execution_id=execution_id,
                records_sha256=records_sha256,
                idempotency_key_digest=key_digest,
                created_at=resolved_created_at,
            )
            receipt = PerformanceExecutionReceipt(
                receipt_id=receipt_id,
                operation_id=operation_id,
                request_digest=request_digest,
                contract_id=contract_id,
                contract_version=contract_version,
                frozen_contract_hash=frozen_contract_hash,
                criterion_id=criterion_id,
                expected_manifest_sha256=expected_manifest_sha256,
                execution_id=execution_id,
                records_sha256=records_sha256,
                idempotency_key_digest=key_digest,
                created_at=resolved_created_at,
            )
            if (
                operation_id in self._receipts_by_operation_id
                or receipt_id in self._receipts_by_id
            ):
                raise PerformanceReceiptIntegrityError(
                    "Performance receipt identity collision."
                )
            self._receipts_by_key_digest[key_digest] = receipt
            self._receipts_by_operation_id[operation_id] = receipt
            self._receipts_by_id[receipt_id] = receipt
            return receipt

    def get_receipt(
        self,
        receipt_id: str,
    ) -> PerformanceExecutionReceipt | None:
        """Return a verified receipt by its public receipt identity."""

        _require_receipt_id(receipt_id)
        with self._lock:
            receipt = self._receipts_by_id.get(receipt_id)
            if receipt is None:
                return None
            validate_performance_receipt(receipt)
            return receipt


def _domain_sha256(domain: bytes, payload: object) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _require_aware_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("created_at must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _require_idempotency_key(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(
            "idempotency_key must be 1-200 printable characters with no "
            "surrounding whitespace."
        )


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(
            "{0} must be 64 lowercase hexadecimal characters.".format(name)
        )


def _require_receipt_id(value: str) -> None:
    if not isinstance(value, str) or not _RECEIPT_ID.fullmatch(value):
        raise ValueError("receipt_id has an invalid shape.")


def _require_operation_id(value: str) -> None:
    if not isinstance(value, str) or not _OPERATION_ID.fullmatch(value):
        raise ValueError("operation_id has an invalid shape.")


def _require_contract_id(value: str) -> None:
    if not isinstance(value, str) or not _CONTRACT_ID.fullmatch(value):
        raise ValueError("contract_id has an invalid shape.")


def _require_contract_version(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or value != value.strip()
    ):
        raise ValueError("contract_version must be 1-64 exact characters.")


def _require_criterion_id(value: str) -> None:
    if not isinstance(value, str) or not _CRITERION_ID.fullmatch(value):
        raise ValueError("criterion_id has an invalid shape.")


def _require_execution_id(value: str) -> None:
    if not isinstance(value, str) or not _EXECUTION_ID.fullmatch(value):
        raise ValueError("execution_id has an invalid shape.")
