"""PR7 acceptance and adversarial coverage for the Zoom-to-POC spine."""

from __future__ import annotations

import json
import time

from exitspec.reference_inference import (
    REFERENCE_ENDPOINT_CLASS,
    REFERENCE_ENDPOINT_PATH,
    REFERENCE_MODEL,
)
from tests.test_poc_performance_lifecycle_web_transport import (
    _request,
    _review_api_path,
    _running_server,
)


def _create_zoom_poc(server) -> str:
    status, payload, _ = _request(
        server,
        "POST",
        "/api/pocs",
        payload={
            "display_name": "Zoom RTMS acceptance POC",
            "customer_label": "Northstar (synthetic)",
            "use_case": "Turn one authorized meeting into bounded proof.",
            "owner": "field_engineer",
            "first_source_choice": "MEETING",
            "idempotency_key": "create-zoom-pr7-e2e",
        },
    )
    assert status == 201
    assert isinstance(payload, dict)
    return str(payload["poc_id"])


def _run_zoom_handoff(server, poc_id: str) -> list[dict]:
    root = f"/api/pocs/{poc_id}/zoom-handoff"
    disclosure = _request(
        server,
        "GET",
        root + "-disclosure",
        content_type=None,
        origin=None,
    )
    assert disclosure[0] == 200
    assert disclosure[1]["provider_connected"] is False
    assert disclosure[1]["live_network"] is False

    started = _request(
        server,
        "POST",
        root,
        payload={
            "action": "start",
            "consent_acknowledged": True,
            "idempotency_key": "zoom-pr7-start",
        },
    )
    start_replay = _request(
        server,
        "POST",
        root,
        payload={
            "action": "start",
            "consent_acknowledged": True,
            "idempotency_key": "zoom-pr7-start",
        },
    )
    stopped = _request(
        server,
        "POST",
        root,
        payload={
            "action": "stop",
            "idempotency_key": "zoom-pr7-stop",
        },
    )
    stop_replay = _request(
        server,
        "POST",
        root,
        payload={
            "action": "stop",
            "idempotency_key": "zoom-pr7-stop",
        },
    )
    processed = _request(
        server,
        "POST",
        root,
        payload={
            "action": "process",
            "idempotency_key": "zoom-pr7-process",
        },
    )
    process_replay = _request(
        server,
        "POST",
        root,
        payload={
            "action": "process",
            "idempotency_key": "zoom-pr7-process",
        },
    )

    assert started[0] == stopped[0] == processed[0] == 201
    assert started[1]["handoff"]["state"] == "LISTENING"
    assert stopped[1]["handoff"]["state"] == "PROCESSING"
    assert processed[1]["handoff"]["state"] == "DRAFT_READY"
    assert processed[1]["handoff"]["proposal_count"] == 2
    assert start_replay[0] == stop_replay[0] == process_replay[0] == 200
    assert start_replay[1]["idempotent_replay"] is True
    assert stop_replay[1]["idempotent_replay"] is True
    assert process_replay[1]["idempotent_replay"] is True

    for payload in (
        started[1],
        stopped[1],
        processed[1],
        start_replay[1],
        stop_replay[1],
        process_replay[1],
    ):
        handoff = payload["handoff"]
        assert handoff["source_provider"] == "ZOOM_RTMS"
        assert handoff["raw_transcript_returned_to_browser"] is False
        assert handoff["may_confirm_contract"] is False
        assert handoff["may_freeze_contract"] is False
        assert handoff["may_start_measurement"] is False
        assert handoff["may_assign_verdict"] is False

    sources = _request(
        server,
        "GET",
        f"/api/pocs/{poc_id}/sources",
        content_type=None,
        origin=None,
    )
    assert sources[0] == 200
    assert len(sources[1]["sources"]) == 1
    return [
        started[1],
        stopped[1],
        processed[1],
        process_replay[1],
    ]


def _review_and_define(server, poc_id: str) -> None:
    status, proposal_payload, _ = _request(
        server,
        "GET",
        f"/api/pocs/{poc_id}/proposals",
        content_type=None,
        origin=None,
    )
    assert status == 200
    proposals = proposal_payload["proposals"]
    assert len(proposals) == 2

    for index, proposal in enumerate(proposals):
        status, _, _ = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/proposals/{proposal['proposal_id']}/decision",
            payload={
                "decision": "KEEP_FOR_CONTRACT",
                "reviewer": "field_engineer",
                "rationale": "Keep this explicit Zoom-backed measurable requirement.",
                "idempotency_key": f"zoom-pr7-keep-{index}",
            },
        )
        assert status == 201

    ordered = sorted(
        proposals,
        key=lambda proposal: (
            "first token" not in proposal["normalized_claim"].lower(),
        ),
    )
    definitions = (
        ("TTFT_P95_MS", 500),
        ("ERROR_RATE_PERCENT", 1),
    )
    for index, (proposal, (metric, threshold)) in enumerate(
        zip(ordered, definitions, strict=True)
    ):
        status, _, _ = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/definitions",
            payload={
                "proposal_id": proposal["proposal_id"],
                "metric": metric,
                "operator": "LT",
                "threshold": threshold,
                "minimum_samples": 100,
                "concurrency": 4,
                "prompt_tokens_min": 512,
                "prompt_tokens_max": 4096,
                "output_tokens_min": 64,
                "output_tokens_max": 512,
                "reviewer": "field_engineer",
                "rationale": "Make the source-backed requirement executable.",
                "idempotency_key": f"zoom-pr7-define-{index}",
            },
        )
        assert status == 201


def _confirm_and_freeze(server, poc_id: str) -> None:
    agreement_root = f"/api/pocs/{poc_id}/agreement"
    endpoint = f"http://127.0.0.1:{server.server_port}{REFERENCE_ENDPOINT_PATH}"
    prepared = _request(
        server,
        "POST",
        agreement_root,
        payload={
            "target_provider": "ExitSpec deterministic reference",
            "endpoint_class": REFERENCE_ENDPOINT_CLASS,
            "endpoint": endpoint,
            "model": REFERENCE_MODEL,
            "evidence_method": "EXIT_SPEC_STREAMING_PROBE",
            "reviewer": "field_engineer",
            "rationale": "Bind the supported deterministic local evaluator.",
            "idempotency_key": "zoom-pr7-prepare",
        },
    )
    assert prepared[0] == 201

    agreement = _request(
        server,
        "GET",
        agreement_root,
        content_type=None,
        origin=None,
    )
    assert agreement[0] == 200
    review_url = agreement[1]["customer_review"]["review_url"]
    review_api = _review_api_path(review_url)
    customer_view = _request(
        server,
        "GET",
        review_api,
        content_type=None,
        origin=None,
    )
    assert customer_view[0] == 200
    review = customer_view[1]["review"]
    confirmed = _request(
        server,
        "POST",
        review_api + "/decision",
        payload={
            "review_id": review["review_id"],
            "contract_id": review["contract_id"],
            "contract_version": review["contract_version"],
            "decision": "CONFIRM",
            "agreement_acknowledged": True,
            "rationale": "The customer confirmed this exact Zoom-backed agreement.",
            "idempotency_key": "zoom-pr7-confirm",
        },
    )
    assert confirmed[0] == 200
    assert confirmed[1]["decision"]["decision"] == "CONFIRM"

    frozen = _request(
        server,
        "POST",
        agreement_root + "/freeze",
        payload={"idempotency_key": "zoom-pr7-freeze"},
    )
    assert frozen[0] == 201
    assert frozen[1]["frozen_contract"]["canonical_hash"]


def test_zoom_source_completes_one_human_confirmed_deterministic_evidence_pack(
    tmp_path,
):
    with _running_server(tmp_path) as server:
        poc_id = _create_zoom_poc(server)
        handoff_payloads = _run_zoom_handoff(server, poc_id)
        _review_and_define(server, poc_id)
        _confirm_and_freeze(server, poc_id)

        root = f"/api/pocs/{poc_id}/runs"
        started = _request(
            server,
            "POST",
            root,
            payload={
                "execution_acknowledged": True,
                "idempotency_key": "zoom-pr7-run",
            },
        )
        assert started[0] == 202
        latest = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = _request(
                server,
                "GET",
                root + "/latest",
                content_type=None,
                origin=None,
            )
            assert response[0] == 200
            latest = response[1]
            if latest["is_terminal"]:
                break
            time.sleep(0.01)

        assert latest is not None
        assert latest["status"] == "COMPLETED"
        assert latest["verdict"] == "PASS"
        evidence = _request(
            server,
            "GET",
            latest["evidence_pack_url"],
            content_type=None,
            origin=None,
        )
        assert evidence[0] == 200
        assert "PASS" in evidence[1]

        workspace = _request(
            server,
            "GET",
            "/api/workspace",
            content_type=None,
            origin=None,
        )
        assert workspace[0] == 200
        matching = [item for item in workspace[1]["pocs"] if item["poc_id"] == poc_id]
        assert len(matching) == 1
        assert matching[0]["next_action_code"] in {
            "RECORD_DECISION_HANDOFF",
            "RECORD_POC_CLOSURE",
            "NONE",
        }
        serialized = json.dumps(handoff_payloads + [started[1], latest]).lower()
        for forbidden in ("raw transcript", "customer data", "provider-user"):
            assert forbidden not in serialized


def test_zoom_boundary_rejects_preconsent_malformed_and_unsupported_paths(tmp_path):
    with _running_server(tmp_path) as server:
        poc_id = _create_zoom_poc(server)
        root = f"/api/pocs/{poc_id}/zoom-handoff"
        no_consent = _request(
            server,
            "POST",
            root,
            payload={
                "action": "start",
                "consent_acknowledged": False,
                "idempotency_key": "zoom-pr7-no-consent",
            },
        )
        malformed = _request(
            server,
            "POST",
            root,
            payload={
                "action": "start",
                "consent_acknowledged": True,
                "idempotency_key": "zoom-pr7-malformed",
                "transcript": "must-not-enter-the-route",
            },
        )
        assert no_consent[0] == 409
        assert no_consent[1]["code"] == "ZOOM_GUIDED_HANDOFF_CONSENT_REQUIRED"
        assert malformed[0] == 400
        assert "must-not-enter-the-route" not in json.dumps(malformed[1])

        _run_zoom_handoff(server, poc_id)
        status, proposals, _ = _request(
            server,
            "GET",
            f"/api/pocs/{poc_id}/proposals",
            content_type=None,
            origin=None,
        )
        assert status == 200
        proposal = proposals["proposals"][0]
        unsupported = _request(
            server,
            "POST",
            f"/api/pocs/{poc_id}/definitions",
            payload={
                "proposal_id": proposal["proposal_id"],
                "metric": "LATENCY_P99_MS",
                "operator": "LT",
                "threshold": 500,
                "minimum_samples": 100,
                "concurrency": 4,
                "prompt_tokens_min": 1,
                "prompt_tokens_max": 64,
                "output_tokens_min": 1,
                "output_tokens_max": 64,
                "reviewer": "field_engineer",
                "rationale": "This unsupported metric must fail closed.",
                "idempotency_key": "zoom-pr7-unsupported-metric",
            },
        )
        assert unsupported[0] == 400
        assert "LATENCY_P99_MS" not in json.dumps(unsupported[1])
