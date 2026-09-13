"""Operator-only local CLI transport. No producer imports or acceptance decisions.

Operator-owned executable/runtime and directories are trusted configuration.
Producer stdout, returned files and handoff content are independently checked.
A declared producer revision is provenance, not an attestation of installed code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .canonical import canonical_json_bytes
from .inferdrome_bundle import InferdromeBundleRejected, verify_inferdrome_bundle
from .inferdrome_prospective import (
    PROSPECTIVE_CASES,
    ProspectiveHandoffError,
    export_prospective_handoff,
    validate_prospective_handoff,
)

MAX_OUTPUT_BYTES = 65_536
MAX_FILE_BYTES = 8 * 1024 * 1024
TERMINAL = frozenset(
    {
        "REFUSED",
        "FAILED",
        "TIMED_OUT",
        "CANCELLED",
        "OUTPUT_LIMIT",
        "RESULT_INVALID",
        "INGESTION_REJECTED",
        "AWAITING_ADMISSION",
    }
)
RETRYABLE = TERMINAL - {"AWAITING_ADMISSION"}
RESULT_KEYS = frozenset(
    {
        "bundle_digest",
        "bundle_path",
        "evidence_eligibility",
        "integrity_status",
        "run_id",
        "workspace_path",
    }
)


class DispatchRejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DispatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    operation_id: str = Field(pattern=r"^iop_[a-f0-9]{32}$")
    run_id: str = Field(pattern=r"^run-[a-f0-9]{32}$")
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_id: str
    executable: str = Field(min_length=1, max_length=4096)
    executable_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operator_declared_producer_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    runs_root: str = Field(min_length=1, max_length=4096)
    timeout_seconds: int = Field(ge=1, le=900)
    attempt_number: int = Field(default=1, ge=1, le=3)
    retry_of: str | None = Field(default=None, pattern=r"^iop_[a-f0-9]{32}$")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _path(value: str | Path, *, directory: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise DispatchRejected("UNSAFE_PATH")
    if directory and not path.is_dir():
        raise DispatchRejected("DIRECTORY_UNAVAILABLE")
    if directory and (
        path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o022
    ):
        raise DispatchRejected("UNTRUSTED_DIRECTORY")
    return path


def _read(path: Path, maximum: int = MAX_FILE_BYTES) -> bytes:
    _path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size > maximum
            ):
                raise DispatchRejected("UNSAFE_FILE")
            raw = bytearray()
            while len(raw) <= maximum:
                chunk = os.read(fd, min(65_536, maximum + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(fd)
            identity = lambda s: (
                s.st_dev,
                s.st_ino,
                s.st_mode,
                s.st_nlink,
                s.st_size,
                s.st_mtime_ns,
                s.st_ctime_ns,
            )
            if (
                identity(before) != identity(after)
                or identity(after) != identity(path.lstat())
                or len(raw) > maximum
            ):
                raise DispatchRejected("FILE_CHANGED")
            return bytes(raw)
        finally:
            os.close(fd)
    except OSError:
        raise DispatchRejected("FILE_UNAVAILABLE") from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DispatchRejected("INVALID_JSON")
        result[key] = value
    return result


def _json(raw: bytes):
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                DispatchRejected("INVALID_JSON")
            ),
        )
    except (ValueError, UnicodeError, RecursionError):
        raise DispatchRejected("INVALID_JSON") from None
    if type(value) is not dict:
        raise DispatchRejected("INVALID_JSON")
    return value


def _write(path: Path, payload) -> None:
    """Publish a complete record under the operation's exclusive writer lock."""
    if path.exists() or path.is_symlink():
        raise DispatchRejected("RECORD_EXISTS")
    fd, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(canonical_json_bytes(payload))
            output.flush()
            os.fsync(output.fileno())
        os.rename(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _event(root: Path, plan_sha256: str, state: str, **facts) -> dict:
    events = sorted(root.glob("event-*.json"))
    if len(events) >= 3:
        raise DispatchRejected("EVENT_LIMIT")
    value = {
        "plan_sha256": plan_sha256,
        "state": state,
        "recorded_at": datetime.now(UTC).isoformat(),
        "acceptance_verdict": None,
        "shipping_authorized": False,
        **facts,
    }
    _write(root / f"event-{len(events)}.json", value)
    return value


def _load(root: Path, expected_plan_sha256: str) -> DispatchPlan:
    _path(root, directory=True)
    raw = _read(root / "plan.json", MAX_OUTPUT_BYTES)
    if (
        not re.fullmatch(r"[a-f0-9]{64}", expected_plan_sha256)
        or _sha(raw) != expected_plan_sha256
    ):
        raise DispatchRejected("PLAN_DIGEST_MISMATCH")
    try:
        plan = DispatchPlan.model_validate(_json(raw))
    except ValidationError:
        raise DispatchRejected("INVALID_PLAN") from None
    if plan.operation_id != root.name or plan.case_id not in {
        c.case_id for c in PROSPECTIVE_CASES
    }:
        raise DispatchRejected("INVALID_PLAN")
    return plan


def status(root: Path, expected_plan_sha256: str) -> dict:
    plan = _load(root, expected_plan_sha256)
    paths = sorted(root.glob("event-*.json"))
    if not 1 <= len(paths) <= 3 or [p.name for p in paths] != [
        f"event-{i}.json" for i in range(len(paths))
    ]:
        raise DispatchRejected("INVALID_HISTORY")
    events = [_json(_read(p, MAX_OUTPUT_BYTES)) for p in paths]
    for i, event in enumerate(events):
        if (
            event.get("plan_sha256") != expected_plan_sha256
            or event.get("acceptance_verdict") is not None
            or event.get("shipping_authorized") is not False
        ):
            raise DispatchRejected("INVALID_HISTORY")
        state = event.get("state")
        if type(state) is not str:
            raise DispatchRejected("INVALID_HISTORY")
        if (
            (i == 0 and state != "PREPARED")
            or (i == 1 and state not in TERMINAL | {"RUNNING"})
            or (i == 2 and (events[1]["state"] != "RUNNING" or state not in TERMINAL))
        ):
            raise DispatchRejected("INVALID_HISTORY")
    return {
        "operation": str(root),
        "run_id": plan.run_id,
        "attempt_number": plan.attempt_number,
        "retry_of": plan.retry_of,
        "plan_sha256": expected_plan_sha256,
        "current": events[-1],
        "history": events,
    }


@contextmanager
def _lock(root: Path):
    import fcntl

    _read(root / "dispatch.lock", 0)
    fd = os.open(root / "dispatch.lock", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DispatchRejected("DISPATCH_BUSY") from None
        yield
    finally:
        os.close(fd)


def _preflight(root: Path, plan: DispatchPlan) -> tuple[list[str], str]:
    executable = _path(plan.executable)
    if _sha(_read(executable)) != plan.executable_sha256 or not os.access(
        executable, os.X_OK
    ):
        raise DispatchRejected("EXECUTABLE_MISMATCH")
    runs = _path(plan.runs_root, directory=True)
    if runs == root or runs.is_relative_to(root) or root.is_relative_to(runs):
        raise DispatchRejected("OVERLAPPING_ROOTS")
    if (runs / plan.run_id).exists() or (runs / plan.run_id).is_symlink():
        raise DispatchRejected("RUN_ALREADY_EXISTS")
    validated = validate_prospective_handoff(root / "handoff")
    if validated.manifest_sha256 != plan.manifest_sha256:
        raise DispatchRejected("HANDOFF_MISMATCH")
    case = next(c for c in validated.manifest.cases if c.case_id == plan.case_id)
    source = root / "handoff" / case.source_yaml_artifact_path
    argv = [
        str(executable),
        "run",
        str(source),
        "--runs-root",
        str(runs),
        "--run-id",
        plan.run_id,
        "--expected-exitspec-contract-digest",
        case.producer_contract_link,
    ]
    return argv, case.producer_contract_link


def preflight(root: Path, expected_plan_sha256: str) -> dict:
    plan = _load(root, expected_plan_sha256)
    current = status(root, expected_plan_sha256)
    if current["current"]["state"] != "PREPARED":
        raise DispatchRejected("NOT_PREPARED")
    argv, link = _preflight(root, plan)
    return {
        **current,
        "argv": argv,
        "expected_contract_digest": link,
        "operator_declared_producer_revision": plan.operator_declared_producer_revision,
        "execution_performed": False,
        "acceptance_verdict": None,
    }


def prepare(
    *,
    handoff: Path,
    operations_root: Path,
    case_id: str,
    executable: Path,
    executable_sha256: str,
    producer_revision: str,
    runs_root: Path,
    timeout_seconds: int = 900,
    retry_of: str | None = None,
    attempt_number: int = 1,
) -> dict:
    operations_root = _path(operations_root, directory=True)
    _path(handoff)
    validated = validate_prospective_handoff(handoff)
    plan = DispatchPlan(
        operation_id="iop_" + uuid.uuid4().hex,
        run_id="run-" + uuid.uuid4().hex,
        manifest_sha256=validated.manifest_sha256,
        case_id=case_id,
        executable=str(executable),
        executable_sha256=executable_sha256,
        operator_declared_producer_revision=producer_revision,
        runs_root=str(runs_root),
        timeout_seconds=timeout_seconds,
        retry_of=retry_of,
        attempt_number=attempt_number,
    )
    if case_id not in {c.case_id for c in PROSPECTIVE_CASES}:
        raise DispatchRejected("INVALID_CASE")
    root = operations_root / plan.operation_id
    root.mkdir(mode=0o700)
    # An interrupted preparation has no PREPARED event and cannot dispatch.
    exported = export_prospective_handoff(handoff, root / "handoff")
    if exported != validated:
        raise DispatchRejected("HANDOFF_MISMATCH")
    argv, _ = _preflight(root, plan)
    _write(root / "plan.json", plan.model_dump(mode="json"))
    (root / "dispatch.lock").touch(mode=0o600, exist_ok=False)
    digest = _sha(_read(root / "plan.json"))
    _event(root, digest, "PREPARED")
    return {**status(root, digest), "argv": argv, "execution_performed": False}


def retry(root: Path, expected_plan_sha256: str) -> dict:
    plan = _load(root, expected_plan_sha256)
    with _lock(root):
        current = status(root, expected_plan_sha256)
        if (
            current["current"]["state"] not in RETRYABLE
            or plan.attempt_number >= 3
            or current["current"].get("reason") == "PROCESS_CONTROL_FAILED"
        ):
            raise DispatchRejected("NOT_RETRYABLE")
        return prepare(
            handoff=root / "handoff",
            operations_root=root.parent,
            case_id=plan.case_id,
            executable=Path(plan.executable),
            executable_sha256=plan.executable_sha256,
            producer_revision=plan.operator_declared_producer_revision,
            runs_root=Path(plan.runs_root),
            timeout_seconds=plan.timeout_seconds,
            retry_of=plan.operation_id,
            attempt_number=plan.attempt_number + 1,
        )


def _stop(child: subprocess.Popen) -> None:
    # Includes descendants retaining the stdout/stderr pipes after the leader exits.
    grace_deadline = time.monotonic() + 0.5
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    # wait() can return immediately for an exited leader. Descendants still
    # need the grace interval to exit and be reaped before the final signal.
    time.sleep(max(0, grace_deadline - time.monotonic()))
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=2)


def _execute(argv: list[str], root: Path, seconds: int, cancelled: threading.Event):
    if cancelled.is_set():
        return "CANCELLED", None, b"", b""
    try:
        child = subprocess.Popen(
            argv,
            cwd=root / "handoff",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
            env={
                "PATH": str(Path(argv[0]).parent) + ":/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
    except OSError:
        return "FAILED", None, b"", b""
    output = [bytearray(), bytearray()]
    deadline = time.monotonic() + seconds
    reason = None
    stopped = False
    try:
        with selectors.DefaultSelector() as selector:
            for index, stream in enumerate((child.stdout, child.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, index)
            while selector.get_map() or child.poll() is None:
                if cancelled.is_set():
                    reason = "CANCELLED"
                    break
                if time.monotonic() >= deadline:
                    reason = "TIMED_OUT"
                    break
                for key, _ in selector.select(
                    min(0.05, max(0, deadline - time.monotonic()))
                ):
                    chunk = os.read(
                        key.fd, min(8192, MAX_OUTPUT_BYTES + 1 - sum(map(len, output)))
                    )
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output[key.data].extend(chunk)
                    if sum(map(len, output)) > MAX_OUTPUT_BYTES:
                        reason = "OUTPUT_LIMIT"
                        break
                if reason:
                    break
        if reason:
            _stop(child)
            stopped = True
        return (
            reason or ("FAILED" if child.returncode else "RETURNED"),
            child.returncode,
            bytes(output[0]),
            bytes(output[1]),
        )
    finally:
        if not stopped and child.poll() is None:
            _stop(child)
        child.stdout.close()
        child.stderr.close()


def _returned_bundle(raw: bytes, plan: DispatchPlan, link: str) -> dict:
    result = _json(raw)
    bundle = Path(plan.runs_root) / plan.run_id / "bundle"
    if (
        set(result) != RESULT_KEYS
        or any(type(value) is not str for value in result.values())
        or result["run_id"] != plan.run_id
        or result["bundle_path"] != str(bundle)
        or result["workspace_path"] != str(bundle.parent)
        or result["integrity_status"] != "VALID"
        or result["evidence_eligibility"] not in {"SYNTHETIC_ONLY", "CUSTOMER_ELIGIBLE"}
        or re.fullmatch(r"sha256:[a-f0-9]{64}", result["bundle_digest"]) is None
    ):
        raise DispatchRejected("RESULT_IDENTITY_MISMATCH")
    verified = verify_inferdrome_bundle(
        bundle,
        expected_bundle_digest=result["bundle_digest"],
        require_customer_eligible=True,
    )
    descriptor = verified.descriptor
    if (
        descriptor["run_id"] != plan.run_id
        or descriptor["evidence_eligibility"] != "CUSTOMER_ELIGIBLE"
        or descriptor["evidence_eligibility"] != result["evidence_eligibility"]
        or descriptor["digests"].get("exitspec_contract_digest") != link
        or verified.resolved_spec.get("links", {}).get("exitspec_contract_digest")
        != link
    ):
        raise DispatchRejected("BUNDLE_LINK_MISMATCH")
    return result


def dispatch(
    root: Path, expected_plan_sha256: str, *, cancelled: threading.Event | None = None
) -> dict:
    plan = _load(root, expected_plan_sha256)
    cancelled = cancelled if cancelled is not None else threading.Event()
    with _lock(root):
        current = status(root, expected_plan_sha256)
        if current["current"]["state"] != "PREPARED":
            raise DispatchRejected("NOT_PREPARED")
        try:
            argv, link = _preflight(root, plan)
        except (DispatchRejected, ProspectiveHandoffError):
            _event(root, expected_plan_sha256, "REFUSED", reason="PREFLIGHT_REJECTED")
            return status(root, expected_plan_sha256)
        _event(root, expected_plan_sha256, "RUNNING")
        try:
            state, returncode, stdout, stderr = _execute(
                argv, root, plan.timeout_seconds, cancelled
            )
        except (OSError, subprocess.SubprocessError):
            _event(
                root, expected_plan_sha256, "FAILED", reason="PROCESS_CONTROL_FAILED"
            )
            return status(root, expected_plan_sha256)
        facts = {
            "returncode": returncode,
            "stdout_sha256": _sha(stdout),
            "stderr_sha256": _sha(stderr),
            "stdout_bytes": len(stdout),
            "stderr_bytes": len(stderr),
        }
        if state == "FAILED" and returncode is None:
            facts["reason"] = "PROCESS_UNAVAILABLE"
        if state == "RETURNED":
            try:
                # No result can replace or modify the frozen input binding.
                if (
                    validate_prospective_handoff(root / "handoff").manifest_sha256
                    != plan.manifest_sha256
                ):
                    raise DispatchRejected("HANDOFF_CHANGED")
                facts["producer_result"] = _returned_bundle(stdout, plan, link)
                state = "AWAITING_ADMISSION"
            except InferdromeBundleRejected as error:
                state = "INGESTION_REJECTED"
                facts["reason"] = error.code.value
            except (DispatchRejected, ProspectiveHandoffError):
                state = "RESULT_INVALID"
                facts["reason"] = "RETURNED_RESULT_REJECTED"
        _event(root, expected_plan_sha256, state, **facts)
        return status(root, expected_plan_sha256)


def add_cli_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "inferdrome-handoff",
        help="Operator-only prospective export and local transport; no acceptance verdict.",
    )
    actions = parser.add_subparsers(dest="handoff_action", required=True)
    path = lambda value: Path(value).absolute()
    export = actions.add_parser(
        "export", help="Copy an existing confirmed/frozen P1 handoff."
    )
    export.add_argument("--handoff", type=path, required=True)
    export.add_argument("--output", type=path, required=True)
    prepare_parser = actions.add_parser(
        "prepare", help="Export immutable inputs and prepare one new transport attempt."
    )
    prepare_parser.add_argument("--handoff", type=path, required=True)
    prepare_parser.add_argument("--operations-root", type=path, required=True)
    prepare_parser.add_argument("--runs-root", type=path, required=True)
    prepare_parser.add_argument(
        "--case",
        dest="case_id",
        choices=[case.case_id for case in PROSPECTIVE_CASES],
        required=True,
    )
    prepare_parser.add_argument("--executable", type=path, required=True)
    prepare_parser.add_argument("--executable-sha256", required=True)
    prepare_parser.add_argument(
        "--producer-revision",
        required=True,
        help="Operator-declared revision; does not attest installed runtime dependencies.",
    )
    prepare_parser.add_argument("--timeout-seconds", type=int, default=900)
    for name in ("preflight", "dispatch", "status", "retry"):
        action = actions.add_parser(name)
        action.add_argument("--operation", type=path, required=True)
        action.add_argument("--plan-sha256", required=True)


def run_cli(args) -> int:
    cancelled = threading.Event()
    previous = {}
    try:
        if os.name != "posix":
            raise DispatchRejected("POSIX_REQUIRED")
        if args.handoff_action == "export":
            exported = export_prospective_handoff(
                _path(args.handoff), _path(args.output)
            )
            result = {
                "handoff": str(args.output),
                "manifest_sha256": exported.manifest_sha256,
                "execution_performed": False,
                "acceptance_verdict": None,
            }
        elif args.handoff_action == "prepare":
            result = prepare(
                handoff=args.handoff,
                operations_root=args.operations_root,
                case_id=args.case_id,
                executable=args.executable,
                executable_sha256=args.executable_sha256,
                producer_revision=args.producer_revision,
                runs_root=args.runs_root,
                timeout_seconds=args.timeout_seconds,
            )
        elif args.handoff_action == "dispatch":
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, lambda *_: cancelled.set())
            result = dispatch(args.operation, args.plan_sha256, cancelled=cancelled)
        else:
            action = {"preflight": preflight, "status": status, "retry": retry}[
                args.handoff_action
            ]
            result = action(args.operation, args.plan_sha256)
        print(json.dumps(result, sort_keys=True))
        if args.handoff_action == "dispatch":
            return 0 if result["current"]["state"] == "AWAITING_ADMISSION" else 2
        return 0
    except (DispatchRejected, ProspectiveHandoffError, ValidationError, OSError):
        print(
            json.dumps(
                {
                    "error": "LOCAL_HANDOFF_REFUSED",
                    "acceptance_verdict": None,
                    "shipping_authorized": False,
                }
            )
        )
        return 2
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
