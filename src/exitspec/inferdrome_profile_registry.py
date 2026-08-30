"""Small, versioned registry for managed Inferdrome evidence profiles.

The registry contains compatibility facts only.  It intentionally does not
contain archive paths, run identifiers, host paths, GPU UUIDs, prompts, or
generated responses.  The legacy profile remains registered so existing A10
imports keep using their established validator; the A100 entry is used by the
external-only admission gate.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from functools import cache
from typing import Any, Final, NoReturn

from .inferdrome_managed_profile import (
    ManagedInferdromeProfileError,
    validate_managed_invocation_profile,
)
from .inferdrome_profile import (
    MANAGED_PROFILE_ID,
    MANAGED_PROFILE_SHA256,
    load_pinned_inferdrome_profile_documents,
)


MANAGED_EVIDENCE_REGISTRY_VERSION: Final = "exitspec.managed-evidence-registry.v1"
A100_MANAGED_PROFILE_ID: Final = "managed-vllm-0.26-qwen3-8b-bf16-v1"
A100_MANAGED_PROFILE_SHA256: Final = (
    "sha256:858382b5ea2e86253f55ed914d11e4ab7e8b13aa6331e8699fb4d364a9ee9369"
)
A100_CAMPAIGN_ID: Final = "qwen-gpu-capability-campaign-v1"
A100_PROFILE_BINDING_SCHEMA_VERSION: Final = "inferdrome.campaign-profile-binding.v1"
A100_WORKLOAD_ID: Final = "inferdrome.qwen-text-mixed-length.v1"
A100_WORKLOAD_SHA256: Final = (
    "sha256:72db7f3a4e8e70c9fb721fe5544d1d96aac37ec6baedbce99e05ad423fdb105f"
)
A100_MODEL_ID: Final = "Qwen/Qwen3-8B"
A100_MODEL_REVISION: Final = "b968826d9c46dd6066d109eabc6255188de91218"
A100_HARDWARE_MODEL: Final = "NVIDIA A100-SXM4-40GB"
A100_METRIC_DEFINITION_ID: Final = "vllm_first_choices_event_v0_26"
A100_REDUCER_ID: Final = "nearest_rank_v1"
A100_METRIC_POPULATION: Final = "successful_measured_requests_with_observed_ttft"
A100_METRIC_UNIT: Final = "ns"
A100_NATIVE_SCHEMA_FINGERPRINT: Final = (
    "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
)
A100_ENVIRONMENT_POLICY: Final = "vllm-scrubbed-offline-v1"
A100_ENVIRONMENT_OVERRIDES: Final = (
    "DO_NOT_TRACK=1",
    "HF_HUB_DISABLE_TELEMETRY=1",
    "HF_HUB_OFFLINE=1",
    "TRANSFORMERS_OFFLINE=1",
    "VLLM_NO_USAGE_STATS=1",
)
A100_PROFILE_ARGUMENTS: Final = (
    "--top-p",
    "0.8",
    "--top-k",
    "20",
    "--min-p",
    "0",
    "--ignore-eos",
    "--extra-body",
    '{"chat_template_kwargs":{"enable_thinking":false},"seed":42}',
)
A100_SERVER_ARGV_TEMPLATE: Final = (
    "{producer_distribution.executable_path}",
    "serve",
    "{model_snapshot.root}",
    "--host",
    "127.0.0.1",
    "--port",
    "{resolved_target_port}",
    "--served-model-name",
    "{resolved_target_model}",
    "--tokenizer",
    "{tokenizer_snapshot.root}",
    "--tokenizer-mode",
    "auto",
    "--dtype",
    "bfloat16",
    "--seed",
    "{resolved_workload_seed}",
    "--load-format",
    "safetensors",
    "--generation-config",
    "vllm",
    "--model-impl",
    "vllm",
    "--max-model-len",
    "2048",
    "--gpu-memory-utilization",
    "0.90",
    "--tensor-parallel-size",
    "1",
    "--device-ids",
    "{selected_gpu_index}",
    "--no-enable-log-requests",
    "--disable-uvicorn-access-log",
    "--uvicorn-log-level",
    "warning",
)

_LOOPBACK_ENDPOINT = re.compile(r"http://127\.0\.0\.1:[0-9]{1,5}\Z")
_MISSING = object()


class ManagedEvidenceProfileErrorCode(str, Enum):
    """Stable reason codes for profile compatibility rejection."""

    PROFILE_UNKNOWN = "PROFILE_UNKNOWN"
    PROFILE_ID_MISMATCH = "PROFILE_ID_MISMATCH"
    PROFILE_SHA256_MISMATCH = "PROFILE_SHA256_MISMATCH"
    PROFILE_FACTS_MISSING = "PROFILE_FACTS_MISSING"
    PROFILE_FACTS_EXTRA = "PROFILE_FACTS_EXTRA"
    PROFILE_FACT_MISMATCH = "PROFILE_FACT_MISMATCH"
    PROFILE_SCHEMA_INVALID = "PROFILE_SCHEMA_INVALID"
    MODEL_MISMATCH = "MODEL_MISMATCH"
    REVISION_MISMATCH = "REVISION_MISMATCH"
    ENGINE_MISMATCH = "ENGINE_MISMATCH"
    ADAPTER_MISMATCH = "ADAPTER_MISMATCH"
    WORKLOAD_MISMATCH = "WORKLOAD_MISMATCH"
    METRIC_SEMANTICS_MISMATCH = "METRIC_SEMANTICS_MISMATCH"
    METRIC_REDUCER_MISMATCH = "METRIC_REDUCER_MISMATCH"
    METRIC_POPULATION_MISMATCH = "METRIC_POPULATION_MISMATCH"
    METRIC_UNITS_MISMATCH = "METRIC_UNITS_MISMATCH"
    CONCURRENCY_MISMATCH = "CONCURRENCY_MISMATCH"
    SAMPLE_COUNT_MISMATCH = "SAMPLE_COUNT_MISMATCH"
    ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    LEGACY_PROFILE_REQUIRES_LEGACY_PATH = "LEGACY_PROFILE_REQUIRES_LEGACY_PATH"


class ManagedEvidenceProfileRejected(ValueError):
    """The evidence did not exactly match the selected managed profile."""

    def __init__(
        self,
        code: ManagedEvidenceProfileErrorCode,
        message: str,
        *,
        path: str | None = None,
    ) -> None:
        self.code = code
        self.path = path
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ManagedEvidenceProfile:
    """Immutable compatibility facts for one managed evidence profile."""

    registry_version: str
    profile_id: str
    profile_sha256: str
    evidence_schema_version: str
    producer_name: str
    producer_version: str
    adapter_id: str
    adapter_version: str
    native_schema_fingerprint: str
    engine: str
    engine_version: str
    model_id: str | None
    model_revision: str | None
    tokenizer_revision: str | None
    dtype: str | None
    checkpoint_precision: str | None
    tensor_parallel_size: int | None
    max_model_len: int | None
    gpu_memory_utilization: str | None
    hardware_model: str | None
    hardware_count: int | None
    workload_id: str | None
    workload_sha256: str | None
    warmup_requests: int | None
    measured_requests: int | None
    configured_concurrency: int | None
    metric_definition_id: str
    metric_population: str
    metric_reducer_id: str
    metric_unit: str
    metric_aggregation: str
    chronology: str | None
    producer_contract_digest: None
    consumer_mode: str | None
    profile_binding: Mapping[str, Any] | None
    benchmark_profile_arguments: tuple[str, ...]


LEGACY_A10_PROFILE: Final = ManagedEvidenceProfile(
    registry_version=MANAGED_EVIDENCE_REGISTRY_VERSION,
    profile_id=MANAGED_PROFILE_ID,
    profile_sha256=MANAGED_PROFILE_SHA256,
    evidence_schema_version="inferdrome.evidence.v1",
    producer_name="vllm",
    producer_version="0.26.0",
    adapter_id="vllm_bench_serve",
    adapter_version="1.0.0",
    native_schema_fingerprint=A100_NATIVE_SCHEMA_FINGERPRINT,
    engine="vllm",
    engine_version="0.26.0",
    model_id=None,
    model_revision=None,
    tokenizer_revision=None,
    dtype=None,
    checkpoint_precision=None,
    tensor_parallel_size=None,
    max_model_len=None,
    gpu_memory_utilization=None,
    hardware_model=None,
    hardware_count=None,
    workload_id=None,
    workload_sha256=None,
    warmup_requests=None,
    measured_requests=None,
    configured_concurrency=None,
    metric_definition_id=A100_METRIC_DEFINITION_ID,
    metric_population=A100_METRIC_POPULATION,
    metric_reducer_id=A100_REDUCER_ID,
    metric_unit=A100_METRIC_UNIT,
    metric_aggregation="p95",
    chronology=None,
    producer_contract_digest=None,
    consumer_mode=None,
    profile_binding=None,
    benchmark_profile_arguments=(),
)

A100_QWEN3_PROFILE: Final = ManagedEvidenceProfile(
    registry_version=MANAGED_EVIDENCE_REGISTRY_VERSION,
    profile_id=A100_MANAGED_PROFILE_ID,
    profile_sha256=A100_MANAGED_PROFILE_SHA256,
    evidence_schema_version="inferdrome.evidence.v1",
    producer_name="vllm",
    producer_version="0.26.0",
    adapter_id="vllm_bench_serve",
    adapter_version="1.0.0",
    native_schema_fingerprint=A100_NATIVE_SCHEMA_FINGERPRINT,
    engine="vllm",
    engine_version="0.26.0",
    model_id=A100_MODEL_ID,
    model_revision=A100_MODEL_REVISION,
    tokenizer_revision=A100_MODEL_REVISION,
    dtype="bfloat16",
    checkpoint_precision="BF16",
    tensor_parallel_size=1,
    max_model_len=2048,
    gpu_memory_utilization="0.90",
    hardware_model=A100_HARDWARE_MODEL,
    hardware_count=1,
    workload_id=A100_WORKLOAD_ID,
    workload_sha256=A100_WORKLOAD_SHA256,
    warmup_requests=12,
    measured_requests=96,
    configured_concurrency=1,
    metric_definition_id=A100_METRIC_DEFINITION_ID,
    metric_population=A100_METRIC_POPULATION,
    metric_reducer_id=A100_REDUCER_ID,
    metric_unit=A100_METRIC_UNIT,
    metric_aggregation="p95",
    chronology="RETROSPECTIVE",
    producer_contract_digest=None,
    consumer_mode="EXTERNAL_RECEIPT_BINDING",
    profile_binding={
        "campaign_id": A100_CAMPAIGN_ID,
        "profile_id": A100_MANAGED_PROFILE_ID,
        "profile_sha256": A100_MANAGED_PROFILE_SHA256,
        "schema_version": A100_PROFILE_BINDING_SCHEMA_VERSION,
        "tokenizer_files": {
            "policy": "bounded-regular-files-no-follow-sha256-v1",
            "tokenizer_config_sha256": (
                "sha256:d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101"
            ),
            "tokenizer_json_sha256": (
                "sha256:aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"
            ),
        },
        "workload_id": A100_WORKLOAD_ID,
        "workload_sha256": A100_WORKLOAD_SHA256,
    },
    benchmark_profile_arguments=A100_PROFILE_ARGUMENTS,
)


@cache
def managed_evidence_profiles() -> tuple[ManagedEvidenceProfile, ...]:
    """Return the immutable registry entries in canonical registry order."""

    return (LEGACY_A10_PROFILE, A100_QWEN3_PROFILE)


def get_managed_evidence_profile(profile_id: str) -> ManagedEvidenceProfile:
    """Resolve one exact profile ID; aliases and unknown IDs are rejected."""

    if type(profile_id) is not str or not profile_id:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_UNKNOWN,
            "Managed evidence profile ID is missing or invalid.",
        )
    for profile in managed_evidence_profiles():
        if profile.profile_id == profile_id:
            return copy.deepcopy(profile)
    _profile_reject(
        ManagedEvidenceProfileErrorCode.PROFILE_UNKNOWN,
        "Managed evidence profile ID is not registered.",
    )


def validate_managed_evidence_profile(
    profile: ManagedEvidenceProfile,
    *,
    descriptor: Mapping[str, Any],
    resolved: Mapping[str, Any],
    plan: Mapping[str, Any],
    environment: Mapping[str, Any],
    execution: Mapping[str, Any],
    invocation: Mapping[str, Any],
    definitions: Mapping[str, Any] | None = None,
) -> None:
    """Match every admitted A100 compatibility fact exactly.

    The A10 entry is deliberately rejected here.  It remains registered for
    lookup and backwards-compatibility introspection, while A10 imports keep
    using the established validator and import path.
    """

    if type(profile) is not ManagedEvidenceProfile:
        raise TypeError("profile must be a ManagedEvidenceProfile.")
    if profile.profile_id == LEGACY_A10_PROFILE.profile_id:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.LEGACY_PROFILE_REQUIRES_LEGACY_PATH,
            "The legacy A10 profile must use the established legacy validator.",
        )
    if profile.profile_id != A100_QWEN3_PROFILE.profile_id:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_UNKNOWN,
            "Managed evidence profile ID is not registered.",
        )
    if profile != A100_QWEN3_PROFILE:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_FACT_MISMATCH,
            "Managed A100 profile facts must come from the registry unchanged.",
        )
    _require_profile_binding(invocation.get("campaign_profile"), profile)
    proof = _object(invocation.get("local_gpu_proof"), "local GPU proof")
    _validate_profile_specific_local_gpu_proof(proof, profile)

    _value(
        descriptor.get("schema_version"),
        profile.evidence_schema_version,
        ManagedEvidenceProfileErrorCode.PROFILE_SCHEMA_INVALID,
        "Bundle evidence schema does not match the managed profile.",
        "bundle.schema_version",
    )
    producer = _object(descriptor.get("producer"), "bundle producer")
    _exact_values(
        producer,
        {
            "name": profile.producer_name,
            "version": profile.producer_version,
            "adapter": profile.adapter_id,
            "adapter_version": profile.adapter_version,
            "native_schema_fingerprint": profile.native_schema_fingerprint,
        },
        "producer",
        category=ManagedEvidenceProfileErrorCode.PROFILE_FACT_MISMATCH,
    )
    target = _object(resolved.get("target"), "resolved target")
    _value(
        target.get("engine"),
        profile.engine,
        ManagedEvidenceProfileErrorCode.ENGINE_MISMATCH,
        "Resolved engine does not match the managed profile.",
        "resolved.target.engine",
    )
    _value(
        target.get("engine_version"),
        profile.engine_version,
        ManagedEvidenceProfileErrorCode.ENGINE_MISMATCH,
        "Resolved engine version does not match the managed profile.",
        "resolved.target.engine_version",
    )
    _value(
        target.get("model"),
        profile.model_id,
        ManagedEvidenceProfileErrorCode.MODEL_MISMATCH,
        "Resolved model does not match the managed profile.",
        "resolved.target.model",
    )
    _value(
        target.get("model_revision"),
        profile.model_revision,
        ManagedEvidenceProfileErrorCode.REVISION_MISMATCH,
        "Resolved model revision does not match the managed profile.",
        "resolved.target.model_revision",
    )
    _value(
        target.get("tokenizer_revision"),
        profile.tokenizer_revision,
        ManagedEvidenceProfileErrorCode.REVISION_MISMATCH,
        "Resolved tokenizer revision does not match the managed profile.",
        "resolved.target.tokenizer_revision",
    )
    workload = _object(resolved.get("workload"), "resolved workload")
    _value(
        workload.get("sha256"),
        profile.workload_sha256,
        ManagedEvidenceProfileErrorCode.WORKLOAD_MISMATCH,
        "Resolved workload digest does not match the managed profile.",
        "resolved.workload.sha256",
    )
    _value(
        workload.get("requested_output_tokens"),
        128,
        ManagedEvidenceProfileErrorCode.WORKLOAD_MISMATCH,
        "Resolved output-token configuration does not match the managed profile.",
        "resolved.workload.requested_output_tokens",
    )
    _value(
        workload.get("temperature"),
        "0.7",
        ManagedEvidenceProfileErrorCode.WORKLOAD_MISMATCH,
        "Resolved sampling temperature does not match the managed profile.",
        "resolved.workload.temperature",
    )
    _value(
        workload.get("seed"),
        42,
        ManagedEvidenceProfileErrorCode.WORKLOAD_MISMATCH,
        "Resolved sampling seed does not match the managed profile.",
        "resolved.workload.seed",
    )
    _value(
        workload.get("prompt_content_policy"),
        "include",
        ManagedEvidenceProfileErrorCode.WORKLOAD_MISMATCH,
        "Resolved prompt-content policy does not match the managed profile.",
        "resolved.workload.prompt_content_policy",
    )
    traffic = _object(resolved.get("traffic"), "resolved traffic")
    traffic_expected = {
        "kind": "concurrent",
        "concurrency": profile.configured_concurrency,
        "warmup_requests": profile.warmup_requests,
        "measured_requests": profile.measured_requests,
    }
    if set(traffic) != set(traffic_expected):
        _profile_reject(
            (
                ManagedEvidenceProfileErrorCode.PROFILE_FACTS_MISSING
                if set(traffic_expected) - set(traffic)
                else ManagedEvidenceProfileErrorCode.PROFILE_FACTS_EXTRA
            ),
            "Managed traffic facts are incomplete or contain unsupported fields.",
            path="resolved.traffic",
        )
    for name, expected_value in traffic_expected.items():
        if traffic[name] != expected_value:
            category = (
                ManagedEvidenceProfileErrorCode.CONCURRENCY_MISMATCH
                if name == "concurrency"
                else (
                    ManagedEvidenceProfileErrorCode.SAMPLE_COUNT_MISMATCH
                    if name in {"warmup_requests", "measured_requests"}
                    else ManagedEvidenceProfileErrorCode.PROFILE_FACT_MISMATCH
                )
            )
            _profile_reject(
                category,
                f"Managed traffic fact {name} does not match the profile.",
                path=f"resolved.traffic.{name}",
            )
    if plan.get("traffic") != traffic:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.CONCURRENCY_MISMATCH,
            "Request plan traffic does not match the managed profile.",
            path="request_plan.traffic",
        )
    execution_settings = _object(resolved.get("execution"), "execution settings")
    _exact_values(
        execution_settings,
        {
            "mode": "attached_endpoint",
            "producer_name": profile.producer_name,
            "producer_version": profile.producer_version,
            "adapter": profile.adapter_id,
            "adapter_version": profile.adapter_version,
        },
        "resolved.execution",
        category=ManagedEvidenceProfileErrorCode.ADAPTER_MISMATCH,
        allow_extra=True,
    )
    measurement = _object(resolved.get("measurement"), "measurement settings")
    _exact_values(
        measurement,
        {
            "ttft_definition": profile.metric_definition_id,
        },
        "resolved.measurement",
        category=ManagedEvidenceProfileErrorCode.METRIC_SEMANTICS_MISMATCH,
        allow_extra=True,
    )
    _value(
        measurement.get("metric_definitions_version"),
        "1.0.0",
        ManagedEvidenceProfileErrorCode.METRIC_SEMANTICS_MISMATCH,
        "Managed metric-definition version does not match the profile.",
        "resolved.measurement.metric_definitions_version",
    )
    _value(
        measurement.get("reducer_version"),
        "1.0.0",
        ManagedEvidenceProfileErrorCode.METRIC_REDUCER_MISMATCH,
        "Managed reducer version does not match the profile.",
        "resolved.measurement.reducer_version",
    )
    _value(
        measurement.get("choices_span_definition"),
        "last_choices_event_span_v1",
        ManagedEvidenceProfileErrorCode.METRIC_SEMANTICS_MISMATCH,
        "Managed span methodology does not match the profile.",
        "resolved.measurement.choices_span_definition",
    )
    _value(
        measurement.get("streaming"),
        True,
        ManagedEvidenceProfileErrorCode.METRIC_SEMANTICS_MISMATCH,
        "Managed streaming methodology does not match the profile.",
        "resolved.measurement.streaming",
    )
    if definitions is not None:
        _require_metric_definition(definitions, profile)

    fields = {
        str(_object(item, "environment field").get("name")): _object(
            item, "environment field"
        )
        for item in _array(environment.get("fields"), "environment fields")
    }
    _environment_value(
        fields,
        "gpu.model",
        profile.hardware_model,
        "LOCALLY_VERIFIED",
    )
    _environment_value(
        fields,
        "gpu.count",
        profile.hardware_count,
        "LOCALLY_VERIFIED",
    )
    _environment_value(
        fields,
        "target.engine_version",
        profile.engine_version,
        "LOCALLY_VERIFIED",
    )
    _environment_value(
        fields,
        "target.model_revision",
        profile.model_revision,
        "CONFIGURED",
    )
    _environment_value(
        fields,
        "target.tokenizer_revision",
        profile.tokenizer_revision,
        "CONFIGURED",
    )
    if environment.get("completeness") != "COMPLETE":
        _profile_reject(
            ManagedEvidenceProfileErrorCode.ENVIRONMENT_MISMATCH,
            "Managed profile requires complete environment evidence.",
            path="environment.completeness",
        )
    _value(
        execution.get("configured_traffic"),
        traffic,
        ManagedEvidenceProfileErrorCode.CONCURRENCY_MISMATCH,
        "Execution traffic does not match the managed profile.",
        "execution.configured_traffic",
    )
    try:
        validate_managed_invocation_profile(
            invocation,
            descriptor=descriptor,
            resolved=resolved,
            environment=environment,
            execution=execution,
            profile_document=_managed_validation_profile_document(profile),
        )
    except ManagedInferdromeProfileError as error:
        raise ManagedEvidenceProfileRejected(
            ManagedEvidenceProfileErrorCode.PROVENANCE_MISMATCH,
            "Managed local GPU proof or invocation failed mature validation.",
            path="invocation.local_gpu_proof",
        ) from error


def validate_managed_profile_binding(
    profile: ManagedEvidenceProfile,
    binding: object,
) -> None:
    """Validate one serialized campaign-profile binding exactly."""

    if type(profile) is not ManagedEvidenceProfile:
        raise TypeError("profile must be a ManagedEvidenceProfile.")
    _require_profile_binding(binding, profile)


def _require_profile_binding(
    actual: object,
    profile: ManagedEvidenceProfile,
) -> None:
    if profile.profile_binding is None:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_SCHEMA_INVALID,
            "Selected managed profile has no exact invocation binding.",
        )
    if type(actual) is not dict:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_FACTS_MISSING,
            "Exact managed profile binding is missing.",
            path="invocation.campaign_profile",
        )
    if actual.get("profile_id") != profile.profile_id:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_ID_MISMATCH,
            "Managed invocation profile ID does not match the registry.",
            path="invocation.campaign_profile.profile_id",
        )
    if actual.get("profile_sha256") != profile.profile_sha256:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_SHA256_MISMATCH,
            "Managed invocation profile digest does not match the registry.",
            path="invocation.campaign_profile.profile_sha256",
        )
    _exact_mapping(actual, profile.profile_binding, "invocation.campaign_profile")


@cache
def _a100_mature_validation_profile_document() -> dict[str, Any]:
    """Return the registry-owned A100 specialization of the mature contract."""

    document = load_pinned_inferdrome_profile_documents().managed_profile
    document["profile_id"] = A100_MANAGED_PROFILE_ID
    document["producer_invocation"] = copy.deepcopy(document["producer_invocation"])
    document["producer_invocation"]["required_root_fields"] = [
        *document["producer_invocation"]["required_root_fields"],
        "campaign_profile",
    ]
    document["server_argv_template"] = list(A100_SERVER_ARGV_TEMPLATE)
    return document


def _managed_validation_profile_document(
    profile: ManagedEvidenceProfile,
) -> Mapping[str, Any]:
    if profile.profile_id == A100_QWEN3_PROFILE.profile_id:
        return copy.deepcopy(_a100_mature_validation_profile_document())
    _profile_reject(
        ManagedEvidenceProfileErrorCode.LEGACY_PROFILE_REQUIRES_LEGACY_PATH,
        "The legacy A10 profile must use the established legacy validator.",
    )


def _validate_profile_specific_local_gpu_proof(
    proof: Mapping[str, Any],
    profile: ManagedEvidenceProfile,
) -> None:
    selected = _array(proof.get("selected_gpu_indices"), "selected GPU indices")
    gpus = _array(proof.get("gpus"), "GPU inventory")
    if len(selected) != profile.hardware_count or len(gpus) != profile.hardware_count:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.ENVIRONMENT_MISMATCH,
            "Managed evidence does not contain the exact hardware population.",
            path="invocation.local_gpu_proof.gpus",
        )
    if any(
        _object(item, "GPU inventory item").get("model") != profile.hardware_model
        for item in gpus
    ):
        _profile_reject(
            ManagedEvidenceProfileErrorCode.ENVIRONMENT_MISMATCH,
            "Managed GPU model does not match the profile.",
            path="invocation.local_gpu_proof.gpus",
        )
    if proof.get("torch_cuda_device_count") != profile.hardware_count:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.ENVIRONMENT_MISMATCH,
            "Managed CUDA device count does not match the profile.",
            path="invocation.local_gpu_proof.torch_cuda_device_count",
        )
    model_snapshot = _object(proof.get("model_snapshot"), "model snapshot")
    tokenizer_snapshot = _object(proof.get("tokenizer_snapshot"), "tokenizer snapshot")
    _exact_values(
        model_snapshot,
        {"kind": "model", "revision": profile.model_revision},
        "invocation.local_gpu_proof.model_snapshot",
        category=ManagedEvidenceProfileErrorCode.REVISION_MISMATCH,
        allow_extra=True,
    )
    _exact_values(
        tokenizer_snapshot,
        {"kind": "tokenizer", "revision": profile.tokenizer_revision},
        "invocation.local_gpu_proof.tokenizer_snapshot",
        category=ManagedEvidenceProfileErrorCode.REVISION_MISMATCH,
        allow_extra=True,
    )
    distribution = _object(proof.get("producer_distribution"), "producer distribution")
    _value(
        distribution.get("version"),
        profile.producer_version,
        ManagedEvidenceProfileErrorCode.ENGINE_MISMATCH,
        "Managed producer version does not match the profile.",
        "invocation.local_gpu_proof.producer_distribution.version",
    )
    server = _object(proof.get("server"), "managed server proof")
    _value(
        server.get("environment_policy"),
        A100_ENVIRONMENT_POLICY,
        ManagedEvidenceProfileErrorCode.ENVIRONMENT_MISMATCH,
        "Managed server environment policy does not match the profile.",
        "invocation.local_gpu_proof.server.environment_policy",
    )
    _value(
        server.get("environment_overrides"),
        list(A100_ENVIRONMENT_OVERRIDES),
        ManagedEvidenceProfileErrorCode.ENVIRONMENT_MISMATCH,
        "Managed server environment overrides do not match the profile.",
        "invocation.local_gpu_proof.server.environment_overrides",
    )
    endpoint = server.get("endpoint")
    if type(endpoint) is not str or _LOOPBACK_ENDPOINT.fullmatch(endpoint) is None:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROVENANCE_MISMATCH,
            "Managed server endpoint is not exact loopback HTTP.",
            path="invocation.local_gpu_proof.server.endpoint",
        )
    server_argv = _array(server.get("argv"), "server arguments")
    stable_options = {
        "--dtype": profile.dtype,
        "--load-format": "safetensors",
        "--generation-config": "vllm",
        "--model-impl": profile.engine,
        "--max-model-len": str(profile.max_model_len),
        "--gpu-memory-utilization": profile.gpu_memory_utilization,
        "--tensor-parallel-size": str(profile.tensor_parallel_size),
    }
    for option, expected in stable_options.items():
        index = [index for index, item in enumerate(server_argv) if item == option]
        if len(index) != 1 or index[0] + 1 >= len(server_argv):
            _profile_reject(
                ManagedEvidenceProfileErrorCode.PROFILE_FACTS_MISSING,
                f"Managed server option {option} is missing or ambiguous.",
                path=f"invocation.local_gpu_proof.server.argv.{option}",
            )
        if server_argv[index[0] + 1] != expected:
            _profile_reject(
                ManagedEvidenceProfileErrorCode.PROFILE_FACT_MISMATCH,
                f"Managed server option {option} does not match the profile.",
                path=f"invocation.local_gpu_proof.server.argv.{option}",
            )


def _require_metric_definition(
    definitions: Mapping[str, Any],
    profile: ManagedEvidenceProfile,
) -> None:
    raw_definitions = _array(definitions.get("definitions"), "metric definitions")
    matching = [
        _object(item, "metric definition")
        for item in raw_definitions
        if _object(item, "metric definition").get("definition_id")
        == profile.metric_definition_id
    ]
    if len(matching) != 1:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.METRIC_SEMANTICS_MISMATCH,
            "Managed metric definition is missing or ambiguous.",
            path="definitions.metrics",
        )
    definition = matching[0]
    _value(
        definition.get("metric"),
        "ttft_ns",
        ManagedEvidenceProfileErrorCode.METRIC_SEMANTICS_MISMATCH,
        "Managed metric identity does not match the profile.",
        "definitions.metrics.vllm_first_choices_event_v0_26.metric",
    )
    _value(
        definition.get("population"),
        profile.metric_population,
        ManagedEvidenceProfileErrorCode.METRIC_POPULATION_MISMATCH,
        "Managed metric population does not match the profile.",
        "definitions.metrics.vllm_first_choices_event_v0_26.population",
    )
    _value(
        definition.get("quantile_method"),
        profile.metric_reducer_id,
        ManagedEvidenceProfileErrorCode.METRIC_REDUCER_MISMATCH,
        "Managed metric reducer does not match the profile.",
        "definitions.metrics.vllm_first_choices_event_v0_26.quantile_method",
    )
    _value(
        definition.get("unit"),
        profile.metric_unit,
        ManagedEvidenceProfileErrorCode.METRIC_UNITS_MISMATCH,
        "Managed metric unit does not match the profile.",
        "definitions.metrics.vllm_first_choices_event_v0_26.unit",
    )
    _value(
        definition.get("allowed_aggregations"),
        ["mean", "p50", "p95", "p99"],
        ManagedEvidenceProfileErrorCode.METRIC_SEMANTICS_MISMATCH,
        "Managed metric aggregation set does not match the profile.",
        "definitions.metrics.vllm_first_choices_event_v0_26.allowed_aggregations",
    )


def _environment_value(
    fields: Mapping[str, Mapping[str, Any]],
    name: str,
    expected: object,
    expected_provenance: str,
) -> None:
    field = fields.get(name)
    if field is None:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.ENVIRONMENT_MISMATCH,
            f"Managed environment field {name} is missing.",
            path=f"environment.fields.{name}",
        )
    if field.get("value") != expected or field.get("provenance") != expected_provenance:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.ENVIRONMENT_MISMATCH,
            f"Managed environment field {name} does not match the profile.",
            path=f"environment.fields.{name}",
        )


def _exact_mapping(actual: object, expected: Mapping[str, Any], path: str) -> None:
    if type(actual) is not dict:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_FACTS_MISSING,
            f"Exact managed profile facts are missing at {path}.",
            path=path,
        )
    actual_keys = set(actual)
    expected_keys = set(expected)
    if expected_keys - actual_keys:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_FACTS_MISSING,
            f"Managed profile facts are missing at {path}.",
            path=path,
        )
    if actual_keys - expected_keys:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_FACTS_EXTRA,
            f"Managed profile facts contain unsupported fields at {path}.",
            path=path,
        )
    if not _strict_equal(actual, expected):
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_FACT_MISMATCH,
            f"Managed profile facts do not match at {path}.",
            path=path,
        )


def _exact_values(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    path: str,
    *,
    category: ManagedEvidenceProfileErrorCode,
    allow_extra: bool = False,
) -> None:
    for name, expected_value in expected.items():
        if name not in actual:
            _profile_reject(
                ManagedEvidenceProfileErrorCode.PROFILE_FACTS_MISSING,
                f"Managed profile fact {path}.{name} is missing.",
                path=f"{path}.{name}",
            )
        if not _strict_equal(actual[name], expected_value):
            _profile_reject(
                category,
                f"Managed profile fact {path}.{name} does not match.",
                path=f"{path}.{name}",
            )
    if not allow_extra and set(actual) != set(expected):
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_FACTS_EXTRA,
            f"Managed profile facts contain unsupported fields at {path}.",
            path=path,
        )


def _value(
    actual: object,
    expected: object,
    code: ManagedEvidenceProfileErrorCode,
    message: str,
    path: str,
) -> None:
    if actual is _MISSING or actual is None:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_FACTS_MISSING,
            message,
            path=path,
        )
    if not _strict_equal(actual, expected):
        _profile_reject(code, message, path=path)


def _strict_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return set(actual) == set(expected) and all(
            _strict_equal(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _strict_equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_FACTS_MISSING,
            f"Managed {label} must be an object.",
        )
    return value


def _array(value: object, label: str) -> list[Any]:
    if type(value) is not list:
        _profile_reject(
            ManagedEvidenceProfileErrorCode.PROFILE_FACTS_MISSING,
            f"Managed {label} must be an array.",
        )
    return value


def _profile_reject(
    code: ManagedEvidenceProfileErrorCode,
    message: str,
    *,
    path: str | None = None,
) -> NoReturn:
    raise ManagedEvidenceProfileRejected(code, message, path=path)


__all__ = [
    "A100_CAMPAIGN_ID",
    "A100_HARDWARE_MODEL",
    "A100_MANAGED_PROFILE_ID",
    "A100_MANAGED_PROFILE_SHA256",
    "A100_METRIC_DEFINITION_ID",
    "A100_METRIC_POPULATION",
    "A100_METRIC_UNIT",
    "A100_MODEL_ID",
    "A100_MODEL_REVISION",
    "A100_PROFILE_ARGUMENTS",
    "A100_PROFILE_BINDING_SCHEMA_VERSION",
    "A100_QWEN3_PROFILE",
    "A100_REDUCER_ID",
    "A100_SERVER_ARGV_TEMPLATE",
    "A100_WORKLOAD_ID",
    "A100_WORKLOAD_SHA256",
    "LEGACY_A10_PROFILE",
    "MANAGED_EVIDENCE_REGISTRY_VERSION",
    "ManagedEvidenceProfile",
    "ManagedEvidenceProfileErrorCode",
    "ManagedEvidenceProfileRejected",
    "get_managed_evidence_profile",
    "managed_evidence_profiles",
    "validate_managed_evidence_profile",
    "validate_managed_profile_binding",
]
