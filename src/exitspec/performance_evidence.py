"""Offline trust boundary for inference-performance workload authoring.

This module validates immutable contract and workload inputs and derives the
exact probe manifest they authorize.  It deliberately performs no network
access, persistence, verdict calculation, receipt creation, or reporting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Final, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError

from .confirmations import ContractConfirmation, require_affirmative_confirmation
from .contracts import verify_contract_digest
from .models import (
    ContractStatus,
    FrozenExitSpecModel,
    InferencePerformanceCriterion,
    POCContract,
    SHA256_PATTERN,
)
from .performance_probe import (
    ProbeConfig,
    ProbeConfigurationError,
    ProbeManifest,
    SyntheticPrompt,
    build_manifest,
)


PERFORMANCE_WORKLOAD_SCHEMA_VERSION: Final = "exitspec.performance-workload.v1"
FIRST_TOKEN_DEFINITION: Final = "first_nonempty_choices_delta_content_v1"
_MAX_WORKLOAD_BYTES: Final = 1024 * 1024
_MAX_PROMPT_BYTES: Final = 4 * 1024 * 1024
_MAX_PATH_LENGTH: Final = 1024

StrictNumber = Annotated[StrictInt | StrictFloat, Field(gt=0)]
Sha256String = Annotated[StrictStr, Field(pattern=SHA256_PATTERN)]


class PerformanceEvidenceError(ValueError):
    """An input cannot safely participate in performance evidence."""


class PerformanceWorkloadV1(FrozenExitSpecModel):
    """Strict schema for the exact workload bytes bound by a contract."""

    schema_version: Literal["exitspec.performance-workload.v1"]
    workload_id: StrictStr = Field(
        pattern=r"^[a-z][a-z0-9-]{2,63}$"
    )
    adapter: StrictStr = Field(min_length=1, max_length=200)
    adapter_version: StrictStr = Field(min_length=1, max_length=100)
    endpoint: StrictStr = Field(min_length=1, max_length=2048)
    model: StrictStr = Field(min_length=1, max_length=512)
    request_count: StrictInt = Field(gt=0)
    concurrency: StrictInt = Field(gt=0)
    warmup_count: StrictInt = Field(ge=0)
    timeout_seconds: StrictNumber
    max_tokens: StrictInt = Field(gt=0)
    max_stream_bytes: StrictInt = Field(gt=0)
    first_token_definition: Literal["first_nonempty_choices_delta_content_v1"]
    warmup_included_in_measurement: Literal[False]
    prompt_fixture_path: StrictStr = Field(min_length=1, max_length=_MAX_PATH_LENGTH)
    prompt_fixture_sha256: Sha256String
    retries: StrictInt = Field(ge=0)


@dataclass(frozen=True, slots=True)
class ValidatedPerformanceContext:
    """Typed, byte-bound inputs from which a performance run may be authored."""

    contract: POCContract
    criterion: InferencePerformanceCriterion
    workload: PerformanceWorkloadV1
    workload_sha256: str
    workload_bytes: bytes = field(repr=False)
    prompt_path: Path
    prompt_sha256: str
    prompt_bytes: bytes = field(repr=False)
    prompts: tuple[SyntheticPrompt, ...] = field(repr=False)
    probe_config: ProbeConfig
    expected_manifest: ProbeManifest


def parse_performance_workload(workload_bytes: bytes) -> PerformanceWorkloadV1:
    """Parse exact UTF-8 JSON bytes without coercion, duplicates, or extensions."""

    payload = _parse_json_object(
        workload_bytes,
        label="Performance workload",
        maximum_bytes=_MAX_WORKLOAD_BYTES,
    )
    try:
        return PerformanceWorkloadV1.model_validate(payload)
    except ValidationError as error:
        raise PerformanceEvidenceError(
            "Performance workload schema is invalid."
        ) from error


def validate_performance_context(
    contract: POCContract,
    workload_bytes: bytes,
    *,
    bundle_root: str | Path,
    criterion_id: str | None = None,
) -> ValidatedPerformanceContext:
    """Validate byte integrity and derive the exact authorized probe manifest.

    An approved contract is sufficient for authoring and customer review.  A
    caller must additionally invoke :func:`require_frozen_confirmed` before any
    execution path.
    """

    if type(contract) is not POCContract:
        raise PerformanceEvidenceError("contract must be a POCContract.")
    if contract.status not in {ContractStatus.APPROVED, ContractStatus.FROZEN}:
        raise PerformanceEvidenceError(
            "Performance context requires an approved or frozen contract."
        )

    root = _resolve_bundle_root(bundle_root)
    _validate_relative_path(
        contract.workload.fixture_path,
        label="Contract workload fixture path",
    )

    exact_workload_bytes = _require_exact_bytes(
        workload_bytes,
        label="Performance workload",
        maximum_bytes=_MAX_WORKLOAD_BYTES,
    )
    workload_sha256 = hashlib.sha256(exact_workload_bytes).hexdigest()
    if workload_sha256 != contract.workload.sha256:
        raise PerformanceEvidenceError(
            "Performance workload bytes do not match the contract SHA-256."
        )
    workload = parse_performance_workload(exact_workload_bytes)
    criterion = _select_performance_criterion(contract, criterion_id)
    _validate_alignment(contract, criterion, workload)

    prompt_path = _resolve_beneath_root(
        root,
        workload.prompt_fixture_path,
        label="Prompt fixture path",
    )
    prompt_bytes = _read_bounded_file(
        prompt_path,
        label="Prompt fixture",
        maximum_bytes=_MAX_PROMPT_BYTES,
    )
    prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest()
    if prompt_sha256 != workload.prompt_fixture_sha256:
        raise PerformanceEvidenceError(
            "Prompt fixture bytes do not match the workload SHA-256."
        )
    prompts = _parse_prompts_jsonl(prompt_bytes)

    try:
        probe_config = ProbeConfig(
            endpoint=workload.endpoint,
            model=workload.model,
            request_count=workload.request_count,
            concurrency=workload.concurrency,
            warmup_count=workload.warmup_count,
            timeout_seconds=float(workload.timeout_seconds),
            max_tokens=workload.max_tokens,
            max_stream_bytes=workload.max_stream_bytes,
        )
        expected_manifest = build_manifest(probe_config, prompts)
    except ProbeConfigurationError as error:
        raise PerformanceEvidenceError(
            "Performance workload exceeds the probe safety bounds."
        ) from error

    if expected_manifest.first_token_definition != workload.first_token_definition:
        raise PerformanceEvidenceError(
            "Probe first-token semantics do not match the workload."
        )
    if (
        expected_manifest.warmup_included_in_measurement
        is not workload.warmup_included_in_measurement
    ):
        raise PerformanceEvidenceError(
            "Probe warmup semantics do not match the workload."
        )

    return ValidatedPerformanceContext(
        contract=contract,
        criterion=criterion,
        workload=workload,
        workload_sha256=workload_sha256,
        workload_bytes=exact_workload_bytes,
        prompt_path=prompt_path,
        prompt_sha256=prompt_sha256,
        prompt_bytes=prompt_bytes,
        prompts=prompts,
        probe_config=probe_config,
        expected_manifest=expected_manifest,
    )


def require_frozen_confirmed(
    context: ValidatedPerformanceContext,
    confirmation: ContractConfirmation,
) -> ValidatedPerformanceContext:
    """Require the exact frozen digest and affirmative confirmation for execution."""

    if type(context) is not ValidatedPerformanceContext:
        raise PerformanceEvidenceError(
            "context must be a ValidatedPerformanceContext."
        )
    if type(confirmation) is not ContractConfirmation:
        raise PerformanceEvidenceError(
            "confirmation must be a ContractConfirmation."
        )

    contract = context.contract
    if contract.status is not ContractStatus.FROZEN:
        raise PerformanceEvidenceError(
            "Performance execution requires a frozen contract."
        )
    if not verify_contract_digest(contract):
        raise PerformanceEvidenceError(
            "Frozen contract canonical digest is missing or invalid."
        )
    if contract.confirmation_id is None:
        raise PerformanceEvidenceError(
            "Frozen contract lacks customer confirmation provenance."
        )
    if contract.confirmation_id != confirmation.confirmation_id:
        raise PerformanceEvidenceError(
            "Customer confirmation identity does not match the frozen contract."
        )
    try:
        require_affirmative_confirmation(contract, confirmation)
    except ValueError as error:
        raise PerformanceEvidenceError(
            "Customer confirmation does not authorize this frozen contract."
        ) from error
    return context


def _validate_alignment(
    contract: POCContract,
    criterion: InferencePerformanceCriterion,
    workload: PerformanceWorkloadV1,
) -> None:
    if workload.adapter != criterion.adapter:
        raise PerformanceEvidenceError(
            "Workload adapter does not match the performance criterion."
        )
    if workload.workload_id != criterion.workload_slice:
        raise PerformanceEvidenceError(
            "Workload identity does not match the performance criterion."
        )
    if workload.adapter_version != criterion.adapter_version:
        raise PerformanceEvidenceError(
            "Workload adapter version does not match the performance criterion."
        )
    if workload.model != contract.target_system.model:
        raise PerformanceEvidenceError(
            "Workload model does not match the contract target model."
        )
    if workload.retries != 0:
        raise PerformanceEvidenceError(
            "Performance evidence v1 requires retries to be zero."
        )
    if workload.warmup_included_in_measurement is not False:
        raise PerformanceEvidenceError(
            "Warmup requests must be excluded from measurement."
        )
    if workload.first_token_definition != FIRST_TOKEN_DEFINITION:
        raise PerformanceEvidenceError(
            "Workload first-token semantics are unsupported."
        )
    if workload.request_count != criterion.error_rate.minimum_attempts:
        raise PerformanceEvidenceError(
            "Workload request count must exactly match the error-rate minimum attempts."
        )
    if criterion.ttft_p95.minimum_successful_samples > workload.request_count:
        raise PerformanceEvidenceError(
            "TTFT minimum successful samples exceed the workload request count."
        )


def _select_performance_criterion(
    contract: POCContract,
    criterion_id: str | None,
) -> InferencePerformanceCriterion:
    if criterion_id is not None and (
        type(criterion_id) is not str or not criterion_id
    ):
        raise PerformanceEvidenceError("criterion_id must be a non-empty string.")

    performance_criteria = tuple(
        criterion
        for criterion in contract.criteria
        if isinstance(criterion, InferencePerformanceCriterion)
    )
    if criterion_id is None:
        if len(performance_criteria) != 1:
            raise PerformanceEvidenceError(
                "Contract must contain exactly one performance criterion "
                "when criterion_id is omitted."
            )
        return performance_criteria[0]

    matches = tuple(
        criterion
        for criterion in performance_criteria
        if criterion.id == criterion_id
    )
    if len(matches) != 1:
        raise PerformanceEvidenceError(
            "Requested performance criterion was not found."
        )
    return matches[0]


def _resolve_bundle_root(bundle_root: str | Path) -> Path:
    if not isinstance(bundle_root, (str, Path)):
        raise PerformanceEvidenceError("bundle_root must be a filesystem path.")
    try:
        root = Path(bundle_root).resolve(strict=True)
    except OSError:
        raise PerformanceEvidenceError("Bundle root could not be resolved.") from None
    if not root.is_dir():
        raise PerformanceEvidenceError("Bundle root must be a directory.")
    return root


def _validate_relative_path(raw_path: str, *, label: str) -> PurePosixPath:
    if (
        type(raw_path) is not str
        or not raw_path
        or len(raw_path) > _MAX_PATH_LENGTH
        or "\x00" in raw_path
        or "\\" in raw_path
    ):
        raise PerformanceEvidenceError(f"{label} is invalid.")
    segments = raw_path.split("/")
    candidate = PurePosixPath(raw_path)
    if (
        candidate.is_absolute()
        or not segments
        or any(segment in {"", ".", ".."} for segment in segments)
    ):
        raise PerformanceEvidenceError(f"{label} must be a safe relative path.")
    return candidate


def _resolve_beneath_root(root: Path, raw_path: str, *, label: str) -> Path:
    relative = _validate_relative_path(raw_path, label=label)
    try:
        resolved = (root / Path(*relative.parts)).resolve(strict=True)
    except OSError:
        raise PerformanceEvidenceError(f"{label} could not be resolved.") from None
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PerformanceEvidenceError(
            f"{label} escapes the bundle root."
        ) from None
    if not resolved.is_file():
        raise PerformanceEvidenceError(f"{label} must resolve to a file.")
    return resolved


def _read_bounded_file(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    try:
        size = path.stat().st_size
        if size <= 0 or size > maximum_bytes:
            raise PerformanceEvidenceError(f"{label} size is invalid.")
        content = path.read_bytes()
    except PerformanceEvidenceError:
        raise
    except OSError:
        raise PerformanceEvidenceError(f"{label} could not be read.") from None
    if len(content) != size:
        raise PerformanceEvidenceError(f"{label} changed while it was read.")
    return content


def _require_exact_bytes(
    value: bytes,
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    if type(value) is not bytes or not value or len(value) > maximum_bytes:
        raise PerformanceEvidenceError(f"{label} bytes are invalid.")
    return value


def _parse_json_object(
    value: bytes,
    *,
    label: str,
    maximum_bytes: int,
) -> dict[str, Any]:
    exact_bytes = _require_exact_bytes(
        value,
        label=label,
        maximum_bytes=maximum_bytes,
    )
    try:
        text = exact_bytes.decode("utf-8")
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, PerformanceEvidenceError):
        raise PerformanceEvidenceError(f"{label} JSON is invalid.") from None
    if type(parsed) is not dict:
        raise PerformanceEvidenceError(f"{label} must be a JSON object.")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PerformanceEvidenceError(f"Duplicate JSON key: {key}.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise PerformanceEvidenceError(f"Non-finite JSON number: {value}.")


def _parse_prompts_jsonl(prompt_bytes: bytes) -> tuple[SyntheticPrompt, ...]:
    exact_bytes = _require_exact_bytes(
        prompt_bytes,
        label="Prompt fixture",
        maximum_bytes=_MAX_PROMPT_BYTES,
    )
    try:
        text = exact_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise PerformanceEvidenceError(
            "Prompt fixture must be valid UTF-8."
        ) from None

    prompts: list[SyntheticPrompt] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            raise PerformanceEvidenceError(
                f"Prompt fixture line {line_number} is blank."
            )
        try:
            value = json.loads(
                raw_line,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
        except (
            json.JSONDecodeError,
            PerformanceEvidenceError,
        ):
            raise PerformanceEvidenceError(
                f"Prompt fixture line {line_number} is invalid."
            ) from None
        if type(value) is not dict or set(value) != {"id", "content"}:
            raise PerformanceEvidenceError(
                f"Prompt fixture line {line_number} has an invalid shape."
            )
        try:
            prompts.append(SyntheticPrompt(value["id"], value["content"]))
        except ProbeConfigurationError as error:
            raise PerformanceEvidenceError(
                f"Prompt fixture line {line_number} is invalid."
            ) from error

    if not prompts:
        raise PerformanceEvidenceError("Prompt fixture is empty.")
    return tuple(prompts)


__all__ = [
    "FIRST_TOKEN_DEFINITION",
    "PERFORMANCE_WORKLOAD_SCHEMA_VERSION",
    "PerformanceEvidenceError",
    "PerformanceWorkloadV1",
    "ValidatedPerformanceContext",
    "parse_performance_workload",
    "require_frozen_confirmed",
    "validate_performance_context",
]
