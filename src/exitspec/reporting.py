"""Static, inspectable POC proof rendering for ExitSpec."""

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


def _source_quote(criterion: Criterion) -> str:
    """Return a short, safe explanation of where a criterion came from."""

    source = criterion.source
    if source is None:
        return (
            "No customer quote is attached. This criterion was explicitly added by a "
            "human reviewer."
        )
    return "{0} said: “{1}” ({2})".format(
        escape(source.speaker), escape(source.quote), escape(source.location)
    )


def _criterion_rule_summary(criterion: Criterion) -> str:
    """Use plain language for the currently supported proportion rule."""

    return (
        "At least {threshold} {metric} across at least {minimum_samples} samples. "
        "The two-sided {confidence_level} Wilson lower bound must also meet "
        "{threshold}."
    ).format(
        threshold=_rate(criterion.rule.threshold),
        metric=escape(criterion.metric.value.replace("_", " ")),
        minimum_samples=criterion.rule.minimum_samples,
        confidence_level="{0:.0%}".format(criterion.rule.confidence_level),
    )


def _evidence_sufficiency_rows(
    criterion: Criterion, measurement: ProportionMeasurement
) -> str:
    """Render the evidence checks that can make a result insufficient."""

    evidence_state = (
        "Present: {0}".format(
            ", ".join(escape(reference) for reference in measurement.evidence_refs)
        )
        if measurement.evidence_refs
        else "Missing: no raw evidence records were produced."
    )
    sample_state = "{0} / {1} collected ({2})".format(
        measurement.sample_count,
        criterion.rule.minimum_samples,
        "minimum met"
        if measurement.sample_count >= criterion.rule.minimum_samples
        else "minimum not met",
    )
    execution_state = "Completed"
    if measurement.external_blocked_reason:
        execution_state = "Blocked: {0}".format(
            escape(measurement.external_blocked_reason)
        )
    elif measurement.internal_error:
        execution_state = "Measurement error: {0}".format(
            escape(measurement.internal_error)
        )

    rows = (
        ("Execution", execution_state),
        ("Raw evidence records", evidence_state),
        (
            "Run metadata",
            "Complete" if measurement.metadata_complete else "Missing required metadata",
        ),
        (
            "Approved workload",
            "Fixture hash matches" if measurement.workload_hash_matches else "Fixture hash mismatch",
        ),
        (
            "Artifact integrity",
            "Valid" if measurement.artifact_integrity_valid else "Integrity check failed",
        ),
        ("Minimum sample count", sample_state),
    )
    return "\n".join(
        "<tr><th>{0}</th><td>{1}</td></tr>".format(escape(label), value)
        for label, value in rows
    )


def _human_next_action(verdict: OverallVerdict) -> str:
    """State the human decision needed after a proof outcome without authorizing it."""

    actions = {
        "PASS": (
            "Review this Proof Pack with the customer and decide whether the stated "
            "POC scope is sufficient for the next step. This result alone does not "
            "authorize any action."
        ),
        "FAIL": (
            "Review the failed evidence with the customer and POC owners. Decide "
            "whether to revise the plan, change the system, or stop this POC scope."
        ),
        "NOT_PROVEN": (
            "Close the evidence gaps, then re-run the frozen contract. Do not treat "
            "this result as a pass."
        ),
        "BLOCKED": (
            "Resolve the external block, verify the run conditions, then re-run the "
            "frozen contract before drawing a conclusion."
        ),
    }
    return actions[verdict.verdict.value]


def _limitations(criterion_verdict: CriterionVerdict, contract: POCContract) -> str:
    """Keep reported limits visible rather than burying them in an artifact."""

    limitations = list(criterion_verdict.limitations)
    limitations.extend(contract.non_goals)
    if not limitations:
        limitations.append("No additional limitations were recorded for this run.")
    return "\n".join("<li>{0}</li>".format(escape(item)) for item in limitations)


def render_customer_draft(contract: POCContract) -> str:
    """Render a static, customer-facing acceptance draft for the review/share step.

    It intentionally describes a proposed POC test. It neither freezes a contract nor
    grants permission to deploy, buy, or take another operational action.
    """

    criterion_cards = "\n".join(
        """<article class="criterion-card">
  <p class="label">What we heard</p>
  <p>{source_quote}</p>
  <h2>{title}</h2>
  <p>{claim}</p>
  <p class="label">How we will measure it</p>
  <p>{rule}</p>
  <dl>
    <div><dt>Workload slice</dt><dd>{workload_slice}</dd></div>
    <div><dt>Measurement adapter</dt><dd>{adapter}@{adapter_version}</dd></div>
    <div><dt>Evidence retained</dt><dd>{evidence_policy}</dd></div>
  </dl>
</article>""".format(
            source_quote=_source_quote(criterion),
            title=escape(criterion.title),
            claim=escape(criterion.normalized_claim),
            rule=_criterion_rule_summary(criterion),
            workload_slice=escape(criterion.workload_slice),
            adapter=escape(criterion.adapter),
            adapter_version=escape(criterion.adapter_version),
            evidence_policy=escape(criterion.evidence_policy),
        )
        for criterion in contract.criteria
    )
    is_frozen = contract.canonical_hash is not None
    version_state = (
        "This version is frozen with canonical hash <code>{0}</code>. Any meaningful "
        "change requires a new version."
    ).format(escape(contract.canonical_hash)) if is_frozen else (
        "This version is not frozen yet. Please review the wording and measurement "
        "before a human freezes the agreed version."
    )

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ExitSpec customer review draft — {contract_id}</title>
  <style>
    :root {{ color: #172033; background: #f5f7fb; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; }} main {{ max-width: 940px; margin: 0 auto; padding: 40px 24px 56px; }}
    h1 {{ font-size: clamp(2rem, 6vw, 3.75rem); letter-spacing: -.045em; margin: 6px 0 16px; }} h2 {{ margin: 6px 0 10px; }}
    p {{ line-height: 1.55; }} .eyebrow, .label {{ color: #52647a; font-size: .78rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    .lead {{ color: #39485f; font-size: 1.15rem; max-width: 720px; }} .notice, .criterion-card {{ background: #fff; border: 1px solid #dfe5ef; border-radius: 14px; padding: 22px; margin-top: 18px; }}
    .notice {{ border-color: #c8d7f4; background: #f3f7ff; }} .criterion-card p {{ color: #39485f; }}
    dl {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin: 20px 0 0; }} dt {{ color: #52647a; font-size: .78rem; font-weight: 700; text-transform: uppercase; }} dd {{ margin: 6px 0 0; line-height: 1.45; }} code {{ word-break: break-all; }}
    @media (max-width: 720px) {{ main {{ padding: 28px 16px 40px; }} dl {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main>
    <p class="eyebrow">ExitSpec / customer review draft</p>
    <h1>Proposed POC acceptance criteria</h1>
    <p class="lead">This is the plain-language test we propose for {customer}. It turns the POC promise into a measurement plan before anyone treats results as proof.</p>

    <section class="notice">
      <p class="label">Review before testing</p>
      <p>{version_state}</p>
      <p>This draft documents a proposed POC test. It does not authorize deployment, spending, procurement, production traffic, or any other action.</p>
    </section>

    <section class="notice">
      <p class="label">POC context</p>
      <p><strong>Use case:</strong> {use_case}</p>
      <p><strong>Target under test:</strong> {provider} / {endpoint_class} / {model}</p>
      <p><strong>Approved workload version:</strong> <code>{workload_hash}</code></p>
    </section>

    {criterion_cards}

    <section class="notice">
      <p class="label">Confirmation requested</p>
      <p>Please confirm that the quoted requirement, metric, workload, and acceptance rule reflect the POC we should run. If anything is wrong, change the draft before it is frozen and measured.</p>
    </section>
  </main>
</body>
</html>
""".format(
        contract_id=escape(contract.id),
        customer=escape(contract.customer),
        version_state=version_state,
        use_case=escape(contract.use_case),
        provider=escape(contract.target_system.provider),
        endpoint_class=escape(contract.target_system.endpoint_class),
        model=escape(contract.target_system.model),
        workload_hash=escape(contract.workload.sha256),
        criterion_cards=criterion_cards,
    )


def render_decision_packet(
    contract: POCContract,
    manifest: RunManifest,
    criterion: Criterion,
    measurement: ProportionMeasurement,
    criterion_verdict: CriterionVerdict,
    overall: OverallVerdict,
) -> str:
    """Render a customer-readable Proof Pack while preserving the Brick 1 API.

    The historical function name and the ``decision-packet.html`` output path remain
    stable for callers. The rendered artifact is evidence for a defined POC criterion,
    not an authorization decision.
    """

    contract_hash = contract.canonical_hash or "Unavailable — contract was not frozen"
    frozen_at = (
        contract.frozen_at.isoformat()
        if contract.frozen_at is not None
        else "Unavailable — contract was not frozen"
    )
    evidence_sufficiency = _evidence_sufficiency_rows(criterion, measurement)

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ExitSpec Proof Pack — {contract_id}</title>
  <style>
    :root {{ color: #172033; background: #f5f7fb; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; }} main {{ max-width: 1040px; margin: 0 auto; padding: 40px 24px 56px; }}
    h1 {{ font-size: clamp(2.1rem, 6vw, 4rem); letter-spacing: -.05em; margin: 6px 0 10px; }} h2 {{ margin: 0 0 14px; }} p {{ line-height: 1.55; }}
    .eyebrow {{ color: #52647a; font-size: .78rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }} .lead {{ color: #39485f; font-size: 1.12rem; max-width: 760px; }}
    section {{ background: #fff; border: 1px solid #dfe5ef; border-radius: 14px; padding: 24px; margin-top: 18px; }} .verdict-panel {{ border-color: #c8d7f4; background: #f3f7ff; }}
    .verdict {{ display: inline-block; border-radius: 999px; font-size: .9rem; font-weight: 750; padding: 8px 12px; background: #e7edf9; }} .status-PASS {{ background: #e6f4eb; color: #1f6b43; }} .status-FAIL {{ background: #fde9e8; color: #9b2c2c; }} .status-BLOCKED {{ background: #fff4d8; color: #805b00; }} .status-NOT_PROVEN {{ background: #eeeaf9; color: #62489a; }}
    table {{ border-collapse: collapse; width: 100%; }} th, td {{ border-bottom: 1px solid #e8edf4; padding: 12px 0; text-align: left; vertical-align: top; }} th {{ color: #52647a; font-size: .88rem; font-weight: 700; padding-right: 24px; width: 31%; }}
    ul {{ line-height: 1.55; margin-bottom: 0; padding-left: 20px; }} code {{ word-break: break-all; }} .disclaimer {{ color: #52647a; font-size: .92rem; }}
    @media (max-width: 700px) {{ main {{ padding: 28px 16px 40px; }} th {{ display: block; width: auto; padding-bottom: 4px; }} td {{ display: block; padding-top: 4px; }} }}
  </style>
</head>
<body>
  <main>
    <p class="eyebrow">ExitSpec / POC Proof Pack / synthetic demonstration</p>
    <h1>Proof Pack: {criterion_title}</h1>
    <p class="lead">A readable record of the agreed POC test, the evidence collected, and what a human needs to decide next.</p>

    <section class="verdict-panel">
      <p class="eyebrow">Evidence outcome</p>
      <p class="verdict status-{overall_verdict}">{overall_verdict}</p>
      <p>{overall_reason}</p>
      <p class="disclaimer">This report documents evidence against a POC acceptance contract. It does not authorize deployment, spending, procurement, production traffic, or any external action.</p>
    </section>

    <section>
      <p class="eyebrow">1. What the customer asked us to prove</p>
      <h2>Source quote</h2>
      <p>{source_quote}</p>
      <p><strong>Normalized acceptance claim:</strong> {normalized_claim}</p>
    </section>

    <section>
      <p class="eyebrow">2. The frozen agreement</p>
      <h2>Frozen contract</h2>
      <table>
        <tr><th>Contract</th><td>{contract_id} v{contract_version}</td></tr>
        <tr><th>Contract status</th><td>{contract_status}</td></tr>
        <tr><th>Frozen at</th><td>{frozen_at}</td></tr>
        <tr><th>Canonical hash</th><td><code>{contract_hash}</code></td></tr>
        <tr><th>POC use case</th><td>{use_case}</td></tr>
      </table>
    </section>

    <section>
      <p class="eyebrow">3. The exact test</p>
      <h2>Exact measurement</h2>
      <table>
        <tr><th>Criterion</th><td>{criterion_id}: {criterion_title}</td></tr>
        <tr><th>Metric and aggregation</th><td>{metric} / {aggregation}</td></tr>
        <tr><th>Acceptance rule</th><td>{rule_summary}</td></tr>
        <tr><th>Workload slice</th><td>{workload_slice}</td></tr>
        <tr><th>Workload hash</th><td><code>{workload_hash}</code></td></tr>
        <tr><th>Measurement adapter</th><td>{adapter}@{adapter_version}</td></tr>
      </table>
    </section>

    <section>
      <p class="eyebrow">4. Is the evidence sufficient?</p>
      <h2>Evidence sufficiency</h2>
      <table>{evidence_sufficiency}</table>
    </section>

    <section>
      <p class="eyebrow">5. What the test observed</p>
      <h2>Criterion result</h2>
      <p class="verdict status-{criterion_verdict}">{criterion_verdict}</p>
      <p>{criterion_reason}</p>
      <table>
        <tr><th>Successful cases</th><td>{success_count} / {measurement_sample_count}</td></tr>
        <tr><th>Observed rate</th><td>{observed_rate}</td></tr>
        <tr><th>Wilson lower bound</th><td>{lower_bound}</td></tr>
        <tr><th>Calculation version</th><td>{calculation_version}</td></tr>
        <tr><th>Run</th><td>{run_id} ({run_status})</td></tr>
        <tr><th>Target under test</th><td>{provider} / {endpoint_class} / {model}</td></tr>
      </table>
    </section>

    <section>
      <p class="eyebrow">6. What this does not prove</p>
      <h2>Limits of this proof</h2>
      <ul>{limitations}</ul>
    </section>

    <section>
      <p class="eyebrow">7. Human follow-up</p>
      <h2>Explicit next human action</h2>
      <p>{next_action}</p>
    </section>
  </main>
</body>
</html>
""".format(
        contract_id=escape(contract.id),
        contract_version=escape(contract.version),
        contract_status=escape(contract.status.value),
        contract_hash=escape(contract_hash),
        frozen_at=escape(frozen_at),
        use_case=escape(contract.use_case),
        overall_verdict=escape(overall.verdict.value),
        overall_reason=escape(overall.reason),
        source_quote=_source_quote(criterion),
        normalized_claim=escape(criterion.normalized_claim),
        criterion_id=escape(criterion.id),
        criterion_title=escape(criterion.title),
        metric=escape(criterion.metric.value.replace("_", " ")),
        aggregation=escape(criterion.aggregation),
        rule_summary=_criterion_rule_summary(criterion),
        workload_slice=escape(criterion.workload_slice),
        workload_hash=escape(contract.workload.sha256),
        adapter=escape(criterion.adapter),
        adapter_version=escape(criterion.adapter_version),
        evidence_sufficiency=evidence_sufficiency,
        criterion_verdict=escape(criterion_verdict.verdict.value),
        criterion_reason=escape(criterion_verdict.reason),
        success_count=measurement.success_count,
        measurement_sample_count=measurement.sample_count,
        observed_rate=_rate(criterion_verdict.observed_rate),
        lower_bound=_rate(criterion_verdict.confidence_lower_bound),
        calculation_version=escape(criterion_verdict.calculation_version),
        run_id=escape(manifest.run_id),
        run_status=escape(manifest.status.value),
        provider=escape(manifest.provider),
        endpoint_class=escape(manifest.endpoint_class),
        model=escape(manifest.model),
        limitations=_limitations(criterion_verdict, contract),
        next_action=escape(_human_next_action(overall)),
    )
