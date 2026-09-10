"""Private admission from an independently reviewed detached approval.

Compiled serving qualification is closed by default. The operator's independent
expected digest binds a detached record to frozen code without a self-reference.
This module never imports a web server, operation engine or supervisor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import select
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from threading import RLock

from .canonical import canonical_json_bytes
from .source_authoring_live_ipc import validate_credential
from .source_authoring_owners import SourceAuthoringOwners
from .source_authoring_policy import (
    HEADER_POLICY_SHA256,
    PROFILE_SHA256,
    REDACTION_CONFIGURATION_DIGEST,
    SCHEMA_SHA256,
    body_digest,
)


class SourceAuthoringLaunchError(ValueError):
    def __init__(self):
        super().__init__("Source authoring launch prerequisites are unavailable.")


@dataclass(frozen=True, repr=False)
class _QualifiedLaunchProfile:
    approval_id: str
    launch_profile_sha256: str
    code_revision: str
    code_tree: str
    files: tuple[tuple[str, str], ...]
    tokenizer_identity: str
    tokenizer_artifacts: tuple[tuple[str, str], ...]
    account_pricing_approval: str
    custody_approval: str
    region: str
    valid_from: float
    valid_until: float
    request_profile_sha256: str = PROFILE_SHA256
    schema_sha256: str = SCHEMA_SHA256
    header_policy_sha256: str = HEADER_POLICY_SHA256
    redaction_sha256: str = REDACTION_CONFIGURATION_DIGEST
    input_tokens_max: int = 8192
    output_tokens_max: int = 2000
    request_budget_usd: str = "0.01"
    launch_budget_usd: str = "0.10"


# These are runtime-issued identities, never literal profiles embedded in code.
_PRODUCTION_PROFILES: tuple[_QualifiedLaunchProfile, ...] = ()
_APPROVALS: dict[int, tuple[_QualifiedLaunchProfile, str, str]] = {}
_APPROVAL_SCHEMA = "exitspec.detached-source-approval.v1"
_APPROVAL_MAX_BYTES = 1024 * 1024
# Populating this requires independent serving/accounting qualification. It does
# not contain this repository's commit/tree/file hashes or an approval digest.
_QUALIFIED_SERVING_CONTRACTS: tuple[str, ...] = ()
_ISSUER = object()
_LOCK = RLock()


class _PrivateHandle:
    __slots__ = ()

    def __init__(self, issuer=None):
        if issuer is not _ISSUER:
            raise SourceAuthoringLaunchError()

    def __copy__(self):
        raise SourceAuthoringLaunchError()

    def __deepcopy__(self, memo):
        raise SourceAuthoringLaunchError()

    def __reduce__(self):
        raise SourceAuthoringLaunchError()

    def __repr__(self):
        return "<source-authoring private launch handle>"


class _AdmittedProfile(_PrivateHandle):
    __slots__ = ()


class LiveSourceAuthoringLaunch(_PrivateHandle):
    __slots__ = ()

    def revoke(self):
        _revoke_launch(self)


class _RuntimeInstall(_PrivateHandle):
    __slots__ = ()


class _LiveLease(_PrivateHandle):
    __slots__ = ()


class _VerifiedTokenProof(_PrivateHandle):
    __slots__ = ()


@dataclass(repr=False)
class _LaunchRecord:
    profile: _QualifiedLaunchProfile
    credential: bytes
    epoch: str = field(default_factory=lambda: secrets.token_hex(32))
    grant: str = field(default_factory=lambda: secrets.token_hex(32))
    generation: int = 1
    state: str = "ISSUED"
    install: _RuntimeInstall | None = None
    lease: _LiveLease | None = None
    owners: SourceAuthoringOwners | None = None
    cancel: object = None


_ADMISSIONS: dict[_AdmittedProfile, _QualifiedLaunchProfile] = {}
_LAUNCHES: dict[LiveSourceAuthoringLaunch, _LaunchRecord] = {}
_INSTALLS: dict[_RuntimeInstall, LiveSourceAuthoringLaunch] = {}
_LEASES: dict[_LiveLease, LiveSourceAuthoringLaunch] = {}
_TOKENS: dict[_VerifiedTokenProof, tuple[_LiveLease, int, str, int]] = {}


def _profile_digest(profile):
    values = asdict(profile)
    values.pop("launch_profile_sha256")
    return hashlib.sha256(b"exitspec-qualified-source-launch-v1\0" + canonical_json_bytes(values)).hexdigest()


def _require_profile(profile):
    """Only issued identities with fixed, finite, complete metadata are usable."""
    if type(profile) is not _QualifiedLaunchProfile or not any(profile is item for item in _PRODUCTION_PROFILES):
        raise SourceAuthoringLaunchError()
    try:
        for value in (profile.approval_id, profile.tokenizer_identity, profile.account_pricing_approval,
                      profile.custody_approval, profile.region):
            if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value) is None:
                raise ValueError()
        for value in (profile.valid_from, profile.valid_until):
            if type(value) is not float or not math.isfinite(value) or value < 0:
                raise ValueError()
        if not profile.valid_from <= time.time() < profile.valid_until:
            raise ValueError()
        if (profile.request_profile_sha256 != PROFILE_SHA256 or profile.schema_sha256 != SCHEMA_SHA256
            or profile.header_policy_sha256 != HEADER_POLICY_SHA256
            or profile.redaction_sha256 != REDACTION_CONFIGURATION_DIGEST
            or type(profile.input_tokens_max) is not int or profile.input_tokens_max != 8192
            or type(profile.output_tokens_max) is not int or profile.output_tokens_max != 2000
            or profile.request_budget_usd != "0.01" or profile.launch_budget_usd != "0.10"):
            raise ValueError()
        if any(type(value) is not str or re.fullmatch(r"[a-f0-9]{40}", value) is None
               for value in (profile.code_revision, profile.code_tree)):
            raise ValueError()
        for entries in (profile.files, profile.tokenizer_artifacts):
            if type(entries) is not tuple or not 1 <= len(entries) <= 4096:
                raise ValueError()
            paths = []
            for item in entries:
                if type(item) is not tuple or len(item) != 2:
                    raise ValueError()
                path, digest = item
                if (type(path) is not str or not 1 <= len(path) <= 4096
                    or any(ord(c) < 32 for c in path)
                    or type(digest) is not str or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
                    raise ValueError()
                paths.append(path)
            if len(paths) != len(set(paths)):
                raise ValueError()
        if profile.launch_profile_sha256 != _profile_digest(profile):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        raise SourceAuthoringLaunchError() from None
    return profile



def _approval_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _approval_constant(_):
    raise ValueError()


def _read_approval(path, expected_sha256):
    """Equality to the operator's independent anchor, not a signature check."""
    descriptor = None
    try:
        if (type(path) is not str or not 1 <= len(path) <= 4096
            or any(ord(c) < 32 for c in path)
            or type(expected_sha256) is not str
            or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None):
            raise ValueError()
        target = Path(path)
        root = Path(__file__).resolve().parents[2]
        if (not target.is_absolute() or ".." in target.parts
            or target.resolve().is_relative_to(root)
            or any(part.is_symlink() for part in (target, *target.parents))):
            raise ValueError()
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        import stat
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError()
        raw = bytearray()
        deadline = time.monotonic() + 1
        while len(raw) <= _APPROVAL_MAX_BYTES:
            if time.monotonic() >= deadline:
                raise ValueError()
            chunk = os.read(descriptor, min(65536, _APPROVAL_MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if (not 1 <= len(raw) <= _APPROVAL_MAX_BYTES
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)):
            raise ValueError()
        value = json.loads(bytes(raw).decode("utf-8"), object_pairs_hook=_approval_pairs,
                           parse_constant=_approval_constant)
        if (type(value) is not dict or set(value) != {"schema_version", "profile"}
            or value["schema_version"] != _APPROVAL_SCHEMA
            or type(value["profile"]) is not dict
            or set(value["profile"]) != {item.name for item in fields(_QualifiedLaunchProfile)}):
            raise ValueError()
        values = value["profile"]
        for name in ("files", "tokenizer_artifacts"):
            entries = values[name]
            if (type(entries) is not list or not 1 <= len(entries) <= 4096
                or any(type(item) is not list or len(item) != 2 for item in entries)):
                raise ValueError()
            values[name] = tuple(tuple(item) for item in entries)
        return _QualifiedLaunchProfile(**values)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise SourceAuthoringLaunchError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_detached_approval(path, expected_sha256):
    global _PRODUCTION_PROFILES
    # No file, terminal, pipe or network effects when serving is unqualified.
    if not _QUALIFIED_SERVING_CONTRACTS:
        raise SourceAuthoringLaunchError()
    profile = _read_approval(path, expected_sha256)
    if profile.tokenizer_identity not in _QUALIFIED_SERVING_CONTRACTS:
        raise SourceAuthoringLaunchError()
    with _LOCK:
        if _PRODUCTION_PROFILES:
            raise SourceAuthoringLaunchError()
        _PRODUCTION_PROFILES = (profile,)
        _APPROVALS[id(profile)] = (profile, path, expected_sha256)
        try:
            _require_profile(profile)
        except Exception:
            _PRODUCTION_PROFILES = ()
            _APPROVALS.pop(id(profile), None)
            raise
    return profile


def _verify_detached_approval(profile):
    stored = _APPROVALS.get(id(profile))
    if stored is None or stored[0] is not profile:
        raise SourceAuthoringLaunchError()
    if _read_approval(stored[1], stored[2]) != profile:
        raise SourceAuthoringLaunchError()
    if profile.tokenizer_identity not in _QUALIFIED_SERVING_CONTRACTS:
        raise SourceAuthoringLaunchError()


def _worker_approval_arguments(lease):
    with _lease_guard(lease) as record:
        _verify_detached_approval(record.profile)
        _, path, digest = _APPROVALS[id(record.profile)]
        return ["--approval-file", path, "--approval-sha256", digest]


def _bootstrap_worker_approval(argv):
    if not _QUALIFIED_SERVING_CONTRACTS:
        raise SourceAuthoringLaunchError()
    if (type(argv) is not list or len(argv) != 4
        or argv[0] != "--approval-file" or argv[2] != "--approval-sha256"):
        raise SourceAuthoringLaunchError()
    _load_detached_approval(argv[1], argv[3])
    return _require_worker_profile()

def _git_read(root, args, deadline, budget):
    """Bound aggregate Git output while reading; never buffer then cap."""
    process = None
    try:
        process = subprocess.Popen(
            ["/usr/bin/git", "-C", str(root), *args], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0"},
        )
        result = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise SourceAuthoringLaunchError()
            chunk = os.read(process.stdout.fileno(), min(4096, budget[0] + 1))
            if not chunk:
                break
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise SourceAuthoringLaunchError()
            result.extend(chunk)
        if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            raise SourceAuthoringLaunchError()
        return bytes(result)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SourceAuthoringLaunchError() from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1)
            finally:
                if process.stdout is not None:
                    process.stdout.close()


def _hash_local_file(path, deadline, budget):
    if time.monotonic() >= deadline or path.is_symlink():
        raise SourceAuthoringLaunchError()
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        import stat
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SourceAuthoringLaunchError()
        digest = hashlib.sha256()
        size = 0
        while True:
            if time.monotonic() >= deadline:
                raise SourceAuthoringLaunchError()
            chunk = os.read(descriptor, 65536)
            if not chunk:
                return digest.hexdigest()
            size += len(chunk)
            budget[0] -= len(chunk)
            if size > 8 * 1024 * 1024 or budget[0] < 0:
                raise SourceAuthoringLaunchError()
            digest.update(chunk)
    except OSError:
        raise SourceAuthoringLaunchError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _verify_code_and_artifacts(profile):
    """Verify the detached anchor and every frozen tracked file before effects."""
    _require_profile(profile)
    _verify_detached_approval(profile)
    root = Path(__file__).resolve().parents[2]
    deadline = time.monotonic() + 5
    budget = [1024 * 1024]
    if (_git_read(root, ["rev-parse", "HEAD"], deadline, budget).decode().strip() != profile.code_revision
        or _git_read(root, ["rev-parse", "HEAD^{tree}"], deadline, budget).decode().strip() != profile.code_tree
        or _git_read(root, ["status", "--porcelain=v1", "--untracked-files=all"], deadline, budget)):
        raise SourceAuthoringLaunchError()
    tracked = _git_read(root, ["ls-files", "-z"], deadline, budget).decode().rstrip("\0").split("\0")
    if sorted(tracked) != sorted(name for name, _ in profile.files):
        raise SourceAuthoringLaunchError()
    file_budget = [64 * 1024 * 1024]
    for name, expected in profile.files:
        relative = Path(name)
        path = root / relative
        if relative.is_absolute() or ".." in relative.parts or not path.resolve().is_relative_to(root):
            raise SourceAuthoringLaunchError()
        if _hash_local_file(path, deadline, file_budget) != expected:
            raise SourceAuthoringLaunchError()
    for name, expected in profile.tokenizer_artifacts:
        path = Path(name)
        if not path.is_absolute() or _hash_local_file(path, deadline, file_budget) != expected:
            raise SourceAuthoringLaunchError()
    _verify_tokenizer_implementation(profile)
    if time.monotonic() >= deadline:
        raise SourceAuthoringLaunchError()


def _verify_tokenizer_implementation(profile):
    from . import source_authoring_tokenizer as tokenizer
    try:
        if (profile.tokenizer_identity != tokenizer.TOKENIZER_ID
            or profile.tokenizer_identity not in _QUALIFIED_SERVING_CONTRACTS):
            raise SourceAuthoringLaunchError()
        tokenizer.verify(profile.tokenizer_artifacts)
    except ValueError:
        raise SourceAuthoringLaunchError() from None


def _evaluate_local_tokens(profile, body):
    from . import source_authoring_tokenizer as tokenizer
    try:
        _require_profile(profile)
        _verify_detached_approval(profile)
        if (profile.tokenizer_identity != tokenizer.TOKENIZER_ID
            or profile.tokenizer_identity not in _QUALIFIED_SERVING_CONTRACTS):
            raise SourceAuthoringLaunchError()
        return tokenizer.count_tokens(profile.tokenizer_artifacts, body)
    except ValueError:
        raise SourceAuthoringLaunchError() from None


def _admit_operator_profile(approval_id, *, approval_file=None, expected_sha256=None):
    if approval_file is not None or expected_sha256 is not None:
        _load_detached_approval(approval_file, expected_sha256)
    if not _PRODUCTION_PROFILES:
        raise SourceAuthoringLaunchError()
    if type(approval_id) is not str or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", approval_id) is None:
        raise SourceAuthoringLaunchError()
    matches = [item for item in _PRODUCTION_PROFILES if item.approval_id == approval_id]
    if len(matches) != 1:
        raise SourceAuthoringLaunchError()
    profile = _require_profile(matches[0])
    _verify_code_and_artifacts(profile)
    with _LOCK:
        _require_profile(profile)
        admitted = _AdmittedProfile(_ISSUER)
        _ADMISSIONS[admitted] = profile
        return admitted


def _admitted_profile(admitted):
    with _LOCK:
        if type(admitted) is not _AdmittedProfile or admitted not in _ADMISSIONS:
            raise SourceAuthoringLaunchError()
        return _require_profile(_ADMISSIONS[admitted])


def _issue_live_launch(admitted, credential):
    profile = _admitted_profile(admitted)
    # Recheck after the operator prompt, before retaining any credential.
    _verify_code_and_artifacts(profile)
    try:
        validate_credential(credential)
    except ValueError:
        raise SourceAuthoringLaunchError() from None
    with _LOCK:
        _admitted_profile(admitted)
        if len(_LAUNCHES) >= 64:
            raise SourceAuthoringLaunchError()
        launch = LiveSourceAuthoringLaunch(_ISSUER)
        _LAUNCHES[launch] = _LaunchRecord(profile, credential)
        del _ADMISSIONS[admitted]
        return launch


def _record_for_launch(launch):
    if type(launch) is not LiveSourceAuthoringLaunch or launch not in _LAUNCHES:
        raise SourceAuthoringLaunchError()
    record = _LAUNCHES[launch]
    if record.state == "REVOKED":
        raise SourceAuthoringLaunchError()
    _require_profile(record.profile)
    return record


def _reserve_runtime_install(launch):
    with _LOCK:
        record = _record_for_launch(launch)
        if record.state != "ISSUED":
            raise SourceAuthoringLaunchError()
        install = _RuntimeInstall(_ISSUER)
        record.install, record.state = install, "RESERVED"
        _INSTALLS[install] = launch
        return install


def _bind_runtime_install(install, owners):
    with _LOCK:
        if type(install) is not _RuntimeInstall or install not in _INSTALLS or type(owners) is not SourceAuthoringOwners:
            raise SourceAuthoringLaunchError()
        launch = _INSTALLS[install]
        record = _record_for_launch(launch)
        if record.state != "RESERVED" or record.install is not install:
            raise SourceAuthoringLaunchError()
        lease = _LiveLease(_ISSUER)
        record.lease, record.owners, record.state = lease, owners, "BOUND"
        _LEASES[lease] = launch
        return lease


def _lease_record(lease, owners=None):
    if type(lease) is not _LiveLease or lease not in _LEASES:
        raise SourceAuthoringLaunchError()
    record = _record_for_launch(_LEASES[lease])
    if record.state != "BOUND" or record.lease is not lease or (owners is not None and record.owners is not owners):
        raise SourceAuthoringLaunchError()
    return record


@contextmanager
def _lease_guard(lease, owners=None):
    """Issuer lock follows owner/operation locks and precedes supervisor lock."""
    with _LOCK:
        yield _lease_record(lease, owners)


def _lease_metadata(lease):
    with _lease_guard(lease) as record:
        return {"epoch": record.epoch, "grant": record.grant,
                "credential_generation": record.generation,
                "launch_profile_sha256": record.profile.launch_profile_sha256,
                "code_revision": record.profile.code_revision,
                "approval_id": record.profile.approval_id}


def _lease_policy_values(lease):
    with _lease_guard(lease) as record:
        profile = record.profile
        return {"launch_profile_sha256": profile.launch_profile_sha256,
                "code_revision": profile.code_revision, "approval_id": profile.approval_id,
                "tokenizer_identity": profile.tokenizer_identity,
                "pricing_approval": profile.account_pricing_approval,
                "custody_approval": profile.custody_approval, "region_policy": profile.region}


def _register_cancel(lease, callback):
    with _lease_guard(lease) as record:
        if record.cancel is not None:
            raise SourceAuthoringLaunchError()
        record.cancel = callback



def _revoke_record_locked(record):
    record.state = "REVOKED"
    record.generation += 1
    record.credential = b""
    callback, record.cancel = record.cancel, None
    for proof, binding in list(_TOKENS.items()):
        if binding[0] is record.lease:
            del _TOKENS[proof]
    return callback

def _revoke_launch(launch):
    with _LOCK:
        if type(launch) is not LiveSourceAuthoringLaunch or launch not in _LAUNCHES:
            raise SourceAuthoringLaunchError()
        record = _LAUNCHES[launch]
        if record.state == "REVOKED":
            return
        callback = _revoke_record_locked(record)
    if callback is not None:
        callback()


def _close_install(install):
    with _LOCK:
        if type(install) is not _RuntimeInstall or install not in _INSTALLS:
            raise SourceAuthoringLaunchError()
        launch = _INSTALLS[install]
    _revoke_launch(launch)



def _abort_reserved_install(install):
    with _LOCK:
        if type(install) is not _RuntimeInstall or install not in _INSTALLS:
            return
        handle = _INSTALLS[install]
        record = _LAUNCHES[handle]
        if record.state != "RESERVED":
            return
        # Check and fence are one atomic issuer transaction. A competing bind
        # cannot win between a failed constructor's check and its revocation.
        callback = _revoke_record_locked(record)
    if callback is not None:
        callback()


def _revoke_lease(lease):
    with _LOCK:
        if type(lease) is not _LiveLease or lease not in _LEASES:
            raise SourceAuthoringLaunchError()
        handle = _LEASES[lease]
    _revoke_launch(handle)

def _issue_token_proof(lease, body):
    with _lease_guard(lease) as record:
        profile, generation = record.profile, record.generation
    if type(body) is not bytes or len(body) > 65536:
        raise SourceAuthoringLaunchError()
    count = _evaluate_local_tokens(profile, body)
    if type(count) is not int or not 1 <= count <= profile.input_tokens_max:
        raise SourceAuthoringLaunchError()
    with _lease_guard(lease) as record:
        if record.generation != generation or len(_TOKENS) >= 65536:
            raise SourceAuthoringLaunchError()
        proof = _VerifiedTokenProof(_ISSUER)
        _TOKENS[proof] = (lease, generation, body_digest(body), count)
        return proof


def _token_metadata(lease, proof, body):
    with _lease_guard(lease) as record:
        if type(proof) is not _VerifiedTokenProof or proof not in _TOKENS:
            raise SourceAuthoringLaunchError()
        stored_lease, generation, digest, count = _TOKENS[proof]
        if stored_lease is not lease or generation != record.generation or digest != body_digest(body):
            raise SourceAuthoringLaunchError()
        return {"body_sha256": digest, "input_tokens": count,
                "tokenizer_identity": record.profile.tokenizer_identity,
                "credential_generation": generation}


def _credential_for_ticket(lease, proof, body):
    with _lease_guard(lease) as record:
        _token_metadata(lease, proof, body)
        return record.credential, record.generation


def _require_worker_profile():
    # Admission precedes descriptor inspection and framing reads in the child.
    if len(_PRODUCTION_PROFILES) != 1:
        raise SourceAuthoringLaunchError()
    profile = _require_profile(_PRODUCTION_PROFILES[0])
    _verify_code_and_artifacts(profile)
    return profile


def _check_worker_binding(profile, binding):
    _require_profile(profile)
    if (binding.profile_sha256 != PROFILE_SHA256
        or binding.launch_profile_sha256 != profile.launch_profile_sha256
        or binding.code_revision != profile.code_revision):
        raise SourceAuthoringLaunchError()


def _display_mode(lease):
    with _lease_guard(lease):
        return "QUALIFIED_FIREWORKS"
