"""Customer-facing Evidence Packs for managed Inferdrome v2 receipts."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from .inferdrome_managed_import import InferdromeManagedImportResult
from .inferdrome_reporting_v2 import validate_managed_receipt
from .models import POCContract


def render_managed_inferdrome_evidence_pack(
    *,
    contract: POCContract,
    result: InferdromeManagedImportResult,
) -> bytes:
    """Render one deterministic, bounded report from ExitSpec-owned facts."""

    receipt = validate_managed_receipt(result.receipt)
    if contract.canonical_hash is None:
        raise ValueError("A frozen contract digest is required.")
    if (
        receipt.contract_hash != contract.canonical_hash
        or receipt.acceptance_verdict != result.verdict.value
    ):
        raise ValueError("The managed Evidence Pack inputs are not mutually bound.")

    issues = (
        "None — exact requested slice"
        if not receipt.applicability_codes
        else ", ".join(code.value for code in receipt.applicability_codes)
    )
    observed_ns = receipt.metric.recalculated_value_ns
    observed_latency = (
        "Not available" if observed_ns is None else _milliseconds(observed_ns)
    )
    threshold = _milliseconds(receipt.metric.threshold_ns)
    error_percent = _percent(receipt.population.observed_error_rate)
    operator_symbol = "<" if receipt.metric.operator == "lt" else "≤"
    verdict = receipt.acceptance_verdict
    summary = _summary(verdict, issues)
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ExitSpec — Managed inference Evidence Pack</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #17191c; color: #f2f1ed; }}
    main {{ width: min(920px, calc(100% - 40px)); margin: 0 auto; padding: 34px 0 52px; }}
    header, section {{ border: 1px solid #34383e; background: #202328; padding: 20px; }}
    section {{ margin-top: 12px; }}
    .eyebrow {{ margin: 0 0 8px; color: #f2a65a; font: 700 12px ui-monospace, monospace; text-transform: uppercase; }}
    h1, h2 {{ margin: 0; }} h1 {{ font-size: 27px; }} h2 {{ font-size: 16px; }}
    .verdict {{ display: inline-block; margin: 18px 0 8px; border-left: 3px solid #f2a65a; padding-left: 12px; font-size: 30px; font-weight: 800; }}
    .summary, .boundary {{ color: #c4c7cc; line-height: 1.55; }}
    dl {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin: 16px 0 0; background: #34383e; }}
    dl div {{ min-width: 0; background: #202328; padding: 12px; }}
    dt {{ color: #9ea4ad; font-size: 12px; }} dd {{ margin: 5px 0 0; overflow-wrap: anywhere; }}
    code {{ color: #d8dbe0; font-size: 12px; }}
    @media (max-width: 640px) {{ dl {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body><main>
  <header>
    <p class="eyebrow">ExitSpec independent acceptance receipt</p>
    <h1>Managed inference Evidence Pack</h1>
    <p class="verdict">{verdict}</p>
    <p class="summary">{summary}</p>
  </header>
  <section>
    <h2>Frozen customer rule</h2>
    <dl>
      <div><dt>Contract</dt><dd>{contract_id} · v{contract_version}</dd></div>
      <div><dt>Contract SHA-256</dt><dd><code>{contract_hash}</code></dd></div>
      <div><dt>Configured maximum concurrency</dt><dd>{required_concurrency}</dd></div>
      <div><dt>Requested TTFT definition</dt><dd><code>{requested_metric_definition}</code></dd></div>
      <div><dt>Native p95 TTFT threshold</dt><dd>{operator_symbol} {threshold}</dd></div>
      <div><dt>Reliability threshold</dt><dd>&lt; {error_threshold:g}%</dd></div>
      <div><dt>Counted population</dt><dd>{required_attempts} canonical measured-request records</dd></div>
    </dl>
  </section>
  <section>
    <h2>Independent recalculation</h2>
    <dl>
      <div><dt>Native p95 TTFT</dt><dd>{observed_latency}</dd></div>
      <div><dt>Observed error rate</dt><dd>{error_percent}</dd></div>
      <div><dt>Records</dt><dd>{attempted} total · {successful} successful · {failed} failed</dd></div>
      <div><dt>Configured maximum concurrency in evidence</dt><dd>{observed_concurrency}</dd></div>
      <div><dt>Metric definition</dt><dd><code>{metric_definition}</code></dd></div>
      <div><dt>Applicability</dt><dd>{issues}</dd></div>
    </dl>
  </section>
  <section>
    <h2>Evidence binding</h2>
    <dl>
      <div><dt>Producer run</dt><dd><code>{run_id}</code></dd></div>
      <div><dt>Unchanged bundle digest</dt><dd><code>{bundle_digest}</code></dd></div>
      <div><dt>ExitSpec receipt</dt><dd><code>{receipt_id}</code></dd></div>
      <div><dt>Receipt purpose</dt><dd>Conformance demonstration</dd></div>
    </dl>
  </section>
  <section class="boundary">
    <h2>Trust and chronology boundary</h2>
    <p>Inferdrome produced the sealed evidence. ExitSpec treated it as untrusted input, verified its internal consistency, independently recalculated supported facts from canonical request records, applied the frozen customer rule, and exclusively issued this verdict.</p>
    <p>This is a retrospective conformance demonstration: the contract was frozen after measurement. Hardware attestation, execution attestation, exact achieved overlap, transport retry behavior, and production authorization are not available. Hashes detect mutation; they do not prove truthful execution or authorship.</p>
  </section>
</main></body></html>
""".format(
        verdict=escape(verdict.replace("_", " ")),
        summary=escape(summary),
        contract_id=escape(contract.id),
        contract_version=escape(contract.version),
        contract_hash=escape(contract.canonical_hash),
        required_concurrency=(receipt.population.required_configured_max_concurrency),
        requested_metric_definition=escape(receipt.metric.requested_definition_id),
        operator_symbol=escape(operator_symbol),
        threshold=escape(threshold),
        error_threshold=(receipt.population.error_threshold_basis_points / 100),
        required_attempts=receipt.population.required_attempts,
        observed_latency=escape(observed_latency),
        error_percent=escape(error_percent),
        attempted=receipt.population.attempted_count,
        successful=receipt.population.successful_count,
        failed=receipt.population.failed_count,
        observed_concurrency=(receipt.population.observed_configured_max_concurrency),
        metric_definition=escape(receipt.metric.observed_definition_id),
        issues=escape(issues),
        run_id=escape(receipt.run_id),
        bundle_digest=escape(receipt.bundle_digest),
        receipt_id=escape(receipt.receipt_id),
    )
    return html.encode("utf-8")


def _milliseconds(nanoseconds: int) -> str:
    return "{0:g} ms".format(nanoseconds / 1_000_000)


def _percent(rate: str) -> str:
    return "{0:f}%".format(Decimal(rate) * 100)


def _summary(verdict: str, issues: str) -> str:
    if verdict == "PASS":
        return "The retained run meets every applicable frozen requirement."
    if verdict == "FAIL":
        return (
            "The retained run conclusively violates an applicable frozen requirement."
        )
    return (
        "The bundle is internally valid, but it cannot prove this requested slice: "
        + issues
        + "."
    )


__all__ = ["render_managed_inferdrome_evidence_pack"]
