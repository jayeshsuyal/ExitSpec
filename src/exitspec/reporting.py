"""Static, inspectable decision-packet rendering for Brick 1."""

from __future__ import annotations

from html import escape
from typing import Optional, Sequence

from .models import (
    Criterion,
    CriterionDraft,
    CriterionVerdict,
    DiscoveryPack,
    DraftStatus,
    OverallVerdict,
    POCContract,
    ProportionMeasurement,
    RunManifest,
)


def _rate(value: Optional[float]) -> str:
    return "—" if value is None else "{0:.2%}".format(value)


def _draft_measurement_summary(draft: CriterionDraft) -> str:
    criterion = draft.proposed_criterion
    if criterion is None:
        return "No executable measurement proposed yet."
    return "{0} ≥ {1:.0%}; at least {2} samples; adapter {3}@{4}".format(
        escape(criterion.metric.value),
        criterion.rule.threshold,
        criterion.rule.minimum_samples,
        escape(criterion.adapter),
        escape(criterion.adapter_version),
    )


def _draft_source_summary(draft: CriterionDraft) -> str:
    if draft.source_span is None:
        return "Human-added: {0}".format(
            escape(draft.human_added_rationale or "No rationale recorded")
        )
    span = draft.source_span
    line_range = (
        str(span.start_line)
        if span.start_line == span.end_line
        else "{0}-{1}".format(span.start_line, span.end_line)
    )
    return "{0}, line {1}: “{2}”".format(
        escape(span.speaker), escape(line_range), escape(span.quote)
    )


def _draft_review_summary(draft: CriterionDraft) -> str:
    if draft.review is None:
        return "No decision recorded yet."
    return "{0} by {1}: {2}".format(
        escape(draft.review.decision.value),
        escape(draft.review.reviewer),
        escape(draft.review.rationale),
    )


def render_define_review(
    discovery_pack: DiscoveryPack,
    reviewed_drafts: Sequence[CriterionDraft],
    contract: POCContract,
) -> str:
    """Render the small, static Define-stage artifact used in the Brick 2 demo."""

    approved_count = sum(
        draft.status == DraftStatus.APPROVED for draft in reviewed_drafts
    )
    rejected_count = sum(
        draft.status == DraftStatus.REJECTED for draft in reviewed_drafts
    )
    pending_count = len(reviewed_drafts) - approved_count - rejected_count
    transcript_rows = "\n".join(
        """<article class=\"transcript-line\">
  <span class=\"line-number\">{line_number}</span>
  <div><strong>{speaker}</strong><p>{text}</p></div>
</article>""".format(
            line_number=line.line_number,
            speaker=escape(line.speaker),
            text=escape(line.text),
        )
        for line in discovery_pack.transcript.lines
    )
    draft_cards = "\n".join(
        """<article class=\"draft-card\">
  <div class=\"card-topline\"><span class=\"draft-id\">{draft_id}</span><span class=\"status status-{status_class}\">{status}</span></div>
  <h3>{claim}</h3>
  <p class=\"label\">Source</p><p>{source}</p>
  <p class=\"label\">Proposed measurement</p><p>{measurement}</p>
  <p class=\"label\">Review</p><p>{review}</p>
  {open_questions}
</article>""".format(
            draft_id=escape(draft.id),
            status=escape(draft.status.value),
            status_class=escape(draft.status.value.lower().replace("_", "-")),
            claim=escape(draft.normalized_claim),
            source=_draft_source_summary(draft),
            measurement=_draft_measurement_summary(draft),
            review=_draft_review_summary(draft),
            open_questions=(
                "<p class=\"label\">Open questions</p><ul>{0}</ul>".format(
                    "".join(
                        "<li>{0}</li>".format(escape(question))
                        for question in draft.open_questions
                    )
                )
                if draft.open_questions
                else ""
            ),
        )
        for draft in reviewed_drafts
    )

    return """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>ExitSpec Define review — {contract_id}</title>
  <style>
    :root {{ color: #172033; background: #f5f7fb; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; }} main {{ max-width: 1240px; margin: 0 auto; padding: 32px 24px 56px; }}
    h1, h2, h3, p {{ margin-top: 0; }} h1 {{ font-size: clamp(2rem, 5vw, 3.5rem); letter-spacing: -.04em; margin-bottom: 10px; }}
    .eyebrow, .label {{ color: #52647a; font-size: .78rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    .lead {{ max-width: 720px; color: #39485f; font-size: 1.1rem; line-height: 1.55; }}
    .summary {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 24px 0; }}
    .summary article, .handoff, .column {{ background: #fff; border: 1px solid #dfe5ef; border-radius: 14px; }}
    .summary article {{ padding: 16px; }} .summary strong {{ display: block; font-size: 1.5rem; margin-top: 4px; }}
    .workspace {{ display: grid; grid-template-columns: minmax(0, .85fr) minmax(0, 1.15fr); gap: 18px; }} .column {{ padding: 22px; }}
    .transcript-line {{ display: grid; grid-template-columns: 28px 1fr; gap: 12px; padding: 14px 0; border-top: 1px solid #e8edf4; }}
    .line-number {{ color: #75859a; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }} .transcript-line p {{ color: #39485f; line-height: 1.5; margin: 5px 0 0; }}
    .draft-card {{ border: 1px solid #dfe5ef; border-radius: 12px; padding: 18px; margin-top: 14px; }} .draft-card h3 {{ line-height: 1.35; margin: 8px 0 18px; }}
    .draft-card p {{ color: #39485f; line-height: 1.45; }} .card-topline {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    .draft-id, .status {{ border-radius: 999px; font-size: .75rem; font-weight: 700; padding: 6px 9px; }} .draft-id {{ background: #eef2f8; color: #52647a; }} .status-approved {{ background: #e6f4eb; color: #1f6b43; }} .status-rejected {{ background: #fde9e8; color: #9b2c2c; }} .status-needs-review {{ background: #fff4d8; color: #805b00; }}
    .handoff {{ margin-top: 18px; padding: 22px; }} .handoff code {{ word-break: break-all; }} ul {{ padding-left: 20px; color: #39485f; }}
    @media (max-width: 760px) {{ .summary, .workspace {{ grid-template-columns: 1fr; }} main {{ padding: 24px 16px 40px; }} }}
  </style>
</head>
<body>
  <main>
    <p class=\"eyebrow\">ExitSpec / Define / synthetic demonstration</p>
    <h1>Agree before you test.</h1>
    <p class=\"lead\">Customer language is useful context, not an executable promise. This review makes every selected claim source-linked, measurable, and explicitly approved before it reaches the frozen contract.</p>

    <section class=\"summary\">
      <article><span class=\"eyebrow\">Candidates</span><strong>{candidate_count}</strong></article>
      <article><span class=\"eyebrow\">Approved</span><strong>{approved_count}</strong></article>
      <article><span class=\"eyebrow\">Rejected</span><strong>{rejected_count}</strong></article>
      <article><span class=\"eyebrow\">Needs review</span><strong>{pending_count}</strong></article>
    </section>

    <section class=\"workspace\">
      <article class=\"column\"><p class=\"eyebrow\">Discovery transcript</p><h2>{transcript_title}</h2>{transcript_rows}</article>
      <article class=\"column\"><p class=\"eyebrow\">Candidate criteria</p><h2>Review decisions</h2>{draft_cards}</article>
    </section>

    <section class=\"handoff\">
      <p class=\"eyebrow\">Ready for Prove</p>
      <h2>{contract_id} v{contract_version} is {contract_status}</h2>
      <p>Only the {approved_count} explicitly approved criterion/criteria entered this contract. The existing freeze-and-run path can now create its canonical hash and evidence packet.</p>
      <code>{contract_hash_note}</code>
    </section>
  </main>
</body>
</html>
""".format(
        contract_id=escape(contract.id),
        candidate_count=len(reviewed_drafts),
        approved_count=approved_count,
        rejected_count=rejected_count,
        pending_count=pending_count,
        transcript_title=escape(discovery_pack.transcript.title),
        transcript_rows=transcript_rows,
        draft_cards=draft_cards,
        contract_version=escape(contract.version),
        contract_status=escape(contract.status.value),
        contract_hash_note="Freeze this approved contract before running it; no hash exists yet.",
    )


def render_decision_packet(
    contract: POCContract,
    manifest: RunManifest,
    criterion: Criterion,
    measurement: ProportionMeasurement,
    criterion_verdict: CriterionVerdict,
    overall: OverallVerdict,
) -> str:
    """Render a minimal report that keeps source, rule, evidence, and verdict adjacent."""

    source = criterion.source
    source_text = "Human-added criterion" if source is None else "{0}: “{1}” ({2})".format(
        escape(source.speaker), escape(source.quote), escape(source.location)
    )
    evidence_links = ", ".join(escape(reference) for reference in measurement.evidence_refs) or "No raw records produced"

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ExitSpec decision packet — {contract_id}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #172033; background: #f5f7fb; margin: 0; }}
    main {{ max-width: 960px; margin: 40px auto; padding: 0 24px 48px; }}
    section {{ background: #fff; border: 1px solid #dfe5ef; border-radius: 12px; padding: 24px; margin-top: 16px; }}
    h1, h2 {{ margin-top: 0; }}
    .eyebrow {{ color: #52647a; font-size: 0.9rem; text-transform: uppercase; letter-spacing: .08em; }}
    .verdict {{ display: inline-block; padding: 8px 12px; border-radius: 999px; font-weight: 700; background: #e8eef9; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 10px 0; border-bottom: 1px solid #e8edf4; vertical-align: top; }}
    th {{ width: 34%; color: #52647a; font-weight: 600; }}
    code {{ word-break: break-all; }}
  </style>
</head>
<body>
  <main>
    <p class="eyebrow">ExitSpec / synthetic evidence packet</p>
    <h1>Support-agent POC decision</h1>
    <p class="verdict">Overall verdict: {overall_verdict}</p>
    <p>{overall_reason}</p>

    <section>
      <h2>Frozen contract</h2>
      <table>
        <tr><th>Contract</th><td>{contract_id} v{contract_version}</td></tr>
        <tr><th>Canonical hash</th><td><code>{contract_hash}</code></td></tr>
        <tr><th>Customer statement</th><td>{source_text}</td></tr>
        <tr><th>Approved rule</th><td>Exact tool selection ≥ {threshold}; at least {minimum_samples} samples; two-sided {confidence_level} Wilson lower bound.</td></tr>
      </table>
    </section>

    <section>
      <h2>Criterion: {criterion_title}</h2>
      <p class="verdict">{criterion_verdict}</p>
      <p>{criterion_reason}</p>
      <table>
        <tr><th>Observed rate</th><td>{observed_rate}</td></tr>
        <tr><th>Sample count</th><td>{sample_count}</td></tr>
        <tr><th>Wilson lower bound</th><td>{lower_bound}</td></tr>
        <tr><th>Evidence</th><td>{evidence_links}</td></tr>
        <tr><th>Calculation version</th><td>{calculation_version}</td></tr>
        <tr><th>Limitations</th><td>{limitations}</td></tr>
      </table>
    </section>

    <section>
      <h2>Run manifest</h2>
      <table>
        <tr><th>Run</th><td>{run_id}</td></tr>
        <tr><th>Target</th><td>{provider} / {endpoint_class} / {model}</td></tr>
        <tr><th>Fixture hash</th><td><code>{fixture_hash}</code></td></tr>
        <tr><th>Run status</th><td>{run_status}</td></tr>
      </table>
    </section>
  </main>
</body>
</html>
""".format(
        contract_id=escape(contract.id),
        contract_version=escape(contract.version),
        contract_hash=escape(contract.canonical_hash or "unavailable"),
        overall_verdict=escape(overall.verdict.value),
        overall_reason=escape(overall.reason),
        source_text=source_text,
        threshold=_rate(criterion.rule.threshold),
        minimum_samples=criterion.rule.minimum_samples,
        confidence_level="{0:.0%}".format(criterion.rule.confidence_level),
        criterion_title=escape(criterion.title),
        criterion_verdict=escape(criterion_verdict.verdict.value),
        criterion_reason=escape(criterion_verdict.reason),
        observed_rate=_rate(criterion_verdict.observed_rate),
        sample_count=criterion_verdict.sample_count,
        lower_bound=_rate(criterion_verdict.confidence_lower_bound),
        evidence_links=evidence_links,
        calculation_version=escape(criterion_verdict.calculation_version),
        limitations=escape("; ".join(criterion_verdict.limitations) or "None recorded."),
        run_id=escape(manifest.run_id),
        provider=escape(manifest.provider),
        endpoint_class=escape(manifest.endpoint_class),
        model=escape(manifest.model),
        fixture_hash=escape(manifest.fixture_hash),
        run_status=escape(manifest.status.value),
    )
