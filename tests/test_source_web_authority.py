"""Authority separation between email source, humans, provider, and evidence."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import threading

import pytest

from exitspec.intake import redact_and_parse_pasted_transcript
from exitspec.providers import ProviderHTTPResponse
from exitspec.source_web import SourceWebRefusal
from exitspec.wave1_execution import Wave1ProviderExecutionConfiguration
from exitspec.wave1_runtime import frozen_wave1_source
from exitspec.web import DemoSession, DemoStateError


API_KEY = "fw_test_source_authority_secret"


class _FakeHTTPSResponse:
    def __init__(self, response: ProviderHTTPResponse) -> None:
        self.status = response.status_code
        self._headers = list(response.headers.items())
        self._body = response.body.encode("utf-8")

    def getheaders(self):
        return list(self._headers)

    def read(self, _amount):
        return self._body

    def close(self):
        return None


class _BlockingConnection:
    def __init__(self, response: _FakeHTTPSResponse) -> None:
        self.response = response
        self.request_started = threading.Event()
        self.release_response = threading.Event()
        self.requests = []

    def request(self, method, path, *, body, headers):
        self.requests.append((method, path, body, dict(headers)))
        self.request_started.set()

    def getresponse(self):
        if not self.release_response.wait(5):
            raise TimeoutError("Test did not release provider response.")
        return self.response

    def close(self):
        return None


class _Factory:
    def __init__(self, connection: _BlockingConnection) -> None:
        self.connection = connection
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls != 1:
            raise AssertionError("Unexpected additional provider call.")
        return self.connection


def _session(tmp_path: Path) -> DemoSession:
    return DemoSession.synthetic_support_agent(tmp_path / "runs")


def _authorize(session: DemoSession, key: str):
    disclosure = session.wave1_provider_disclosure_payload()
    return session.authorize_wave1_provider_egress(
        disclosure_id=disclosure["disclosure_id"],
        acknowledged=True,
        idempotency_key=key,
    )


def _provider_proposal() -> dict:
    source = frozen_wave1_source()
    intake = redact_and_parse_pasted_transcript(
        source["transcript"],
        transcript_id=source["transcript_id"],
        title=source["title"],
        customer_terms=source["customer_terms"],
    )
    line = intake.transcript.lines[0]
    return {
        "line_number": line.line_number,
        "speaker": line.speaker,
        "quote": line.text,
        "title": "Exact tool selection",
        "normalized_claim": (
            "Exact tool-selection accuracy is at least 95% over at least "
            "200 approved cases."
        ),
        "classification": "measurable",
        "threshold": 0.95,
        "minimum_samples": 200,
        "open_questions": [],
    }


def _provider_response() -> ProviderHTTPResponse:
    body = json.dumps(
        {
            "id": "provider-response-must-not-publish",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"proposals": [_provider_proposal()]}
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 100,
                "total_tokens": 600,
            },
        }
    )
    return ProviderHTTPResponse(
        status_code=200,
        headers={"X-Request-ID": "provider-request-marker"},
        body=body,
    )


def _configuration(factory: _Factory) -> Wave1ProviderExecutionConfiguration:
    return Wave1ProviderExecutionConfiguration(
        enabled=True,
        api_key=API_KEY,
        connection_factory=factory,
        sleeper=lambda _delay: None,
        monotonic=lambda: 10.0,
        wall_clock=lambda: 10.0,
    )


def test_authority_attack_source_has_zero_approval_or_execution_power(
    tmp_path,
):
    session = _session(tmp_path)
    response = session.import_guided_source_fixture("authority-attack")
    state = session.state_payload()
    assert response["state"]["drafts"][0]["status"] == "NEEDS_REVIEW"
    assert response["state"]["drafts"][0]["review"] is None
    assert response["state"]["drafts"][0]["proposed_criterion"] is None
    assert state["approved_criterion_count"] == 0
    assert state["confirmation"] is None
    assert state["ready_to_freeze"] is False
    assert state["ready_to_prove"] is False
    assert state["proof_pack"] is None
    assert state["safety"]["provider_calls"] is False
    assert session._wave1_provider_execution_operations == {}
    assert session.customer_review_invitation is None
    assert session.frozen_contract is None
    assert session.last_run is None

    with pytest.raises(DemoStateError):
        session.freeze()
    with pytest.raises(DemoStateError):
        session.prove("pass")


def test_accepted_import_invalidates_active_fireworks_authority_only(
    tmp_path,
):
    session = _session(tmp_path)
    _authorize(session, "source-accepted-authority")
    active = session._wave1_provider_authorization
    operations = dict(session._wave1_provider_authorization_operations)
    spend = session._wave1_provider_reserved_spend_usd
    assert active is not None

    session.import_guided_source_fixture("thread-root")
    assert session._wave1_provider_authorization is None
    assert session._wave1_provider_authorization_operations == operations
    assert session._wave1_provider_reserved_spend_usd == spend
    assert session.state_payload()["safety"]["provider_calls"] is False


def test_replay_and_refusal_preserve_active_fireworks_authority(
    tmp_path,
):
    session = _session(tmp_path)
    session.import_guided_source_fixture("thread-root")
    _authorize(session, "source-replay-authority")
    active = session._wave1_provider_authorization
    epoch = session._wave1_workflow_epoch
    assert active is not None

    replay = session.import_guided_source_fixture("thread-root")
    assert replay["receipt"]["outcome_code"] == "duplicate_replay"
    assert session._wave1_provider_authorization is active
    assert session._wave1_workflow_epoch == epoch

    with pytest.raises(SourceWebRefusal) as different:
        session.import_guided_source_fixture("authority-attack")
    assert different.value.code == "source_change_requires_reset"
    assert session._wave1_provider_authorization is active
    assert session._wave1_workflow_epoch == epoch

    first, second = session.reviewed_drafts
    session.review(
        first.id,
        "APPROVE",
        "field_engineer",
        "Human approval of the complete deterministic rule.",
    )
    session.review(
        second.id,
        "REJECT",
        "field_engineer",
        "Human decision to keep latency outside this adapter.",
    )
    session.create_customer_draft()
    _authorize(session, "source-locked-authority")
    locked_active = session._wave1_provider_authorization
    with pytest.raises(SourceWebRefusal) as locked:
        session.import_guided_source_fixture("thread-root")
    assert locked.value.code == "source_import_locked"
    assert session._wave1_provider_authorization is locked_active


def test_late_fireworks_execution_is_stale_after_source_publication(
    tmp_path,
):
    connection = _BlockingConnection(
        _FakeHTTPSResponse(_provider_response())
    )
    factory = _Factory(connection)
    configuration = _configuration(factory)
    session = _session(tmp_path)
    session.configure_wave1_provider_execution(configuration)
    _authorize(session, "source-stale-provider-authority")
    completed = {}

    def execute():
        completed["result"] = session.execute_wave1_provider_assist(
            configuration=configuration,
            idempotency_key="source-stale-provider-execution",
        )

    worker = threading.Thread(target=execute)
    worker.start()
    assert connection.request_started.wait(5)

    imported = session.import_guided_source_fixture("thread-root")
    imported_drafts = imported["state"]["drafts"]
    assert session.state_payload()["source_intake"] is not None

    connection.release_response.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    result = completed["result"]["execution"]
    assert result["outcome_code"] == "stale_workflow"
    assert result["proposals_created"] == 0
    assert session.state_payload()["drafts"] == imported_drafts
    assert session.state_payload()["source_intake"]["fixture_case_id"] == (
        "thread-root"
    )
    assert "provider-response-must-not-publish" not in json.dumps(
        session.state_payload(),
        sort_keys=True,
    )
    assert factory.calls == 1


def test_reset_and_non_email_intake_preserve_provider_tombstones_and_spend(
    tmp_path,
):
    session = _session(tmp_path)
    _authorize(session, "source-provider-tombstone")
    operations = dict(session._wave1_provider_authorization_operations)
    session._wave1_provider_reserved_spend_usd = Decimal("0.04")
    session.import_guided_source_fixture("thread-root")

    session.reset_to_synthetic_sample()
    assert session._wave1_provider_authorization_operations == operations
    assert session._wave1_provider_reserved_spend_usd == Decimal("0.04")
    assert session.state_payload()["source_intake"] is None
    assert session._source_runtime.counts().source_version_count == 0

    replay = _authorize(session, "source-provider-tombstone")
    assert replay["authorization"]["idempotent_replay"] is True
    assert session._wave1_provider_authorization is None

    session.import_guided_source_fixture("authority-attack")
    session.intake(
        "Customer: The system must select the correct support tool.",
        title="Replacement synthetic notes",
    )
    assert session._wave1_provider_authorization_operations == operations
    assert session._wave1_provider_reserved_spend_usd == Decimal("0.04")
    assert session.state_payload()["source_intake"] is None
    assert session._source_runtime.counts().source_version_count == 0
