from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import exitspec.inferdrome_archive as archive_module
import exitspec.inferdrome_managed_profile as managed_profile_module
import exitspec.inferdrome_profile as profile_module
from exitspec.inferdrome_profile import (
    HANDOFF_MANIFEST_SHA256,
    LOCAL_GPU_PROOF_SCHEMA_SHA256,
    MANAGED_PROFILE_SHA256,
    PUBLICATION_REVIEW_SHA256,
    canonical_document_sha256,
    load_pinned_inferdrome_profile_documents,
)
from exitspec.inferdrome_managed_profile import (
    ManagedInferdromeProfileError,
    validate_managed_invocation_profile,
    validate_managed_local_gpu_proof,
)


FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "inferdrome" / "profiles" / "v1"
)


def test_vendored_profile_documents_match_all_canonical_producer_pins():
    documents = load_pinned_inferdrome_profile_documents()

    assert canonical_document_sha256(documents.managed_profile) == (
        MANAGED_PROFILE_SHA256
    )
    assert canonical_document_sha256(documents.local_gpu_proof_schema) == (
        LOCAL_GPU_PROOF_SCHEMA_SHA256
    )
    assert canonical_document_sha256(documents.publication_review) == (
        PUBLICATION_REVIEW_SHA256
    )
    assert canonical_document_sha256(documents.handoff_manifest) == (
        HANDOFF_MANIFEST_SHA256
    )


def test_loaded_profile_documents_are_detached_from_later_loads():
    first = load_pinned_inferdrome_profile_documents()
    first.managed_profile["profile_id"] = "mutated"

    replay = load_pinned_inferdrome_profile_documents()

    assert replay.managed_profile["profile_id"] != "mutated"


def test_vendored_handoff_preserves_external_only_acceptance_boundary():
    documents = load_pinned_inferdrome_profile_documents()
    handoff = documents.handoff_manifest

    assert handoff["fixture_delivery"]["publication_state"] == (
        "BLOCKED_PENDING_OWNER_APPROVAL"
    )
    assert handoff["contract_binding"] == {
        "chronology": "RETROSPECTIVE",
        "chronology_disclosure": (
            "A future ExitSpec contract may be frozen before evaluation, but "
            "this capture does not prove that contract preceded measurement."
        ),
        "producer_exitspec_contract_digest": None,
        "required_consumer_mode": "EXTERNAL_RECEIPT_BINDING",
    }
    assert handoff["acceptance_boundary"]["inferdrome_acceptance_verdict"] is None


def test_vendored_local_gpu_proof_conformance_vectors_match_public_schema():
    documents = load_pinned_inferdrome_profile_documents()
    validator = Draft202012Validator(
        documents.local_gpu_proof_schema,
        format_checker=FormatChecker(),
    )
    cases = json.loads((FIXTURE_ROOT / "cases.json").read_bytes())

    for case in cases:
        if case["kind"] != "local_gpu_proof":
            continue
        value = json.loads((FIXTURE_ROOT / case["fixture"]).read_bytes())
        mutation = case.get("mutation")
        if mutation is not None:
            value = _mutate(value, mutation)
        assert (not list(validator.iter_errors(value))) is case["schema_valid"]


def test_every_public_managed_profile_conformance_vector_is_replayed():
    cases = json.loads((FIXTURE_ROOT / "cases.json").read_bytes())

    for case in cases:
        value = json.loads((FIXTURE_ROOT / case["fixture"]).read_bytes())
        mutation = case.get("mutation")
        if mutation is not None:
            value = _mutate(value, mutation)
        validator = (
            validate_managed_local_gpu_proof
            if case["kind"] == "local_gpu_proof"
            else validate_managed_invocation_profile
        )
        try:
            validator(value)
        except ManagedInferdromeProfileError:
            profile_valid = False
        else:
            profile_valid = True
        assert profile_valid is case["profile_valid"], case["name"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"operation": "replace", "path": "/torch_cuda_device_count", "value": 0},
        {
            "operation": "replace",
            "path": "/gpu_query_argv/3",
            "value": "--id=1",
        },
        {
            "operation": "replace",
            "path": "/server/process_group_id",
            "value": 1201,
        },
        {
            "operation": "replace",
            "path": "/server/gpu_processes/0/process_group_id",
            "value": 1201,
        },
        {
            "operation": "replace",
            "path": "/server/ready_at",
            "value": "2026-08-20T00:03:00Z",
        },
        {
            "operation": "replace",
            "path": "/model_snapshot/kind",
            "value": "tokenizer",
        },
        {
            "operation": "replace",
            "path": "/producer_distribution/source_wheel_filename",
            "value": "wrong.whl",
        },
        {
            "operation": "replace",
            "path": "/server/environment_overrides/0",
            "value": "DO_NOT_TRACK=0",
        },
        {
            "operation": "replace",
            "path": "/server/endpoint",
            "value": "http://127.0.0.1:70000",
        },
    ],
)
def test_managed_local_gpu_semantic_constraints_fail_closed(mutation):
    value = json.loads(
        (FIXTURE_ROOT / "valid" / "local-gpu-proof.json").read_bytes()
    )

    with pytest.raises(ManagedInferdromeProfileError):
        validate_managed_local_gpu_proof(_mutate(value, mutation))


@pytest.mark.parametrize(
    "mutation",
    [
        {
            "operation": "replace",
            "path": "/server/argv/3",
            "value": "0.0.0.0",
        },
        {
            "operation": "replace",
            "path": "/tokenizer_snapshot/root",
            "value": "/opt/inferdrome/models/other",
        },
        {
            "operation": "replace",
            "path": "/server/endpoint",
            "value": "http://127.0.0.1:18081",
        },
    ],
)
def test_managed_invocation_cross_bindings_fail_closed(mutation):
    value = json.loads(
        (FIXTURE_ROOT / "valid" / "managed-vllm-invocation.json").read_bytes()
    )

    with pytest.raises(ManagedInferdromeProfileError):
        validate_managed_invocation_profile(
            _mutate(value, {**mutation, "path": "/local_gpu_proof" + mutation["path"]})
        )


def test_consumer_profile_and_archive_modules_import_no_inferdrome_runtime():
    for module in (profile_module, archive_module, managed_profile_module):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        names.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert not any(
            name == "inferdrome" or name.startswith("inferdrome.")
            for name in names
        )


def _mutate(value: object, mutation: dict[str, object]) -> object:
    result = copy.deepcopy(value)
    parts = [part for part in str(mutation["path"]).split("/") if part]
    parent = result
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    leaf = parts[-1]
    operation = mutation["operation"]
    if operation in {"add", "replace"}:
        if isinstance(parent, list):
            parent[int(leaf)] = mutation["value"]
        else:
            parent[leaf] = mutation["value"]
    elif operation == "remove":
        if isinstance(parent, list):
            parent.pop(int(leaf))
        else:
            del parent[leaf]
    else:
        raise AssertionError("unsupported fixture mutation")
    return result
