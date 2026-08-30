"""Independent validation for Inferdrome's pinned managed-vLLM profile.

The producer's JSON Schema proves shape.  This module separately replays the
profile's cross-field rules before a bundle may be treated as managed local-GPU
evidence.  It deliberately imports no Inferdrome runtime code and makes no
hardware-attestation claim.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import PurePosixPath
from typing import Any, Final, NoReturn

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from .inferdrome_profile import (
    LOCAL_GPU_PROOF_SCHEMA_ID,
    LOCAL_GPU_PROOF_SCHEMA_SHA256,
    MANAGED_PROFILE_ID,
    MANAGED_PROFILE_SHA256,
    load_pinned_inferdrome_profile_documents,
)


MANAGED_PROFILE_VALIDATOR_VERSION: Final = "1.0.0"
_DECIMAL_INTEGER: Final = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_LOOPBACK_ENDPOINT: Final = re.compile(r"http://127\.0\.0\.1:(?P<port>[0-9]{1,5})\Z")
_INVOCATION_PATH: Final = "native/invocation.json"


class ManagedInferdromeProfileError(ValueError):
    """Managed producer evidence violates its pinned consumer profile."""


@dataclass(frozen=True, slots=True)
class ManagedInferdromeProfileFacts:
    """Profile identity and internally consistent producer claims."""

    profile_id: str
    profile_sha256: str
    local_gpu_proof_schema_id: str
    local_gpu_proof_schema_sha256: str
    validator_version: str
    claims_assurance: str
    selected_gpu_indices: tuple[int, ...]
    gpu_models: tuple[str, ...]
    producer_distribution_sha256: str
    executable_path: str
    executable_sha256: str
    source_wheel_sha256: str
    model_snapshot_sha256: str
    tokenizer_snapshot_sha256: str


def validate_managed_local_gpu_proof(
    value: Mapping[str, Any],
    *,
    profile_document: Mapping[str, Any] | None = None,
) -> ManagedInferdromeProfileFacts:
    """Validate schema plus every self-contained managed-profile relation."""

    proof = _object(value, "local GPU proof")
    try:
        _local_gpu_proof_validator().validate(proof)
    except ValidationError as error:
        raise ManagedInferdromeProfileError(
            "Managed local GPU proof does not match its pinned schema."
        ) from error

    profile = (
        profile_document
        if profile_document is not None
        else load_pinned_inferdrome_profile_documents().managed_profile
    )
    selected = tuple(
        _integer(item, "selected GPU index")
        for item in _array(proof.get("selected_gpu_indices"), "selected GPU indices")
    )
    gpus = tuple(
        _object(item, "GPU inventory item")
        for item in _array(proof.get("gpus"), "GPU inventory")
    )
    if len(selected) != 1 or len(gpus) != 1:
        _invalid("Managed evidence must select exactly one GPU.")
    selected_index = selected[0]
    if selected != (_integer(gpus[0].get("index"), "GPU index"),):
        _invalid("Managed selected GPU index disagrees with inventory.")
    if _integer(proof.get("torch_cuda_device_count"), "CUDA device count") <= (
        selected_index
    ):
        _invalid("Managed selected GPU index is not visible to the runtime.")

    nvidia_smi_path = _string(proof.get("nvidia_smi_path"), "nvidia-smi path")
    inventory_query = [
        nvidia_smi_path,
        "--query-gpu=index,name,uuid,driver_version",
        "--format=csv,noheader,nounits",
        f"--id={selected_index}",
    ]
    if proof.get("gpu_query_argv") != inventory_query:
        _invalid("Managed GPU inventory query differs from the pinned profile.")
    parsed_inventory = _parse_inventory_csv(
        _string(proof.get("gpu_query_stdout"), "GPU query output")
    )
    expected_inventory = tuple(
        {
            "driver_version": _string(item.get("driver_version"), "GPU driver"),
            "index": _integer(item.get("index"), "GPU index"),
            "model": _string(item.get("model"), "GPU model"),
            "uuid": _string(item.get("uuid"), "GPU UUID"),
        }
        for item in gpus
    )
    if parsed_inventory != expected_inventory:
        _invalid("Managed GPU inventory cannot be replayed from captured CSV.")

    server = _object(proof.get("server"), "managed server proof")
    process_group_id = _integer(server.get("process_group_id"), "server process group")
    if _integer(server.get("pid"), "server PID") != process_group_id:
        _invalid("Managed server PID and process group must be identical.")
    compute_query = [
        nvidia_smi_path,
        "--query-compute-apps=pid,gpu_uuid",
        "--format=csv,noheader,nounits",
        f"--id={selected_index}",
    ]
    if server.get("compute_query_argv") != compute_query:
        _invalid("Managed compute-process query differs from the pinned profile.")
    parsed_processes = _parse_process_csv(
        _string(server.get("compute_query_stdout"), "compute-process output")
    )
    processes = tuple(
        _object(item, "GPU process")
        for item in _array(server.get("gpu_processes"), "GPU processes")
    )
    expected_processes = tuple(
        (
            _integer(item.get("pid"), "GPU process PID"),
            _string(item.get("gpu_uuid"), "GPU process UUID"),
        )
        for item in processes
    )
    if not processes or parsed_processes != expected_processes:
        _invalid("Managed GPU processes cannot be replayed from captured CSV.")
    process_ids = tuple(item[0] for item in expected_processes)
    if len(process_ids) != len(set(process_ids)):
        _invalid("Managed GPU process identities must be unique.")
    inventory_uuids = {_string(item.get("uuid"), "GPU UUID") for item in gpus}
    process_uuids = {item[1] for item in expected_processes}
    if process_uuids != inventory_uuids or any(
        _integer(item.get("process_group_id"), "GPU process group") != process_group_id
        for item in processes
    ):
        _invalid("Managed GPU process coverage disagrees with selected inventory.")

    started_at = _timestamp(server.get("started_at"), "server start")
    ready_at = _timestamp(server.get("ready_at"), "server ready time")
    captured_at = _timestamp(proof.get("captured_at"), "local proof capture")
    if not started_at <= ready_at <= captured_at:
        _invalid("Managed local proof capture chronology is invalid.")

    model_snapshot = _object(proof.get("model_snapshot"), "model snapshot")
    tokenizer_snapshot = _object(proof.get("tokenizer_snapshot"), "tokenizer snapshot")
    if (
        model_snapshot.get("kind") != "model"
        or tokenizer_snapshot.get("kind") != "tokenizer"
    ):
        _invalid("Managed snapshot kinds are inconsistent.")

    distribution = _object(proof.get("producer_distribution"), "producer distribution")
    architecture = _string(proof.get("client_arch"), "client architecture")
    policies = _object(
        profile.get("snapshot_and_distribution_policies"),
        "snapshot and distribution policies",
    )
    source_wheels = _object(policies.get("source_wheels"), "source wheel pins")
    wheel_pin = _object(source_wheels.get(architecture), "architecture wheel pin")
    wheel_filename = _string(
        distribution.get("source_wheel_filename"), "source wheel filename"
    )
    if (
        wheel_filename != wheel_pin.get("filename")
        or distribution.get("source_wheel_sha256") != wheel_pin.get("sha256")
        or PurePosixPath(
            _string(distribution.get("source_wheel_path"), "source wheel path")
        ).name
        != wheel_filename
    ):
        _invalid("Managed source wheel does not match its architecture pin.")

    boundary = _object(profile.get("server_boundary"), "server boundary")
    if server.get("environment_policy") != boundary.get(
        "environment_policy"
    ) or server.get("environment_overrides") != boundary.get("environment_overrides"):
        _invalid("Managed server environment differs from the pinned boundary.")
    _loopback_port(_string(server.get("endpoint"), "managed server endpoint"))

    return ManagedInferdromeProfileFacts(
        profile_id=MANAGED_PROFILE_ID,
        profile_sha256=MANAGED_PROFILE_SHA256,
        local_gpu_proof_schema_id=LOCAL_GPU_PROOF_SCHEMA_ID,
        local_gpu_proof_schema_sha256=LOCAL_GPU_PROOF_SCHEMA_SHA256,
        validator_version=MANAGED_PROFILE_VALIDATOR_VERSION,
        claims_assurance="INTERNAL_CONSISTENCY_ONLY",
        selected_gpu_indices=selected,
        gpu_models=tuple(_string(item.get("model"), "GPU model") for item in gpus),
        producer_distribution_sha256=_string(
            distribution.get("sha256"), "producer distribution digest"
        ),
        executable_path=_string(
            distribution.get("executable_path"), "producer executable path"
        ),
        executable_sha256=_string(
            distribution.get("executable_sha256"), "producer executable digest"
        ),
        source_wheel_sha256=_string(
            distribution.get("source_wheel_sha256"), "source wheel digest"
        ),
        model_snapshot_sha256=_string(
            model_snapshot.get("sha256"), "model snapshot digest"
        ),
        tokenizer_snapshot_sha256=_string(
            tokenizer_snapshot.get("sha256"), "tokenizer snapshot digest"
        ),
    )


def validate_managed_invocation_profile(
    value: Mapping[str, Any],
    *,
    descriptor: Mapping[str, Any] | None = None,
    resolved: Mapping[str, Any] | None = None,
    environment: Mapping[str, Any] | None = None,
    execution: Mapping[str, Any] | None = None,
    profile_document: Mapping[str, Any] | None = None,
) -> ManagedInferdromeProfileFacts:
    """Validate managed invocation internals and optional bundle bindings."""

    invocation = _object(value, "producer invocation")
    profile = (
        profile_document
        if profile_document is not None
        else load_pinned_inferdrome_profile_documents().managed_profile
    )
    invocation_contract = _object(
        profile.get("producer_invocation"), "producer invocation profile"
    )
    required_fields = set(
        _array(invocation_contract.get("required_root_fields"), "invocation fields")
    )
    if set(invocation) != required_fields or invocation.get(
        "schema_version"
    ) != invocation_contract.get("schema_version"):
        _invalid("Managed producer invocation field set is unsupported.")

    argv = tuple(
        _string(item, "producer argument")
        for item in _array(invocation.get("argv"), "producer arguments")
    )
    argv_contract = _object(invocation_contract.get("argv_contract"), "argv contract")
    max_arguments = _integer(argv_contract.get("max_arguments"), "argument limit")
    max_characters = _integer(
        argv_contract.get("max_argument_characters"), "argument length limit"
    )
    if (
        not argv
        or len(argv) > max_arguments
        or any(len(item) > max_characters for item in argv)
    ):
        _invalid("Managed producer argument vector is invalid.")

    metadata = _object(invocation.get("metadata"), "producer metadata")
    metadata_contract = _object(invocation_contract.get("metadata"), "metadata profile")
    metadata_fields = set(
        _array(metadata_contract.get("required_fields"), "metadata fields")
    )
    if set(metadata) != metadata_fields:
        _invalid("Managed producer metadata field set is unsupported.")

    proof = _object(invocation.get("local_gpu_proof"), "local GPU proof")
    facts = validate_managed_local_gpu_proof(
        proof,
        profile_document=profile,
    )
    distribution = _object(proof.get("producer_distribution"), "producer distribution")
    server = _object(proof.get("server"), "managed server proof")
    if (
        argv[0] != facts.executable_path
        or _array(server.get("argv"), "server arguments")[0] != facts.executable_path
    ):
        _invalid("Managed producer executable differs across command lines.")
    if (
        metadata.get("inferdrome_run_id") != proof.get("run_id")
        or metadata.get("inferdrome_producer_version") != distribution.get("version")
        or metadata.get("inferdrome_adapter_version") != "1.0.0"
    ):
        _invalid("Managed producer metadata disagrees with local proof.")

    target_model = _option_value(argv, "--model")
    benchmark_endpoint = _option_value(argv, "--base-url").rstrip("/")
    tokenizer_root = _string(
        _object(proof.get("tokenizer_snapshot"), "tokenizer snapshot").get("root"),
        "tokenizer snapshot root",
    )
    if _option_value(
        argv, "--tokenizer"
    ) != tokenizer_root or benchmark_endpoint != server.get("endpoint"):
        _invalid("Managed benchmark target differs from local server proof.")
    port = _loopback_port(_string(server.get("endpoint"), "managed server endpoint"))
    expected_server_argv = _expected_server_argv(
        profile,
        proof,
        port=port,
        target_model=target_model,
        seed=_option_value(argv, "--seed"),
    )
    if server.get("argv") != expected_server_argv:
        _invalid("Managed server arguments cannot be replayed from the profile.")

    metadata_index = _single_option_index(argv, "--metadata")
    expected_metadata_argv = tuple(f"{key}={metadata[key]}" for key in sorted(metadata))
    if argv[metadata_index + 1 :] != expected_metadata_argv:
        _invalid("Managed benchmark metadata arguments are not canonical.")

    contexts = (descriptor, resolved, environment, execution)
    if any(item is not None for item in contexts):
        if any(item is None for item in contexts):
            raise TypeError("Managed bundle context must be supplied completely.")
        _validate_bundle_bindings(
            invocation,
            proof,
            _object(descriptor, "bundle descriptor"),
            _object(resolved, "resolved experiment"),
            _object(environment, "environment evidence"),
            _object(execution, "execution evidence"),
        )
    return facts


def _validate_bundle_bindings(
    invocation: Mapping[str, Any],
    proof: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    resolved: Mapping[str, Any],
    environment: Mapping[str, Any],
    execution: Mapping[str, Any],
) -> None:
    metadata = _object(invocation.get("metadata"), "producer metadata")
    producer = _object(descriptor.get("producer"), "bundle producer")
    target = _object(resolved.get("target"), "resolved target")
    workload = _object(resolved.get("workload"), "resolved workload")
    model_snapshot = _object(proof.get("model_snapshot"), "model snapshot")
    tokenizer_snapshot = _object(proof.get("tokenizer_snapshot"), "tokenizer snapshot")
    server = _object(proof.get("server"), "managed server proof")
    distribution = _object(proof.get("producer_distribution"), "producer distribution")
    expected_producer = {
        "adapter": "vllm_bench_serve",
        "adapter_version": "1.0.0",
        "name": "vllm",
        "native_schema_fingerprint": (
            "sha256:3a4fdee6fe9b45ce5b42c41fd3bfc6614245a36ecfe6f94de92b59717a136abb"
        ),
        "version": "0.26.0",
    }
    if producer != expected_producer:
        _invalid("Managed bundle producer differs from the pinned profile.")
    digest_claims = _object(descriptor.get("digests"), "bundle digests")
    if (
        proof.get("run_id") != descriptor.get("run_id")
        or metadata.get("inferdrome_run_id") != descriptor.get("run_id")
        or metadata.get("inferdrome_execution_fingerprint")
        != digest_claims.get("execution_fingerprint")
        or metadata.get("inferdrome_workload_sha256") != workload.get("sha256")
        or model_snapshot.get("revision") != target.get("model_revision")
        or tokenizer_snapshot.get("revision") != target.get("tokenizer_revision")
        or distribution.get("version") != target.get("engine_version")
        or server.get("endpoint") != str(target.get("endpoint")).rstrip("/")
        or _option_value(
            tuple(_array(invocation.get("argv"), "producer arguments")), "--model"
        )
        != target.get("model")
        or _option_value(
            tuple(_array(invocation.get("argv"), "producer arguments")), "--seed"
        )
        != str(workload.get("seed"))
    ):
        _invalid("Managed local proof disagrees with frozen bundle inputs.")

    fields = tuple(
        _object(item, "environment field")
        for item in _array(environment.get("fields"), "environment fields")
    )
    by_name = {str(item.get("name")): item for item in fields}
    gpus = tuple(
        _object(item, "GPU inventory item")
        for item in _array(proof.get("gpus"), "GPU inventory")
    )
    expected_environment = {
        "client.os": (proof.get("client_os"), "CLIENT_OBSERVED"),
        "client.arch": (proof.get("client_arch"), "CLIENT_OBSERVED"),
        "client.python_version": (
            proof.get("client_python_version"),
            "CLIENT_OBSERVED",
        ),
        "producer.distribution_sha256": (
            distribution.get("sha256"),
            "LOCALLY_VERIFIED",
        ),
        "target.engine_version": (distribution.get("version"), "LOCALLY_VERIFIED"),
        "target.model_revision": (model_snapshot.get("revision"), "CONFIGURED"),
        "target.tokenizer_revision": (
            tokenizer_snapshot.get("revision"),
            "CONFIGURED",
        ),
        "gpu.model": (gpus[0].get("model"), "LOCALLY_VERIFIED"),
        "gpu.count": (len(gpus), "LOCALLY_VERIFIED"),
        "cuda.version": (proof.get("cuda_runtime_version"), "LOCALLY_VERIFIED"),
        "driver.version": (gpus[0].get("driver_version"), "LOCALLY_VERIFIED"),
    }
    if any(
        name not in by_name
        or by_name[name].get("value") != expected_value
        or by_name[name].get("provenance") != expected_provenance
        or by_name[name].get("evidence_path") != _INVOCATION_PATH
        for name, (expected_value, expected_provenance) in expected_environment.items()
    ):
        _invalid("Managed environment fields disagree with local proof.")

    proof_capture = _timestamp(proof.get("captured_at"), "local proof capture")
    execution_start = _timestamp(execution.get("started_at"), "execution start")
    execution_end = _timestamp(execution.get("ended_at"), "execution end")
    environment_capture = _timestamp(
        environment.get("captured_at"), "environment capture"
    )
    if not proof_capture <= execution_start <= execution_end <= environment_capture:
        _invalid("Managed proof and benchmark capture chronology is invalid.")


def _expected_server_argv(
    profile: Mapping[str, Any],
    proof: Mapping[str, Any],
    *,
    port: int,
    target_model: str,
    seed: str,
) -> list[str]:
    distribution = _object(proof.get("producer_distribution"), "producer distribution")
    model_snapshot = _object(proof.get("model_snapshot"), "model snapshot")
    tokenizer_snapshot = _object(proof.get("tokenizer_snapshot"), "tokenizer snapshot")
    selected = _array(proof.get("selected_gpu_indices"), "selected GPU indices")
    substitutions = {
        "{producer_distribution.executable_path}": distribution.get("executable_path"),
        "{model_snapshot.root}": model_snapshot.get("root"),
        "{resolved_target_port}": str(port),
        "{resolved_target_model}": target_model,
        "{tokenizer_snapshot.root}": tokenizer_snapshot.get("root"),
        "{resolved_workload_seed}": seed,
        "{selected_gpu_index}": str(selected[0]),
    }
    template = _array(profile.get("server_argv_template"), "server argv template")
    return [str(substitutions.get(str(item), item)) for item in template]


def _parse_inventory_csv(value: str) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for line in value.splitlines():
        if not line.strip():
            continue
        columns = tuple(part.strip() for part in line.split(","))
        if len(columns) != 4 or not _DECIMAL_INTEGER.fullmatch(columns[0]):
            _invalid("Managed GPU inventory CSV is malformed.")
        rows.append(
            {
                "driver_version": columns[3],
                "index": int(columns[0]),
                "model": columns[1],
                "uuid": columns[2],
            }
        )
    return tuple(rows)


def _parse_process_csv(value: str) -> tuple[tuple[int, str], ...]:
    rows: list[tuple[int, str]] = []
    for line in value.splitlines():
        if not line.strip():
            continue
        columns = tuple(part.strip() for part in line.split(","))
        if len(columns) != 2 or not _DECIMAL_INTEGER.fullmatch(columns[0]):
            _invalid("Managed compute-process CSV is malformed.")
        rows.append((int(columns[0]), columns[1]))
    return tuple(rows)


def _single_option_index(argv: tuple[str, ...], option: str) -> int:
    indices = tuple(index for index, item in enumerate(argv) if item == option)
    if len(indices) != 1 or indices[0] + 1 >= len(argv):
        _invalid("Managed producer option set is incomplete or ambiguous.")
    return indices[0]


def _option_value(argv: tuple[str, ...], option: str) -> str:
    return argv[_single_option_index(argv, option) + 1]


def _loopback_port(endpoint: str) -> int:
    match = _LOOPBACK_ENDPOINT.fullmatch(endpoint)
    if match is None:
        _invalid("Managed server endpoint is not exact loopback HTTP.")
    port = int(match.group("port"))
    if not 1 <= port <= 65_535:
        _invalid("Managed server endpoint port is invalid.")
    return port


def _timestamp(value: object, label: str) -> datetime:
    if type(value) is not str:
        _invalid(f"Managed {label} is invalid.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ManagedInferdromeProfileError(f"Managed {label} is invalid.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _invalid(f"Managed {label} has no timezone.")
    return parsed


@cache
def _local_gpu_proof_validator() -> Draft202012Validator:
    schema = load_pinned_inferdrome_profile_documents().local_gpu_proof_schema
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ManagedInferdromeProfileError(
            "Pinned managed local GPU proof schema is invalid."
        ) from error
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        _invalid(f"Managed {label} must be an object.")
    return value


def _array(value: object, label: str) -> list[Any]:
    if type(value) is not list:
        _invalid(f"Managed {label} must be an array.")
    return value


def _string(value: object, label: str) -> str:
    if type(value) is not str or not value:
        _invalid(f"Managed {label} must be a non-empty string.")
    return value


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        _invalid(f"Managed {label} must be an integer.")
    return value


def _invalid(message: str) -> NoReturn:
    raise ManagedInferdromeProfileError(message)


__all__ = [
    "MANAGED_PROFILE_VALIDATOR_VERSION",
    "ManagedInferdromeProfileError",
    "ManagedInferdromeProfileFacts",
    "validate_managed_invocation_profile",
    "validate_managed_local_gpu_proof",
]
