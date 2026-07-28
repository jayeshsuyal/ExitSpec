"""Integrity checks for the separate Wave 2 implementation evidence record."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = (
    PROJECT_ROOT
    / "examples/support-agent/evidence/wave-2-implementation-evidence-v1.json"
)
PACKAGED_EVIDENCE_PATH = (
    PROJECT_ROOT
    / "src/exitspec/demo_data/support_agent/evidence/"
    "wave-2-implementation-evidence-v1.json"
)
CURRENT_TEST_PATH = "tests/test_wave2_implementation_evidence.py"

EXPECTED_CONTRACT_HASHES = {
    "examples/support-agent/email/wave-2-acceptance-v1.json": (
        "aa514787eb6b14a93216682d702fc29a32d630eb1a91a16dae6ce0873a268ae2"
    ),
    "examples/support-agent/email/wave-2-source-web-v1.json": (
        "f89825510155b1d579814da0f6e3a639c1b03d3111deba170556654eaca35ffd"
    ),
}
EXPECTED_CLAIM_IDS = {
    "rfc822_source_adapter",
    "atomic_source_store",
    "fixture_catalog_endpoint",
    "strict_source_import_endpoint",
    "guided_email_ui",
    "full_agreement_to_evidence_loop",
    "powerless_authority_attack",
    "replay_reset_and_race_safety",
    "bounded_viewport_workbench",
    "evidence_pack_handoff",
    "packaging_and_ci",
}
EXPECTED_LIMITATION_IDS = {
    "live_email_connectivity",
    "arbitrary_email_or_rfc822_upload",
    "attachment_browsing_or_execution",
    "real_customer_email",
    "speech_to_text_or_audio",
    "hosted_authenticated_identity",
    "durable_multi_tenant_storage",
    "successful_live_fireworks_smoke",
    "live_hosted_measurement",
    "arbitrary_metric_execution",
    "production_authorization",
}
EXPECTED_AUTHORITY_STAGES = [
    "email_source",
    "employee_review",
    "customer_confirmation",
    "frozen_agreement",
    "measurement",
    "verdict",
    "business_authorization",
]
EXPECTED_GUIDED_STEPS = [
    "email_mode_opened",
    "catalog_rendered",
    "thread_root_imported",
    "first_root_proposal_reviewed",
    "all_root_proposals_reviewed",
    "customer_review_created",
    "customer_confirmed",
    "confirmed_contract_frozen",
    "reference_set_a_completed",
    "evidence_pack_link_rendered",
]
EXPECTED_MEASURED_STATES = {
    "choose_sample",
    "review_first_proposal",
    "draft_ready",
    "freeze_ready",
    "proof_ready",
    "evidence_pass",
}


def _payload(path: Path) -> bytes:
    return path.read_bytes()


def _load(path: Path = EVIDENCE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(_payload(path)).hexdigest()


def _top_level_test_functions(path: Path) -> set[str]:
    parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in parsed.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def test_evidence_record_is_separate_byte_identical_and_closed_scope():
    assert _payload(EVIDENCE_PATH) == _payload(PACKAGED_EVIDENCE_PATH)

    evidence = _load()
    assert set(evidence) == {
        "schema_version",
        "evidence_id",
        "evidence_version",
        "resource_namespace",
        "recorded_on",
        "status",
        "implementation",
        "contract_baseline",
        "closed_loop_authority",
        "implementation_claims",
        "browser_observation",
        "limitations",
    }
    assert evidence["schema_version"] == "1.0"
    assert evidence["evidence_id"] == (
        "exitspec-wave-2-synthetic-email-implementation"
    )
    assert evidence["evidence_version"] == "1.0.0"
    assert evidence["resource_namespace"] == "post_implementation_evidence"
    assert evidence["recorded_on"] == "2026-07-27"
    assert evidence["status"] == "IMPLEMENTED_WITH_SYNTHETIC_EVIDENCE"

    implementation = evidence["implementation"]
    assert re.fullmatch(r"[0-9a-f]{40}", implementation["commit"])
    assert implementation["commit"] == (
        "5b4c837ad385d9dc5105783a4a7afcf013bd2489"
    )
    assert "manifest-approved synthetic RFC822" in implementation["scope"]
    assert implementation["external_provider_required"] is False
    assert implementation["frozen_contracts_mutated"] is False


def test_frozen_contract_hashes_and_historical_status_remain_unchanged():
    evidence = _load()
    baselines = {
        item["path"]: item for item in evidence["contract_baseline"]
    }
    assert set(baselines) == set(EXPECTED_CONTRACT_HASHES)

    for path_text, expected_hash in EXPECTED_CONTRACT_HASHES.items():
        baseline = baselines[path_text]
        authoritative = PROJECT_ROOT / path_text
        packaged = PROJECT_ROOT / baseline["packaged_path"]
        assert baseline["sha256"] == expected_hash
        assert _sha256(authoritative) == expected_hash
        assert _payload(authoritative) == _payload(packaged)
        assert baseline["status"] == "FROZEN"
        assert baseline["mutation_policy"] == "immutable historical contract"

    acceptance = json.loads(
        (
            PROJECT_ROOT
            / "examples/support-agent/email/wave-2-acceptance-v1.json"
        ).read_text(encoding="utf-8")
    )
    assert acceptance["status"] == "FROZEN"

    web_contract = json.loads(
        (
            PROJECT_ROOT
            / "examples/support-agent/email/wave-2-source-web-v1.json"
        ).read_text(encoding="utf-8")
    )
    assert web_contract["status"] == "FROZEN"
    assert web_contract["implementation_status"] == {
        "contract_only": True,
        "fixture_catalog_endpoint_implemented": False,
        "source_import_endpoint_implemented": False,
        "email_intake_ui_implemented": False,
        "existing_review_confirmation_freeze_prove_flow_implemented": True,
        "implementation_must_not_be_inferred_from_this_contract": True,
    }
    source_baseline = baselines[
        "examples/support-agent/email/wave-2-source-web-v1.json"
    ]
    assert (
        source_baseline["historical_status"]
        == web_contract["implementation_status"]
    )


def test_every_code_and_test_reference_resolves_without_circular_proof():
    evidence = _load()
    all_test_refs: list[str] = []

    for claim in evidence["implementation_claims"]:
        assert claim["code_refs"]
        assert claim["test_refs"]
        for path_text in claim["code_refs"]:
            path = PROJECT_ROOT / path_text
            assert path.is_file(), f"Missing code reference: {path_text}"
        for reference in claim["test_refs"]:
            path_text, separator, function_name = reference.partition("::")
            assert separator == "::"
            assert path_text != CURRENT_TEST_PATH, (
                "The evidence-integrity test cannot prove runtime behavior."
            )
            path = PROJECT_ROOT / path_text
            assert path.is_file(), f"Missing test reference: {path_text}"
            assert function_name in _top_level_test_functions(path), (
                f"Missing referenced test function: {reference}"
            )
            all_test_refs.append(reference)

    assert len(set(all_test_refs)) >= len(EXPECTED_CLAIM_IDS) * 2


def test_claim_limit_and_authority_inventories_are_exact():
    evidence = _load()

    claims = evidence["implementation_claims"]
    assert {claim["claim_id"] for claim in claims} == EXPECTED_CLAIM_IDS
    assert all(
        claim["status"]
        in {
            "PROVEN_BY_REFERENCED_TESTS",
            "EXECUTABLE_TEST_AND_BROWSER_OBSERVATION",
            "PROVEN_BY_REFERENCED_TESTS_AND_BROWSER_OBSERVATION",
            "CONTRACT_TESTED_AND_BROWSER_OBSERVED",
        }
        for claim in claims
    )

    limitations = evidence["limitations"]
    assert {
        limitation["limitation_id"] for limitation in limitations
    } == EXPECTED_LIMITATION_IDS
    assert all(limitation["statement"] for limitation in limitations)

    authority = evidence["closed_loop_authority"]
    assert [item["stage"] for item in authority] == EXPECTED_AUTHORITY_STAGES
    assert all(item["authority"] and item["cannot"] for item in authority)
    assert "propose" in authority[0]["authority"]
    assert "approve" in authority[0]["cannot"]
    assert "separate human" in authority[-1]["authority"]
    assert "inferred from a POC verdict" in authority[-1]["cannot"]


def test_browser_observation_is_manual_bounded_and_does_not_overclaim():
    browser = _load()["browser_observation"]
    assert browser["observation_kind"] == (
        "real_local_browser_manual_acceptance"
    )
    assert browser["observed_on"] == "2026-07-27"
    assert browser["ci_automation"] is False
    assert browser["stored_screenshot_claimed"] is False
    assert browser["persisted_browser_artifact_claimed"] is False
    assert browser["environment"]["external_provider_calls"] == 0
    assert browser["guided_steps_observed"] == EXPECTED_GUIDED_STEPS

    measured = browser["measured_app_states"]
    assert {item["state"] for item in measured} == EXPECTED_MEASURED_STATES
    assert len(measured) == 6
    for state in measured:
        assert state["viewport_width_css_px"] == 1280
        assert state["viewport_height_css_px"] == 720
        assert state["page_width_css_px"] == 1280
        assert state["page_height_css_px"] == 720
        assert state["body_level_scroll"] is False

    document_surfaces = browser["document_surfaces"]
    assert set(document_surfaces) == {"customer_review", "evidence_pack"}
    for surface in document_surfaces.values():
        assert surface["document_scroll_expected"] is True
        assert surface["page_height_css_px"] > surface["viewport_height_css_px"]
    assert document_surfaces["evidence_pack"]["artifact_link_count"] == 6
    assert (
        document_surfaces["evidence_pack"]["authorization_boundary_visible"]
        is True
    )

    result = browser["reference_a_result"]
    assert result == {
        "verdict": "PASS",
        "required_rate": "0.9500",
        "success_count": 197,
        "sample_count": 200,
        "observed_rate": "0.9850",
        "wilson_lower_bound": "0.9568",
    }

    attack = browser["authority_attack"]
    assert attack["fixture_case_id"] == "authority-attack"
    assert attack["candidate_count"] == 1
    assert attack["candidate_state"] == "NEEDS_REVIEW"
    assert attack["approved_criterion_count"] == 0
    assert attack["visible_authority_actions"] == []
    assert all(
        attack[key] is False
        for key in (
            "contract_created",
            "ready_to_freeze",
            "ready_to_prove",
            "proof_pack_created",
        )
    )
    assert attack["provider_calls"] == 0

    replay = browser["duplicate_replay"]
    assert replay == {
        "outcome_code": "duplicate_replay",
        "source_version": 1,
        "new_source_version_count": 0,
        "new_candidate_count": 0,
        "existing_reviews_preserved": True,
    }

    responsive = browser["responsive_observation"]
    assert responsive["viewport_width_css_px"] == 768
    assert responsive["viewport_height_css_px"] == 900
    assert responsive["page_width_css_px"] == 768
    assert responsive["horizontal_overflow"] is False
    assert responsive["bounded_scroll_containers"] == [
        "source-drawer",
        "source-card",
    ]
    assert set(browser["browser_console_errors"]) == {
        "owner_workbench",
        "customer_review",
        "evidence_pack",
        "ordinary_workbench",
    }
    assert all(
        count == 0 for count in browser["browser_console_errors"].values()
    )
