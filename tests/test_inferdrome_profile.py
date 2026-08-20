from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

import exitspec.inferdrome_archive as archive_module
import exitspec.inferdrome_profile as profile_module
from exitspec.inferdrome_profile import (
    HANDOFF_MANIFEST_SHA256,
    LOCAL_GPU_PROOF_SCHEMA_SHA256,
    MANAGED_PROFILE_SHA256,
    PUBLICATION_REVIEW_SHA256,
    canonical_document_sha256,
    load_pinned_inferdrome_profile_documents,
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


def test_consumer_profile_and_archive_modules_import_no_inferdrome_runtime():
    for module in (profile_module, archive_module):
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
