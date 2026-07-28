from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest

from exitspec.confirmations import (
    ConfirmationDecision,
    ContractConfirmation,
    record_confirmation,
)
from exitspec.contracts import freeze_confirmed_contract
from exitspec.models import POCContract, VerdictStatus
from exitspec.performance_decision import (
    AuthorizedPerformanceDecision,
    authorize_performance_decision,
)
from exitspec.performance_evidence import (
    ValidatedPerformanceContext,
    validate_performance_context,
)
from exitspec.performance_probe import (
    PROBE_SCHEMA_VERSION,
    ProbeManifest,
    ProbeOutcome,
    ProbePhase,
    ProbeRecord,
    ProbeRun,
    records_jsonl,
)
from exitspec.performance_receipts import (
    InMemoryPerformanceReceiptStore,
    PerformanceExecutionReceipt,
)
from exitspec.performance_reporting import (
    PERFORMANCE_REPORT_SCHEMA_VERSION,
    PerformanceReportIntegrityError,
    render_performance_evidence_pack,
)
from exitspec.runner import load_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/contracts/vllm-ttft-v1.yaml"
)
WORKLOAD_PATH = (
    PROJECT_ROOT
    / "examples/inference-performance/workloads/concurrency-4-v1.json"
)
FIXED_TIME = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
EXECUTION_ID = "run_" + "a" * 32


class _StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fact_rows = 0
        self.scripts = 0
        self.external_assets: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if "fact-row" in classes:
            self.fact_rows += 1
        if tag == "script":
            self.scripts += 1
        for attribute in ("href", "src"):
            value = attributes.get(attribute)
            if value:
                self.external_assets.append(value)


def _approved_contract(
    *,
    customer: str | None = None,
    criterion_title: str | None = None,
) -> POCContract:
    approved = load_contract(CONTRACT_PATH)
    updates: dict[str, object] = {}
    if customer is not None:
        updates["customer"] = customer
    if criterion_title is not None:
        criterion = approved.criteria[0].model_copy(
            update={"title": criterion_title}
        )
        updates["criteria"] = (criterion,)
    return approved.model_copy(update=updates)


def _authorized_context(
    *,
    customer: str | None = None,
    criterion_title: str | None = None,
) -> tuple[ValidatedPerformanceContext, ContractConfirmation]:
    approved = _approved_contract(
        customer=customer,
        criterion_title=criterion_title,
    )
    confirmation = record_confirmation(
        approved,
        confirmer_identity="customer@example.com",
        decision=ConfirmationDecision.CONFIRM,
        agreement_acknowledged=True,
        rationale="The frozen performance requirements are correct.",
        idempotency_key="performance-report-confirmation-v1",
        decided_at=FIXED_TIME,
    )
    frozen = freeze_confirmed_contract(
        approved,
        confirmation,
        FIXED_TIME,
    )
    context = validate_performance_context(
        frozen,
        WORKLOAD_PATH.read_bytes(),
        bundle_root=PROJECT_ROOT,
    )
    return context, confirmation


def _probe_run(
    manifest: ProbeManifest,
    *,
    mode: str = "pass",
    execution_id: str = EXECUTION_ID,
) -> ProbeRun:
    records: list[ProbeRecord] = []
    for phase, count in (
        (ProbePhase.WARMUP, manifest.warmup_count),
        (ProbePhase.MEASURED, manifest.request_count),
    ):
        for ordinal in range(1, count + 1):
            prompt = manifest.prompts[
                (ordinal - 1) % len(manifest.prompts)
            ]
            measured = phase is ProbePhase.MEASURED
            is_fail = measured and mode == "fail" and ordinal == count
            is_not_proven = (
                measured and mode == "not_proven" and ordinal == count
            )
            if is_fail:
                outcome = ProbeOutcome.HTTP_ERROR
            elif is_not_proven:
                outcome = ProbeOutcome.INTERNAL_ERROR
            else:
                outcome = ProbeOutcome.SUCCESS
            success = outcome is ProbeOutcome.SUCCESS
            records.append(
                ProbeRecord(
                    schema_version=PROBE_SCHEMA_VERSION,
                    execution_id=execution_id,
                    manifest_sha256=manifest.manifest_sha256,
                    request_id=(
                        "warmup" if phase is ProbePhase.WARMUP else "measured"
                    )
                    + f"-{ordinal:05d}",
                    phase=phase,
                    ordinal=ordinal,
                    included_in_measurement=measured,
                    prompt_id=prompt.prompt_id,
                    prompt_sha256=prompt.sha256,
                    outcome=outcome,
                    http_status=200 if success else (429 if is_fail else None),
                    ttft_ns=100_000_000 if success else None,
                    duration_ns=100_000_001 if success else 1,
                )
            )
    immutable_records = tuple(records)
    records_sha256 = hashlib.sha256(
        records_jsonl(immutable_records).encode("utf-8")
    ).hexdigest()
    return ProbeRun(
        execution_id=execution_id,
        manifest=manifest,
        records_sha256=records_sha256,
        records=immutable_records,
    )


def _receipt(
    context: ValidatedPerformanceContext,
    run: ProbeRun,
) -> PerformanceExecutionReceipt:
    contract_hash = context.contract.canonical_hash
    assert contract_hash is not None
    return InMemoryPerformanceReceiptStore().record_receipt(
        idempotency_key="performance-report-run-v1",
        contract_id=context.contract.id,
        contract_version=context.contract.version,
        frozen_contract_hash=contract_hash,
        criterion_id=context.criterion.id,
        expected_manifest_sha256=(
            context.expected_manifest.manifest_sha256
        ),
        execution_id=run.execution_id,
        records_sha256=run.records_sha256,
        created_at=FIXED_TIME,
    )


def _pack(
    *,
    mode: str = "pass",
    customer: str | None = None,
    criterion_title: str | None = None,
) -> tuple[
    bytes,
    AuthorizedPerformanceDecision,
    ValidatedPerformanceContext,
    ProbeRun,
]:
    context, confirmation = _authorized_context(
        customer=customer,
        criterion_title=criterion_title,
    )
    run = _probe_run(context.expected_manifest, mode=mode)
    decision = authorize_performance_decision(
        context,
        confirmation,
        run,
        _receipt(context, run),
    )
    rendered = render_performance_evidence_pack(decision, context, run)
    return rendered, decision, context, run


def test_pack_is_deterministic_exact_utf8_bytes():
    first, decision, context, run = _pack()

    second = render_performance_evidence_pack(decision, context, run)

    assert type(first) is bytes
    assert first == second
    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()
    assert first.startswith(b"<!doctype html>\n")
    assert (
        f'name="exitspec-report-schema" '
        f'content="{PERFORMANCE_REPORT_SCHEMA_VERSION}"'
    ).encode() in first


@pytest.mark.parametrize(
    ("mode", "status"),
    [
        ("pass", VerdictStatus.PASS),
        ("fail", VerdictStatus.FAIL),
        ("not_proven", VerdictStatus.NOT_PROVEN),
    ],
)
def test_pack_renders_only_recomputed_status_semantics(mode, status):
    rendered, decision, _context, _run = _pack(mode=mode)
    html = rendered.decode("utf-8")

    assert decision.performance_verdict.verdict is status
    assert f'data-verdict="{status.value}"' in html
    assert f"<h1 id=\"report-verdict\">{status.value.replace('_', ' ')}</h1>" in html
    assert "p95 client-observed TTFT" in html
    assert "Measured error rate" in html


def test_pack_is_compact_static_and_contains_exactly_two_fact_rows():
    rendered, _decision, _context, _run = _pack()
    html = rendered.decode("utf-8")
    parser = _StructureParser()
    parser.feed(html)

    assert parser.fact_rows == 2
    assert parser.scripts == 0
    assert parser.external_assets == []
    assert 'http-equiv="Content-Security-Policy"' in html
    assert "default-src 'none'" in html
    assert "<canvas" not in html
    assert "<svg" not in html
    assert "Observed" in html
    assert "Threshold" in html
    assert "100 successful measured requests" in html
    assert "0 errors / 100 measured attempts" in html
    assert "100 ms (100,000,000 ns)" in html
    assert "1%" in html
    assert "Run" in html
    assert "Model" in html
    assert "Workload" in html
    assert "Criterion" in html


def test_pack_states_measurement_scope_and_human_authorization_boundary():
    rendered, _decision, _context, _run = _pack()
    html = rendered.decode("utf-8")

    assert (
        "TTFT is client-observed and includes network, proxy, queueing, "
        "and inference time."
    ) in html
    assert html.count("Evidence is not authorization.") >= 1
    assert "A human still decides whether to" in html
    assert "ship, expand traffic, procure, spend" in html


def test_pack_escapes_untrusted_customer_and_criterion_labels():
    attack = '<img src=x onerror="alert(1)">'
    rendered, _decision, _context, _run = _pack(
        customer=attack,
        criterion_title=f"Latency {attack}",
    )
    html = rendered.decode("utf-8")

    assert attack not in html
    assert "<img" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    parser = _StructureParser()
    parser.feed(html)
    assert parser.external_assets == []


def test_pack_rejects_fabricated_caller_supplied_verdict():
    _rendered, decision, context, run = _pack()
    forged_verdict = replace(
        decision.performance_verdict,
        verdict=VerdictStatus.FAIL,
        reason="Caller says this failed.",
    )
    forged_decision = replace(
        decision,
        performance_verdict=forged_verdict,
    )

    with pytest.raises(
        PerformanceReportIntegrityError,
        match="does not match recalculated evidence",
    ):
        render_performance_evidence_pack(
            forged_decision,
            context,
            run,
        )


def test_pack_rejects_mismatched_run_chain():
    _rendered, decision, context, run = _pack()
    other_run = _probe_run(
        context.expected_manifest,
        mode="fail",
        execution_id="run_" + "b" * 32,
    )

    with pytest.raises(
        PerformanceReportIntegrityError,
        match="receipt does not bind",
    ):
        render_performance_evidence_pack(
            decision,
            context,
            other_run,
        )
    assert other_run.records_sha256 != run.records_sha256


def test_pack_rejects_tampered_context_bytes():
    _rendered, decision, context, run = _pack()
    tampered_context = replace(
        context,
        workload_bytes=context.workload_bytes + b" ",
    )

    with pytest.raises(
        PerformanceReportIntegrityError,
        match="workload bytes do not match",
    ):
        render_performance_evidence_pack(
            decision,
            tampered_context,
            run,
        )
