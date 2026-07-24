import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest

import exitspec.web as web_module
from exitspec.web import DemoSession, ExitSpecDemoServer


def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = "http://127.0.0.1:{0}".format(server.server_port)
    return server, worker, base_url


def _get_json(url: str):
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json_error(url: str, payload: dict):
    try:
        _post_json(url, payload)
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))
    raise AssertionError("Request unexpectedly succeeded.")


def _close_internal_review(base_url: str):
    state = _get_json(base_url + "/api/state")
    first, second = state["drafts"]
    _post_json(
        base_url + "/api/review",
        {
            "draft_id": first["id"],
            "decision": "APPROVE",
            "reviewer": "field_engineer",
            "rationale": "The measurable requirement is complete for customer review.",
        },
    )
    return _post_json(
        base_url + "/api/review",
        {
            "draft_id": second["id"],
            "decision": "REJECT",
            "reviewer": "field_engineer",
            "rationale": "No measurable acceptance rule was agreed for this request.",
        },
    )["state"]


def _review_api_url(base_url: str, customer_review_url: str) -> str:
    token = customer_review_url.rstrip("/").split("/")[-1]
    return "{0}/api/review/{1}".format(base_url, token)


def _confirmed_session(tmp_path: Path) -> DemoSession:
    session = DemoSession.synthetic_support_agent(output_root=tmp_path / "runs")
    first, second = session.reviewed_drafts
    session.review(
        first.id,
        "APPROVE",
        "field_engineer",
        "The measurable rule is complete.",
    )
    session.review(
        second.id,
        "REJECT",
        "field_engineer",
        "The request remains vague.",
    )
    session.create_customer_draft()
    assert session.customer_review_token
    session.record_customer_decision(
        session.customer_review_token,
        decision="CONFIRM",
        confirmer="customer_approver",
        rationale="The exact agreement is confirmed.",
        idempotency_key="serialize-confirmed-session",
    )
    return session


def test_customer_confirmation_is_required_before_freeze_and_prove(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        internally_reviewed = _close_internal_review(base_url)
        assert internally_reviewed["ready_to_prove"] is False

        status, error = _post_json_error(
            base_url + "/api/prove", {"scenario": "pass"}
        )
        assert status == 409
        assert "confirm" in error["error"].lower()

        prepared = _post_json(base_url + "/api/customer-draft", {})
        review_url = prepared["customer_review_url"]
        assert review_url.startswith("/review/")
        review_api = _review_api_url(base_url, review_url)

        customer_view = _get_json(review_api)
        assert customer_view["review"]["status"] == "PENDING"
        assert customer_view["review"]["contract_version"]
        assert customer_view["review"]["confirmation_fingerprint"]
        assert customer_view["safety"]["not_evidence"] is True
        assert customer_view["safety"]["not_production_authorization"] is True

        confirmed = _post_json(
            review_api + "/decision",
            {
                "decision": "CONFIRM",
                "confirmer": "customer_approver",
                "rationale": "These are the requirements we agreed to evaluate.",
                "idempotency_key": "confirm-support-agent-v1",
            },
        )
        confirmation_id = confirmed["confirmation"]["confirmation_id"]
        assert confirmed["confirmation"]["decision"] == "CONFIRM"

        duplicate = _post_json(
            review_api + "/decision",
            {
                "decision": "CONFIRM",
                "confirmer": "customer_approver",
                "rationale": "These are the requirements we agreed to evaluate.",
                "idempotency_key": "confirm-support-agent-v1",
            },
        )
        assert duplicate["confirmation"]["confirmation_id"] == confirmation_id

        pre_freeze = _get_json(base_url + "/api/state")
        assert pre_freeze["ready_to_freeze"] is True
        assert pre_freeze["ready_to_prove"] is False

        frozen = _post_json(base_url + "/api/freeze", {})
        assert frozen["contract"]["status"] == "FROZEN"
        assert frozen["contract"]["canonical_hash"]
        assert frozen["ready_to_freeze"] is False
        assert frozen["ready_to_prove"] is True

        proved = _post_json(base_url + "/api/prove", {"scenario": "pass"})
        assert proved["proof_pack"]["overall_verdict"] == "PASS"
        assert (
            proved["proof_pack"]["contract_hash"]
            == frozen["contract"]["canonical_hash"]
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_request_changes_cannot_freeze_and_reusing_key_for_new_decision_conflicts(
    tmp_path,
):
    server, worker, base_url = _running_server(tmp_path)
    try:
        _close_internal_review(base_url)
        prepared = _post_json(base_url + "/api/customer-draft", {})
        review_api = _review_api_url(base_url, prepared["customer_review_url"])

        changed = _post_json(
            review_api + "/decision",
            {
                "decision": "REQUEST_CHANGES",
                "confirmer": "customer_approver",
                "rationale": "The workload does not match the call.",
                "idempotency_key": "customer-decision-v1",
            },
        )
        assert changed["confirmation"]["decision"] == "REQUEST_CHANGES"

        status, error = _post_json_error(
            review_api + "/decision",
            {
                "decision": "CONFIRM",
                "confirmer": "customer_approver",
                "rationale": "Attempt to reuse the same operation key.",
                "idempotency_key": "customer-decision-v1",
            },
        )
        assert status == 409
        assert "idempotency" in error["error"].lower()

        status, error = _post_json_error(base_url + "/api/freeze", {})
        assert status == 409
        assert "cannot be frozen" in error["error"].lower()
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_request_changes_can_start_a_structured_new_contract_version(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        _close_internal_review(base_url)
        prepared = _post_json(base_url + "/api/customer-draft", {})
        old_review_api = _review_api_url(
            base_url,
            prepared["customer_review_url"],
        )
        changed = _post_json(
            old_review_api + "/decision",
            {
                "decision": "REQUEST_CHANGES",
                "confirmer": "customer_approver",
                "rationale": "Use at least 250 multilingual support cases.",
                "idempotency_key": "customer-revision-v1",
            },
        )
        assert changed["confirmation"]["decision"] == "REQUEST_CHANGES"

        revision = _post_json(base_url + "/api/revision/start", {})
        assert revision["confirmation"] is None
        assert revision["customer_review_url"] is None
        assert revision["revision_request"] == (
            "Use at least 250 multilingual support cases."
        )
        reopened = [
            draft
            for draft in revision["drafts"]
            if draft["status"] == "NEEDS_REVIEW"
        ]
        assert len(reopened) == 1
        assert reopened[0]["open_questions"]

        try:
            _get_json(old_review_api)
        except HTTPError as error:
            assert error.code in (404, 410)
        else:
            raise AssertionError("The superseded customer review link remained valid.")

        edited = _post_json(
            base_url + "/api/revision/edit",
            {
                "draft_id": reopened[0]["id"],
                "normalized_claim": (
                    "The agent selects the exact expected tool on at least 95% "
                    "of 250 fixed multilingual support cases."
                ),
                "threshold_percent": 95,
                "minimum_samples": 250,
                "workload_slice": "support-tool-selection-multilingual-v2",
            },
        )
        revised_draft = edited["revised_draft"]
        assert revised_draft["open_questions"] == []
        assert revised_draft["proposed_criterion"]["rule"]["minimum_samples"] == 250

        reviewed = _post_json(
            base_url + "/api/review",
            {
                "draft_id": revised_draft["id"],
                "decision": "APPROVE",
                "reviewer": "field_engineer",
                "rationale": "The customer-requested structured revision is complete.",
            },
        )["state"]
        assert reviewed["ready_to_prepare_customer_review"] is True
        assert reviewed["contract"]["version"] == "0.1.1"
        assert (
            reviewed["contract"]["parent_version"]
            == "support-agent-tool-selection@0.1.0"
        )

        prepared_revision = _post_json(base_url + "/api/customer-draft", {})
        revised_review = _get_json(
            _review_api_url(
                base_url,
                prepared_revision["customer_review_url"],
            )
        )
        assert revised_review["review"]["contract_version"] == "0.1.1"
        assert (
            revised_review["review"]["criteria"][0]["rule"]["minimum_samples"]
            == 250
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_reset_invalidates_customer_review_link_and_confirmation_state(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        _close_internal_review(base_url)
        prepared = _post_json(base_url + "/api/customer-draft", {})
        review_api = _review_api_url(base_url, prepared["customer_review_url"])
        _post_json(
            review_api + "/decision",
            {
                "decision": "CONFIRM",
                "confirmer": "customer_approver",
                "rationale": "Confirmed before the internal workflow changed.",
                "idempotency_key": "confirmation-before-reset",
            },
        )

        reset = _post_json(base_url + "/api/reset", {})
        assert reset["customer_review_url"] is None
        assert reset["confirmation"] is None
        assert reset["ready_to_freeze"] is False
        assert reset["ready_to_prove"] is False

        try:
            _get_json(review_api)
        except HTTPError as error:
            assert error.code in (404, 410)
        else:
            raise AssertionError("A stale customer review link remained valid.")
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_customer_review_payload_excludes_internal_review_and_raw_source(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        internal_state = _close_internal_review(base_url)
        prepared = _post_json(base_url + "/api/customer-draft", {})
        review_api = _review_api_url(base_url, prepared["customer_review_url"])
        customer_payload = _get_json(review_api)
        serialized = json.dumps(customer_payload)

        assert "drafts" not in customer_payload
        assert "transcript" not in customer_payload
        assert "reviewer" not in serialized
        for draft in internal_state["drafts"]:
            review = draft.get("review")
            if review:
                assert review["rationale"] not in serialized
        assert customer_payload["review"]["criteria"]
        assert customer_payload["review"]["non_goals"] is not None
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_customer_review_page_is_served_without_echoing_token_in_markup(tmp_path):
    server, worker, base_url = _running_server(tmp_path)
    try:
        _close_internal_review(base_url)
        prepared = _post_json(base_url + "/api/customer-draft", {})
        review_path = prepared["customer_review_url"]
        token = review_path.rstrip("/").split("/")[-1]

        with urlopen(base_url + review_path, timeout=5) as response:
            html = response.read().decode("utf-8")

        assert "Confirm requirements" in html
        assert "Request changes" in html
        assert token not in html
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_reset_cannot_race_a_freeze_and_leave_stale_proof_authority(
    tmp_path,
    monkeypatch,
):
    session = _confirmed_session(tmp_path)
    freeze_entered = threading.Event()
    allow_freeze = threading.Event()
    reset_finished = threading.Event()
    errors = []
    original_freeze = web_module.freeze_confirmed_contract

    def paused_freeze(*args, **kwargs):
        freeze_entered.set()
        if not allow_freeze.wait(timeout=5):
            raise AssertionError("Timed out waiting to release the freeze.")
        return original_freeze(*args, **kwargs)

    monkeypatch.setattr(web_module, "freeze_confirmed_contract", paused_freeze)

    def freeze_worker():
        try:
            session.freeze()
        except Exception as error:  # pragma: no cover - diagnostic capture
            errors.append(error)

    def reset_worker():
        try:
            session.reset_to_synthetic_sample()
        except Exception as error:  # pragma: no cover - diagnostic capture
            errors.append(error)
        finally:
            reset_finished.set()

    freezing = threading.Thread(target=freeze_worker)
    resetting = threading.Thread(target=reset_worker)
    freezing.start()
    assert freeze_entered.wait(timeout=5)
    resetting.start()

    assert not reset_finished.wait(timeout=0.05)
    allow_freeze.set()
    freezing.join(timeout=5)
    resetting.join(timeout=5)

    assert not errors
    assert reset_finished.is_set()
    assert session.frozen_contract is None
    assert session.customer_confirmation is None
    assert len(session.pending_drafts) == 2
    with pytest.raises(
        web_module.DemoStateError,
        match="Resolve every candidate|confirmation",
    ):
        session.prove("pass")
