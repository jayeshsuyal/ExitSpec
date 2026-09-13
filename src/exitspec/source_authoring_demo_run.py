"""Private same-user/host demo consumption. No credentials or source text.

Cooperating processes cannot reuse a consumed run. An owner/admin can delete or
roll back local files; this is not tamper-proof or a cross-host invoice limit.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from .canonical import canonical_json_bytes

CONTRACT = "exitspec-demo-local-accounting-v1"
MODE = "DEMO_FIREWORKS"
PURPOSE = "Demo only: ONE Fireworks attempt for this approved run, then human confirmation and synthetic proof. No automatic retry or replacement attempt."
CUSTODY = ("This exact redacted text goes to Fireworks under the approved data handling terms. "
           "The global endpoint has no regional guarantee. Local token counts do not prove server token parity. "
           "$0.01 is local bookkeeping, not a guaranteed invoice ceiling; provider-reported usage is recorded separately. "
           "A failed, canceled or uncertain attempt remains consumed. Review, confirmation and handoff remain available.")


class DemoRunError(ValueError):
    def __init__(self):
        super().__init__("Demo run unavailable.")


def _directory(profile):
    fd = None
    try:
        path = Path(profile.run_directory)
        if (not path.is_absolute() or str(path) != str(path.resolve()) or ".." in path.parts
            or any(p.is_symlink() for p in (path, *path.parents))
            or type(profile.run_id) is not str or not re.fullmatch(r"[a-f0-9]{64}", profile.run_id)
            or profile.run_uid != os.getuid() or profile.run_host != os.uname().nodename):
            raise ValueError()
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != profile.run_uid or info.st_dev != profile.run_device
            or info.st_ino != profile.run_inode):
            raise ValueError()
        return fd
    except (OSError, ValueError, TypeError):
        if fd is not None:
            os.close(fd)
        raise DemoRunError() from None


def verify(profile):
    fd = _directory(profile)
    os.close(fd)


def consumed(profile):
    fd = _directory(profile)
    try:
        try:
            os.stat("consumed.json", dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True  # Any entry, including incomplete or hostile, closes admission.
    finally:
        os.close(fd)


def _write_once(profile, name, value):
    directory = _directory(profile)
    fd = None
    try:
        raw = canonical_json_bytes(value)
        if len(raw) > 8192:
            raise ValueError()
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        offset = 0
        while offset < len(raw):
            n = os.write(fd, raw[offset:])
            if n <= 0:
                raise ValueError()
            offset += n
        os.fsync(fd)
        os.fsync(directory)
        # Darwin full flush requests stable storage, not just kernel cache.
        if os.uname().sysname == "Darwin":
            import fcntl
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
        verify(profile)  # Refuse a replaced pathname even with an open old fd.
    except (OSError, ValueError, TypeError):
        # Never unlink/refund a partial record.
        raise DemoRunError() from None
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def consume(profile, approval_digest, *, operation, source_sha256, body_sha256):
    values = (approval_digest, operation, source_sha256, body_sha256)
    if any(type(v) is not str or not re.fullmatch(r"[a-f0-9]{64}", v) for v in values):
        raise DemoRunError()
    record = {"contract": CONTRACT, "run_id": profile.run_id, "approval_sha256": approval_digest,
              "candidate": profile.code_revision, "launch_profile_sha256": profile.launch_profile_sha256,
              "operation": operation, "source_sha256": source_sha256,
              "body_sha256": body_sha256, "usage_status": "UNKNOWN", "provider_usage": None,
              "billing_status": "UNKNOWN", "attempt_consumed": True}
    _write_once(profile, "consumed.json", record)
    return hashlib.sha256(canonical_json_bytes(record)).hexdigest()


def observe(profile, consumption_digest, observation):
    directory = _directory(profile)
    fd = None
    try:
        fd = os.open("consumed.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 8192):
            raise ValueError()
        raw = os.read(fd, 8193)
        if hashlib.sha256(raw).hexdigest() != consumption_digest:
            raise ValueError()
        record = json.loads(raw)
        record.update(observation)
        record["consumption_sha256"] = consumption_digest
        _write_once(profile, "observation.json", record)
    except (OSError, ValueError, TypeError):
        raise DemoRunError() from None
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def claim_dispatch(profile, binding):
    """Second fence in the installed child: only the consumed operation may send.

    Even replaying the same child protocol cannot dispatch twice on this run.
    A crash after this marker is durable but before POST still consumes it.
    """
    directory = _directory(profile)
    fd = None
    try:
        fd = os.open("consumed.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 8192):
            raise ValueError()
        raw = os.read(fd, 8193)
        value = json.loads(raw)
        if (canonical_json_bytes(value) != raw or value.get("contract") != CONTRACT
            or value.get("run_id") != profile.run_id or value.get("candidate") != profile.code_revision
            or value.get("launch_profile_sha256") != binding.launch_profile_sha256
            or value.get("body_sha256") != binding.body_sha256 or value.get("operation") != binding.operation):
            raise ValueError()
        _write_once(profile, "dispatch.json", {"consumption_sha256": hashlib.sha256(raw).hexdigest(),
                                               "operation": binding.operation, "body_sha256": binding.body_sha256})
    except (OSError, ValueError, TypeError):
        raise DemoRunError() from None
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)
