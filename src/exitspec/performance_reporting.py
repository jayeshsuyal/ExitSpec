"""Deterministic customer-facing reporting for inference-performance evidence.

The renderer is deliberately a projection, not a decision boundary.  It
revalidates the complete in-memory chain and independently recalculates the
two performance facts before emitting a static HTML artifact.
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal
from html import escape
from typing import Final

from .contracts import verify_contract_digest
from .models import ContractStatus, VerdictStatus
from .performance_decision import AuthorizedPerformanceDecision
from .performance_evidence import ValidatedPerformanceContext
from .performance_probe import (
    ProbeConfigurationError,
    ProbeEvidenceError,
    ProbeRun,
    build_manifest,
    validate_probe_run,
)
from .performance_receipts import (
    PerformanceReceiptIntegrityError,
    validate_performance_receipt,
)
from .performance_verdicts import (
    PerformanceCriterionVerdict,
    PerformanceOutcomeCounts,
    evaluate_performance_criterion,
)


PERFORMANCE_REPORT_SCHEMA_VERSION: Final = (
    "exitspec.performance-evidence-pack.v1"
)


class PerformanceReportIntegrityError(ValueError):
    """The supplied evidence chain is not safe to render."""


def render_performance_evidence_pack(
    decision: AuthorizedPerformanceDecision,
    context: ValidatedPerformanceContext,
    probe_run: ProbeRun,
) -> bytes:
    """Render one byte-stable, static customer Evidence Pack.

    No verdict or integrity flag is accepted from the caller.  The authorized
    decision must match the frozen context, execution receipt, probe manifest,
    probe records, and an independently recalculated verdict.
    """

    verdict = _require_authorized_chain(decision, context, probe_run)
    contract = context.contract
    criterion = context.criterion
    receipt = decision.receipt
    manifest = probe_run.manifest

    overall_status = verdict.verdict.value
    ttft = verdict.ttft_p95
    error_rate = verdict.error_rate
    ttft_operator = "&lt;" if ttft.operator == "lt" else "&le;"
    error_operator = "&lt;"
    ttft_observed = _format_ttft(ttft.observed_ns)
    ttft_threshold = _format_ttft(ttft.threshold_ns)
    error_observed = _format_rate(error_rate.observed_rate)
    error_threshold = _format_rate(error_rate.threshold)
    counts = _require_outcome_counts(verdict)
    counting_boundary, counting_identity = _counting_policy_copy(
        context,
        probe_run,
        verdict,
    )
    limitations = _ordered_limitations(
        verdict,
        contract.non_goals,
    )
    limitation_items = "\n".join(
        f"          <li>{_safe(item)}</li>" for item in limitations
    )

    values = {
        "schema_version": _safe(PERFORMANCE_REPORT_SCHEMA_VERSION),
        "status": _safe(overall_status),
        "status_label": _safe(overall_status.replace("_", " ")),
        "reason": _safe(verdict.reason),
        "customer": _safe(contract.customer),
        "use_case": _safe(contract.use_case),
        "run_id": _safe(probe_run.execution_id),
        "model": _safe(manifest.model),
        "workload": _safe(context.workload.workload_id),
        "criterion_id": _safe(criterion.id),
        "criterion_title": _safe(criterion.title),
        "contract_id": _safe(contract.id),
        "contract_version": _safe(contract.version),
        "receipt_id": _safe(receipt.receipt_id),
        "manifest_hash": _safe(manifest.manifest_sha256),
        "records_hash": _safe(probe_run.records_sha256),
        "ttft_status": _safe(ttft.verdict.value),
        "ttft_operator": ttft_operator,
        "ttft_observed": _safe(ttft_observed),
        "ttft_threshold": _safe(ttft_threshold),
        "ttft_samples": _safe(
            f"{ttft.successful_samples} successful measured requests"
        ),
        "ttft_minimum": _safe(
            f"minimum required: {ttft.minimum_successful_samples}"
        ),
        "error_status": _safe(error_rate.verdict.value),
        "error_operator": error_operator,
        "error_observed": _safe(error_observed),
        "error_threshold": _safe(error_threshold),
        "error_samples": _safe(
            f"{error_rate.error_count} errors / "
            f"{error_rate.attempted_count} measured attempts"
        ),
        "error_minimum": _safe(
            f"required attempts: {error_rate.minimum_attempts}"
        ),
        "outcome_summary": _safe(
            _outcome_summary(counts, verdict.attempted_count)
        ),
        "latency_population": _safe(
            "Latency population: "
            f"{verdict.successful_count} successful measured requests "
            "with valid first-token timing."
        ),
        "reliability_population": _safe(
            "Reliability population: "
            f"all {verdict.attempted_count} measured attempts; HTTP, "
            "timeout, protocol, and transport errors count."
        ),
        "counting_boundary": _safe(counting_boundary),
        "counting_identity": _safe(counting_identity),
        "limitations": limitation_items,
    }
    html = _HTML_TEMPLATE.format_map(values)
    return html.encode("utf-8")


def _require_authorized_chain(
    decision: AuthorizedPerformanceDecision,
    context: ValidatedPerformanceContext,
    probe_run: ProbeRun,
) -> PerformanceCriterionVerdict:
    if type(decision) is not AuthorizedPerformanceDecision:
        _reject("An AuthorizedPerformanceDecision is required.")
    if type(context) is not ValidatedPerformanceContext:
        _reject("A ValidatedPerformanceContext is required.")
    if type(probe_run) is not ProbeRun:
        _reject("A complete ProbeRun is required.")

    contract = context.contract
    criterion = context.criterion
    if (
        contract.status is not ContractStatus.FROZEN
        or contract.confirmation_id is None
        or not verify_contract_digest(contract)
    ):
        _reject("The report requires a valid frozen, confirmed contract.")
    if criterion not in contract.criteria or not criterion.approved:
        _reject("The selected criterion is not approved by the frozen contract.")

    _require_context_bindings(context)
    try:
        validate_probe_run(probe_run)
    except (ProbeEvidenceError, TypeError, ValueError) as error:
        raise PerformanceReportIntegrityError(
            "Probe evidence integrity validation failed."
        ) from error
    if probe_run.manifest != context.expected_manifest:
        _reject("The probe manifest does not match the authorized workload.")

    try:
        receipt = validate_performance_receipt(decision.receipt)
    except (
        PerformanceReceiptIntegrityError,
        TypeError,
        ValueError,
    ) as error:
        raise PerformanceReportIntegrityError(
            "Execution receipt integrity validation failed."
        ) from error
    if receipt != decision.receipt:
        _reject("The validated execution receipt changed unexpectedly.")

    contract_hash = contract.canonical_hash
    if contract_hash is None:
        _reject("The frozen contract digest is missing.")
    expected_receipt_fields = (
        (receipt.contract_id, contract.id),
        (receipt.contract_version, contract.version),
        (receipt.frozen_contract_hash, contract_hash),
        (receipt.criterion_id, criterion.id),
        (
            receipt.expected_manifest_sha256,
            context.expected_manifest.manifest_sha256,
        ),
        (receipt.execution_id, probe_run.execution_id),
        (receipt.records_sha256, probe_run.records_sha256),
    )
    if any(
        not hmac.compare_digest(actual, expected)
        for actual, expected in expected_receipt_fields
    ):
        _reject("The execution receipt does not bind this exact evidence chain.")

    recomputed = evaluate_performance_criterion(criterion, probe_run)
    if recomputed != decision.performance_verdict:
        _reject("The supplied decision does not match recalculated evidence.")
    if recomputed.verdict not in {
        VerdictStatus.PASS,
        VerdictStatus.FAIL,
        VerdictStatus.NOT_PROVEN,
    }:
        _reject("The performance Evidence Pack status is unsupported.")
    return recomputed


def _require_context_bindings(context: ValidatedPerformanceContext) -> None:
    contract = context.contract
    criterion = context.criterion
    workload = context.workload
    config = context.probe_config

    if not hmac.compare_digest(
        context.workload_sha256,
        hashlib.sha256(context.workload_bytes).hexdigest(),
    ):
        _reject("The validated workload bytes do not match their digest.")
    if not hmac.compare_digest(
        context.workload_sha256,
        contract.workload.sha256,
    ):
        _reject("The workload is not bound to the frozen contract.")
    if not hmac.compare_digest(
        context.prompt_sha256,
        hashlib.sha256(context.prompt_bytes).hexdigest(),
    ):
        _reject("The validated prompt bytes do not match their digest.")
    if not hmac.compare_digest(
        context.prompt_sha256,
        workload.prompt_fixture_sha256,
    ):
        _reject("The prompt fixture is not bound to the validated workload.")

    aligned = (
        (workload.endpoint, config.endpoint),
        (workload.model, config.model),
        (workload.request_count, config.request_count),
        (workload.concurrency, config.concurrency),
        (workload.warmup_count, config.warmup_count),
        (float(workload.timeout_seconds), float(config.timeout_seconds)),
        (workload.max_tokens, config.max_tokens),
        (workload.max_stream_bytes, config.max_stream_bytes),
        (workload.workload_id, criterion.workload_slice),
        (workload.adapter, criterion.adapter),
        (workload.adapter_version, criterion.adapter_version),
        (workload.model, contract.target_system.model),
    )
    if any(left != right for left, right in aligned):
        _reject("The contract, criterion, workload, and probe are not aligned.")
    try:
        independently_derived = build_manifest(config, context.prompts)
    except (
        ProbeConfigurationError,
        ProbeEvidenceError,
        TypeError,
        ValueError,
    ) as error:
        raise PerformanceReportIntegrityError(
            "The expected probe manifest cannot be independently derived."
        ) from error
    if independently_derived != context.expected_manifest:
        _reject("The expected probe manifest does not match its bound inputs.")


def _ordered_limitations(
    verdict: PerformanceCriterionVerdict,
    contract_non_goals: tuple[str, ...],
) -> tuple[str, ...]:
    required = (
        "TTFT is client-observed and includes network, proxy, queueing, "
        "and inference time.",
        "This run proves only the frozen model, workload, endpoint, and "
        "measurement conditions shown in this pack.",
    )
    return tuple(
        dict.fromkeys(
            (*verdict.limitations, *contract_non_goals, *required)
        )
    )


def _require_outcome_counts(
    verdict: PerformanceCriterionVerdict,
) -> PerformanceOutcomeCounts:
    counts = verdict.outcome_counts
    if type(counts) is not PerformanceOutcomeCounts:
        _reject("The recalculated outcome population is unavailable.")
    all_outcomes = (
        counts.success
        + counts.http_error
        + counts.timeout
        + counts.protocol_error
        + counts.transport_error
        + counts.cancelled
        + counts.internal_error
    )
    if (
        all_outcomes != verdict.attempted_count
        or counts.success != verdict.successful_count
        or counts.external_error_count != verdict.error_count
    ):
        _reject("The recalculated outcome population is inconsistent.")
    return counts


def _outcome_summary(
    counts: PerformanceOutcomeCounts,
    attempted_count: int,
) -> str:
    parts = [
        f"{attempted_count} attempts",
        f"{counts.success} successful",
    ]
    labels = (
        (counts.http_error, "HTTP error", "HTTP errors"),
        (counts.timeout, "timeout", "timeouts"),
        (counts.protocol_error, "protocol error", "protocol errors"),
        (counts.transport_error, "transport error", "transport errors"),
        (counts.cancelled, "cancelled", "cancelled"),
        (counts.internal_error, "internal error", "internal errors"),
    )
    parts.extend(
        f"{count} {singular if count == 1 else plural}"
        for count, singular, plural in labels
        if count > 0
    )
    return " · ".join(parts)


def _counting_policy_copy(
    context: ValidatedPerformanceContext,
    probe_run: ProbeRun,
    verdict: PerformanceCriterionVerdict,
) -> tuple[str, str]:
    policy = probe_run.manifest.measurement_policy
    if policy is None:
        return (
            "Warmups and readiness preflight are outside the measured "
            f"population · retries {context.workload.retries} · invalid "
            "evidence is NOT PROVEN.",
            f"Calculation policy: {verdict.calculation_version}",
        )
    return (
        "Warmups and readiness preflight are excluded · "
        f"retries {policy.retries} · invalid evidence is "
        f"{policy.invalid_evidence_disposition}.",
        f"Frozen population policy SHA-256: {policy.policy_sha256}",
    )


def _format_ttft(value_ns: int | None) -> str:
    if value_ns is None:
        return "Not available"
    milliseconds = Decimal(value_ns) / Decimal(1_000_000)
    return f"{_decimal_text(milliseconds)} ms ({value_ns:,} ns)"


def _format_rate(value: Decimal | None) -> str:
    if value is None:
        return "Not available"
    return f"{_decimal_text(value * Decimal(100))}%"


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _safe(value: object) -> str:
    return escape(str(value), quote=True)


def _reject(message: str) -> None:
    raise PerformanceReportIntegrityError(message)


_HTML_TEMPLATE: Final = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none';
                 font-src 'none'; connect-src 'none'; media-src 'none';
                 object-src 'none'; frame-src 'none'; base-uri 'none';
                 form-action 'none'">
  <meta name="exitspec-report-schema" content="{schema_version}">
  <title>ExitSpec performance Evidence Pack — {contract_id}</title>
  <style>
    :root {{
      color-scheme: dark;
      --canvas: #0b0d0c;
      --mast: #101310;
      --sheet: #151815;
      --raised: #1b1f1b;
      --primary: #f2f0e8;
      --secondary: #bec4ba;
      --muted: #858d84;
      --rule: #30362f;
      --signal: #ff6b3d;
      --success: #78d6a3;
      --danger: #ff7c68;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system,
        BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--canvas);
      color: var(--primary);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      min-width: 320px;
      margin: 0;
      background: var(--canvas);
    }}
    main {{
      width: min(1040px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 48px;
    }}
    p, h1, h2, dl, dd {{ margin: 0; }}
    .mast {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      border-bottom: 1px solid var(--rule);
      padding: 0 2px 14px;
    }}
    .brand {{ font-size: .92rem; font-weight: 800; letter-spacing: -.02em; }}
    .brand span {{ color: var(--signal); }}
    .schema {{
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: .68rem;
    }}
    .summary {{
      margin-top: 14px;
      border: 1px solid var(--rule);
      border-top: 3px solid var(--signal);
      border-radius: 14px;
      background: var(--sheet);
      padding: 20px;
    }}
    .eyebrow, dt {{
      color: var(--muted);
      font-size: .66rem;
      font-weight: 800;
      letter-spacing: .1em;
      text-transform: uppercase;
    }}
    .verdict {{
      display: grid;
      grid-template-columns: minmax(150px, .32fr) minmax(0, 1fr);
      align-items: end;
      gap: 24px;
    }}
    h1 {{
      margin-top: 5px;
      color: var(--signal);
      font-size: clamp(2.4rem, 6vw, 4.5rem);
      letter-spacing: -.065em;
      line-height: .88;
    }}
    .status-PASS h1, .status-PASS .fact-status {{ color: var(--success); }}
    .status-FAIL h1, .status-FAIL .fact-status {{ color: var(--danger); }}
    .status-NOT_PROVEN h1,
    .status-NOT_PROVEN .fact-status {{ color: var(--signal); }}
    .reason {{
      max-width: 650px;
      color: var(--secondary);
      font-size: .94rem;
      line-height: 1.5;
    }}
    .context {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 1px;
      overflow: hidden;
      margin-top: 18px;
      border: 1px solid var(--rule);
      border-radius: 10px;
      background: var(--rule);
    }}
    .context div {{ min-width: 0; background: var(--raised); padding: 11px; }}
    dd {{
      overflow-wrap: anywhere;
      margin-top: 4px;
      color: var(--primary);
      font-size: .78rem;
    }}
    .facts {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 10px;
    }}
    .fact-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 13px;
      border: 1px solid var(--rule);
      border-radius: 12px;
      background: var(--sheet);
      padding: 15px;
    }}
    .fact-row h2 {{ font-size: .94rem; letter-spacing: -.015em; }}
    .fact-status {{
      align-self: start;
      font-size: .68rem;
      font-weight: 850;
      letter-spacing: .08em;
    }}
    .comparison {{
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: 1fr auto 1fr;
      align-items: center;
      gap: 10px;
      border-top: 1px solid var(--rule);
      padding-top: 12px;
    }}
    .value span {{
      display: block;
      margin-bottom: 4px;
      color: var(--muted);
      font-size: .64rem;
      font-weight: 800;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    .value strong {{
      overflow-wrap: anywhere;
      color: var(--primary);
      font-size: .84rem;
    }}
    .operator {{ color: var(--signal); font-size: 1.15rem; font-weight: 900; }}
    .samples {{
      grid-column: 1 / -1;
      color: var(--secondary);
      font-size: .72rem;
      line-height: 1.45;
    }}
    .samples span {{ color: var(--muted); }}
    .counting {{
      display: grid;
      grid-template-columns: minmax(170px, .32fr) minmax(0, 1fr);
      gap: 18px;
      margin-top: 10px;
      border: 1px solid var(--rule);
      border-left: 3px solid var(--signal);
      border-radius: 12px;
      background: var(--raised);
      padding: 14px 15px;
    }}
    .counting h2 {{ margin-top: 4px; font-size: .92rem; }}
    .counting-copy {{ display: grid; gap: 4px; min-width: 0; }}
    .counting-copy p {{
      color: var(--secondary);
      font-size: .74rem;
      line-height: 1.45;
    }}
    .counting-copy .outcomes {{ color: var(--primary); font-weight: 750; }}
    .counting-copy .policy-identity {{
      overflow-wrap: anywhere;
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: .66rem;
    }}
    .details {{
      display: grid;
      grid-template-columns: minmax(0, 1.25fr) minmax(250px, .75fr);
      gap: 10px;
      margin-top: 10px;
    }}
    .panel {{
      border: 1px solid var(--rule);
      border-radius: 12px;
      background: var(--sheet);
      padding: 15px;
    }}
    .panel h2 {{ margin-bottom: 9px; font-size: .86rem; }}
    .identities {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 9px 16px;
    }}
    .mono {{
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: .7rem;
    }}
    ul {{ margin: 0; padding-left: 18px; }}
    li {{
      margin-top: 6px;
      color: var(--secondary);
      font-size: .76rem;
      line-height: 1.45;
    }}
    .notice {{
      margin-top: 10px;
      border-left: 3px solid var(--signal);
      border-radius: 8px;
      background: var(--raised);
      padding: 12px 14px;
      color: var(--secondary);
      font-size: .78rem;
      line-height: 1.5;
    }}
    .notice strong {{ color: var(--primary); }}
    @media (max-width: 760px) {{
      .verdict, .facts, .counting, .details {{ grid-template-columns: 1fr; }}
      .context {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .schema {{ display: none; }}
    }}
    @media print {{
      :root {{ color-scheme: light; }}
      body, main {{ background: white; color: #171a18; }}
      .summary, .fact-row, .counting, .panel {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <main class="status-{status}" data-verdict="{status}">
    <header class="mast">
      <p class="brand">Exit<span>Spec</span> · Performance Evidence Pack</p>
      <p class="schema">{schema_version}</p>
    </header>

    <section class="summary" aria-labelledby="report-verdict">
      <div class="verdict">
        <div>
          <p class="eyebrow">Evidence verdict</p>
          <h1 id="report-verdict">{status_label}</h1>
        </div>
        <p class="reason">{reason}</p>
      </div>
      <dl class="context" aria-label="POC context">
        <div><dt>Customer</dt><dd>{customer}</dd></div>
        <div><dt>Use case</dt><dd>{use_case}</dd></div>
        <div><dt>Model</dt><dd>{model}</dd></div>
        <div><dt>Workload</dt><dd>{workload}</dd></div>
      </dl>
    </section>

    <section class="facts" aria-label="Measured performance facts">
      <article class="fact-row status-{ttft_status}">
        <h2>p95 client-observed TTFT</h2>
        <span class="fact-status">{ttft_status}</span>
        <div class="comparison">
          <div class="value">
            <span>Observed</span>
            <strong>{ttft_observed}</strong>
          </div>
          <span class="operator" aria-label="must be">{ttft_operator}</span>
          <div class="value">
            <span>Threshold</span>
            <strong>{ttft_threshold}</strong>
          </div>
        </div>
        <p class="samples">{ttft_samples} · <span>{ttft_minimum}</span></p>
      </article>

      <article class="fact-row status-{error_status}">
        <h2>Measured error rate</h2>
        <span class="fact-status">{error_status}</span>
        <div class="comparison">
          <div class="value">
            <span>Observed</span>
            <strong>{error_observed}</strong>
          </div>
          <span class="operator" aria-label="must be">{error_operator}</span>
          <div class="value">
            <span>Threshold</span>
            <strong>{error_threshold}</strong>
          </div>
        </div>
        <p class="samples">{error_samples} · <span>{error_minimum}</span></p>
      </article>
    </section>

    <section class="counting" aria-labelledby="counting-title">
      <div>
        <p class="eyebrow">Measurement population</p>
        <h2 id="counting-title">How results were counted</h2>
      </div>
      <div class="counting-copy">
        <p class="outcomes">{outcome_summary}</p>
        <p>{latency_population}</p>
        <p>{reliability_population}</p>
        <p>{counting_boundary}</p>
        <p class="policy-identity">{counting_identity}</p>
      </div>
    </section>

    <section class="details" aria-label="Evidence identity and limitations">
      <div class="panel">
        <h2>Exact evidence identity</h2>
        <dl class="identities">
          <div><dt>Run</dt><dd class="mono">{run_id}</dd></div>
          <div><dt>Criterion</dt><dd>{criterion_id} · {criterion_title}</dd></div>
          <div><dt>Contract</dt><dd>{contract_id} · v{contract_version}</dd></div>
          <div><dt>Receipt</dt><dd class="mono">{receipt_id}</dd></div>
          <div><dt>Manifest SHA-256</dt><dd class="mono">{manifest_hash}</dd></div>
          <div><dt>Records SHA-256</dt><dd class="mono">{records_hash}</dd></div>
        </dl>
      </div>
      <div class="panel">
        <h2>Limits of this evidence</h2>
        <ul>
{limitations}
        </ul>
      </div>
    </section>

    <p class="notice"><strong>Evidence is not authorization.</strong> This pack
      reports what the frozen test observed. A human still decides whether to
      ship, expand traffic, procure, spend, or change the production system.</p>
  </main>
</body>
</html>
"""


__all__ = [
    "PERFORMANCE_REPORT_SCHEMA_VERSION",
    "PerformanceReportIntegrityError",
    "render_performance_evidence_pack",
]
