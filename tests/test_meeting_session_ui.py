import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


STATIC_ROOT = (
    Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
)
HTML_PATH = STATIC_ROOT / "source_intake.html"
CSS_PATH = STATIC_ROOT / "source_intake.css"
JS_PATH = STATIC_ROOT / "source_intake.js"


def _asset(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(source: str, name: str, next_name: str) -> str:
    section = source.split(f"function {name}", 1)[1]
    return section.split(f"function {next_name}", 1)[0]


class _Inventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del tag
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(str(attributes["id"]))


def test_meeting_session_is_one_submode_with_one_primary_action():
    html = _asset(HTML_PATH)
    inventory = _Inventory()
    inventory.feed(html)

    assert len(inventory.ids) == len(set(inventory.ids))
    assert html.count('class="primary-action"') == 1
    assert 'id="capture-source"' in html
    assert 'id="meeting-mode-session"' in html
    assert 'value="SESSION"' in html
    assert 'id="meeting-session-panel"' in html
    assert "Meeting session" in html
    assert "Paste transcript" in html
    assert "Record synthetic demo" in html
    assert "Not connected" in html
    assert "no customer data or live Zoom connection is used" in html
    assert "human review" in html
    assert "cannot approve or freeze anything" in html
    assert "Connect Zoom" not in html
    assert "Zoom connected" not in html


def test_session_progress_is_bounded_to_consent_start_draft_review():
    html = _asset(HTML_PATH)
    progress = html.split('id="meeting-session-progress"', 1)[1].split(
        "</ol>", 1
    )[0]

    assert progress.count("data-meeting-step=") == 4
    for step in ("CONSENT", "START", "DRAFT", "REVIEW"):
        assert f'data-meeting-step="{step}"' in progress
    assert progress.count('aria-current="step"') == 1
    assert "Measure" not in progress
    assert "PASS" not in progress


def test_exact_disclosure_and_complete_consent_precede_synthetic_start():
    javascript = _asset(JS_PATH)
    advance = _function(
        javascript,
        "advanceMeetingSession",
        "renderRecordingControls",
    )

    create_at = advance.index('"CREATE"')
    consent_at = advance.index('"CONSENT"')
    start_at = advance.index('"START"')
    draft_at = advance.index('"DRAFT"')
    assert create_at < consent_at < start_at < draft_at
    assert "!allMeetingSessionAcknowledgementsChecked()" in advance
    assert "all_participants_consented: true" in advance
    assert "meetingSessionDisclosure.disclosure_id" in advance
    assert "recording_notice_acknowledged: true" in advance
    assert "synthetic_demo_acknowledged: true" in advance


def test_browser_can_only_choose_actions_not_provider_or_transcript_facts():
    javascript = _asset(JS_PATH)
    action = _function(
        javascript,
        "advanceMeetingSession",
        "renderRecordingControls",
    )

    for allowed in (
        "idempotency_key",
        "all_participants_consented",
        "disclosure_id",
        "recording_notice_acknowledged",
        "synthetic_demo_acknowledged",
    ):
        assert allowed in action
    for forbidden in (
        "meeting_id",
        "participant_id",
        "stream_id",
        "transcript_text",
        "provider_connected",
        "webhook_signature_verified",
        "may_confirm_contract: true",
        "may_freeze_contract: true",
        "may_start_measurement: true",
        "may_assign_verdict: true",
    ):
        assert forbidden not in action


def test_all_meeting_responses_are_exactly_validated_before_rendering():
    javascript = _asset(JS_PATH)
    disclosure = _function(
        javascript,
        "isTrustedMeetingSessionDisclosure",
        "isSafeMeetingTimestamp",
    )
    session = _function(
        javascript,
        "isTrustedMeetingSession",
        "isTrustedMeetingSessionAction",
    )
    action = _function(
        javascript,
        "isTrustedMeetingSessionAction",
        "isTrustedSourceList",
    )

    assert "hasExactKeys(payload" in disclosure
    assert "payload.provider_connected === false" in javascript
    assert "payload.raw_audio_requested === false" in disclosure
    assert "payload.may_freeze_contract === false" in disclosure
    assert "hasExactKeys(payload" in session
    assert "MEETING_SESSION_ID_PATTERN.test(payload.session_id)" in session
    assert "payload.next_action !== MEETING_SESSION_STATE_ACTIONS" in session
    assert "sourceFacts.every((value) => value === null)" in session
    assert 'payload.review_state === "NEEDS_REVIEW"' in session
    assert "hasExactKeys(payload, [\"idempotent_replay\", \"session\"])" in action
    assert "isTrustedMeetingSession(payload.session)" in action


def test_recovery_is_read_only_and_mutation_retries_keep_operation_keys():
    javascript = _asset(JS_PATH)
    recovery = _function(
        javascript,
        "recoverMeetingSession",
        "runMeetingSessionAction",
    )
    key = _function(
        javascript,
        "meetingOperationKey",
        "allMeetingSessionAcknowledgementsChecked",
    )

    assert "requestJson(meetingSessionCurrentApi)" in recovery
    assert "method:" not in recovery
    assert "JSON.stringify" not in recovery
    assert "meetingSessionOperationKeys[action]" in key
    assert "newScopedIdempotencyKey" in key
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "indexedDB" not in javascript
    assert "console." not in javascript


def test_session_failures_use_allowlisted_copy_and_never_reflect_raw_errors():
    javascript = _asset(JS_PATH)
    trusted = _function(
        javascript,
        "trustedMeetingSessionFailure",
        "isTrustedMeetingSessionAdapter",
    )
    safe_copy = _function(
        javascript,
        "safeMeetingSessionFailureCopy",
        "advanceMeetingSession",
    )

    assert 'hasExactKeys(payload, ["code", "error", "next_action"])' in trusted
    assert "payload.code" in trusted
    assert "payload.error" not in trusted
    assert "payload.next_action" not in trusted
    assert "MEETING_SESSION_FAILURES[error.failureCode]" in safe_copy
    assert "error.message" not in safe_copy
    assert "response.text" not in javascript


def test_graphite_session_panel_is_finite_responsive_and_gimmick_free():
    css = _asset(CSS_PATH)
    session_css = css.split(".meeting-session {", 1)[1].split(
        ".record-demo {", 1
    )[0]

    assert "max-width: 700px" in session_css
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in session_css
    assert "var(--navigation)" in session_css
    assert "var(--raised)" in session_css
    assert "var(--orange)" in session_css
    assert "var(--green)" in session_css
    assert ":focus-visible" in session_css
    assert "gradient" not in session_css.lower()
    assert "backdrop-filter" not in session_css.lower()
    assert "#000" not in session_css.lower()
    assert re.search(r"font-size:\s*(?:8|9|10)px", session_css) is None
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css


def test_meeting_session_javascript_has_valid_syntax():
    result = subprocess.run(
        ["node", "--check", str(JS_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
