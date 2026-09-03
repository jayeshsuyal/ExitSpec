#!/usr/bin/env python3
"""Run bounded qualification checks from a trusted control checkout.

The candidate contributes only the source tree under test. Inputs, expected
states, process limits, and this harness come from the base checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_SOURCE_ENTRIES = 10_000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
CHILD_TIMEOUT_SECONDS = 20


def _identity(path: Path) -> tuple[int, int, int, int, int, int]:
    value = os.lstat(path)
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _admit_source(root: Path) -> Path:
    supplied = root.absolute()
    if not supplied.is_dir() or supplied.is_symlink():
        raise ValueError("candidate source must be a real directory")
    resolved = supplied.resolve(strict=True)
    before = _identity(resolved)
    total_bytes = 0
    for entries, path in enumerate((resolved, *resolved.rglob("*")), start=1):
        if entries > MAX_SOURCE_ENTRIES or path.is_symlink():
            raise ValueError("candidate source is outside the bounded tree")
        value = os.lstat(path)
        if stat.S_ISREG(value.st_mode):
            if value.st_nlink != 1:
                raise ValueError("candidate source contains a hard link")
            total_bytes += value.st_size
            if total_bytes > MAX_SOURCE_BYTES:
                raise ValueError("candidate source is oversized")
        elif not stat.S_ISDIR(value.st_mode):
            raise ValueError("candidate source contains an unsupported node")
    if _identity(resolved) != before:
        raise ValueError("candidate source changed during inspection")
    return resolved


def _run_candidate(source: Path, arguments: list[str]) -> tuple[int, bytes, bytes]:
    child = """
import sys
source = sys.argv[1]
sys.path.insert(0, source)
from exitspec.cli import main
raise SystemExit(main(sys.argv[2:]))
"""
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", child, str(source), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=CHILD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise RuntimeError("candidate qualification command timed out") from error
    if len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > MAX_OUTPUT_BYTES:
        raise RuntimeError("candidate qualification output exceeded its bound")
    return process.returncode, stdout, stderr


def _assert_invalid_receipt_case(source: Path, root: Path) -> None:
    control_root = Path(__file__).resolve().parents[1]
    subject = root / "subject.json"
    scope = root / "scope.json"
    receipt = root / "receipt.json"
    subject.write_bytes(
        (control_root / "tests/fixtures/serving_subject/v1/golden.json").read_bytes()
    )
    scope.write_bytes(
        (
            control_root / "tests/fixtures/qualification_scope/v1/golden-scope.json"
        ).read_bytes()
    )
    receipt.write_bytes(b'{"schema_version":"invalid"}')
    code, stdout, stderr = _run_candidate(
        source,
        [
            "qualification",
            "check",
            "--subject",
            str(subject),
            "--scope",
            str(scope),
            "--receipt",
            str(receipt),
            "--assessed-at",
            "2026-08-01T12:00:00Z",
            "--json",
        ],
    )
    if stderr or code != 6:
        raise AssertionError(
            f"candidate invalid-receipt result was not code 6: {code}"
        )
    payload = json.loads(stdout)
    if payload.get("validity") != "INVALID":
        raise AssertionError("candidate did not fail closed on an invalid receipt")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_source", type=Path)
    args = parser.parse_args()
    source = _admit_source(args.candidate_source)
    with tempfile.TemporaryDirectory(prefix="exitspec-qualification-control-") as raw:
        _assert_invalid_receipt_case(source, Path(raw))
    print("Base-owned qualification control harness passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
