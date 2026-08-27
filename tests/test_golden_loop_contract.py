"""Executable integrity checks for the frozen Train A A1 constitution."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "docs" / "GOLDEN_LOOP_CONTRACT.md"
MATRIX_PATH = (
    PROJECT_ROOT
    / "examples"
    / "product"
    / "request-to-proof-acceptance-v1.json"
)
ORDINAL_FREE_ARTIFACTS = (
    CONTRACT_PATH,
    MATRIX_PATH,
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "docs" / "ARCHITECTURE.md",
    Path(__file__),
    PROJECT_ROOT / "tests" / "test_golden_loop_characterization.py",
)

EXPECTED_TOP_LEVEL_FIELDS = {
    "schema_version",
    "contract_id",
    "contract_version",
    "status",
    "release_train",
    "release_target",
    "decision",
    "a1_statement",
    "source_model",
    "run_statuses",
    "evidence_presentation_states",
    "ingestion_dispositions",
    "acceptance_verdicts",
    "claim_scopes",
    "capability_outcomes",
    "agreement_states",
    "mixed_claim_policy",
    "state_model",
    "authority",
    "fail_closed_rules",
    "matrix",
    "preserved_semantics",
    "gap_ownership",
    "out_of_scope_for_a1",
}
EXPECTED_SOURCE_MODEL_FIELDS = {
    "stable_product_object",
    "source_types",
    "domain_source_kinds",
    "human_added_is_source_type",
    "source_is_authority",
    "source_may_approve",
    "source_may_confirm",
    "source_may_freeze",
    "source_may_execute",
    "source_may_issue_evidence",
    "source_may_issue_verdict",
    "new_source_default",
    "input_aliases",
    "notes_compatibility",
}
EXPECTED_STATE_MODEL_FIELDS = {
    "run_status",
    "evidence_presentation",
    "ingestion_disposition",
    "acceptance_verdict",
}
EXPECTED_MIXED_POLICY_FIELDS = {
    "retained_claim_visibility",
    "clarification_required_must_have",
    "unsupported_claim",
    "overall_acceptance_reduction",
    "a1_is_policy_only",
    "implementation_owner",
}
EXPECTED_REDUCTION_FIELDS = {
    "all_frozen_must_have_pass",
    "any_must_have_fail",
    "absent_or_insufficient_supported_evidence",
    "external_operational_blocker",
}
EXPECTED_AUTHORITY_FIELDS = {
    "source_and_provider",
    "internal_human",
    "customer",
    "contract_service",
    "approved_adapter",
    "exitspec_verifier",
    "deployment",
    "identity_assurance",
}
EXPECTED_ROW_FIELDS = {
    "id",
    "stage",
    "statement",
    "observable",
    "failure_outcome",
    "authority_boundary",
    "a1_baseline_status",
    "a1_baseline_coverage",
    "target_train_slice",
    "blocking_for_a1",
    "blocking_for_v0_3",
}
EXPECTED_IDS = [f"GL-{index:02d}" for index in range(1, 17)]
ALLOWED_STATUSES = {
    "covered",
    "partial",
    "characterized_gap",
    "unverified_gap",
}
EXPECTED_SOURCE_TYPES = [
    "email",
    "meeting_transcript",
    "document",
    "existing_contract",
]
EXPECTED_RUN_STATUSES = [
    "QUEUED",
    "VALIDATING",
    "RUNNING",
    "AGGREGATING",
    "COMPLETED",
    "BLOCKED",
    "FAILED_INTERNAL",
    "CANCELLED",
]
EXPECTED_EVIDENCE_PRESENTATION_STATES = [
    "NOT_RUN",
    "PASS",
    "FAIL",
    "BLOCKED",
    "NOT_PROVEN",
]
EXPECTED_INGESTION_DISPOSITIONS = ["ADMITTED", "INGESTION_REJECTED"]
EXPECTED_ACCEPTANCE_VERDICTS = ["PASS", "FAIL", "BLOCKED", "NOT_PROVEN"]
EXPECTED_CAPABILITY_OUTCOMES = [
    "EXECUTABLE",
    "EVIDENCE_IMPORT",
    "CLARIFICATION_REQUIRED",
    "UNSUPPORTED",
]
REFERENCE_PATTERN = re.compile(
    r"^tests/[A-Za-z0-9_/-]+\.py::test_[a-z0-9_]+$"
)
PRIVATE_LITERAL_PATTERN = re.compile(
    r"(?:^|[\s(])/(?:Users|private|var|tmp)/|(?:sk|fw)_[A-Za-z0-9_-]{10,}"
)
_ORDINAL_MARKER = "P" + "R #"
_ORDINAL_PLURAL_MARKER = "P" + "Rs #"
ORDINAL_TRAIN_PATTERN = re.compile(
    r"\b"
    + re.escape(_ORDINAL_MARKER)
    + r"[1-7]\b|\b"
    + re.escape(_ORDINAL_PLURAL_MARKER)
    + r"[1-7]\b"
)
MAX_STRING_LENGTH = 2_000
EXPECTED_MATRIX_SHA256 = (
    "78509332c92b17bc2a68a18263a67b67e722aefb963daa87dcd51e7fa44086f4"
)


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value):
    raise ValueError(f"non-finite JSON number: {value}")


def _strict_loads(raw: str) -> dict:
    value = json.loads(
        raw,
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=_reject_non_finite,
    )
    if not isinstance(value, dict):
        raise ValueError("acceptance contract must be a JSON object")
    return value


def _matrix() -> dict:
    return _strict_loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _assert_bounded_non_empty_strings(value, path="contract"):
    if isinstance(value, str):
        assert 0 < len(value) <= MAX_STRING_LENGTH, path
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_bounded_non_empty_strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_bounded_non_empty_strings(item, f"{path}.{key}")


def _assert_contract_shape(contract: dict) -> None:
    assert set(contract) == EXPECTED_TOP_LEVEL_FIELDS
    assert set(contract["source_model"]) == EXPECTED_SOURCE_MODEL_FIELDS
    assert set(contract["state_model"]) == EXPECTED_STATE_MODEL_FIELDS
    assert set(contract["mixed_claim_policy"]) == EXPECTED_MIXED_POLICY_FIELDS
    assert set(
        contract["mixed_claim_policy"]["overall_acceptance_reduction"]
    ) == EXPECTED_REDUCTION_FIELDS
    assert set(contract["authority"]) == EXPECTED_AUTHORITY_FIELDS
    for row in contract["matrix"]:
        assert set(row) == EXPECTED_ROW_FIELDS


def test_contract_is_strictly_loaded_versioned_and_declares_a1_boundary():
    contract = _matrix()

    _assert_contract_shape(contract)
    assert contract["schema_version"] == "exitspec.request-to-proof-acceptance.v1"
    assert contract["contract_id"] == "request-to-proof-golden-loop-v0.3"
    assert contract["contract_version"] == "1.0.0"
    assert contract["status"] == "FROZEN_FOR_TRAIN"
    assert contract["release_train"] == "A"
    assert "does not claim" in contract["a1_statement"]
    assert CONTRACT_PATH.is_file()
    _assert_bounded_non_empty_strings(contract)


def test_frozen_matrix_bytes_are_sha256_pinned():
    assert hashlib.sha256(MATRIX_PATH.read_bytes()).hexdigest() == (
        EXPECTED_MATRIX_SHA256
    )


def test_strict_loader_rejects_duplicates_and_non_finite_numbers():
    with pytest.raises(ValueError, match="duplicate"):
        _strict_loads('{"schema_version":"v1","schema_version":"v2"}')
    with pytest.raises(ValueError, match="non-finite"):
        _strict_loads('{"schema_version":"v1","value":NaN}')
    with pytest.raises(ValueError, match="non-finite"):
        _strict_loads('{"schema_version":"v1","value":Infinity}')


def test_matrix_rejects_unknown_or_additive_fields_under_the_same_schema_version():
    contract = _matrix()

    top_level_extra = deepcopy(contract)
    top_level_extra["future_field"] = "must version first"
    with pytest.raises(AssertionError):
        _assert_contract_shape(top_level_extra)

    row_extra = deepcopy(contract)
    row_extra["matrix"][0]["future_field"] = "must version first"
    with pytest.raises(AssertionError):
        _assert_contract_shape(row_extra)


def test_matrix_has_canonical_rows_exact_fields_and_a1_baseline_statuses():
    contract = _matrix()
    rows = contract["matrix"]

    assert len(rows) == len(EXPECTED_IDS)
    assert [row["id"] for row in rows] == EXPECTED_IDS
    assert len({row["id"] for row in rows}) == len(EXPECTED_IDS)
    for row in rows:
        assert row["a1_baseline_status"] in ALLOWED_STATUSES
        assert row["target_train_slice"] in {f"A{index}" for index in range(2, 8)}
        assert row["blocking_for_a1"] is False
        assert row["blocking_for_v0_3"] is True
        if row["a1_baseline_status"] == "unverified_gap":
            assert row["a1_baseline_coverage"] == []
        else:
            assert row["a1_baseline_coverage"]


def test_a1_trace_anchors_resolve_to_named_test_functions_only():
    """Name resolution is an A1 trace check, not release-closure evidence."""
    all_references = [
        reference
        for row in _matrix()["matrix"]
        for reference in row["a1_baseline_coverage"]
    ]
    assert len(all_references) == len(set(all_references))
    for row in _matrix()["matrix"]:
        references = row["a1_baseline_coverage"]
        assert len(references) == len(set(references))
        for reference in references:
            assert REFERENCE_PATTERN.fullmatch(reference), reference
            test_path, test_name = reference.split("::", maxsplit=1)
            source = PROJECT_ROOT / test_path
            assert source.is_file(), reference
            assert re.search(
                rf"(?m)^def {re.escape(test_name)}\(",
                source.read_text(encoding="utf-8"),
            ), reference


def test_source_state_and_capability_vocabularies_are_exact():
    contract = _matrix()
    source_model = contract["source_model"]

    assert source_model["stable_product_object"] == "POC"
    assert source_model["source_types"] == EXPECTED_SOURCE_TYPES
    assert source_model["domain_source_kinds"] == [
        "EMAIL",
        "MEETING",
        "DOCUMENT",
        "EXISTING_CONTRACT",
    ]
    assert set(source_model["input_aliases"]) == {"notes"}
    assert source_model["input_aliases"] == {"notes": "DOCUMENT"}
    assert "note" not in source_model["source_types"]
    assert source_model["human_added_is_source_type"] is False
    assert source_model["source_is_authority"] is False
    assert source_model["source_may_approve"] is False
    assert source_model["source_may_confirm"] is False
    assert source_model["source_may_freeze"] is False
    assert source_model["source_may_execute"] is False
    assert source_model["source_may_issue_evidence"] is False
    assert source_model["source_may_issue_verdict"] is False
    assert contract["run_statuses"] == EXPECTED_RUN_STATUSES
    assert contract["evidence_presentation_states"] == (
        EXPECTED_EVIDENCE_PRESENTATION_STATES
    )
    assert contract["ingestion_dispositions"] == EXPECTED_INGESTION_DISPOSITIONS
    assert contract["acceptance_verdicts"] == EXPECTED_ACCEPTANCE_VERDICTS
    assert "RUNNING" not in contract["acceptance_verdicts"]
    assert "INGESTION_REJECTED" not in contract["acceptance_verdicts"]
    assert "INGESTION_REJECTED" not in contract["evidence_presentation_states"]
    assert contract["claim_scopes"] == ["MUST_HAVE", "ADVISORY"]
    assert contract["capability_outcomes"] == EXPECTED_CAPABILITY_OUTCOMES
    assert contract["agreement_states"] == [
        "DRAFT",
        "IN_REVIEW",
        "APPROVED",
        "FROZEN",
        "SUPERSEDED",
    ]


def test_state_separation_and_mixed_claim_policy_are_exact():
    contract = _matrix()
    assert contract["state_model"] == {
        "run_status": "RunStatus/operation state only. RUNNING describes an in-flight operation and is never an evidence presentation state or acceptance verdict.",
        "evidence_presentation": "Workspace/presentation state only: NOT_RUN or one of the terminal acceptance verdict values. It does not add a verdict value.",
        "ingestion_disposition": "External-evidence trust-boundary outcome: ADMITTED reaches acceptance evaluation; INGESTION_REJECTED means invalid, corrupt, unsafe, unsupported, or incompatible evidence with no acceptance verdict.",
        "acceptance_verdict": "ExitSpec's independent acceptance result, exactly PASS, FAIL, BLOCKED, or NOT_PROVEN. A valid recognized profile that is insufficient or inapplicable is admitted and may become NOT_PROVEN.",
    }
    policy = contract["mixed_claim_policy"]
    assert policy["a1_is_policy_only"] is True
    assert policy["implementation_owner"] == "A6"
    assert "one planner disposition" in policy["retained_claim_visibility"]
    assert "MUST_HAVE" in policy["retained_claim_visibility"]
    assert "ADVISORY" in policy["retained_claim_visibility"]
    assert "blocks customer-ready confirmation and freeze" in policy[
        "clarification_required_must_have"
    ]
    assert "cannot enter an executable criterion" in policy["unsupported_claim"]
    assert policy["overall_acceptance_reduction"] == {
        "all_frozen_must_have_pass": "PASS",
        "any_must_have_fail": "FAIL",
        "absent_or_insufficient_supported_evidence": "NOT_PROVEN",
        "external_operational_blocker": "BLOCKED",
    }


def test_authority_assignments_and_fail_closed_rules_are_exact():
    contract = _matrix()
    assert contract["authority"] == {
        "source_and_provider": "May normalize bounded material and propose source-linked facts; all output remains untrusted proposal material.",
        "internal_human": "May review, retain, reject, or correct a proposal and define a criterion with rationale.",
        "customer": "May confirm the exact visible agreement version or request changes.",
        "contract_service": "May validate lifecycle invariants and freeze only the exactly confirmed contract.",
        "approved_adapter": "May execute an approved plan or import evidence and return typed facts, artifacts, and declared provenance.",
        "exitspec_verifier": "May independently validate evidence, enforce declared provenance and integrity bindings, and calculate the typed acceptance verdict.",
        "deployment": "Remains outside ExitSpec; PASS is not production, procurement, spend, traffic, or security authorization.",
        "identity_assurance": "Process-local reviewer and confirmer labels establish the recorded decision boundary only; they are not authenticated person or organization identity proof. Hosted identity is outside v0.3.",
    }
    rules = set(contract["fail_closed_rules"])
    assert (
        "External evidence that is invalid, corrupt, unsafe, unsupported, or incompatible is INGESTION_REJECTED with no acceptance verdict; it is not relabeled NOT_PROVEN."
        in rules
    )
    assert (
        "A valid recognized external evidence profile that is insufficient or inapplicable is admitted for acceptance evaluation and may become NOT_PROVEN; it is never substituted into a different criterion."
        in rules
    )
    assert (
        "A producer-generated verdict is evidence input only and is never accepted as ExitSpec's verdict."
        in rules
    )


def test_a1_baseline_gaps_are_conservatively_labeled_and_owned():
    contract = _matrix()
    rows = {row["id"]: row for row in contract["matrix"]}

    for row in rows.values():
        if row["a1_baseline_status"] != "covered":
            assert row["target_train_slice"] in contract["gap_ownership"]

    assert rows["GL-01"]["a1_baseline_status"] == "partial"
    assert rows["GL-02"]["a1_baseline_status"] == "partial"
    assert rows["GL-04"]["a1_baseline_status"] == "characterized_gap"
    assert rows["GL-05"]["a1_baseline_status"] == "partial"
    assert rows["GL-06"]["a1_baseline_status"] == "characterized_gap"
    assert rows["GL-10"]["a1_baseline_status"] == "partial"
    assert rows["GL-11"]["a1_baseline_status"] == "partial"
    assert rows["GL-12"]["a1_baseline_status"] == "partial"
    assert rows["GL-13"]["a1_baseline_status"] == "partial"
    assert rows["GL-14"]["a1_baseline_status"] == "unverified_gap"
    assert rows["GL-14"]["a1_baseline_coverage"] == []
    assert rows["GL-16"]["a1_baseline_status"] == "partial"


def test_frozen_artifacts_have_no_private_fixture_literals_or_secret_like_values():
    """This is a narrow literal guard, not a complete secret scanner."""
    for path in ORDINAL_FREE_ARTIFACTS[:2]:
        text = path.read_text(encoding="utf-8")
        assert PRIVATE_LITERAL_PATTERN.search(text) is None
        for value in ("customer@example.com", "api_key=", "Bearer "):
            assert value not in text


def test_markdown_matrix_parity_is_exact_for_ids_statuses_and_owners():
    contract = _matrix()
    rows_by_id = {row["id"]: row for row in contract["matrix"]}
    markdown_rows = []
    in_table = False
    for line in CONTRACT_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("| ID | Statement | A1 baseline status | Owner |"):
            in_table = True
            continue
        if in_table and line.startswith("| ---"):
            continue
        if in_table and line.startswith("| GL-"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            assert len(cells) == 4
            markdown_rows.append((cells[0], cells[2], cells[3]))
        elif in_table:
            break

    assert [row[0] for row in markdown_rows] == EXPECTED_IDS
    assert len(markdown_rows) == len(EXPECTED_IDS)
    for row_id, status, owner in markdown_rows:
        row = rows_by_id[row_id]
        assert status == row["a1_baseline_status"]
        assert owner == row["target_train_slice"]


def test_train_slice_ordinals_do_not_drift_into_permanent_artifacts():
    for path in ORDINAL_FREE_ARTIFACTS:
        assert ORDINAL_TRAIN_PATTERN.search(path.read_text(encoding="utf-8")) is None


def test_preserved_semantics_and_scope_are_explicit():
    contract = _matrix()
    semantics = set(contract["preserved_semantics"])
    assert (
        "RUNNING remains a RunStatus/operation state and never becomes an evidence presentation state or acceptance verdict."
        in semantics
    )
    assert (
        "INGESTION_REJECTED is an ingestion disposition with no acceptance verdict; admitted but insufficient or inapplicable exact-profile evidence may become NOT_PROVEN."
        in semantics
    )
    assert (
        "A Wilson lower bound remains a Wilson lower bound; it is not renamed or described as a confidence interval by this contract."
        in semantics
    )
    assert "A1" not in " ".join(contract["out_of_scope_for_a1"])
