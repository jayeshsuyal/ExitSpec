from http import HTTPStatus

from exitspec.poc_evidence_web_api import handle_poc_evidence_web_api_request
from tests.test_a6_executable_orchestration import _pack_service


def test_generic_evidence_api_is_server_selected_and_replayable(tmp_path):
    service = _pack_service(tmp_path)
    target = "/api/pocs/poc_a6_executable_test/evidence"

    initial = handle_poc_evidence_web_api_request(
        method="GET",
        target=target,
        payload=None,
        runtime=service,
    )
    assert initial is not None
    assert initial.status is HTTPStatus.OK
    assert initial.payload["current"] is None

    started = handle_poc_evidence_web_api_request(
        method="POST",
        target=target,
        payload={
            "acknowledgement": True,
            "idempotency_key": "a6-api-start",
        },
        runtime=service,
    )
    assert started is not None
    assert started.status is HTTPStatus.CREATED
    assert started.payload["replayed"] is False
    attempt_id = started.payload["attempt"]["attempt_id"]

    replay = handle_poc_evidence_web_api_request(
        method="POST",
        target=target,
        payload={
            "acknowledgement": True,
            "idempotency_key": "a6-api-start",
        },
        runtime=service,
    )
    assert replay is not None
    assert replay.status is HTTPStatus.OK
    assert replay.payload["replayed"] is True
    assert replay.payload["attempt"]["attempt_id"] == attempt_id

    injected = handle_poc_evidence_web_api_request(
        method="POST",
        target=target,
        payload={
            "acknowledgement": True,
            "idempotency_key": "a6-api-injected",
            "method": "EXECUTABLE",
        },
        runtime=service,
    )
    assert injected is not None
    assert injected.status is HTTPStatus.BAD_REQUEST

    pack = handle_poc_evidence_web_api_request(
        method="GET",
        target=f"{target}/{attempt_id}/pack",
        payload=None,
        runtime=service,
    )
    assert pack is not None
    assert pack.status is HTTPStatus.OK
    assert pack.payload["attempt"]["evidence_pack_url"].endswith(
        "/decision-packet.html"
    )

    closed = handle_poc_evidence_web_api_request(
        method="POST",
        target=f"{target}/{attempt_id}/handoff",
        payload={
            "decided_by": "a6.api.customer",
            "rationale": "Review the exact current pack.",
            "idempotency_key": "a6-api-handoff",
        },
        runtime=service,
    )
    assert closed is not None
    assert closed.status is HTTPStatus.OK
    assert closed.payload["closure"]["decision"] == "HANDOFF_COMPLETED"


def test_generic_evidence_api_rejects_noncanonical_routes_and_authority_fields(tmp_path):
    service = _pack_service(tmp_path)
    query = handle_poc_evidence_web_api_request(
        method="GET",
        target="/api/pocs/poc_a6_executable_test/evidence?method=EXECUTABLE",
        payload=None,
        runtime=service,
    )
    assert query is not None
    assert query.status is HTTPStatus.BAD_REQUEST
    for target in (
        "/api/pocs/poc_a6_executable_test/evidence/../evidence",
        "/api/pocs/Poc_A6_executable_test/evidence",
    ):
        assert handle_poc_evidence_web_api_request(
            method="GET",
            target=target,
            payload=None,
            runtime=service,
        ) is None
