"""Independent, offline verification of ``inferdrome.evidence.v1`` bundles.

This module deliberately imports no Inferdrome code.  It verifies vendored
public schemas, exact artifact bytes, filesystem safety, cross-artifact
identity, and every v1 derived measurement before returning typed facts to the
ExitSpec-owned acceptance layer.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from enum import Enum
from functools import cache
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, Final, NoReturn

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from .canonical import CanonicalizationError, canonical_json_bytes
from .inferdrome_managed_profile import (
    ManagedInferdromeProfileError,
    ManagedInferdromeProfileFacts,
    validate_managed_invocation_profile,
)

INFERDROME_VERIFIER_VERSION: Final = "1.0.0"

_TAGGED_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PINNED_VLLM_VERSION = re.compile(r"0\.26\.0(?:\+[0-9A-Za-z.-]+)?\Z")
_VLLM_ADAPTER_VERSION: Final = "1.0.0"
_VLLM_NATIVE_SCHEMA_FINGERPRINT: Final = (
    "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
)
_MAX_CONTROL_BYTES: Final = 1_048_576
_MAX_INVOCATION_BYTES: Final = 2_097_152
_MAX_PREFLIGHT_RESPONSE_BYTES: Final = 1_048_576
_MAX_VERSION_BYTES: Final = 65_536
_MAX_NATIVE_ARRAY_ITEMS: Final = 100_000
_MAX_NATIVE_ITL_ITEMS: Final = 16_384
_MAX_NATIVE_TEXT_CHARACTERS: Final = 16 * 1024 * 1024
_MAX_NATIVE_ERROR_CHARACTERS: Final = 1024 * 1024
_MAX_JSON_NUMBER_DIGITS: Final = 128
_MAX_JSON_DECIMAL_EXPONENT: Final = 1_000
_SEMVER_LINE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)
_EXPECTED_ROLES: Final = (
    "bundle_descriptor",
    "original_spec",
    "resolved_spec",
    "request_plan",
    "environment",
    "execution",
    "producer_invocation",
    "producer_version",
    "producer_exit_status",
    "native_result",
    "native_stdout",
    "native_stderr",
    "request_records",
    "metric_definitions",
    "measurements",
    "integrity_manifest",
)
_MEDIA_TYPE_BY_ROLE: Final = {
    "bundle_descriptor": "application/json",
    "original_spec": "application/yaml",
    "resolved_spec": "application/json",
    "request_plan": "application/json",
    "environment": "application/json",
    "execution": "application/json",
    "producer_invocation": "application/json",
    "producer_version": "text/plain",
    "producer_exit_status": "text/plain",
    "native_result": "application/json",
    "native_stdout": "text/plain",
    "native_stderr": "text/plain",
    "request_records": "application/x-ndjson",
    "metric_definitions": "application/json",
    "measurements": "application/json",
    "integrity_manifest": "application/json",
}
_ENVIRONMENT_FIELD_NAMES: Final = frozenset(
    {
        "client.os",
        "client.arch",
        "client.python_version",
        "producer.version",
        "producer.distribution_sha256",
        "target.engine_version",
        "target.model_revision",
        "target.tokenizer_revision",
        "server.model_id",
        "gpu.model",
        "gpu.count",
        "cuda.version",
        "driver.version",
    }
)
_SCHEMA_BY_ROLE: Final = {
    "bundle_descriptor": "evidence-bundle.schema.json",
    "resolved_spec": "experiment.schema.json",
    "request_plan": "request-plan.schema.json",
    "environment": "environment.schema.json",
    "execution": "execution.schema.json",
    "request_records": "request-record.schema.json",
    "metric_definitions": "metric-definitions.schema.json",
    "measurements": "measurements.schema.json",
}
_SCHEMA_SHA256: Final = {
    "environment.schema.json": (
        "0a0c43552f86d45579786f30f71da62cf6c02ea7c5c2cfcf76dc1427dc9df777"
    ),
    "evidence-bundle.schema.json": (
        "276a8e2c3d14fd18f45f428bdda31964af879adbad0341ae5959c599dd5c3437"
    ),
    "execution.schema.json": (
        "f4615a340bea6566c6924e02777927c9491cd351a43f8aafa01ef9f34002dfe5"
    ),
    "experiment.schema.json": (
        "244f45d5aba43a45e7e9f0cf98965881a26667a56977fcc2bc418368382f86ab"
    ),
    "measurements.schema.json": (
        "39b86747910842a9f726ac8bcdd035cad6c2bdd9454cd090fde8eb3739438ecb"
    ),
    "metric-definitions.schema.json": (
        "d501d11c030e7b9fee71dfafd5f9c5462e48f237bac918139cb2cfaff34bc204"
    ),
    "request-plan.schema.json": (
        "c866742180909e982a6466553d296a8412734c49c2b5f5bbb549a6c63fb2417d"
    ),
    "request-record.schema.json": (
        "a65f763947207f0a312770d743a623363ae4eec36336e0126e87df350ec07ee4"
    ),
}
_UNAVAILABLE_METRICS: Final = (
    "first_nonempty_content_ttft_ns",
    "terminal_e2e_latency_ns",
    "upstream_tpot_ns",
    "exact_achieved_concurrency",
    "scheduled_offset_ns",
    "http_status",
    "finish_reason",
)
_VLLM_NATIVE_FIELDS: Final = frozenset(
    {
        "backend",
        "burstiness",
        "completed",
        "date",
        "duration",
        "endpoint_type",
        "errors",
        "failed",
        "generated_texts",
        "inferdrome_adapter_version",
        "inferdrome_execution_fingerprint",
        "inferdrome_producer_version",
        "inferdrome_run_id",
        "inferdrome_workload_sha256",
        "input_lens",
        "itls",
        "label",
        "max_concurrency",
        "max_concurrent_requests",
        "max_output_tokens_per_s",
        "mean_e2el_ms",
        "median_e2el_ms",
        "model_id",
        "num_prompts",
        "output_lens",
        "output_throughput",
        "p50_e2el_ms",
        "p95_e2el_ms",
        "p99_e2el_ms",
        "request_goodput",
        "request_rate",
        "request_throughput",
        "rtfx",
        "start_times",
        "std_e2el_ms",
        "tokenizer_id",
        "total_input_tokens",
        "total_output_tokens",
        "total_token_throughput",
        "ttfts",
    }
)


class InferdromeBundleErrorCode(str, Enum):
    """Stable rejection classes for untrusted external evidence."""

    UNSAFE_BUNDLE = "UNSAFE_BUNDLE"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    INTEGRITY_MISMATCH = "INTEGRITY_MISMATCH"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    EVIDENCE_INELIGIBLE = "EVIDENCE_INELIGIBLE"
    INTERNAL_INCONSISTENCY = "INTERNAL_INCONSISTENCY"


class InferdromeBundleRejected(ValueError):
    """The supplied directory cannot participate in acceptance evaluation."""

    def __init__(self, code: InferdromeBundleErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class InferdromeBundleLimits:
    """Resource bounds applied before external artifact content is trusted."""

    max_files: int = 64
    max_directories: int = 64
    max_file_bytes: int = 256 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024
    max_jsonl_line_bytes: int = 4 * 1024 * 1024
    max_request_records: int = 100_000
    max_depth: int = 8

    def __post_init__(self) -> None:
        values = (
            self.max_files,
            self.max_directories,
            self.max_file_bytes,
            self.max_total_bytes,
            self.max_jsonl_line_bytes,
            self.max_request_records,
            self.max_depth,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("Inferdrome bundle limits must be positive integers.")


@dataclass(frozen=True, slots=True)
class RecalculatedInferdromeMeasurements:
    """Facts independently reduced from canonical request records."""

    attempted_count: int
    successful_count: int
    failed_count: int
    anomalous_count: int
    error_rate: Decimal
    p95_ttft_ns: int | None
    ttft_definition: str
    records_sha256: str
    recalculation_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedInferdromeBundle:
    """Verified external bytes plus the minimum facts needed by ExitSpec."""

    root: Path
    bundle_digest: str
    descriptor: Mapping[str, Any]
    resolved_spec: Mapping[str, Any]
    request_plan: Mapping[str, Any]
    execution: Mapping[str, Any]
    environment: Mapping[str, Any]
    records: tuple[Mapping[str, Any], ...]
    recalculated: RecalculatedInferdromeMeasurements
    managed_profile: ManagedInferdromeProfileFacts | None


@dataclass(frozen=True, slots=True)
class _Identity:
    device: int
    inode: int
    mode: int
    size: int
    link_count: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _ScannedNode:
    relative_path: str
    identity: _Identity


def _identity(value: os.stat_result) -> _Identity:
    return _Identity(
        device=value.st_dev,
        inode=value.st_ino,
        mode=value.st_mode,
        size=value.st_size,
        link_count=value.st_nlink,
        modified_ns=value.st_mtime_ns,
        changed_ns=value.st_ctime_ns,
    )


class _SafeBundleReader:
    """No-follow, bounded, stable-snapshot reader for one bundle directory."""

    def __init__(self, root: Path, limits: InferdromeBundleLimits) -> None:
        supplied = Path(root)
        try:
            supplied_stat = os.lstat(supplied)
        except OSError as error:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome bundle root is missing or inaccessible.",
                error,
            )
        if stat.S_ISLNK(supplied_stat.st_mode) or not stat.S_ISDIR(
            supplied_stat.st_mode
        ):
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome bundle root must be a real directory.",
            )
        try:
            self.root = supplied.resolve(strict=True)
        except OSError as error:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome bundle root cannot be resolved safely.",
                error,
            )
        self.limits = limits
        self._root_identity = _identity(os.lstat(self.root))
        self._files: dict[str, _ScannedNode] = {}
        self._directories: dict[str, _ScannedNode] = {}
        self._cache: dict[str, bytes] = {}
        self._total_bytes = 0
        self._nodes_seen = 0
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            root_fd = os.open(self.root, directory_flags)
        except OSError as error:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome bundle root cannot be opened safely.",
                error,
            )
        try:
            if _identity(os.fstat(root_fd)) != self._root_identity:
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome bundle root changed before inspection.",
                )
            self._scan_directory(root_fd, "", 0)
            if _identity(os.fstat(root_fd)) != self._root_identity:
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome bundle root changed during inspection.",
                )
        finally:
            os.close(root_fd)
        self._verify_root_identity()

    @property
    def files(self) -> frozenset[str]:
        return frozenset(self._files)

    @property
    def directories(self) -> frozenset[str]:
        return frozenset(self._directories)

    def file_size(self, relative_path: str) -> int:
        _validate_relative_path(relative_path)
        node = self._files.get(relative_path)
        if node is None:
            _reject(
                InferdromeBundleErrorCode.INTEGRITY_MISMATCH,
                "A declared Inferdrome artifact is missing.",
            )
        return node.identity.size

    def _scan_directory(
        self,
        directory_fd: int,
        relative: str,
        depth: int,
    ) -> None:
        if depth > self.limits.max_depth:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome bundle directory depth exceeds its limit.",
            )
        try:
            with os.scandir(directory_fd) as iterator:
                entries = []
                for entry in iterator:
                    self._nodes_seen += 1
                    if self._nodes_seen > (
                        self.limits.max_files
                        + self.limits.max_directories
                    ):
                        _reject(
                            InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                            "Inferdrome bundle node count exceeds its limit.",
                        )
                    entries.append(entry)
                entries.sort(key=lambda item: item.name)
        except OSError as error:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome bundle directory cannot be inspected safely.",
                error,
            )
        for entry in entries:
            child_relative = entry.name if not relative else f"{relative}/{entry.name}"
            _validate_relative_path(child_relative)
            try:
                child_stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome bundle node changed during inspection.",
                    error,
                )
            child_identity = _identity(child_stat)
            if stat.S_ISLNK(child_stat.st_mode):
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome bundle cannot contain symlinks.",
                )
            if stat.S_ISDIR(child_stat.st_mode):
                self._directories[child_relative] = _ScannedNode(
                    child_relative,
                    child_identity,
                )
                if len(self._directories) > self.limits.max_directories:
                    _reject(
                        InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                        "Inferdrome bundle directory count exceeds its limit.",
                    )
                directory_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                try:
                    child_fd = os.open(
                        entry.name,
                        directory_flags,
                        dir_fd=directory_fd,
                    )
                except OSError as error:
                    _reject(
                        InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                        "Inferdrome bundle directory cannot be opened safely.",
                        error,
                    )
                try:
                    if _identity(os.fstat(child_fd)) != child_identity:
                        _reject(
                            InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                            "Inferdrome bundle directory changed before inspection.",
                        )
                    self._scan_directory(child_fd, child_relative, depth + 1)
                    if _identity(os.fstat(child_fd)) != child_identity:
                        _reject(
                            InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                            "Inferdrome bundle directory changed during inspection.",
                        )
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(child_stat.st_mode):
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome bundle contains an unsupported filesystem node.",
                )
            if child_stat.st_nlink != 1:
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome bundle cannot contain hard-linked files.",
                )
            if child_stat.st_size > self.limits.max_file_bytes:
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome bundle file exceeds its byte limit.",
                )
            self._files[child_relative] = _ScannedNode(
                child_relative,
                child_identity,
            )
            self._total_bytes += child_stat.st_size
            if len(self._files) > self.limits.max_files:
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome bundle file count exceeds its limit.",
                )
            if self._total_bytes > self.limits.max_total_bytes:
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome bundle total bytes exceed their limit.",
                )

    def _verify_root_identity(self) -> None:
        try:
            current = _identity(os.lstat(self.root))
        except OSError as error:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome bundle root changed during verification.",
                error,
            )
        if current != self._root_identity:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome bundle root changed during verification.",
            )

    def read_bytes(self, relative_path: str) -> bytes:
        _validate_relative_path(relative_path)
        cached = self._cache.get(relative_path)
        if cached is not None:
            return cached
        node = self._files.get(relative_path)
        if node is None:
            _reject(
                InferdromeBundleErrorCode.INTEGRITY_MISMATCH,
                "A declared Inferdrome artifact is missing.",
            )
        parts = PurePosixPath(relative_path).parts
        root_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptors: list[int] = []
        try:
            current_fd = os.open(self.root, root_flags)
            descriptors.append(current_fd)
            if _identity(os.fstat(current_fd)) != self._root_identity:
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome bundle root changed before artifact capture.",
                )
            prefix: list[str] = []
            for part in parts[:-1]:
                prefix.append(part)
                directory_name = "/".join(prefix)
                expected = self._directories.get(directory_name)
                if expected is None:
                    _reject(
                        InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                        "Inferdrome artifact parent directory is undeclared.",
                    )
                current_fd = os.open(
                    part,
                    root_flags,
                    dir_fd=current_fd,
                )
                descriptors.append(current_fd)
                if _identity(os.fstat(current_fd)) != expected.identity:
                    _reject(
                        InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                        "Inferdrome bundle directory changed during capture.",
                    )
            file_fd = os.open(parts[-1], file_flags, dir_fd=current_fd)
            descriptors.append(file_fd)
            if _identity(os.fstat(file_fd)) != node.identity:
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome artifact changed before capture.",
                )
            content = bytearray()
            remaining = node.identity.size
            while remaining:
                chunk = os.read(file_fd, min(remaining, 1024 * 1024))
                if not chunk:
                    _reject(
                        InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                        "Inferdrome artifact was truncated during capture.",
                    )
                content.extend(chunk)
                remaining -= len(chunk)
            if os.read(file_fd, 1):
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome artifact grew during capture.",
                )
            if _identity(os.fstat(file_fd)) != node.identity:
                _reject(
                    InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                    "Inferdrome artifact changed during capture.",
                )
        except InferdromeBundleRejected:
            raise
        except OSError as error:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome artifact cannot be opened safely.",
                error,
            )
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        try:
            final_identity = _identity(os.lstat(self.root.joinpath(*parts)))
        except OSError as error:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome artifact changed during capture.",
                error,
            )
        if final_identity != node.identity:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome artifact changed during capture.",
            )
        exact = bytes(content)
        self._cache[relative_path] = exact
        return exact

    def assert_unchanged(self) -> None:
        current = _SafeBundleReader(self.root, self.limits)
        if (
            current._root_identity != self._root_identity
            or current._files != self._files
            or current._directories != self._directories
            or current._total_bytes != self._total_bytes
        ):
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome bundle changed during verification.",
            )


def verify_inferdrome_bundle(
    bundle_path: Path,
    *,
    expected_bundle_digest: str | None = None,
    limits: InferdromeBundleLimits | None = None,
    require_customer_eligible: bool = True,
) -> VerifiedInferdromeBundle:
    """Verify a closed bundle without importing Inferdrome or using network I/O."""

    reader = _SafeBundleReader(
        bundle_path,
        limits if limits is not None else InferdromeBundleLimits(),
    )
    if reader.file_size("bundle.json") > _MAX_CONTROL_BYTES:
        _reject(
            InferdromeBundleErrorCode.UNSAFE_BUNDLE,
            "Inferdrome bundle descriptor exceeds its format-specific limit.",
        )
    descriptor_bytes = reader.read_bytes("bundle.json")
    descriptor = _parse_canonical_object(
        descriptor_bytes,
        "Inferdrome bundle descriptor",
    )
    if descriptor.get("schema_version") != "inferdrome.evidence.v1":
        _reject(
            InferdromeBundleErrorCode.UNSUPPORTED_SCHEMA,
            "Inferdrome bundle schema version is unsupported.",
        )
    _validate_schema("bundle_descriptor", descriptor)
    role_paths = _validate_descriptor(descriptor)
    declared_paths = set(role_paths.values())
    if reader.files != declared_paths:
        _reject(
            InferdromeBundleErrorCode.INTEGRITY_MISMATCH,
            "Inferdrome bundle has missing or undeclared files.",
        )
    if reader.directories != _expected_directories(declared_paths):
        _reject(
            InferdromeBundleErrorCode.INTEGRITY_MISMATCH,
            "Inferdrome bundle has missing or undeclared directories.",
        )
    _validate_artifact_size_contracts(reader, role_paths)

    manifest_path = role_paths["integrity_manifest"]
    manifest_bytes = reader.read_bytes(manifest_path)
    manifest = _parse_canonical_object(
        manifest_bytes,
        "Inferdrome integrity manifest",
    )
    bundle_digest = _bundle_digest(manifest_bytes)
    if expected_bundle_digest is not None:
        _require_tagged_sha256(expected_bundle_digest, "expected bundle digest")
        if not hmac.compare_digest(bundle_digest, expected_bundle_digest):
            _reject(
                InferdromeBundleErrorCode.INTEGRITY_MISMATCH,
                "Inferdrome bundle digest does not match the retained digest.",
            )
    _verify_manifest(reader, descriptor, role_paths, manifest)

    if require_customer_eligible and (
        descriptor.get("evidence_eligibility") != "CUSTOMER_ELIGIBLE"
        or descriptor.get("execution_mode") != "attached_endpoint"
        or _mapping(descriptor.get("producer"), "bundle producer").get("name") != "vllm"
    ):
        _reject(
            InferdromeBundleErrorCode.EVIDENCE_INELIGIBLE,
            "Synthetic or ineligible Inferdrome evidence cannot support a customer verdict.",
        )

    exact_by_role = {role: reader.read_bytes(path) for role, path in role_paths.items()}
    documents: dict[str, dict[str, Any]] = {}
    for role in (
        "resolved_spec",
        "request_plan",
        "environment",
        "execution",
        "metric_definitions",
        "measurements",
    ):
        document = _parse_canonical_object(
            exact_by_role[role],
            f"Inferdrome {role.replace('_', ' ')}",
        )
        _validate_schema(role, document)
        documents[role] = document

    invocation = _parse_canonical_object(
        exact_by_role["producer_invocation"],
        "Inferdrome producer invocation",
    )
    native_result = _parse_native_result(
        exact_by_role["native_result"],
        "Inferdrome native result",
    )
    _validate_text_artifacts(exact_by_role)
    records = _parse_request_records(
        exact_by_role["request_records"],
        reader.limits.max_jsonl_line_bytes,
        reader.limits.max_request_records,
    )
    recalculated, managed_profile = _verify_cross_artifact_semantics(
        descriptor=descriptor,
        role_paths=role_paths,
        exact_by_role=exact_by_role,
        resolved=documents["resolved_spec"],
        plan=documents["request_plan"],
        environment=documents["environment"],
        execution=documents["execution"],
        invocation=invocation,
        native_result=native_result,
        definitions=documents["metric_definitions"],
        measurements=documents["measurements"],
        records=records,
    )
    reader.assert_unchanged()
    return VerifiedInferdromeBundle(
        root=reader.root,
        bundle_digest=bundle_digest,
        descriptor=descriptor,
        resolved_spec=documents["resolved_spec"],
        request_plan=documents["request_plan"],
        execution=documents["execution"],
        environment=documents["environment"],
        records=records,
        recalculated=recalculated,
        managed_profile=managed_profile,
    )


def _validate_descriptor(descriptor: Mapping[str, Any]) -> dict[str, str]:
    if descriptor.get("schema_version") != "inferdrome.evidence.v1":
        _reject(
            InferdromeBundleErrorCode.UNSUPPORTED_SCHEMA,
            "Inferdrome bundle schema version is unsupported.",
        )
    artifacts = _sequence(descriptor.get("artifacts"), "artifact inventory")
    role_paths: dict[str, str] = {}
    artifacts_by_role: dict[str, Mapping[str, Any]] = {}
    seen_paths: set[str] = set()
    for raw_artifact in artifacts:
        artifact = _mapping(raw_artifact, "artifact inventory entry")
        role = artifact.get("role")
        path = artifact.get("path")
        if type(role) is not str or role not in _EXPECTED_ROLES:
            _reject(
                InferdromeBundleErrorCode.SCHEMA_INVALID,
                "Inferdrome artifact role is unsupported.",
            )
        if type(path) is not str:
            _reject(
                InferdromeBundleErrorCode.SCHEMA_INVALID,
                "Inferdrome artifact path is invalid.",
            )
        _validate_relative_path(path)
        if role in role_paths or path in seen_paths:
            _reject(
                InferdromeBundleErrorCode.SCHEMA_INVALID,
                "Inferdrome artifact roles and paths must be unique.",
            )
        role_paths[role] = path
        artifacts_by_role[role] = artifact
        seen_paths.add(path)
    if set(role_paths) != set(_EXPECTED_ROLES):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            "Inferdrome bundle does not declare the complete v1 role set.",
        )
    if role_paths["bundle_descriptor"] != "bundle.json":
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            "Inferdrome bundle descriptor path is unsupported.",
        )
    if descriptor.get("integrity_manifest_path") != role_paths["integrity_manifest"]:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            "Inferdrome integrity-manifest linkage is inconsistent.",
        )
    if any(
        artifact.get("media_type") != _MEDIA_TYPE_BY_ROLE[role]
        or artifact.get("required") is not True
        for role, artifact in artifacts_by_role.items()
    ):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            "Inferdrome artifact media type or required status is invalid.",
        )
    sensitivity = _mapping(
        descriptor.get("sensitivity"),
        "bundle sensitivity declaration",
    )
    prompt_content = sensitivity.get("prompt_content_in_request_plan") is True
    canonical_content = sensitivity.get("canonical_response_content_included") is True
    expected_sensitivity = {role: "PUBLIC" for role in _EXPECTED_ROLES}
    for role in (
        "environment",
        "producer_invocation",
        "native_stdout",
        "native_stderr",
    ):
        expected_sensitivity[role] = "INTERNAL_DIAGNOSTIC"
    if prompt_content:
        expected_sensitivity["original_spec"] = "PROMPT_CONTENT"
        expected_sensitivity["request_plan"] = "PROMPT_CONTENT"
    expected_sensitivity["native_result"] = "RESPONSE_CONTENT"
    if canonical_content:
        expected_sensitivity["request_records"] = "RESPONSE_CONTENT"
    if any(
        artifact.get("sensitivity") != expected_sensitivity[role]
        for role, artifact in artifacts_by_role.items()
    ):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            "Inferdrome artifact sensitivity classification is inconsistent.",
        )
    return role_paths


def _verify_manifest(
    reader: _SafeBundleReader,
    descriptor: Mapping[str, Any],
    role_paths: Mapping[str, str],
    manifest: Mapping[str, Any],
) -> None:
    if set(manifest) != {
        "schema_version",
        "run_id",
        "hash_algorithm",
        "path_ordering",
        "entries",
    }:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            "Inferdrome integrity manifest has an unsupported field set.",
        )
    if (
        manifest.get("schema_version") != "inferdrome.integrity-manifest.v1"
        or manifest.get("run_id") != descriptor.get("run_id")
        or manifest.get("hash_algorithm") != "sha256"
        or manifest.get("path_ordering") != "normalized_posix_ascending_v1"
    ):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            "Inferdrome integrity manifest identity is invalid.",
        )
    entries = _sequence(manifest.get("entries"), "integrity entries")
    expected_roles = set(_EXPECTED_ROLES) - {"integrity_manifest"}
    by_role: dict[str, Mapping[str, Any]] = {}
    paths: list[str] = []
    for raw_entry in entries:
        entry = _mapping(raw_entry, "integrity entry")
        if set(entry) != {"path", "role", "size_bytes", "sha256"}:
            _reject(
                InferdromeBundleErrorCode.SCHEMA_INVALID,
                "Inferdrome integrity entry has an unsupported field set.",
            )
        role = entry.get("role")
        path = entry.get("path")
        size = entry.get("size_bytes")
        digest = entry.get("sha256")
        if (
            type(role) is not str
            or role not in expected_roles
            or role in by_role
            or type(path) is not str
            or path != role_paths[role]
            or type(size) is not int
            or size < 0
            or type(digest) is not str
            or _TAGGED_SHA256.fullmatch(digest) is None
        ):
            _reject(
                InferdromeBundleErrorCode.SCHEMA_INVALID,
                "Inferdrome integrity entry is invalid.",
            )
        by_role[role] = entry
        paths.append(path)
    if set(by_role) != expected_roles or paths != sorted(paths):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            "Inferdrome integrity manifest is not complete and ordered.",
        )
    for role, entry in by_role.items():
        content = reader.read_bytes(role_paths[role])
        if entry["size_bytes"] != len(content) or not hmac.compare_digest(
            str(entry["sha256"]), _sha256_tagged(content)
        ):
            _reject(
                InferdromeBundleErrorCode.INTEGRITY_MISMATCH,
                "Inferdrome artifact bytes do not match the integrity manifest.",
            )


def _validate_artifact_size_contracts(
    reader: _SafeBundleReader,
    role_paths: Mapping[str, str],
) -> None:
    limits = {
        "bundle_descriptor": _MAX_CONTROL_BYTES,
        "original_spec": _MAX_CONTROL_BYTES,
        "resolved_spec": _MAX_CONTROL_BYTES,
        "environment": _MAX_CONTROL_BYTES,
        "execution": _MAX_CONTROL_BYTES,
        "producer_invocation": _MAX_INVOCATION_BYTES,
        "producer_version": _MAX_VERSION_BYTES,
        "producer_exit_status": _MAX_VERSION_BYTES,
        "metric_definitions": _MAX_CONTROL_BYTES,
        "measurements": _MAX_CONTROL_BYTES,
        "integrity_manifest": _MAX_CONTROL_BYTES,
    }
    for role, limit in limits.items():
        if reader.file_size(role_paths[role]) > limit:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome producer evidence exceeds its format-specific limit.",
            )


def _parse_request_records(
    content: bytes,
    max_line_bytes: int,
    max_records: int,
) -> tuple[dict[str, Any], ...]:
    if not content or not content.endswith(b"\n"):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            "Inferdrome request records must be non-empty newline-terminated JSONL.",
        )
    records: list[dict[str, Any]] = []
    offset = 0
    while offset < len(content):
        line_end = content.find(b"\n", offset)
        if line_end < 0:
            _reject(
                InferdromeBundleErrorCode.SCHEMA_INVALID,
                "Inferdrome request records are not newline-terminated.",
            )
        line_length = line_end - offset
        if line_length <= 0 or line_length > max_line_bytes:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome request-record line is blank or exceeds its limit.",
            )
        if len(records) >= max_records:
            _reject(
                InferdromeBundleErrorCode.UNSAFE_BUNDLE,
                "Inferdrome request-record count exceeds its limit.",
            )
        raw_line = content[offset:line_end]
        record = _parse_json_object(raw_line, "Inferdrome request record")
        _validate_schema("request_records", record)
        try:
            canonical = canonical_json_bytes(record)
        except (CanonicalizationError, ValueError) as error:
            _reject(
                InferdromeBundleErrorCode.SCHEMA_INVALID,
                "Inferdrome request record cannot be canonicalized.",
                error,
            )
        if raw_line != canonical:
            _reject(
                InferdromeBundleErrorCode.SCHEMA_INVALID,
                "Inferdrome request record is not canonical JSON.",
            )
        records.append(record)
        offset = line_end + 1
    return tuple(records)


def _verify_cross_artifact_semantics(
    *,
    descriptor: Mapping[str, Any],
    role_paths: Mapping[str, str],
    exact_by_role: Mapping[str, bytes],
    resolved: Mapping[str, Any],
    plan: Mapping[str, Any],
    environment: Mapping[str, Any],
    execution: Mapping[str, Any],
    invocation: Mapping[str, Any],
    native_result: Mapping[str, Any],
    definitions: Mapping[str, Any],
    measurements: Mapping[str, Any],
    records: tuple[dict[str, Any], ...],
) -> tuple[
    RecalculatedInferdromeMeasurements,
    ManagedInferdromeProfileFacts | None,
]:
    run_id = descriptor.get("run_id")
    if any(
        value != run_id
        for value in (
            plan.get("run_id"),
            environment.get("run_id"),
            execution.get("run_id"),
            measurements.get("run_id"),
            *(record.get("run_id") for record in records),
        )
    ):
        _inconsistent("Inferdrome artifacts disagree on run identity.")
    experiment = _mapping(resolved.get("experiment"), "resolved experiment")
    if descriptor.get("experiment_id") != experiment.get("id") or plan.get(
        "experiment_id"
    ) != experiment.get("id"):
        _inconsistent("Inferdrome artifacts disagree on experiment identity.")

    traffic = _mapping(resolved.get("traffic"), "resolved traffic")
    if plan.get("traffic") != traffic or execution.get("configured_traffic") != traffic:
        _inconsistent("Inferdrome resolved, planned, and executed traffic disagree.")
    _validate_execution(execution, run_id, traffic)

    digest_claims = _mapping(descriptor.get("digests"), "bundle digests")
    source_digest = _domain_digest("source-spec-v1", exact_by_role["original_spec"])
    if (
        digest_claims.get("source_spec_digest") != source_digest
        or plan.get("source_spec_digest") != source_digest
    ):
        _inconsistent("Inferdrome source-spec digest is inconsistent.")
    expected_fingerprint = _execution_fingerprint(resolved)
    if digest_claims.get("execution_fingerprint") != expected_fingerprint:
        _inconsistent("Inferdrome execution fingerprint is inconsistent.")
    if digest_claims.get("request_plan_digest") != _domain_digest(
        "request-plan-v1", exact_by_role["request_plan"]
    ):
        _inconsistent("Inferdrome request-plan digest is inconsistent.")
    definitions_digest = _domain_digest(
        "metric-definitions-v1", exact_by_role["metric_definitions"]
    )
    if (
        digest_claims.get("metric_definitions_digest") != definitions_digest
        or measurements.get("metric_definitions_digest") != definitions_digest
    ):
        _inconsistent("Inferdrome metric-definition digest is inconsistent.")
    links = _mapping(resolved.get("links"), "resolved links")
    if digest_claims.get("exitspec_contract_digest") != links.get(
        "exitspec_contract_digest"
    ):
        _inconsistent("Inferdrome ExitSpec contract linkage is inconsistent.")

    if measurements.get("request_records_sha256") != _sha256_tagged(
        exact_by_role["request_records"]
    ):
        _inconsistent("Inferdrome request-record hash is inconsistent.")
    if measurements.get("execution_sha256") != _sha256_tagged(
        exact_by_role["execution"]
    ):
        _inconsistent("Inferdrome execution hash is inconsistent.")
    if execution.get("native_result_sha256") != _sha256_tagged(
        exact_by_role["native_result"]
    ):
        _inconsistent("Inferdrome native-result hash is inconsistent.")

    producer = _mapping(descriptor.get("producer"), "bundle producer")
    execution_settings = _mapping(resolved.get("execution"), "execution settings")
    target = _mapping(resolved.get("target"), "resolved target")
    if (
        producer.get("name") != "vllm"
        or producer.get("version") != "0.26.0"
        or producer.get("adapter") != "vllm_bench_serve"
        or producer.get("adapter_version") != _VLLM_ADAPTER_VERSION
        or producer.get("native_schema_fingerprint") != _VLLM_NATIVE_SCHEMA_FINGERPRINT
        or execution_settings.get("mode") != "attached_endpoint"
        or execution_settings.get("producer_name") != producer.get("name")
        or execution_settings.get("producer_version") != producer.get("version")
        or execution_settings.get("adapter") != producer.get("adapter")
        or execution_settings.get("adapter_version") != producer.get("adapter_version")
        or target.get("engine") != "vllm"
        or traffic.get("measured_requests")
        > execution_settings.get("max_measured_requests", 0)
    ):
        _inconsistent("Inferdrome producer and resolved target disagree.")

    _validate_plan_and_records(plan, resolved, execution, producer, role_paths, records)
    _validate_bundle_semantics(descriptor, resolved, plan, environment, records)
    _validate_environment(environment, descriptor, target, role_paths)
    managed_profile = _validate_invocation(
        invocation,
        descriptor,
        resolved,
        plan,
        environment,
        execution,
    )
    if managed_profile is not None:
        _validate_native_record_bindings(
            native_result,
            descriptor,
            resolved,
            invocation,
            execution,
            records,
        )
    _validate_metric_definitions(definitions)
    return (
        _recalculate_and_compare(
            records,
            execution,
            definitions_digest,
            measurements,
            exact_by_role["request_records"],
        ),
        managed_profile,
    )


def _validate_execution(
    execution: Mapping[str, Any],
    run_id: Any,
    traffic: Mapping[str, Any],
) -> None:
    started = _parse_timestamp(execution.get("started_at"), "execution start")
    ended = _parse_timestamp(execution.get("ended_at"), "execution end")
    if ended < started or execution.get("terminal_state") != "COMPLETE":
        _inconsistent("Inferdrome execution lifecycle is invalid.")
    if execution.get("producer_exit_status") != 0:
        _inconsistent("Complete Inferdrome execution has a non-zero exit status.")
    if (
        execution.get("measurement_window_definition")
        != "vllm_benchmark_duration_v0_26"
        or execution.get("monotonic_clock_domain_id") != f"vllm-bench-serve-{run_id}"
    ):
        _inconsistent("Inferdrome execution measurement identity is invalid.")
    phases = _sequence(execution.get("phases"), "execution phases")
    expected_names = ("PREFLIGHT", "WARMUP", "MEASURING", "FINALIZING")
    if tuple(_mapping(item, "execution phase").get("phase") for item in phases) != (
        expected_names
    ):
        _inconsistent("Inferdrome execution phases are incomplete or unordered.")
    expected_counts = (
        1,
        traffic.get("warmup_requests"),
        traffic.get("measured_requests"),
        None,
    )
    if (
        tuple(
            _mapping(item, "execution phase").get("configured_request_count")
            for item in phases
        )
        != expected_counts
    ):
        _inconsistent("Inferdrome execution phase populations are inconsistent.")
    prior_end: datetime | None = None
    for raw_phase in phases:
        phase = _mapping(raw_phase, "execution phase")
        status_value = phase.get("timing_status")
        raw_start = phase.get("started_at")
        raw_end = phase.get("ended_at")
        if status_value == "UNAVAILABLE":
            if raw_start is not None or raw_end is not None:
                _inconsistent("Unavailable Inferdrome phase carries timestamps.")
            continue
        if status_value != "OBSERVED":
            _inconsistent("Inferdrome phase timing status is invalid.")
        phase_start = _parse_timestamp(raw_start, "phase start")
        phase_end = _parse_timestamp(raw_end, "phase end")
        if (
            phase_end < phase_start
            or phase_start < started
            or phase_end > ended
            or (prior_end is not None and phase_start < prior_end)
        ):
            _inconsistent("Inferdrome phase timestamps are inverted or overlapping.")
        prior_end = phase_end


def _validate_plan_and_records(
    plan: Mapping[str, Any],
    resolved: Mapping[str, Any],
    execution: Mapping[str, Any],
    producer: Mapping[str, Any],
    role_paths: Mapping[str, str],
    records: tuple[dict[str, Any], ...],
) -> None:
    planned = _sequence(plan.get("requests"), "planned requests")
    traffic = _mapping(plan.get("traffic"), "planned traffic")
    expected_count = traffic.get("measured_requests")
    if type(expected_count) is not int or len(planned) != expected_count:
        _inconsistent("Inferdrome request-plan population is inconsistent.")
    if len(records) != expected_count or not records:
        _inconsistent("Inferdrome canonical record population is inconsistent.")
    prefix = plan.get("producer_request_id_prefix")
    workload = _mapping(resolved.get("workload"), "resolved workload")
    first_record_producer: Mapping[str, Any] | None = None
    seen_request_ids: set[str] = set()
    seen_producer_ids: set[str] = set()
    for index, (raw_planned, record) in enumerate(zip(planned, records, strict=True)):
        planned_request = _mapping(raw_planned, "planned request")
        expected_request_id = f"req-{index:08d}"
        expected_producer_id = f"{prefix}{index}"
        prompt = _mapping(planned_request.get("prompt"), "planned prompt")
        sampling = _mapping(planned_request.get("sampling"), "planned sampling")
        if (
            planned_request.get("sequence_index") != index
            or planned_request.get("request_id") != expected_request_id
            or planned_request.get("producer_request_id") != expected_producer_id
            or sampling.get("requested_output_tokens")
            != workload.get("requested_output_tokens")
            or sampling.get("temperature") != workload.get("temperature")
            or sampling.get("seed") != workload.get("seed")
        ):
            _inconsistent("Inferdrome request plan is not contiguous and frozen.")
        if prompt.get("kind") == "inline":
            text = prompt.get("text")
            if type(text) is not str or prompt.get("sha256") != _sha256_tagged(
                text.encode("utf-8")
            ):
                _inconsistent("Inferdrome inline prompt digest is inconsistent.")

        request_id = record.get("request_id")
        producer_request_id = record.get("producer_request_id")
        if (
            record.get("sequence_index") != index
            or request_id != expected_request_id
            or producer_request_id != expected_producer_id
            or request_id in seen_request_ids
            or producer_request_id in seen_producer_ids
        ):
            _inconsistent("Inferdrome request record identities are invalid.")
        seen_request_ids.add(str(request_id))
        seen_producer_ids.add(str(producer_request_id))
        native_source = _mapping(record.get("native_source"), "native source")
        content = _mapping(record.get("content"), "record content")
        if (
            native_source.get("array_index") != index
            or native_source.get("artifact_path") != role_paths["native_result"]
            or content.get("prompt_sha256") != prompt.get("sha256")
        ):
            _inconsistent("Inferdrome record source or prompt binding is invalid.")
        record_producer = _mapping(record.get("producer"), "record producer")
        if first_record_producer is None:
            first_record_producer = record_producer
        if (
            record_producer != first_record_producer
            or record_producer.get("producer_name") != producer.get("name")
            or record_producer.get("producer_version") != producer.get("version")
            or record_producer.get("adapter_name") != producer.get("adapter")
            or record_producer.get("adapter_version") != producer.get("adapter_version")
            or record_producer.get("native_schema_fingerprint")
            != producer.get("native_schema_fingerprint")
        ):
            _inconsistent("Inferdrome request-record producer identity is invalid.")
        _validate_record_observations(record)
    if execution.get("configured_traffic") != traffic:
        _inconsistent("Inferdrome execution population differs from its plan.")


def _validate_record_observations(record: Mapping[str, Any]) -> None:
    outcome = _mapping(record.get("outcome"), "record outcome")
    timing = _mapping(record.get("timing"), "record timing")
    tokens = _mapping(record.get("tokens"), "record tokens")
    content = _mapping(record.get("content"), "record content")
    status_value = outcome.get("status")
    producer_error = outcome.get("producer_error")
    ttft = timing.get("ttft_ns")
    itls = _sequence(timing.get("itl_ns"), "record inter-token intervals")
    if timing.get("ttft_definition") != "vllm_first_choices_event_v0_26":
        _inconsistent("Inferdrome request TTFT definition is unsupported.")
    if ttft is None and itls:
        _inconsistent("Inferdrome request has ITLs without an observed first event.")
    if status_value == "SUCCESS":
        if (
            producer_error is not None
            or ttft is None
            or content.get("response_sha256") is None
        ):
            _inconsistent("Inferdrome successful request evidence is incomplete.")
    elif status_value == "FAILED":
        if type(producer_error) is not str or not producer_error:
            _inconsistent("Inferdrome failed request has no producer error.")
    elif status_value == "ANOMALOUS_EMPTY_STREAM":
        if (
            producer_error is not None
            or ttft is not None
            or itls
            or tokens.get("output_tokens") != 0
            or content.get("response_sha256") != _sha256_tagged(b"")
        ):
            _inconsistent("Inferdrome empty-stream anomaly is inconsistent.")
    else:
        _inconsistent("Inferdrome request outcome is unsupported.")
    canonical_content = content.get("canonical_response_content")
    if canonical_content is not None and (
        type(canonical_content) is not str
        or content.get("response_sha256")
        != _sha256_tagged(canonical_content.encode("utf-8"))
    ):
        _inconsistent("Inferdrome canonical response digest is inconsistent.")


def _validate_bundle_semantics(
    descriptor: Mapping[str, Any],
    resolved: Mapping[str, Any],
    plan: Mapping[str, Any],
    environment: Mapping[str, Any],
    records: tuple[dict[str, Any], ...],
) -> None:
    if descriptor.get("environment_completeness") != environment.get("completeness"):
        _inconsistent("Inferdrome environment completeness summary disagrees.")
    planned = _sequence(plan.get("requests"), "planned requests")
    prompt_kinds = tuple(
        _mapping(
            _mapping(item, "planned request").get("prompt"),
            "planned prompt",
        ).get("kind")
        for item in planned
    )
    expected_replayability = "LIMITED" if "digest_only" in prompt_kinds else "FULL"
    if (
        plan.get("replayability") != expected_replayability
        or descriptor.get("replayability") != expected_replayability
    ):
        _inconsistent("Inferdrome replayability disagrees with prompt material.")
    workload = _mapping(resolved.get("workload"), "resolved workload")
    prompt_policy_includes = workload.get("prompt_content_policy") == "include"
    prompt_content_present = any(kind == "inline" for kind in prompt_kinds)
    sensitivity = _mapping(
        descriptor.get("sensitivity"),
        "bundle sensitivity declaration",
    )
    if (
        prompt_content_present != prompt_policy_includes
        or sensitivity.get("prompt_content_in_request_plan")
        is not prompt_policy_includes
    ):
        _inconsistent("Inferdrome prompt-content policy is inconsistent.")
    evidence = _mapping(resolved.get("evidence"), "resolved evidence policy")
    canonical_policy_includes = evidence.get("canonical_response_content") == "include"
    record_content_flags = tuple(
        _mapping(record.get("content"), "record content").get(
            "canonical_response_content"
        )
        is not None
        for record in records
    )
    record_content_matches_policy = (
        all(record_content_flags)
        if canonical_policy_includes
        else not any(record_content_flags)
    )
    if (
        evidence.get("include_request_plan") is not True
        or evidence.get("native_output_sensitivity") != "RESPONSE_CONTENT"
        or sensitivity.get("canonical_response_content_included")
        is not canonical_policy_includes
        or not record_content_matches_policy
        or sensitivity.get("native_response_content_present") is not True
        or sensitivity.get("secrets_permitted") is not False
    ):
        _inconsistent("Inferdrome evidence sensitivity policy is inconsistent.")


def _validate_environment(
    environment: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    target: Mapping[str, Any],
    role_paths: Mapping[str, str],
) -> None:
    fields = _sequence(environment.get("fields"), "environment fields")
    by_name: dict[str, Mapping[str, Any]] = {}
    unknown_count = 0
    for raw_field in fields:
        field = _mapping(raw_field, "environment field")
        name = field.get("name")
        if type(name) is not str or name in by_name:
            _inconsistent("Inferdrome environment fields are not unique.")
        by_name[name] = field
        provenance = field.get("provenance")
        value = field.get("value")
        evidence_path = field.get("evidence_path")
        if provenance == "UNKNOWN":
            unknown_count += 1
            if value is not None or evidence_path is not None:
                _inconsistent("Unknown Inferdrome environment evidence claims a value.")
        elif value is None:
            _inconsistent("Known Inferdrome environment evidence has no value.")
        if evidence_path is not None and evidence_path not in set(role_paths.values()):
            _inconsistent("Inferdrome environment evidence path is undeclared.")
    if set(by_name) != _ENVIRONMENT_FIELD_NAMES:
        _inconsistent("Inferdrome environment field set is incomplete.")
    expected_completeness = (
        "COMPLETE"
        if unknown_count == 0
        else "UNKNOWN"
        if unknown_count == len(fields)
        else "PARTIAL"
    )
    if environment.get("completeness") != expected_completeness:
        _inconsistent("Inferdrome environment completeness is inconsistent.")
    producer = _mapping(descriptor.get("producer"), "bundle producer")
    producer_version = _mapping(
        by_name.get("producer.version"),
        "producer version field",
    )
    if (
        producer_version.get("value") != producer.get("version")
        or producer_version.get("evidence_path") != role_paths["producer_version"]
    ):
        _inconsistent("Inferdrome environment producer version disagrees.")
    server_model = _mapping(by_name.get("server.model_id"), "server model field")
    if (
        server_model.get("value") != target.get("model")
        or server_model.get("provenance") != "SERVER_REPORTED"
        or server_model.get("evidence_path") != role_paths["producer_invocation"]
    ):
        _inconsistent("Inferdrome environment server model evidence disagrees.")
    configured_target_values = {
        "target.engine_version": target.get("engine_version"),
        "target.model_revision": target.get("model_revision"),
        "target.tokenizer_revision": target.get("tokenizer_revision"),
    }
    if any(
        expected is not None
        and by_name[name].get("value") is not None
        and by_name[name].get("value") != expected
        for name, expected in configured_target_values.items()
    ):
        _inconsistent("Inferdrome environment target identity disagrees.")


def _validate_invocation(
    invocation: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    resolved: Mapping[str, Any],
    plan: Mapping[str, Any],
    environment: Mapping[str, Any],
    execution: Mapping[str, Any],
) -> ManagedInferdromeProfileFacts | None:
    managed_profile: ManagedInferdromeProfileFacts | None = None
    managed_invocation = "local_gpu_proof" in invocation
    if managed_invocation:
        try:
            managed_profile = validate_managed_invocation_profile(
                invocation,
                descriptor=descriptor,
                resolved=resolved,
                environment=environment,
                execution=execution,
            )
        except ManagedInferdromeProfileError as error:
            _reject(
                InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY,
                "Inferdrome managed producer evidence violates its pinned profile.",
                error,
            )
    expected_fields = {
        "argv",
        "endpoint_preflight",
        "metadata",
        "schema_version",
    }
    if managed_invocation:
        expected_fields.add("local_gpu_proof")
    if (
        set(invocation) != expected_fields
        or invocation.get("schema_version") != "inferdrome.producer-invocation.v1"
    ):
        _inconsistent("Inferdrome producer invocation field set is unsupported.")
    argv_raw = _sequence(invocation.get("argv"), "producer arguments")
    if (
        not argv_raw
        or len(argv_raw) > 2_048
        or any(
            type(item) is not str or not item or len(item) > 8_192 for item in argv_raw
        )
    ):
        _inconsistent("Inferdrome producer argument vector is invalid.")
    argv = tuple(str(item) for item in argv_raw)
    target = _mapping(resolved.get("target"), "resolved target")
    workload = _mapping(resolved.get("workload"), "resolved workload")
    traffic = _mapping(resolved.get("traffic"), "resolved traffic")
    digest_claims = _mapping(descriptor.get("digests"), "bundle digests")
    producer = _mapping(descriptor.get("producer"), "bundle producer")
    expected_metadata = {
        "inferdrome_adapter_version": producer.get("adapter_version"),
        "inferdrome_execution_fingerprint": digest_claims.get("execution_fingerprint"),
        "inferdrome_producer_version": producer.get("version"),
        "inferdrome_run_id": descriptor.get("run_id"),
        "inferdrome_workload_sha256": workload.get("sha256"),
    }
    if invocation.get("metadata") != expected_metadata:
        _inconsistent("Inferdrome invocation metadata is inconsistent.")
    tokenizer_path = _absolute_argv_path(argv, "--tokenizer")
    dataset_path = _absolute_argv_path(argv, "--dataset-path")
    result_directory = _absolute_argv_path(argv, "--result-dir")
    expected_argv = [
        managed_profile.executable_path if managed_profile is not None else "vllm",
        "bench",
        "serve",
        "--backend",
        "openai-chat",
        "--base-url",
        str(target.get("endpoint")).rstrip("/"),
        "--endpoint",
        "/v1/chat/completions",
        "--model",
        str(target.get("model")),
        "--tokenizer",
        tokenizer_path,
        "--dataset-name",
        "custom",
        "--dataset-path",
        dataset_path,
        "--custom-output-len",
        str(workload.get("requested_output_tokens")),
        "--num-prompts",
        str(traffic.get("measured_requests")),
        "--disable-shuffle",
        "--skip-chat-template",
    ]
    if traffic.get("kind") == "concurrent":
        expected_argv.extend(
            [
                "--request-rate",
                "inf",
                "--burstiness",
                "1",
                "--max-concurrency",
                str(traffic.get("concurrency")),
            ]
        )
    elif traffic.get("kind") == "request_rate":
        request_rate = traffic.get("requests_per_second")
        burstiness = traffic.get("burstiness")
        try:
            positive_rate = Decimal(str(request_rate)) > 0
            positive_burstiness = Decimal(str(burstiness)) > 0
        except InvalidOperation:
            positive_rate = False
            positive_burstiness = False
        if not positive_rate or not positive_burstiness:
            _inconsistent("Inferdrome request-rate traffic is invalid.")
        expected_argv.extend(
            [
                "--request-rate",
                str(request_rate),
                "--burstiness",
                str(burstiness),
            ]
        )
        max_concurrency = traffic.get("max_concurrency")
        if max_concurrency is not None:
            expected_argv.extend(["--max-concurrency", str(max_concurrency)])
    else:
        _inconsistent("Inferdrome traffic kind is unsupported.")
    expected_argv.extend(
        [
            "--num-warmups",
            str(traffic.get("warmup_requests")),
            "--ready-check-timeout-sec",
            "5",
            "--temperature",
            str(workload.get("temperature")),
            "--seed",
            str(workload.get("seed")),
            "--request-id-prefix",
            str(plan.get("producer_request_id_prefix")),
            "--percentile-metrics",
            "e2el",
            "--metric-percentiles",
            "50,95,99",
            "--save-result",
            "--save-detailed",
            "--result-dir",
            result_directory,
            "--result-filename",
            "benchmark-result.json",
            "--metadata",
            *(f"{key}={value}" for key, value in expected_metadata.items()),
        ]
    )
    if argv != tuple(expected_argv):
        _inconsistent("Inferdrome producer arguments differ from frozen inputs.")
    preflight = _mapping(invocation.get("endpoint_preflight"), "endpoint preflight")
    if set(preflight) != {"response_base64", "result"}:
        _inconsistent("Inferdrome endpoint preflight field set is invalid.")
    encoded = preflight.get("response_base64")
    if (
        type(encoded) is not str
        or len(encoded) > ((_MAX_PREFLIGHT_RESPONSE_BYTES + 2) // 3) * 4
    ):
        _inconsistent("Inferdrome endpoint preflight response is invalid.")
    try:
        response = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        _reject(
            InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY,
            "Inferdrome endpoint preflight response is invalid.",
            error,
        )
    if len(response) > _MAX_PREFLIGHT_RESPONSE_BYTES:
        _inconsistent("Inferdrome endpoint preflight response exceeds its limit.")
    response_value = _parse_json_object(response, "endpoint preflight response")
    data = response_value.get("data")
    if not isinstance(data, list) or not data:
        _inconsistent("Inferdrome endpoint preflight has no model entries.")
    model_ids: list[str] = []
    for raw_item in data:
        item = _mapping(raw_item, "endpoint model entry")
        model_id = item.get("id")
        if type(model_id) is not str:
            _inconsistent("Inferdrome endpoint model entry is invalid.")
        model_ids.append(model_id)
    result = _mapping(preflight.get("result"), "endpoint preflight result")
    if (
        result.get("schema_version") != "inferdrome.endpoint-preflight.v1"
        or result.get("api") != "openai_chat_completions"
        or result.get("status") != 200
        or result.get("target_model") != target.get("model")
        or result.get("server_reported_models") != model_ids
        or len(model_ids) != len(set(model_ids))
        or target.get("model") not in model_ids
        or result.get("response_sha256") != _sha256_tagged(response)
    ):
        _inconsistent("Inferdrome endpoint preflight result is inconsistent.")
    return managed_profile


def _validate_native_record_bindings(
    native: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    resolved: Mapping[str, Any],
    invocation: Mapping[str, Any],
    execution: Mapping[str, Any],
    records: tuple[dict[str, Any], ...],
) -> None:
    """Bind managed canonical records back to untouched native vLLM arrays."""

    if set(native) != _VLLM_NATIVE_FIELDS:
        _inconsistent("Inferdrome native vLLM result field set is unsupported.")
    _native_string(native.get("date"), "native result date", 256)
    label = native.get("label")
    if label is not None:
        _native_string(label, "native result label", 4_096)
    for field in (
        "max_output_tokens_per_s",
        "mean_e2el_ms",
        "median_e2el_ms",
        "output_throughput",
        "p50_e2el_ms",
        "p95_e2el_ms",
        "p99_e2el_ms",
        "request_throughput",
        "rtfx",
        "std_e2el_ms",
        "total_token_throughput",
    ):
        _native_nonnegative_decimal(native.get(field), f"native {field}")
    request_goodput = native.get("request_goodput")
    if request_goodput is not None:
        _native_nonnegative_decimal(request_goodput, "native request goodput")
    for field in (
        "completed",
        "failed",
        "num_prompts",
        "total_input_tokens",
        "total_output_tokens",
    ):
        _native_nonnegative_integer(native.get(field), f"native {field}")
    _native_nonnegative_integer(
        native.get("max_concurrent_requests"),
        "native maximum concurrent requests",
    )
    expected_count = len(records)
    if not 0 < expected_count <= _MAX_NATIVE_ARRAY_ITEMS:
        _inconsistent("Inferdrome native vLLM population is invalid.")
    array_names = (
        "errors",
        "generated_texts",
        "input_lens",
        "itls",
        "output_lens",
        "start_times",
        "ttfts",
    )
    arrays = {
        name: _native_array(native.get(name), f"native {name}")
        for name in array_names
    }
    if any(len(value) != expected_count for value in arrays.values()):
        _inconsistent("Inferdrome native vLLM arrays have inconsistent populations.")

    errors = tuple(
        _native_string(item, "native producer error", _MAX_NATIVE_ERROR_CHARACTERS)
        for item in arrays["errors"]
    )
    generated_texts = tuple(
        _native_string(
            item,
            "native generated response",
            _MAX_NATIVE_TEXT_CHARACTERS,
        )
        for item in arrays["generated_texts"]
    )
    input_lens = tuple(
        _native_nonnegative_integer(item, "native input length")
        for item in arrays["input_lens"]
    )
    output_lens = tuple(
        _native_nonnegative_integer(item, "native output length")
        for item in arrays["output_lens"]
    )
    ttfts = tuple(
        _native_nonnegative_decimal(item, "native TTFT")
        for item in arrays["ttfts"]
    )
    start_times = tuple(
        _native_nonnegative_decimal(item, "native start time")
        for item in arrays["start_times"]
    )
    itls: tuple[tuple[Decimal, ...], ...] = tuple(
        tuple(
            _native_nonnegative_decimal(item, "native inter-token interval")
            for item in _native_array(raw, "native inter-token intervals")
        )
        for raw in arrays["itls"]
    )
    if any(len(values) > _MAX_NATIVE_ITL_ITEMS for values in itls):
        _inconsistent("Inferdrome native inter-token population exceeds its limit.")

    target = _mapping(resolved.get("target"), "resolved target")
    workload = _mapping(resolved.get("workload"), "resolved workload")
    traffic = _mapping(resolved.get("traffic"), "resolved traffic")
    digests = _mapping(descriptor.get("digests"), "bundle digests")
    metadata = _mapping(invocation.get("metadata"), "producer metadata")
    argv = tuple(
        str(item) for item in _sequence(invocation.get("argv"), "producer arguments")
    )
    if (
        native.get("backend") != "openai-chat"
        or native.get("endpoint_type") != "openai-chat"
        or native.get("model_id") != target.get("model")
        or native.get("tokenizer_id") != _absolute_argv_path(argv, "--tokenizer")
        or native.get("num_prompts") != expected_count
        or native.get("inferdrome_adapter_version") != _VLLM_ADAPTER_VERSION
        or native.get("inferdrome_execution_fingerprint")
        != digests.get("execution_fingerprint")
        or native.get("inferdrome_producer_version") != "0.26.0"
        or native.get("inferdrome_run_id") != descriptor.get("run_id")
        or native.get("inferdrome_workload_sha256") != workload.get("sha256")
        or any(native.get(key) != metadata.get(key) for key in metadata)
    ):
        _inconsistent("Inferdrome native vLLM identity disagrees with frozen inputs.")

    if traffic.get("kind") == "concurrent":
        expected_request_rate: str | Decimal = "inf"
        expected_burstiness = Decimal(1)
        expected_concurrency = traffic.get("concurrency")
    else:
        expected_request_rate = Decimal(str(traffic.get("requests_per_second")))
        expected_burstiness = Decimal(str(traffic.get("burstiness")))
        expected_concurrency = traffic.get("max_concurrency")
    if expected_concurrency is not None:
        _native_nonnegative_integer(native.get("max_concurrency"), "native concurrency")
    if (
        native.get("request_rate") != expected_request_rate
        or _native_decimal(native.get("burstiness"), "native burstiness")
        != expected_burstiness
        or native.get("max_concurrency") != expected_concurrency
    ):
        _inconsistent("Inferdrome native vLLM traffic disagrees with its plan.")

    native_failed = sum(1 for error in errors if error)
    native_completed = expected_count - native_failed
    if (
        native.get("completed") != native_completed
        or native.get("failed") != native_failed
        or native.get("total_input_tokens") != sum(input_lens)
        or native.get("total_output_tokens") != sum(output_lens)
        or _seconds_to_ns(
            _native_nonnegative_decimal(native.get("duration"), "native duration")
        )
        != execution.get("measurement_window_ns")
    ):
        _inconsistent("Inferdrome native vLLM population summary is inconsistent.")

    first_start = min(start_times)
    for index, record in enumerate(records):
        outcome = _mapping(record.get("outcome"), "record outcome")
        timing = _mapping(record.get("timing"), "record timing")
        tokens = _mapping(record.get("tokens"), "record tokens")
        content = _mapping(record.get("content"), "record content")
        error = errors[index]
        generated = generated_texts[index]
        if error:
            expected_status = "FAILED"
        elif output_lens[index] == 0 and not generated:
            expected_status = "ANOMALOUS_EMPTY_STREAM"
        else:
            expected_status = "SUCCESS"
        expected_ttft = (
            _seconds_to_ns(ttfts[index])
            if expected_status == "SUCCESS"
            else None
        )
        expected_itls = (
            [_seconds_to_ns(item) for item in itls[index]]
            if expected_status == "SUCCESS"
            else []
        )
        if (
            outcome.get("status") != expected_status
            or outcome.get("producer_error") != (error or None)
            or timing.get("start_offset_ns")
            != _seconds_to_ns(start_times[index] - first_start)
            or timing.get("ttft_ns") != expected_ttft
            or timing.get("itl_ns") != expected_itls
            or tokens.get("input_tokens") != input_lens[index]
            or tokens.get("output_tokens") != output_lens[index]
            or content.get("response_sha256")
            != _sha256_tagged(generated.encode("utf-8"))
            or _mapping(record.get("native_source"), "native source").get(
                "array_index"
            )
            != index
        ):
            _inconsistent(
                "Inferdrome canonical record disagrees with native vLLM evidence."
            )


def _validate_metric_definitions(definitions: Mapping[str, Any]) -> None:
    if definitions != _expected_metric_definitions():
        _inconsistent("Inferdrome metric definitions differ from the frozen v1 set.")


def _recalculate_and_compare(
    records: tuple[dict[str, Any], ...],
    execution: Mapping[str, Any],
    definitions_digest: str,
    measurements: Mapping[str, Any],
    records_bytes: bytes,
) -> RecalculatedInferdromeMeasurements:
    successful = tuple(
        record
        for record in records
        if _mapping(record.get("outcome"), "record outcome").get("status") == "SUCCESS"
    )
    failed = tuple(
        record
        for record in records
        if _mapping(record.get("outcome"), "record outcome").get("status") != "SUCCESS"
    )
    anomalous = tuple(
        record
        for record in records
        if _mapping(record.get("outcome"), "record outcome").get("status")
        == "ANOMALOUS_EMPTY_STREAM"
    )
    ttfts = tuple(
        int(_mapping(record.get("timing"), "record timing")["ttft_ns"])
        for record in successful
    )
    spans = tuple(
        int(_mapping(record.get("timing"), "record timing")["ttft_ns"])
        + sum(
            int(value)
            for value in _sequence(
                _mapping(record.get("timing"), "record timing").get("itl_ns"),
                "record inter-token intervals",
            )
        )
        for record in successful
    )
    attempted_count = len(records)
    successful_count = len(successful)
    failed_count = len(failed)
    expected_measurements: list[dict[str, Any]] = [
        _measurement(
            "measured_request_count",
            "count",
            attempted_count,
            attempted_count,
            "count",
            "all_measured_requests",
            "measured_request_count_v1",
            None,
            "none",
        ),
        _measurement(
            "successful_request_count",
            "count",
            successful_count,
            successful_count,
            "count",
            "successful_measured_requests",
            "successful_request_count_v1",
            None,
            "none",
        ),
        _measurement(
            "failed_request_count",
            "count",
            failed_count,
            failed_count,
            "count",
            "failed_measured_requests",
            "failed_request_count_v1",
            None,
            "none",
        ),
        _measurement(
            "error_rate",
            "ratio",
            _decimal_ratio(failed_count, attempted_count),
            attempted_count,
            "ratio",
            "all_measured_requests",
            "measured_failure_ratio_v1",
            None,
            "decimal_half_even_6_v1",
        ),
    ]
    expected_measurements.extend(
        _latency_measurements(
            "ttft_ns",
            "vllm_first_choices_event_v0_26",
            ttfts,
        )
    )
    expected_measurements.extend(
        _latency_measurements(
            "last_choices_event_span_ns",
            "last_choices_event_span_v1",
            spans,
        )
    )
    window_ns = execution.get("measurement_window_ns")
    if type(window_ns) is not int or window_ns <= 0:
        _inconsistent("Inferdrome measurement window is invalid.")
    output_tokens = sum(
        int(_mapping(record.get("tokens"), "record tokens")["output_tokens"])
        for record in successful
    )
    expected_measurements.extend(
        (
            _measurement(
                "attempted_request_throughput_per_s",
                "rate",
                _decimal_ratio(attempted_count * 1_000_000_000, window_ns),
                attempted_count,
                "requests/s",
                "all_measured_requests",
                "attempted_measured_requests_per_window_second_v1",
                None,
                "decimal_half_even_6_v1",
            ),
            _measurement(
                "successful_request_throughput_per_s",
                "rate",
                _decimal_ratio(successful_count * 1_000_000_000, window_ns),
                successful_count,
                "requests/s",
                "successful_measured_requests",
                "successful_measured_requests_per_window_second_v1",
                None,
                "decimal_half_even_6_v1",
            ),
            _measurement(
                "output_token_throughput_per_s",
                "rate",
                _decimal_ratio(output_tokens * 1_000_000_000, window_ns),
                successful_count,
                "tokens/s",
                "successful_measured_requests",
                "successful_output_tokens_per_window_second_v1",
                None,
                "decimal_half_even_6_v1",
            ),
        )
    )
    expected_unavailable = [
        {
            "capability_matrix": "vllm-0.26.0",
            "metric": metric,
            "reason": "SOURCE_OBSERVATION_UNAVAILABLE",
        }
        for metric in _UNAVAILABLE_METRICS
    ]
    if (
        measurements.get("reducer_version") != "1.0.0"
        or measurements.get("measurements") != expected_measurements
        or measurements.get("unavailable") != expected_unavailable
        or measurements.get("metric_definitions_digest") != definitions_digest
    ):
        _reject(
            InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY,
            "Inferdrome summary measurements disagree with independent recalculation.",
        )
    error_rate = Decimal(failed_count) / Decimal(attempted_count)
    p95_ttft = _nearest_rank(ttfts, 95) if ttfts else None
    records_sha256 = hashlib.sha256(records_bytes).hexdigest()
    recalculation_payload = {
        "anomalous_count": len(anomalous),
        "attempted_count": attempted_count,
        "calculation_version": "exitspec.inferdrome-recalculation.v1",
        "error_rate": format(error_rate, "f"),
        "failed_count": failed_count,
        "p95_ttft_ns": p95_ttft,
        "records_sha256": records_sha256,
        "successful_count": successful_count,
        "ttft_definition": "vllm_first_choices_event_v0_26",
    }
    recalculation_sha256 = hashlib.sha256(
        b"exitspec:inferdrome-recalculation-v1\x00"
        + canonical_json_bytes(recalculation_payload)
    ).hexdigest()
    return RecalculatedInferdromeMeasurements(
        attempted_count=attempted_count,
        successful_count=successful_count,
        failed_count=failed_count,
        anomalous_count=len(anomalous),
        error_rate=error_rate,
        p95_ttft_ns=p95_ttft,
        ttft_definition="vllm_first_choices_event_v0_26",
        records_sha256=records_sha256,
        recalculation_sha256=recalculation_sha256,
    )


def _expected_metric_definitions() -> dict[str, Any]:
    return {
        "definition_set_version": "1.0.0",
        "definitions": [
            _definition(
                "measured_request_count_v1",
                "measured_request_count",
                "count",
                "all_measured_requests",
                ["count"],
                ["request.outcome.status"],
                None,
                "none",
            ),
            _definition(
                "successful_request_count_v1",
                "successful_request_count",
                "count",
                "successful_measured_requests",
                ["count"],
                ["request.outcome.status"],
                None,
                "none",
            ),
            _definition(
                "failed_request_count_v1",
                "failed_request_count",
                "count",
                "failed_measured_requests",
                ["count"],
                ["request.outcome.status"],
                None,
                "none",
            ),
            _definition(
                "measured_failure_ratio_v1",
                "error_rate",
                "ratio",
                "all_measured_requests",
                ["ratio"],
                ["request.outcome.status"],
                None,
                "decimal_half_even_6_v1",
            ),
            _definition(
                "vllm_first_choices_event_v0_26",
                "ttft_ns",
                "ns",
                "successful_measured_requests_with_observed_ttft",
                ["mean", "p50", "p95", "p99"],
                ["request.timing.ttft_ns"],
                "nearest_rank_v1",
                "decimal_half_even_6_v1",
            ),
            _definition(
                "last_choices_event_span_v1",
                "last_choices_event_span_ns",
                "ns",
                "successful_measured_requests_with_observed_ttft",
                ["mean", "p50", "p95", "p99"],
                ["request.timing.ttft_ns", "request.timing.itl_ns"],
                "nearest_rank_v1",
                "decimal_half_even_6_v1",
            ),
            _definition(
                "attempted_measured_requests_per_window_second_v1",
                "attempted_request_throughput_per_s",
                "requests/s",
                "all_measured_requests",
                ["rate"],
                ["request.outcome.status", "execution.measurement_window_ns"],
                None,
                "decimal_half_even_6_v1",
            ),
            _definition(
                "successful_measured_requests_per_window_second_v1",
                "successful_request_throughput_per_s",
                "requests/s",
                "successful_measured_requests",
                ["rate"],
                ["request.outcome.status", "execution.measurement_window_ns"],
                None,
                "decimal_half_even_6_v1",
            ),
            _definition(
                "successful_output_tokens_per_window_second_v1",
                "output_token_throughput_per_s",
                "tokens/s",
                "successful_measured_requests",
                ["rate"],
                [
                    "request.tokens.output_tokens",
                    "request.outcome.status",
                    "execution.measurement_window_ns",
                ],
                None,
                "decimal_half_even_6_v1",
            ),
        ],
        "schema_version": "inferdrome.metric-definitions.v1",
    }


def _definition(
    definition_id: str,
    metric: str,
    unit: str,
    population: str,
    aggregations: list[str],
    observations: list[str],
    quantile: str | None,
    rounding: str,
) -> dict[str, Any]:
    return {
        "allowed_aggregations": aggregations,
        "definition_id": definition_id,
        "metric": metric,
        "population": population,
        "quantile_method": quantile,
        "required_observations": observations,
        "rounding_policy": rounding,
        "unit": unit,
    }


def _measurement(
    metric: str,
    aggregation: str,
    value: int | str,
    sample_count: int,
    unit: str,
    population: str,
    definition_id: str,
    quantile_method: str | None,
    rounding_policy: str,
) -> dict[str, Any]:
    return {
        "aggregation": aggregation,
        "definition_id": definition_id,
        "metric": metric,
        "population": population,
        "quantile_method": quantile_method,
        "rounding_policy": rounding_policy,
        "sample_count": sample_count,
        "unit": unit,
        "value": value,
    }


def _latency_measurements(
    metric: str,
    definition_id: str,
    values: tuple[int, ...],
) -> list[dict[str, Any]]:
    if not values:
        return []
    result = [
        _measurement(
            metric,
            "mean",
            _decimal_ratio(sum(values), len(values)),
            len(values),
            "ns",
            "successful_measured_requests_with_observed_ttft",
            definition_id,
            None,
            "decimal_half_even_6_v1",
        )
    ]
    for aggregation, percentile in (("p50", 50), ("p95", 95), ("p99", 99)):
        result.append(
            _measurement(
                metric,
                aggregation,
                _nearest_rank(values, percentile),
                len(values),
                "ns",
                "successful_measured_requests_with_observed_ttft",
                definition_id,
                "nearest_rank_v1",
                "decimal_half_even_6_v1",
            )
        )
    return result


def _nearest_rank(values: tuple[int, ...], percentile: int) -> int:
    ordered = sorted(values)
    rank = (percentile * len(ordered) + 99) // 100
    return ordered[rank - 1]


def _decimal_ratio(numerator: int, denominator: int) -> str:
    precision = max(50, len(str(numerator)) + len(str(denominator)) + 20)
    with localcontext() as context:
        context.prec = precision
        value = (Decimal(numerator) / Decimal(denominator)).quantize(
            Decimal("0.000001"),
            rounding=ROUND_HALF_EVEN,
        )
    return format(value, "f")


def _seconds_to_ns(value: Decimal) -> int:
    precision = max(50, len(value.as_tuple().digits) + abs(value.adjusted()) + 20)
    with localcontext() as context:
        context.prec = precision
        nanoseconds = (value * Decimal(1_000_000_000)).quantize(
            Decimal(1),
            rounding=ROUND_HALF_EVEN,
        )
    if nanoseconds < 0 or nanoseconds > Decimal(2**63 - 1):
        _inconsistent("Inferdrome native timing exceeds its integer range.")
    return int(nanoseconds)


def _validate_text_artifacts(exact_by_role: Mapping[str, bytes]) -> None:
    for role in ("producer_version", "native_stdout", "native_stderr"):
        try:
            text = exact_by_role[role].decode("utf-8")
        except UnicodeDecodeError as error:
            _reject(
                InferdromeBundleErrorCode.SCHEMA_INVALID,
                "Inferdrome text artifact is not valid UTF-8.",
                error,
            )
        if role == "producer_version":
            versions = tuple(
                line.strip()
                for line in text.splitlines()
                if _SEMVER_LINE.fullmatch(line.strip())
            )
            if (
                len(versions) != 1
                or _PINNED_VLLM_VERSION.fullmatch(versions[0]) is None
            ):
                _inconsistent("Inferdrome producer version is not pinned 0.26.0.")
    if exact_by_role["producer_exit_status"] != b"0\n":
        _inconsistent("Inferdrome producer exit-status artifact is invalid.")


def _execution_fingerprint(resolved: Mapping[str, Any]) -> str:
    workload = _mapping(resolved.get("workload"), "resolved workload")
    projection = {
        "projection_version": "inferdrome.execution-fingerprint-input.v1",
        "execution": resolved.get("execution"),
        "target": resolved.get("target"),
        "workload": {
            "sha256": workload.get("sha256"),
            "requested_output_tokens": workload.get("requested_output_tokens"),
            "temperature": workload.get("temperature"),
            "seed": workload.get("seed"),
        },
        "traffic": resolved.get("traffic"),
        "measurement": resolved.get("measurement"),
    }
    return _domain_digest(
        "execution-fingerprint-v1",
        canonical_json_bytes(projection),
    )


def _argv_option(argv: tuple[str, ...], option: str) -> str:
    positions = tuple(index for index, item in enumerate(argv) if item == option)
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        _inconsistent("Inferdrome producer option is missing or duplicated.")
    return argv[positions[0] + 1]


def _absolute_argv_path(argv: tuple[str, ...], option: str) -> str:
    value = _argv_option(argv, option)
    if not Path(value).is_absolute():
        _inconsistent("Inferdrome producer path argument is not absolute.")
    return value


def _parse_timestamp(raw: Any, label: str) -> datetime:
    if type(raw) is not str:
        _inconsistent(f"Inferdrome {label} is invalid.")
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as error:
        _reject(
            InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY,
            f"Inferdrome {label} is invalid.",
            error,
        )
    if value.tzinfo is None or value.utcoffset() is None:
        _inconsistent(f"Inferdrome {label} is not timezone-aware.")
    return value


def _expected_directories(paths: set[str]) -> frozenset[str]:
    directories: set[str] = set()
    for path in paths:
        parent = PurePosixPath(path).parent
        while parent.as_posix() != ".":
            directories.add(parent.as_posix())
            parent = parent.parent
    return frozenset(directories)


def _validate_relative_path(value: str) -> None:
    if type(value) is not str or not value or len(value) > 512 or "\\" in value:
        _reject(
            InferdromeBundleErrorCode.UNSAFE_BUNDLE,
            "Inferdrome bundle contains an unsafe relative path.",
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _reject(
            InferdromeBundleErrorCode.UNSAFE_BUNDLE,
            "Inferdrome bundle contains an unsafe relative path.",
        )


def _parse_canonical_object(content: bytes, label: str) -> dict[str, Any]:
    value = _parse_json_object(content, label)
    try:
        canonical = canonical_json_bytes(value)
    except (CanonicalizationError, ValueError) as error:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} cannot be canonicalized.",
            error,
        )
    if content != canonical:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} is not canonical JSON.",
        )
    return value


def _parse_native_result(content: bytes, label: str) -> dict[str, Any]:
    """Parse native result numbers as bounded decimals for exact replay."""

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} is not valid UTF-8.",
            error,
        )

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _reject(
                    InferdromeBundleErrorCode.SCHEMA_INVALID,
                    f"{label} contains duplicate JSON keys.",
                )
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} contains a non-finite number.",
        )

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=lambda raw: _bounded_json_decimal(raw, label),
            parse_int=lambda raw: _bounded_json_integer(raw, label),
        )
    except InferdromeBundleRejected:
        raise
    except (
        json.JSONDecodeError,
        InvalidOperation,
        RecursionError,
        ValueError,
    ) as error:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} is not valid bounded JSON.",
            error,
        )
    if type(value) is not dict:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} must contain one JSON object.",
        )
    return value


def _parse_json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} is not valid UTF-8.",
            error,
        )

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _reject(
                    InferdromeBundleErrorCode.SCHEMA_INVALID,
                    f"{label} contains duplicate JSON keys.",
                )
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} contains a non-finite number.",
        )

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=lambda raw: _bounded_json_float(raw, label),
            parse_int=lambda raw: _bounded_json_integer(raw, label),
        )
    except InferdromeBundleRejected:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} is not valid JSON.",
            error,
        )
    if not isinstance(value, dict):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} must contain one JSON object.",
        )
    return value


def _bounded_json_decimal(raw: str, label: str) -> Decimal:
    value = Decimal(raw)
    if (
        not value.is_finite()
        or len(value.as_tuple().digits) > _MAX_JSON_NUMBER_DIGITS
        or abs(value.adjusted()) > _MAX_JSON_DECIMAL_EXPONENT
    ):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} contains a number outside consumer limits.",
        )
    return value


def _bounded_json_float(raw: str, label: str) -> float:
    value = _bounded_json_decimal(raw, label)
    converted = float(value)
    if not math.isfinite(converted):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} contains a number outside binary float limits.",
        )
    return converted


def _bounded_json_integer(raw: str, label: str) -> int:
    digits = raw[1:] if raw.startswith("-") else raw
    if len(digits) > _MAX_JSON_NUMBER_DIGITS:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"{label} contains an integer outside consumer limits.",
        )
    return int(raw)


def _native_array(value: object, label: str) -> list[Any]:
    if type(value) is not list or len(value) > _MAX_NATIVE_ARRAY_ITEMS:
        _inconsistent(f"Inferdrome {label} is not a bounded array.")
    return value


def _native_string(value: object, label: str, limit: int) -> str:
    if type(value) is not str or len(value) > limit:
        _inconsistent(f"Inferdrome {label} is not a bounded string.")
    return value


def _native_nonnegative_integer(value: object, label: str) -> int:
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        _inconsistent(f"Inferdrome {label} is not a non-negative integer.")
    return value


def _native_decimal(value: object, label: str) -> Decimal:
    if type(value) is int:
        result = Decimal(value)
    elif isinstance(value, Decimal):
        result = value
    else:
        _inconsistent(f"Inferdrome {label} is not a decimal number.")
    if not result.is_finite():
        _inconsistent(f"Inferdrome {label} is not finite.")
    return result


def _native_nonnegative_decimal(value: object, label: str) -> Decimal:
    result = _native_decimal(value, label)
    if result < 0:
        _inconsistent(f"Inferdrome {label} is negative.")
    return result


@cache
def _schema_validator(schema_name: str) -> Draft202012Validator:
    if schema_name not in _SCHEMA_SHA256:
        _reject(
            InferdromeBundleErrorCode.UNSUPPORTED_SCHEMA,
            "Requested Inferdrome schema is not vendored.",
        )
    resource = resources.files("exitspec").joinpath(
        "schemas", "inferdrome", "v1", schema_name
    )
    try:
        raw = resource.read_bytes()
    except (FileNotFoundError, OSError) as error:
        _reject(
            InferdromeBundleErrorCode.UNSUPPORTED_SCHEMA,
            "Vendored Inferdrome schema is unavailable.",
            error,
        )
    if not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), _SCHEMA_SHA256[schema_name]
    ):
        _reject(
            InferdromeBundleErrorCode.UNSUPPORTED_SCHEMA,
            "Vendored Inferdrome schema digest is invalid.",
        )
    schema = _parse_json_object(raw, "Vendored Inferdrome schema")
    try:
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, format_checker=FormatChecker())
    except SchemaError as error:
        _reject(
            InferdromeBundleErrorCode.UNSUPPORTED_SCHEMA,
            "Vendored Inferdrome schema is invalid.",
            error,
        )


def _validate_schema(role: str, value: Mapping[str, Any]) -> None:
    schema_name = _SCHEMA_BY_ROLE.get(role)
    if schema_name is None:
        _reject(
            InferdromeBundleErrorCode.UNSUPPORTED_SCHEMA,
            "Inferdrome artifact schema is not vendored.",
        )
    try:
        _schema_validator(schema_name).validate(value)
    except ValidationError as error:
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            "Inferdrome artifact failed its vendored public schema.",
            error,
        )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"Inferdrome {label} must be an object.",
        )
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _reject(
            InferdromeBundleErrorCode.SCHEMA_INVALID,
            f"Inferdrome {label} must be an array.",
        )
    return value


def _sha256_tagged(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _domain_digest(domain: str, content: bytes) -> str:
    return _sha256_tagged(f"inferdrome:{domain}\0".encode() + content)


def _bundle_digest(manifest_bytes: bytes) -> str:
    return _domain_digest("bundle-manifest-v1", manifest_bytes)


def _require_tagged_sha256(value: str, label: str) -> None:
    if type(value) is not str or _TAGGED_SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a tagged SHA-256 digest.")


def _inconsistent(message: str) -> NoReturn:
    _reject(InferdromeBundleErrorCode.INTERNAL_INCONSISTENCY, message)


def _reject(
    code: InferdromeBundleErrorCode,
    message: str,
    cause: BaseException | None = None,
) -> NoReturn:
    error = InferdromeBundleRejected(code, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "INFERDROME_VERIFIER_VERSION",
    "InferdromeBundleErrorCode",
    "InferdromeBundleLimits",
    "InferdromeBundleRejected",
    "RecalculatedInferdromeMeasurements",
    "VerifiedInferdromeBundle",
    "verify_inferdrome_bundle",
]
