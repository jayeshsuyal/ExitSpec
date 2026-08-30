from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
JS_PATH = STATIC_ROOT / "assisted_authoring.js"


def _asset() -> str:
    return JS_PATH.read_text(encoding="utf-8")


def _function(source: str, name: str, next_name: str) -> str:
    section = source.split(f"function {name}", 1)[1]
    return section.split(f"function {next_name}", 1)[0]


def test_browser_receipt_and_proposal_provenance_are_cross_bound():
    javascript = _asset()
    receipt = _function(javascript, "isTrustedReceipt", "isTrustedNumericFacts")
    proposal = _function(javascript, "isTrustedProposal", "isTrustedAuthoringResponse")
    response = _function(javascript, "isTrustedAuthoringResponse", "isTrustedApiPath")

    assert "sourceReceiptIdForSourceId(payload.source_id)" in receipt
    assert "payload.source_id === receipt.source_id" in proposal
    assert "payload.source_kind === receipt.source_kind" in proposal
    assert "payload.source_adapter_name === receipt.source_adapter_name" in proposal
    assert "payload.source_adapter_version === receipt.source_adapter_version" in proposal
    assert "payload.redaction_policy_version === receipt.redaction_policy_version" in proposal
    assert "proposalId === payload.authoring_receipt.proposal_ids[index]" in response
    assert "ids.length === payload.authoring_receipt.proposal_ids.length" in response
    assert "payload.authoring_receipt.source_receipt_id !== attempt.sourceReceiptId" in response
    assert '"generated_at"' in receipt
    assert "Number.isFinite(Date.parse(payload.generated_at))" in receipt


def test_assisted_retry_preserves_source_and_reenables_only_the_same_attempt():
    javascript = _asset()
    submit = javascript.split('form.addEventListener("submit"', 1)[1]
    assert "sourceReceiptId: selectedReceipt" in submit
    assert "isTrustedAuthoringResponse(response, pendingAttempt)" in submit
    assert "submit.disabled = !selectedReceipt || inFlight;" in javascript
    assert "radio.disabled = inFlight || Boolean(pendingAttempt);" in javascript
    assert "radio.checked = radio.value === pendingAttempt.sourceReceiptId;" in javascript
    assert "response.status >= 500" in javascript
    assert "response.status === 429" in javascript
