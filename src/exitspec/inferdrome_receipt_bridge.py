"""Explicit CLI bridge from one dispatch operation to an immutable P1 receipt."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import inferdrome_dispatch as transport
from .canonical import canonical_json_bytes
from .inferdrome_bundle import InferdromeBundleRejected
from .inferdrome_prospective_admission import (
    ProspectiveAdmissionRejected,
    admit_prospective_bundle,
    verify_prospective_receipt,
)
from .inferdrome_prospective_context import capture_prospective_context
from .inferdrome_prospective_receipt import (
    MAX_RECEIPT_BYTES,
    _timestamp,
    serialize_prospective_receipt,
)

INTENT = "receipt-intent.json"
RECEIPT = "prospective-receipt.json"
RESULT = "receipt-result.json"
FILES = (INTENT, RECEIPT, RESULT)


class BridgeRejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class _OperationFiles:
    """Fixed-name fd-relative storage anchored in the trusted operation directory."""

    def __init__(self, root):
        self.root = transport._path(root, directory=True)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.identity = self._directory_identity(os.fstat(self.fd))
        try:
            self.check()
        except BaseException:
            os.close(self.fd)
            raise

    @staticmethod
    def _directory_identity(value):
        if (
            not stat.S_ISDIR(value.st_mode)
            or value.st_uid != os.geteuid()
            or value.st_mode & 0o022
        ):
            raise BridgeRejected("UNSAFE_STORAGE")
        return value.st_dev, value.st_ino, value.st_mode, value.st_uid

    def check(self):
        if (
            self._directory_identity(self.root.lstat()) != self.identity
            or self._directory_identity(os.fstat(self.fd)) != self.identity
        ):
            raise BridgeRejected("STORAGE_CHANGED")
        with os.scandir(self.fd) as entries:
            for index, _ in enumerate(entries):
                if index >= 32:
                    raise BridgeRejected("STORAGE_ENTRY_LIMIT")

    def close(self):
        os.close(self.fd)

    def read(self, name, maximum=MAX_RECEIPT_BYTES, *, sync=False):
        self.check()
        try:
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd
            )
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.geteuid()
                or before.st_mode & 0o022
                or before.st_size > maximum
            ):
                raise BridgeRejected("UNSAFE_STORAGE_FILE")
            raw = bytearray()
            while len(raw) <= maximum:
                chunk = os.read(fd, min(65536, maximum + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            if (
                len(raw) != before.st_size
                or len(raw) > maximum
                or _identity(before) != _identity(os.fstat(fd))
                or _identity(before)
                != _identity(os.stat(name, dir_fd=self.fd, follow_symlinks=False))
            ):
                raise BridgeRejected("STORAGE_CHANGED")
            if sync:
                os.fsync(fd)
            self.check()
            return bytes(raw)
        finally:
            os.close(fd)

    def publish(self, name, raw):
        if (
            name not in FILES
            or type(raw) is not bytes
            or not 0 < len(raw) <= MAX_RECEIPT_BYTES
        ):
            raise BridgeRejected("INVALID_STORAGE_PAYLOAD")
        self.check()
        temporary = ".receipt-" + uuid.uuid4().hex
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.fd,
        )
        inode = os.fstat(fd).st_ino
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            self.check()
            # Same no-clobber hard-link publication primitive as the P1 materializer,
            # anchored by directory descriptors rather than mutable parent paths.
            os.link(
                temporary,
                name,
                src_dir_fd=self.fd,
                dst_dir_fd=self.fd,
                follow_symlinks=False,
            )
            os.unlink(temporary, dir_fd=self.fd)
            os.fsync(self.fd)
            if self.read(name) != raw:
                raise BridgeRejected("STORAGE_CHANGED")
        finally:
            try:
                if (
                    os.stat(temporary, dir_fd=self.fd, follow_symlinks=False).st_ino
                    == inode
                ):
                    os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def barrier(self, expected):
        for name, raw in expected.items():
            if self.read(name, sync=True) != raw:
                raise BridgeRejected("STORAGE_CHANGED")
        os.fsync(self.fd)
        self.check()


@contextmanager
def _locked(root):
    files = _OperationFiles(root)
    lock = None
    try:
        if files.read("dispatch.lock", 0) != b"":
            raise BridgeRejected("INVALID_OPERATION_LOCK")
        lock = os.open(
            "dispatch.lock",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=files.fd,
        )
        metadata = os.fstat(lock)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != 0
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise BridgeRejected("INVALID_OPERATION_LOCK")
        identity = _identity(metadata)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BridgeRejected("DISPATCH_BUSY") from None
        if identity != _identity(
            os.stat("dispatch.lock", dir_fd=files.fd, follow_symlinks=False)
        ):
            raise BridgeRejected("OPERATION_LOCK_CHANGED")
        yield files
        if identity != _identity(
            os.stat("dispatch.lock", dir_fd=files.fd, follow_symlinks=False)
        ):
            raise BridgeRejected("OPERATION_LOCK_CHANGED")
        files.check()
    finally:
        if lock is not None:
            os.close(lock)
        files.close()


def _object(raw):
    value = transport._json(raw)
    if canonical_json_bytes(value) != raw:
        raise BridgeRejected("NONCANONICAL_RECORD")
    return value


def _cancelled(cancelled):
    if cancelled.is_set():
        raise BridgeRejected("CANCELLED")


def _inputs(root, pin, files):
    plan = transport._load(root, pin)
    if files.read("plan.json") is None or _sha(files.read("plan.json")) != pin:
        raise BridgeRejected("PLAN_CHANGED")
    current = transport.status(root, pin)
    if current["current"]["state"] != "AWAITING_ADMISSION":
        raise BridgeRejected("NOT_AWAITING_ADMISSION")
    event_name = f"event-{len(current['history']) - 1}.json"
    event_raw = files.read(event_name)
    if event_raw is None or _object(event_raw) != current["current"]:
        raise BridgeRejected("TRANSPORT_RECORD_CHANGED")
    returned = current["current"].get("producer_result")
    runs = transport._path(plan.runs_root, directory=True)
    bundle = runs / plan.run_id / "bundle"
    if (
        type(returned) is not dict
        or set(returned) != transport.RESULT_KEYS
        or any(type(v) is not str for v in returned.values())
        or returned["run_id"] != plan.run_id
        or returned["workspace_path"] != str(bundle.parent)
        or returned["bundle_path"] != str(bundle)
        or returned["integrity_status"] != "VALID"
        or returned["evidence_eligibility"] != "CUSTOMER_ELIGIBLE"
        or re.fullmatch(r"sha256:[a-f0-9]{64}", returned["bundle_digest"]) is None
    ):
        raise BridgeRejected("TRANSPORT_BINDING_MISMATCH")
    context = capture_prospective_context(
        root / "handoff", plan.case_id, plan.manifest_sha256
    )
    binding = {
        "plan_sha256": pin,
        "transport_record_sha256": _sha(event_raw),
        "run_id": plan.run_id,
        "case_id": plan.case_id,
        "handoff_manifest_sha256": plan.manifest_sha256,
        "bundle_digest": returned["bundle_digest"],
        "producer_contract_link": context.case.producer_contract_link,
    }
    pins = {
        "expected_handoff_manifest_sha256": plan.manifest_sha256,
        "expected_bundle_digest": returned["bundle_digest"],
        "expected_run_id": plan.run_id,
    }
    return binding, (root / "handoff", plan.case_id, bundle), pins, context


def _intent(files, binding):
    raw = files.read(INTENT)
    if raw is None:
        if files.read(RECEIPT) is not None or files.read(RESULT) is not None:
            raise BridgeRejected("INTENT_MISSING")
        value = {**binding, "received_at": _timestamp(datetime.now(UTC))}
        raw = canonical_json_bytes(value)
        files.publish(INTENT, raw)
    value = _object(raw)
    if (
        set(value) != set(binding) | {"received_at"}
        or any(
            type(value[k]) is not type(v) or value[k] != v for k, v in binding.items()
        )
        or type(value["received_at"]) is not str
    ):
        raise BridgeRejected("INTENT_BINDING_MISMATCH")
    received_at = datetime.fromisoformat(value["received_at"])
    if _timestamp(received_at) != value["received_at"]:
        raise BridgeRejected("INVALID_INTENT_TIME")
    # Reestablish a durability barrier even if a previous invocation died after link.
    files.barrier({INTENT: raw})
    return raw, received_at


def _record(binding, *, receipt=None, raw=None, reason=None):
    return {
        **binding,
        "bridge_status": "RECEIPT_SAVED"
        if receipt is not None
        else "ADMISSION_REJECTED",
        "receipt_id": receipt.receipt_id if receipt is not None else None,
        "receipt_sha256": _sha(raw) if raw is not None else None,
        "receipt_bytes": len(raw) if raw is not None else 0,
        "acceptance_verdict": receipt.acceptance_verdict
        if receipt is not None
        else None,
        "reason": reason,
        "shipping_authorized": False,
    }


def _same_operation(root, pin, files, binding):
    current = transport.status(root, pin)
    raw = files.read(f"event-{len(current['history']) - 1}.json")
    plan_raw = files.read("plan.json")
    if (
        plan_raw is None
        or _sha(plan_raw) != pin
        or raw is None
        or _sha(raw) != binding["transport_record_sha256"]
        or current["current"]["state"] != "AWAITING_ADMISSION"
    ):
        raise BridgeRejected("OPERATION_CHANGED")


def _finalize(root, pin, files, cancelled):
    binding, arguments, pins, context = _inputs(root, pin, files)
    _cancelled(cancelled)
    intent, received_at = _intent(files, binding)
    result_raw, receipt_raw = files.read(RESULT), files.read(RECEIPT)
    existing = _object(result_raw) if result_raw is not None else None
    if existing is not None and existing.get("bridge_status") == "ADMISSION_REJECTED":
        reason = existing.get("reason")
        if (
            receipt_raw is not None
            or type(reason) is not str
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", reason) is None
            or result_raw != canonical_json_bytes(_record(binding, reason=reason))
        ):
            raise BridgeRejected("RESULT_BINDING_MISMATCH")
        files.barrier({INTENT: intent, RESULT: result_raw})
        context.reader.assert_unchanged()
        _same_operation(root, pin, files, binding)
        return existing
    if existing is not None and receipt_raw is None:
        raise BridgeRejected("RECEIPT_MISSING")
    if receipt_raw is None:
        try:
            receipt = admit_prospective_bundle(
                *arguments, **pins, received_at=received_at
            )
            receipt_raw = serialize_prospective_receipt(receipt)
            verify_prospective_receipt(receipt_raw, *arguments, **pins)
        except (ProspectiveAdmissionRejected, InferdromeBundleRejected) as error:
            reason = error.code if isinstance(error.code, str) else error.code.value
            _cancelled(cancelled)
            rejected = _record(binding, reason=reason)
            result_raw = canonical_json_bytes(rejected)
            files.publish(RESULT, result_raw)
            files.barrier({INTENT: intent, RESULT: result_raw})
            context.reader.assert_unchanged()
            _same_operation(root, pin, files, binding)
            return rejected
        _cancelled(cancelled)
        _same_operation(root, pin, files, binding)
        files.publish(RECEIPT, receipt_raw)
    # An orphan complete receipt is reconciled; an orphan success record is refused.
    receipt = verify_prospective_receipt(receipt_raw, *arguments, **pins)
    if (
        receipt.received_at != received_at
        or receipt.producer_contract_link != binding["producer_contract_link"]
    ):
        raise BridgeRejected("RECEIPT_BINDING_MISMATCH")
    saved = _record(binding, receipt=receipt, raw=receipt_raw)
    expected_result = canonical_json_bytes(saved)
    if existing is not None and result_raw != expected_result:
        raise BridgeRejected("RESULT_BINDING_MISMATCH")
    _cancelled(cancelled)
    context.reader.assert_unchanged()
    # The receipt's file and directory barriers precede the SAVED terminal record.
    files.barrier({INTENT: intent, RECEIPT: receipt_raw})
    if result_raw is None:
        _cancelled(cancelled)
        files.publish(RESULT, expected_result)
    files.barrier({INTENT: intent, RECEIPT: receipt_raw, RESULT: expected_result})
    context.reader.assert_unchanged()
    _same_operation(root, pin, files, binding)
    return saved


def run_operation(
    root: Path, expected_plan_sha256: str, *, cancelled: threading.Event | None = None
) -> dict:
    """Dispatch at most once, then finalize/reconcile without automatic producer retry."""
    cancelled = cancelled if cancelled is not None else threading.Event()
    state = None
    dispatched = False
    try:
        root = transport._path(root, directory=True)
        with _locked(root) as files:
            current = transport.status(root, expected_plan_sha256)
            state = current["current"]["state"]
            if state == "PREPARED" and any(
                files.read(name) is not None for name in FILES
            ):
                raise BridgeRejected("UNEXPECTED_COMPLETION_ARTIFACT")
        _cancelled(cancelled)
        if state == "PREPARED":
            dispatched = True
            current = transport.dispatch(
                root, expected_plan_sha256, cancelled=cancelled
            )
            state = current["current"]["state"]
        if state != "AWAITING_ADMISSION":
            return {
                "transport_status": state,
                "bridge_status": "NO_RECEIPT",
                "receipt_saved": False,
                "acceptance_verdict": None,
                "shipping_authorized": False,
                "producer_dispatch_requested": dispatched,
                "reason": current["current"].get("reason", state),
            }
        with _locked(root) as files:
            result = _finalize(root, expected_plan_sha256, files, cancelled)
        return {
            **result,
            "transport_status": state,
            "receipt_saved": result["bridge_status"] == "RECEIPT_SAVED",
            "receipt_path": str(root / RECEIPT)
            if result["bridge_status"] == "RECEIPT_SAVED"
            else None,
            "producer_dispatch_requested": dispatched,
        }
    except (
        BridgeRejected,
        transport.DispatchRejected,
        ProspectiveAdmissionRejected,
        InferdromeBundleRejected,
    ) as error:
        reason = error.code if isinstance(error.code, str) else error.code.value
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        reason = "STORAGE_OR_CONTEXT_UNAVAILABLE"
    return {
        "transport_status": state,
        "bridge_status": "REFUSED",
        "receipt_saved": False,
        "receipt_path": None,
        "acceptance_verdict": None,
        "shipping_authorized": False,
        "producer_dispatch_requested": dispatched,
        "reason": reason,
        "reconciliation_required": True,
    }


def exit_code(result):
    if result.get("bridge_status") != "RECEIPT_SAVED":
        return 2
    return {"PASS": 0, "FAIL": 3, "NOT_PROVEN": 4}[result["acceptance_verdict"]]
