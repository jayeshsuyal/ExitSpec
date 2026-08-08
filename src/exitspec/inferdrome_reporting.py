"""Customer-facing Evidence Pack rendering for accepted Inferdrome imports."""

from __future__ import annotations

from html import escape

from .inferdrome_import import InferdromeImportResult
from .models import POCContract


def render_inferdrome_evidence_pack(
    *,
    contract: POCContract,
    result: InferdromeImportResult,
) -> bytes:
    """Render one bounded report from ExitSpec-owned recalculation facts."""

    if contract.canonical_hash is None:
        raise ValueError("A frozen contract digest is required.")
    verdict = result.performance_verdict
    receipt = result.receipt
    issues = (
        "None"
        if not result.applicability.issues
        else ", ".join(issue.value for issue in result.applicability.issues)
    )
    p95 = (
        "Not available"
        if result.recalculated.p95_ttft_ns is None
        else "{0:g} ms".format(result.recalculated.p95_ttft_ns / 1_000_000)
    )
    error_rate = "{0:g}%".format(result.recalculated.error_rate * 100)
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ExitSpec — Inferdrome Evidence Pack</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0; background: #17191c; color: #f2f1ed; }}
    main {{ width: min(900px, calc(100% - 40px)); margin: 0 auto; padding: 36px 0 56px; }}
    header, section {{ border: 1px solid #34383e; background: #202328; padding: 20px; }}
    section {{ margin-top: 12px; }}
    .eyebrow {{ margin: 0 0 8px; color: #f2a65a; font: 700 12px ui-monospace, monospace; text-transform: uppercase; }}
    h1, h2 {{ margin: 0; }} h1 {{ font-size: 28px; }} h2 {{ font-size: 16px; }}
    .verdict {{ display: inline-block; margin-top: 18px; border-left: 3px solid #f2a65a; padding-left: 12px; font-size: 30px; font-weight: 800; }}
    dl {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin: 16px 0 0; background: #34383e; }}
    dl div {{ min-width: 0; background: #202328; padding: 12px; }}
    dt {{ color: #9ea4ad; font-size: 12px; }} dd {{ margin: 5px 0 0; overflow-wrap: anywhere; }}
    code {{ color: #d8dbe0; font-size: 12px; }}
    .boundary {{ color: #c4c7cc; line-height: 1.55; }}
    @media (max-width: 620px) {{ dl {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body><main>
  <header>
    <p class="eyebrow">ExitSpec independently verified</p>
    <h1>Inferdrome Evidence Pack</h1>
    <p class="verdict">{verdict}</p>
    <p>{reason}</p>
  </header>
  <section>
    <h2>Frozen agreement</h2>
    <dl>
      <div><dt>Contract</dt><dd>{contract_id} · v{contract_version}</dd></div>
      <div><dt>Contract SHA-256</dt><dd><code>{contract_hash}</code></dd></div>
      <div><dt>Criterion</dt><dd>{criterion_id}</dd></div>
      <div><dt>Customer</dt><dd>{customer}</dd></div>
    </dl>
  </section>
  <section>
    <h2>Imported evidence</h2>
    <dl>
      <div><dt>Producer run</dt><dd><code>{producer_run}</code></dd></div>
      <div><dt>Bundle digest</dt><dd><code>{bundle_digest}</code></dd></div>
      <div><dt>Ingestion receipt</dt><dd><code>{receipt_id}</code></dd></div>
      <div><dt>Applicability</dt><dd>{issues}</dd></div>
    </dl>
  </section>
  <section>
    <h2>Independent recalculation</h2>
    <dl>
      <div><dt>Attempts</dt><dd>{attempted}</dd></div>
      <div><dt>Successful</dt><dd>{successful}</dd></div>
      <div><dt>Failed</dt><dd>{failed}</dd></div>
      <div><dt>Observed error rate</dt><dd>{error_rate}</dd></div>
      <div><dt>Observed p95 TTFT</dt><dd>{p95}</dd></div>
      <div><dt>TTFT definition</dt><dd><code>{ttft_definition}</code></dd></div>
    </dl>
  </section>
  <section class="boundary">
    <h2>Trust boundary</h2>
    <p>Inferdrome produced the measurements and sealed bundle. ExitSpec treated those bytes as untrusted input, verified integrity, recalculated supported facts, checked exact contract compatibility, and exclusively issued this acceptance verdict. Producer verdicts were ignored.</p>
    <p>Hashes detect mutation after publication; they do not prove authorship, truthful execution, hardware identity, or endpoint ownership. Evidence is not authorization to ship.</p>
  </section>
</main></body></html>
""".format(
        verdict=escape(verdict.verdict.value.replace("_", " ")),
        reason=escape(verdict.reason),
        contract_id=escape(contract.id),
        contract_version=escape(contract.version),
        contract_hash=escape(contract.canonical_hash),
        criterion_id=escape(receipt.criterion_id),
        customer=escape(contract.customer),
        producer_run=escape(result.run_id),
        bundle_digest=escape(receipt.bundle_digest),
        receipt_id=escape(receipt.receipt_id),
        issues=escape(issues),
        attempted=result.recalculated.attempted_count,
        successful=result.recalculated.successful_count,
        failed=result.recalculated.failed_count,
        error_rate=escape(error_rate),
        p95=escape(p95),
        ttft_definition=escape(result.recalculated.ttft_definition),
    )
    return html.encode("utf-8")


__all__ = ["render_inferdrome_evidence_pack"]
