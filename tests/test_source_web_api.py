"""State-machine and HTTP integration tests for guided synthetic source import."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from exitspec.source_web import SourceWebRefusal
from exitspec.web import DemoSession, ExitSpecDemoServer


@contextmanager
def _running_server(tmp_path: Path):
    session = DemoSession.synthetic_support_agent(tmp_path / "runs")
    server = ExitSpecDemoServer(("127.0.0.1", 0), session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = "http://127.0.0.1:{0}".format(server.server_port)
    try:
        yield session, base_url
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_bytes(url: str) -> bytes:
    with urlopen(url, timeout=5) as response:
        return response.read()


def _post_json(
    url: str,
    payload: dict,
    *,
    source: bool = False,
) -> dict:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if source:
        origin = url.split("/api/", 1)[0]
        headers.update(
            {
                "Origin": origin,
                "Sec-Fetch-Site": "same-origin",
            }
        )
    request = Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_error(
    url: str,
    payload: dict,
    *,
    source: bool = False,
) -> tuple[int, dict]:
    try:
        _post_json(url, payload, source=source)
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))
    raise AssertionError("Expected request to fail.")


def _import(base_url: str, case_id: str) -> dict:
    return _post_json(
        base_url + "/api/source/import",
        {"fixture_case_id": case_id},
        source=True,
    )


def _review(
    session: DemoSession,
    draft_id: str,
    decision: str,
) -> None:
    session.review(
        draft_id,
        decision,
        "field_engineer",
        "Explicit human decision for the frozen guided-source acceptance test.",
    )


def _review_root(session: DemoSession, *, partial: bool = False) -> None:
    _review(session, "EMAIL-REQ-01", "APPROVE")
    if not partial:
        _review(session, "EMAIL-REQ-02", "REJECT")


def _create_customer_review(session: DemoSession) -> None:
    session.create_customer_draft()
    assert session.customer_review_token is not None


def _confirm(session: DemoSession) -> None:
    _create_customer_review(session)
    assert session.customer_review_token is not None
    session.record_customer_decision(
        session.customer_review_token,
        decision="CONFIRM",
        confirmer="customer_approver",
        agreement_acknowledged=True,
        rationale="The exact guided-source agreement is confirmed.",
        idempotency_key="guided-source-confirmation",
    )


def _session_in_state(tmp_path: Path, state: str) -> DemoSession:
    session = DemoSession.synthetic_support_agent(tmp_path / state / "runs")
    if state == "NO_SOURCE":
        return session
    if state == "SOURCE_REVIEWED_WITHOUT_RULE":
        session.import_guided_source_fixture("authority-attack")
        _review(session, "EMAIL-REQ-01", "REJECT")
        return session

    session.import_guided_source_fixture("thread-root")
    if state == "SOURCE_ZERO_REVIEW":
        return session
    if state == "SOURCE_PARTIAL_REVIEW":
        _review_root(session, partial=True)
        return session
    _review_root(session)
    if state == "SOURCE_REVIEWED_WITH_RULE":
        return session
    if state == "CUSTOMER_REVIEW_CREATED":
        _create_customer_review(session)
        return session
    _confirm(session)
    if state == "CUSTOMER_CONFIRMED":
        return session
    session.freeze()
    if state == "FROZEN":
        return session
    if state == "EVIDENCE_EXISTS":
        session.prove("pass")
        return session
    raise AssertionError("Unknown setup state.")


def test_catalog_and_import_are_live_before_the_generic_router(tmp_path):
    with _running_server(tmp_path) as (session, base_url):
        catalog = _get_json(base_url + "/api/source/fixtures")
        assert catalog["default_fixture_case_id"] == "thread-root"
        assert [item["fixture_case_id"] for item in catalog["fixtures"]] == [
            "thread-root",
            "authority-attack",
        ]

        imported = _import(base_url, "thread-root")
        assert imported["receipt"]["outcome_code"] == "accepted"
        assert imported["receipt"]["candidate_count"] == 2
        assert session.state_payload()["source_intake"] == (
            imported["state"]["source_intake"]
        )

        # Similar names stay under the existing router, not the source gates.
        try:
            _get_json(base_url + "/api/source-control")
        except HTTPError as error:
            body = json.loads(error.read().decode("utf-8"))
            assert error.code == 404
            assert body == {"error": "Page not found."}
        else:
            raise AssertionError("Unknown static route unexpectedly succeeded.")


def test_same_source_replay_preserves_zero_partial_and_full_reviews(tmp_path):
    for reviewed_count in (0, 1, 2):
        session = DemoSession.synthetic_support_agent(
            tmp_path / str(reviewed_count) / "runs"
        )
        session.import_guided_source_fixture("thread-root")
        if reviewed_count >= 1:
            _review(session, "EMAIL-REQ-01", "APPROVE")
        if reviewed_count == 2:
            _review(session, "EMAIL-REQ-02", "REJECT")
        before_state = {
            "source_intake": session.state_payload()["source_intake"],
            "drafts": session.state_payload()["drafts"],
        }
        before_bytes = json.dumps(
            before_state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        replay = session.import_guided_source_fixture("thread-root")
        after_bytes = json.dumps(
            replay["state"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert replay["receipt"]["outcome_code"] == "duplicate_replay"
        assert replay["receipt"]["candidate_count"] == 0
        assert after_bytes == before_bytes
        assert (
            session._source_runtime.counts().accepted_write_transaction_count
            == 1
        )


@pytest.mark.parametrize(
    "state",
    [
        "SOURCE_ZERO_REVIEW",
        "SOURCE_PARTIAL_REVIEW",
        "SOURCE_REVIEWED_WITH_RULE",
        "SOURCE_REVIEWED_WITHOUT_RULE",
    ],
)
def test_source_stage_matrix_replays_same_and_requires_reset_for_different(
    tmp_path,
    state,
):
    same_case = (
        "authority-attack"
        if state == "SOURCE_REVIEWED_WITHOUT_RULE"
        else "thread-root"
    )
    different_case = (
        "thread-root"
        if same_case == "authority-attack"
        else "authority-attack"
    )

    same = _session_in_state(tmp_path / "same", state)
    replay = same.import_guided_source_fixture(same_case)
    assert replay["receipt"]["outcome_code"] == "duplicate_replay"
    assert replay["receipt"]["candidate_count"] == 0

    different = _session_in_state(tmp_path / "different", state)
    before = different.state_payload()
    with pytest.raises(SourceWebRefusal) as caught:
        different.import_guided_source_fixture(different_case)
    assert caught.value.code == "source_change_requires_reset"
    assert different.state_payload() == before
    assert (
        different._source_runtime.counts().accepted_write_transaction_count
        == 1
    )


@pytest.mark.parametrize(
    "state",
    [
        "CUSTOMER_REVIEW_CREATED",
        "CUSTOMER_CONFIRMED",
        "FROZEN",
        "EVIDENCE_EXISTS",
    ],
)
@pytest.mark.parametrize(
    "case_id",
    ["thread-root", "authority-attack"],
)
def test_every_guided_import_is_locked_in_downstream_states(
    tmp_path,
    state,
    case_id,
):
    session = _session_in_state(tmp_path, state)
    before = session.state_payload()
    with pytest.raises(SourceWebRefusal) as caught:
        session.import_guided_source_fixture(case_id)
    assert caught.value.code == "source_import_locked"
    assert session.state_payload() == before
    assert session._source_runtime.counts().accepted_write_transaction_count == 1


def test_unknown_fixture_precedes_downstream_lock_over_http(tmp_path):
    with _running_server(tmp_path) as (session, base_url):
        _import(base_url, "thread-root")
        _review_root(session)
        _create_customer_review(session)
        before = _get_bytes(base_url + "/api/state")
        status, refusal = _post_error(
            base_url + "/api/source/import",
            {"fixture_case_id": "not-guided"},
            source=True,
        )
        assert status == 404
        assert refusal["error"]["code"] == "source_not_approved"
        assert refusal["state_unchanged"] is True
        assert _get_bytes(base_url + "/api/state") == before


def test_reset_clears_source_store_and_reimport_starts_at_version_one(
    tmp_path,
):
    with _running_server(tmp_path) as (session, base_url):
        _import(base_url, "thread-root")
        _review(session, "EMAIL-REQ-01", "APPROVE")
        reset = _post_json(base_url + "/api/reset", {})
        assert reset["source_intake"] is None
        assert reset["source_receipt"] is None
        counts = session._source_runtime.counts()
        assert counts.source_version_count == 0
        assert counts.accepted_write_transaction_count == 0

        reimported = _import(base_url, "authority-attack")
        assert reimported["receipt"]["source_version"] == 1
        assert reimported["receipt"]["outcome_code"] == "accepted"
        assert session._source_runtime.counts().source_version_count == 1


def test_non_email_intake_clears_source_state_and_private_store(tmp_path):
    session = _session_in_state(tmp_path, "SOURCE_PARTIAL_REVIEW")
    session.intake(
        "Customer: The support agent must select the correct tool.",
        title="Replacement synthetic meeting notes",
    )
    state = session.state_payload()
    assert state["source_intake"] is None
    assert state["source_receipt"] is None
    assert session._source_runtime.counts().source_version_count == 0
    imported = session.import_guided_source_fixture("authority-attack")
    assert imported["receipt"]["source_version"] == 1
    assert imported["receipt"]["outcome_code"] == "accepted"


def test_concurrent_different_imports_publish_once_and_require_reset_once(
    tmp_path,
):
    session = _session_in_state(tmp_path, "NO_SOURCE")
    barrier = threading.Barrier(2)

    def import_case(case_id: str):
        barrier.wait(timeout=5)
        try:
            response = session.import_guided_source_fixture(case_id)
            return "ok", case_id, response["receipt"]["outcome_code"]
        except SourceWebRefusal as error:
            return "error", case_id, error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(import_case, ["thread-root", "authority-attack"])
        )
    assert sorted(result[0] for result in results) == ["error", "ok"]
    accepted = next(result for result in results if result[0] == "ok")
    refused = next(result for result in results if result[0] == "error")
    assert accepted[2] == "accepted"
    assert refused[2] == "source_change_requires_reset"
    assert (
        session.state_payload()["source_intake"]["fixture_case_id"]
        == accepted[1]
    )
    counts = session._source_runtime.counts()
    assert counts.accepted_write_transaction_count == 1
    assert counts.source_version_count == 1


def test_reset_import_race_never_leaves_hybrid_state(tmp_path):
    for iteration in range(12):
        session = _session_in_state(
            tmp_path / str(iteration),
            "SOURCE_ZERO_REVIEW",
        )
        barrier = threading.Barrier(2)

        def reset():
            barrier.wait(timeout=5)
            session.reset_to_synthetic_sample()

        def reimport():
            barrier.wait(timeout=5)
            return session.import_guided_source_fixture("thread-root")

        with ThreadPoolExecutor(max_workers=2) as pool:
            reset_future = pool.submit(reset)
            import_future = pool.submit(reimport)
            reset_future.result(timeout=5)
            import_future.result(timeout=5)

        state = session.state_payload()
        counts = session._source_runtime.counts()
        if state["source_intake"] is None:
            assert state["source_receipt"] is None
            assert counts.source_version_count == 0
            assert counts.accepted_write_transaction_count == 0
            assert all(
                not draft["id"].startswith("EMAIL-REQ-")
                for draft in state["drafts"]
            )
        else:
            assert state["source_intake"]["fixture_case_id"] == "thread-root"
            assert state["source_receipt"]["source_version"] == 1
            assert counts.source_version_count == 1
            assert counts.accepted_write_transaction_count == 1
            assert all(
                draft["id"].startswith("EMAIL-REQ-")
                for draft in state["drafts"]
            )


def test_review_replay_race_preserves_one_review_and_one_store_write(
    tmp_path,
):
    session = _session_in_state(tmp_path, "SOURCE_ZERO_REVIEW")
    barrier = threading.Barrier(2)

    def review():
        barrier.wait(timeout=5)
        _review(session, "EMAIL-REQ-01", "APPROVE")

    def replay():
        barrier.wait(timeout=5)
        return session.import_guided_source_fixture("thread-root")

    with ThreadPoolExecutor(max_workers=2) as pool:
        review_future = pool.submit(review)
        replay_future = pool.submit(replay)
        review_future.result(timeout=5)
        replay_response = replay_future.result(timeout=5)

    assert replay_response["receipt"]["outcome_code"] == "duplicate_replay"
    state = session.state_payload()
    assert state["drafts"][0]["status"] == "APPROVED"
    assert state["drafts"][1]["status"] == "NEEDS_REVIEW"
    assert state["source_intake"]["pending_count"] == 1
    assert session._source_runtime.counts().accepted_write_transaction_count == 1


def test_customer_review_import_race_is_serialized_without_hybrid_state(
    tmp_path,
):
    session = _session_in_state(tmp_path, "SOURCE_REVIEWED_WITH_RULE")
    barrier = threading.Barrier(2)

    def create_review():
        barrier.wait(timeout=5)
        session.create_customer_draft()
        return "created"

    def replay():
        barrier.wait(timeout=5)
        try:
            response = session.import_guided_source_fixture("thread-root")
            return response["receipt"]["outcome_code"]
        except SourceWebRefusal as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        created_future = pool.submit(create_review)
        replay_future = pool.submit(replay)
        created = created_future.result(timeout=5)
        replay_outcome = replay_future.result(timeout=5)

    assert created == "created"
    assert replay_outcome in {"duplicate_replay", "source_import_locked"}
    state = session.state_payload()
    assert state["customer_review_url"] is not None
    assert state["source_intake"]["status"] == "REVIEWED"
    assert session._source_runtime.counts().accepted_write_transaction_count == 1


def test_guided_source_runs_existing_review_confirm_freeze_prove_loop(
    tmp_path,
):
    with _running_server(tmp_path) as (_session, base_url):
        imported = _import(base_url, "thread-root")
        first, second = imported["state"]["drafts"]
        _post_json(
            base_url + "/api/review",
            {
                "draft_id": first["id"],
                "decision": "APPROVE",
                "reviewer": "field_engineer",
                "rationale": "The deterministic acceptance rule matches intent.",
            },
        )
        reviewed = _post_json(
            base_url + "/api/review",
            {
                "draft_id": second["id"],
                "decision": "REJECT",
                "reviewer": "field_engineer",
                "rationale": "Latency remains context without an adapter.",
            },
        )
        assert reviewed["state"]["source_intake"]["status"] == "REVIEWED"

        customer = _post_json(base_url + "/api/customer-draft", {})
        token = customer["customer_review_url"].rstrip("/").split("/")[-1]
        _post_json(
            base_url + "/api/review/{0}/decision".format(token),
            {
                "decision": "CONFIRM",
                "agreement_acknowledged": True,
                "confirmer": "customer_approver",
                "rationale": "The exact POC agreement is confirmed.",
                "idempotency_key": "guided-source-e2e-confirm",
            },
        )
        frozen = _post_json(base_url + "/api/freeze", {})
        assert frozen["ready_to_prove"] is True
        proved = _post_json(
            base_url + "/api/prove",
            {"scenario": "pass"},
        )
        assert proved["proof_pack"]["overall_verdict"] == "PASS"
        assert "not an automatic" in proved["proof_pack"][
            "next_human_action"
        ].lower()

        status, locked = _post_error(
            base_url + "/api/source/import",
            {"fixture_case_id": "thread-root"},
            source=True,
        )
        assert status == 409
        assert locked["error"]["code"] == "source_import_locked"
