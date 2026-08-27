"""Executable integrity checks for the v0.3 Request -> Proof constitution."""

from __future__ import annotations

import json
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "docs" / "GOLDEN_LOOP_CONTRACT.md"
MATRIX_PATH = (
    PROJECT_ROOT
    / "examples"
    / "product"
    / "request-to-proof-acceptance-v1.json"
)

REQUIRED_ROW_FIELDS = {
    "id",
    "stage",
    "statement",
    "observable",
    "failure_outcome",
    "authority_boundary",
    "current_status",
    "current_coverage",
    "target_pr",
    "blocking_for_pr_1",
    "blocking_for_v0_3",
}
ALLOWED_STATUSES = {"covered", "partial", "characterized_gap"}
EXPECTED_IDS = {"GL-{0:02d}".format(index) for index in range(1, 17)}
PRIVATE_PATH_PATTERN = re.compile(
    r"(?:^|[\s(])/(?:Users|private|var|tmp)/|(?:sk|fw)_[A-Za-z0-9_-]{10,}"
)


def _matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_contract_is_versioned_and_declares_pr1_boundary():
    contract = _matrix()

    assert contract["schema_version"] == "exitspec.request-to-proof-acceptance.v1"
    assert contract["contract_id"] == "request-to-proof-golden-loop-v0.3"
    assert contract["contract_version"] == "1.0.0"
    assert contract["status"] == "FROZEN_FOR_TRAIN"
    assert contract["release_train"] == "A"
    assert "does not claim" in contract["pr_1_statement"]
    assert CONTRACT_PATH.is_file()


def test_matrix_has_one_explicit_row_for_every_golden_loop_boundary():
    rows = _matrix()["matrix"]

    assert len(rows) == len(EXPECTED_IDS)
    assert {row["id"] for row in rows} == EXPECTED_IDS
    for row in rows:
        assert set(row) >= REQUIRED_ROW_FIELDS
        assert row["current_status"] in ALLOWED_STATUSES
        assert re.fullmatch(r"PR #[2-7]", row["target_pr"])
        assert row["blocking_for_pr_1"] is False
        assert row["blocking_for_v0_3"] is True
        assert row["current_coverage"]


def test_current_coverage_references_resolve_to_repository_tests():
    for row in _matrix()["matrix"]:
        for reference in row["current_coverage"]:
            test_path, test_name = reference.split("::", maxsplit=1)
            source = PROJECT_ROOT / test_path
            assert source.is_file(), reference
            assert re.search(
                rf"(?m)^def {re.escape(test_name)}\(",
                source.read_text(encoding="utf-8"),
            ), reference


def test_source_and_capability_vocabularies_are_explicit_and_source_agnostic():
    contract = _matrix()

    assert contract["source_model"]["stable_product_object"] == "POC"
    assert set(contract["source_model"]["source_types"]) >= {
        "email",
        "meeting_transcript",
        "note",
        "document",
        "existing_contract",
    }
    assert contract["source_model"]["human_added_is_source_type"] is False
    assert set(contract["capability_outcomes"]) == {
        "EXECUTABLE",
        "EVIDENCE_IMPORT",
        "CLARIFICATION_REQUIRED",
        "UNSUPPORTED",
    }


def test_authority_and_fail_closed_contract_is_complete():
    contract = _matrix()
    authority = contract["authority"]
    rules = " ".join(contract["fail_closed_rules"])

    assert "untrusted proposal" in authority["source_and_provider"]
    assert "not" in authority["deployment"].lower()
    for marker in (
        "Malformed",
        "authority-bearing",
        "Unsupported",
        "missing",
        "successor",
        "producer-generated verdict",
    ):
        assert marker.casefold() in rules.casefold()


def test_every_gap_is_owned_and_current_gaps_are_not_mislabeled_as_covered():
    contract = _matrix()
    rows = {row["id"]: row for row in contract["matrix"]}

    for row in rows.values():
        if row["current_status"] != "covered":
            assert row["target_pr"] in contract["gap_ownership"]

    assert rows["GL-04"]["current_status"] == "characterized_gap"
    assert rows["GL-06"]["current_status"] == "characterized_gap"
    assert rows["GL-11"]["current_status"] == "partial"
    assert rows["GL-16"]["current_status"] == "partial"


def test_matrix_and_contract_contain_no_private_paths_or_fixture_secrets():
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    matrix_text = MATRIX_PATH.read_text(encoding="utf-8")

    assert PRIVATE_PATH_PATTERN.search(contract_text) is None
    assert PRIVATE_PATH_PATTERN.search(matrix_text) is None
    for value in ("customer@example.com", "api_key=", "Bearer "):
        assert value not in contract_text
        assert value not in matrix_text


def test_markdown_explains_the_same_release_constitution_as_the_matrix():
    document = CONTRACT_PATH.read_text(encoding="utf-8")

    for marker in (
        "## Decision",
        "## Normative vocabulary",
        "## Authority and fail-closed rules",
        "## Acceptance matrix",
        "## Current characterization boundary",
        "## Train A ownership",
        "## Change control and non-goals",
        "request-to-proof-golden-loop-v0.3",
        "covered",
        "partial",
        "characterized_gap",
        "It is not deployment",
        "routing qualification",
    ):
        assert marker in document
