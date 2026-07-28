"""Static, inspectable POC acceptance-evidence rendering for ExitSpec."""

from __future__ import annotations

from html import escape
from typing import Optional, Sequence

from .contracts import verify_contract_digest
from .models import (
    ContractStatus,
    Criterion,
    CriterionDraft,
    CriterionVerdict,
    DiscoveryPack,
    DraftStatus,
    OverallVerdict,
    POCContract,
    ProportionMeasurement,
    RunManifest,
    InferencePerformanceCriterion,
    ContractCriterion,
)
from .verdicts import aggregate_overall_verdict, evaluate_proportion_criterion


def _rate(value: Optional[float]) -> str:
    return "—" if value is None else "{0:.2%}".format(value)


def _draft_measurement_summary(draft: CriterionDraft) -> str:
    criterion = draft.proposed_criterion
    if criterion is None:
        return "No executable measurement proposed yet."
    if isinstance(criterion, InferencePerformanceCriterion):
        return (
            "Client-observed p95 TTFT {ttft_operator} {ttft_threshold:g} ms; "
            "attempted-request error rate &lt; {error_threshold:.2%}; "
            "both must pass; adapter {adapter}@{version}"
        ).format(
            ttft_operator=_operator_symbol(criterion.ttft_p95.operator),
            ttft_threshold=criterion.ttft_p95.threshold,
            error_threshold=criterion.error_rate.threshold,
            adapter=escape(criterion.adapter),
            version=escape(criterion.adapter_version),
        )
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


def _source_quote(criterion: ContractCriterion) -> str:
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


def _operator_symbol(operator: str) -> str:
    return {
        "lt": "&lt;",
        "lte": "≤",
        "gt": "&gt;",
        "gte": "≥",
        "eq": "=",
    }[operator]


def _criterion_rule_summary(criterion: ContractCriterion) -> str:
    """Use plain language for each supported agreement rule."""

    if isinstance(criterion, InferencePerformanceCriterion):
        return (
            "Client-observed p95 time to first non-empty content must be "
            "{ttft_operator} {ttft_threshold:g} milliseconds across at least "
            "{ttft_samples} successful measured requests. Attempted-request "
            "error rate must be &lt; {error_threshold:.2%} across exactly the "
            "frozen {attempts}-attempt workload. Both checks must pass."
        ).format(
            ttft_operator=_operator_symbol(criterion.ttft_p95.operator),
            ttft_threshold=criterion.ttft_p95.threshold,
            ttft_samples=criterion.ttft_p95.minimum_successful_samples,
            error_threshold=criterion.error_rate.threshold,
            attempts=criterion.error_rate.minimum_attempts,
        )

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
            "Review this POC Acceptance Evidence Pack with the customer and decide "
            "whether the stated POC scope is sufficient for the next step. This "
            "result alone does not authorize any action."
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


def _limitation_items(
    criterion_verdict: CriterionVerdict, contract: POCContract
) -> list[str]:
    """Return every recorded limit in stable, customer-readable order."""

    limitations = list(criterion_verdict.limitations)
    limitations.extend(contract.non_goals)
    if not limitations:
        limitations.append("No additional limitations were recorded for this run.")
    return limitations


def _limitations(criterion_verdict: CriterionVerdict, contract: POCContract) -> str:
    """Keep reported limits visible rather than burying them in an artifact."""

    return "\n".join(
        "<li>{0}</li>".format(escape(item))
        for item in _limitation_items(criterion_verdict, contract)
    )


def _reject_inconsistent_packet(reason: str) -> None:
    """Reject contradictory inputs without echoing customer or evidence content."""

    raise ValueError(
        "Cannot render POC Acceptance Evidence Pack: {0}.".format(reason)
    )


def _validate_decision_packet_inputs(
    contract: POCContract,
    manifest: RunManifest,
    criterion: Criterion,
    measurement: ProportionMeasurement,
    criterion_verdict: CriterionVerdict,
    overall: OverallVerdict,
) -> None:
    """Fail closed unless every rendered decision input belongs to one proof chain."""

    if contract.status != ContractStatus.FROZEN or contract.frozen_at is None:
        _reject_inconsistent_packet("contract is not frozen")
    if contract.canonical_hash is None or not verify_contract_digest(contract):
        _reject_inconsistent_packet("contract digest is missing or invalid")

    if (
        manifest.contract_id != contract.id
        or manifest.contract_version != contract.version
        or manifest.contract_hash != contract.canonical_hash
    ):
        _reject_inconsistent_packet("run manifest does not match the frozen contract")

    if len(contract.criteria) != 1:
        _reject_inconsistent_packet(
            "Brick 1 evidence packs require exactly one frozen criterion"
        )
    frozen_criterion = contract.criteria[0]
    if criterion != frozen_criterion:
        _reject_inconsistent_packet(
            "rendered criterion does not match the frozen criterion"
        )
    if measurement.criterion_id != frozen_criterion.id:
        _reject_inconsistent_packet(
            "measurement does not match the frozen criterion"
        )
    if criterion_verdict.criterion_id != frozen_criterion.id:
        _reject_inconsistent_packet(
            "criterion verdict does not match the frozen criterion"
        )

    expected_criterion_verdict = evaluate_proportion_criterion(
        frozen_criterion, measurement
    )
    if criterion_verdict != expected_criterion_verdict:
        _reject_inconsistent_packet(
            "criterion verdict does not match deterministic recomputation"
        )

    expected_overall = aggregate_overall_verdict(
        contract.criteria, [expected_criterion_verdict]
    )
    if overall != expected_overall:
        _reject_inconsistent_packet(
            "overall verdict does not match deterministic recomputation"
        )


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
  <title>ExitSpec customer confirmation draft — {contract_id}</title>
  <style>
    :root {{ color: #172033; background: #f5f7fb; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; }} main {{ max-width: 940px; margin: 0 auto; padding: 40px 24px 56px; }}
    h1 {{ font-size: clamp(2rem, 6vw, 3.75rem); letter-spacing: -.045em; margin: 6px 0 16px; }} h2 {{ margin: 6px 0 10px; }}
    p {{ line-height: 1.55; }} .eyebrow, .label {{ color: #52647a; font-size: .78rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    .lead {{ color: #39485f; font-size: 1.15rem; max-width: 720px; }} .draft-state {{ display: inline-block; border: 1px solid #e2cc9e; border-radius: 999px; padding: 7px 10px; background: #fff8e7; color: #805b00; font-size: .75rem; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; }} .notice, .criterion-card {{ background: #fff; border: 1px solid #dfe5ef; border-radius: 14px; padding: 22px; margin-top: 18px; }}
    .notice {{ border-color: #c8d7f4; background: #f3f7ff; }} .criterion-card p {{ color: #39485f; }}
    dl {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin: 20px 0 0; }} dt {{ color: #52647a; font-size: .78rem; font-weight: 700; text-transform: uppercase; }} dd {{ margin: 6px 0 0; line-height: 1.45; }} code {{ word-break: break-all; }}
    @media (max-width: 720px) {{ main {{ padding: 28px 16px 40px; }} dl {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main>
    <p class="eyebrow">ExitSpec / customer confirmation draft / not evidence</p>
    <p class="draft-state">Draft — customer confirmation required</p>
    <h1>Customer confirmation draft</h1>
    <p class="label">Proposed POC acceptance criteria</p>
    <p class="lead">This plain-language test turns the POC promise for {customer} into a measurement plan before anyone treats results as acceptance evidence.</p>

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
    """Render a customer-readable POC Acceptance Evidence Pack.

    The historical function name and the ``decision-packet.html`` output path remain
    stable for callers. The rendered artifact is evidence for a defined POC criterion,
    not an authorization decision.
    """

    _validate_decision_packet_inputs(
        contract,
        manifest,
        criterion,
        measurement,
        criterion_verdict,
        overall,
    )

    contract_hash = contract.canonical_hash
    frozen_at = contract.frozen_at.isoformat()
    evidence_sufficiency = _evidence_sufficiency_rows(criterion, measurement)
    limitations = _limitation_items(criterion_verdict, contract)
    limitations_html = "\n".join(
        "<li>{0}</li>".format(escape(item)) for item in limitations
    )
    primary_limitation = limitations[0]
    next_action = _human_next_action(overall)

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ExitSpec POC Acceptance Evidence Pack — {contract_id}</title>
  <style>
    :root {{
      color-scheme: dark;
      color: #f4f1e9;
      background: #0d1215;
      font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-synthesis: none;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      min-width: 320px;
      margin: 0;
      background:
        radial-gradient(circle at 74% -12%, rgba(255, 119, 36, .10), transparent 32rem),
        #0d1215;
    }}
    main {{ width: min(1180px, calc(100% - 40px)); margin: 0 auto; padding: 18px 0 64px; }}
    p, h1, h2 {{ margin-top: 0; }}
    p {{ line-height: 1.48; }}
    a {{ color: inherit; }}
    code {{
      color: #d8d6ce;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      overflow-wrap: anywhere;
    }}
    .product-bar {{
      display: flex;
      min-height: 42px;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      border-bottom: 1px solid #2b3439;
      padding: 0 2px 14px;
    }}
    .identity {{ display: flex; align-items: center; gap: 11px; }}
    .identity-mark {{
      display: grid;
      width: 31px;
      height: 31px;
      place-items: center;
      border: 1px solid #ff7a24;
      border-radius: 8px;
      color: #ff8b43;
      font-size: .68rem;
      font-weight: 900;
      letter-spacing: -.04em;
    }}
    .identity strong {{ display: block; font-size: .94rem; letter-spacing: -.01em; }}
    .identity span:last-child {{ display: block; color: #89949a; font-size: .68rem; letter-spacing: .09em; text-transform: uppercase; }}
    .packet-ref {{
      margin: 0;
      color: #89949a;
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: .72rem;
      text-align: right;
    }}
    .proof-sheet {{
      --status: #ff8b43;
      --status-soft: rgba(255, 139, 67, .11);
      position: relative;
      overflow: hidden;
      margin-top: 14px;
      border: 1px solid #303a40;
      border-top: 3px solid var(--status);
      border-radius: 17px;
      background: linear-gradient(145deg, rgba(29, 37, 42, .98), rgba(20, 27, 31, .98));
      box-shadow: 0 22px 60px rgba(0, 0, 0, .24);
      padding: 20px 22px 18px;
    }}
    .proof-sheet::after {{
      position: absolute;
      z-index: 0;
      top: -8rem;
      right: -7rem;
      width: 26rem;
      height: 22rem;
      border-radius: 50%;
      background: radial-gradient(circle, var(--status-soft), transparent 68%);
      content: "";
      pointer-events: none;
    }}
    .proof-sheet > * {{ position: relative; z-index: 1; }}
    .status-panel-PASS {{ --status: #58d693; --status-soft: rgba(88, 214, 147, .12); }}
    .status-panel-FAIL {{ --status: #ff6c5f; --status-soft: rgba(255, 108, 95, .12); }}
    .status-panel-BLOCKED {{ --status: #ffad42; --status-soft: rgba(255, 173, 66, .13); }}
    .status-panel-NOT_PROVEN {{ --status: #8eb6c6; --status-soft: rgba(142, 182, 198, .12); }}
    .verdict-row {{
      display: grid;
      grid-template-columns: minmax(220px, .42fr) minmax(0, 1fr);
      align-items: end;
      gap: 28px;
    }}
    .eyebrow, .label {{
      display: block;
      margin: 0 0 6px;
      color: #8e999f;
      font-size: .66rem;
      font-weight: 800;
      letter-spacing: .11em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 0;
      color: var(--status);
      font-size: clamp(2.55rem, 5vw, 4.7rem);
      font-weight: 830;
      letter-spacing: -.065em;
      line-height: .86;
    }}
    .verdict-reason {{
      max-width: 720px;
      margin: 0;
      color: #c5cbcd;
      font-size: .94rem;
    }}
    .equation-panel {{
      margin-top: 16px;
      border: 1px solid #364147;
      border-radius: 11px;
      background: #101619;
      padding: 11px 14px 12px;
    }}
    .equation {{
      margin: 0;
      color: #f4f1e9;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: clamp(.83rem, 1.55vw, 1.06rem);
      font-weight: 720;
      letter-spacing: -.025em;
      line-height: 1.35;
    }}
    .equation .operator {{ color: #ff8b43; padding: 0 .33rem; }}
    .decision-grid {{
      display: grid;
      grid-template-columns: minmax(0, .84fr) minmax(0, 1.16fr);
      gap: 10px;
      margin-top: 10px;
    }}
    .decision-grid article {{
      min-height: 72px;
      border: 1px solid #303a40;
      border-radius: 10px;
      background: rgba(13, 18, 21, .56);
      padding: 10px 12px;
    }}
    .decision-grid p {{ margin: 0; color: #c3c9cb; font-size: .79rem; line-height: 1.38; }}
    .integrity-row {{
      display: grid;
      grid-template-columns: 165px minmax(0, 1fr);
      align-items: center;
      gap: 14px;
      margin-top: 10px;
      border-top: 1px solid #303a40;
      padding-top: 10px;
    }}
    .integrity-row .label {{ margin: 0; }}
    .integrity-row code {{ font-size: .69rem; line-height: 1.4; }}
    .artifact-row {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 7px;
      margin-top: 10px;
    }}
    .artifact-row a {{
      display: flex;
      min-width: 0;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      border: 1px solid #344047;
      border-radius: 9px;
      background: #11181b;
      padding: 9px 10px;
      color: #cbd0d1;
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: .63rem;
      text-decoration: none;
      transition: border-color .15s ease, color .15s ease, transform .15s ease;
    }}
    .artifact-row a::after {{ color: #ff8b43; content: "↗"; font-family: inherit; }}
    .artifact-row a:hover, .artifact-row a:focus-visible {{
      border-color: #ff7a24;
      color: #fff;
      outline: none;
      transform: translateY(-1px);
    }}
    .disclaimer {{
      margin: 10px 0 0;
      color: #858f94;
      font-size: .68rem;
      line-height: 1.35;
    }}
    .disclaimer strong {{ color: #e2dfd7; }}
    .audit-trail {{ margin-top: 16px; }}
    .audit-heading {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 16px;
      padding: 0 2px 8px;
    }}
    .audit-heading h2 {{ margin: 0; font-size: 1.08rem; letter-spacing: -.025em; }}
    .audit-heading p {{ margin: 0; color: #7f8a90; font-size: .72rem; }}
    details {{
      border-top: 1px solid #2a3439;
      background: rgba(20, 27, 31, .52);
    }}
    details:last-child {{ border-bottom: 1px solid #2a3439; }}
    summary {{
      display: grid;
      grid-template-columns: 42px minmax(0, 1fr) minmax(170px, .5fr);
      align-items: center;
      gap: 12px;
      min-height: 49px;
      padding: 0 14px;
      color: #e4e2dc;
      cursor: pointer;
      font-size: .83rem;
      font-weight: 720;
    }}
    summary::marker {{ color: #ff8b43; }}
    .row-number {{
      color: #ff8b43;
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: .66rem;
      letter-spacing: .08em;
    }}
    .row-hint {{ color: #778389; font-size: .69rem; font-weight: 500; text-align: right; }}
    details[open] summary {{ border-bottom: 1px solid #2a3439; background: rgba(255, 122, 36, .035); }}
    .detail-body {{ padding: 17px 58px 20px; color: #bdc4c6; font-size: .84rem; }}
    .detail-body h2 {{ margin: 0 0 10px; color: #f0ede6; font-size: 1rem; }}
    .detail-body p:last-child, .detail-body ul:last-child {{ margin-bottom: 0; }}
    .detail-verdict {{
      display: inline-block;
      margin: 0 0 8px;
      border: 1px solid var(--status);
      border-radius: 999px;
      padding: 4px 8px;
      color: var(--status);
      font-size: .7rem;
      font-weight: 850;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      border-bottom: 1px solid #2a3439;
      padding: 9px 0;
      text-align: left;
      vertical-align: top;
    }}
    th {{ width: 29%; padding-right: 24px; color: #7f8b90; font-size: .72rem; font-weight: 750; }}
    td {{ color: #c7cdcf; font-size: .82rem; }}
    ul {{ margin: 8px 0 0; padding-left: 19px; line-height: 1.5; }}
    @media (max-width: 900px) {{
      .artifact-row {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .proof-sheet {{ padding: 18px; }}
    }}
    @media (max-width: 700px) {{
      main {{ width: min(100% - 24px, 1180px); padding-top: 12px; }}
      .product-bar {{ align-items: flex-start; }}
      .packet-ref {{ max-width: 56%; }}
      .verdict-row, .decision-grid {{ grid-template-columns: 1fr; gap: 10px; }}
      .verdict-row {{ align-items: start; }}
      h1 {{ font-size: 2.75rem; }}
      .artifact-row {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .integrity-row {{ grid-template-columns: 1fr; gap: 5px; }}
      summary {{ grid-template-columns: 32px minmax(0, 1fr); padding: 0 10px; }}
      .row-hint {{ display: none; }}
      .detail-body {{ padding: 15px 18px 18px; }}
      th {{ display: block; width: auto; border-bottom: 0; padding: 9px 0 0; }}
      td {{ display: block; padding-top: 4px; }}
    }}
    @media print {{
      :root {{ color-scheme: light; }}
      body {{ background: #fff; color: #172033; }}
      main {{ width: 100%; padding: 0; }}
      .product-bar, .proof-sheet, details {{ break-inside: avoid; }}
      .proof-sheet {{ background: #fff; box-shadow: none; }}
      details {{ background: #fff; }}
      details > .detail-body {{ display: block; }}
      .artifact-row a {{ background: #fff; }}
    }}
  </style>
</head>
<body>
  <main data-legacy-artifact-name="Proof Pack">
    <header class="product-bar">
      <div class="identity">
        <span class="identity-mark" aria-hidden="true">E/S</span>
        <div><strong>ExitSpec</strong><span>POC acceptance evidence</span></div>
      </div>
      <p class="packet-ref">{contract_id} · v{contract_version}<br>{run_id} · synthetic demonstration</p>
    </header>

    <section class="proof-sheet status-panel-{overall_verdict}" aria-labelledby="evidence-verdict">
      <div class="verdict-row">
        <div>
          <span class="eyebrow">Evidence verdict</span>
          <h1 id="evidence-verdict">{overall_verdict}</h1>
        </div>
        <p class="verdict-reason"><span class="label">Why this verdict</span>{overall_reason}</p>
      </div>

      <div class="equation-panel" aria-label="Evidence equation">
        <span class="label">Exact evidence equation</span>
        <p class="equation">Required ≥ {required_threshold}<span class="operator">·</span>Observed {success_count}/{measurement_sample_count} ({observed_rate})<span class="operator">·</span>Wilson lower bound {lower_bound}<span class="operator">·</span>{overall_verdict}</p>
      </div>

      <div class="decision-grid">
        <article>
          <span class="label">Concise limitation</span>
          <p>{primary_limitation}</p>
        </article>
        <article>
          <span class="label">Exact next human action</span>
          <p>{next_action}</p>
        </article>
      </div>

      <div class="integrity-row">
        <span class="label">Canonical contract hash</span>
        <code>{contract_hash}</code>
      </div>

      <nav class="artifact-row" aria-label="Evidence artifacts">
        <a href="contract.json">contract.json</a>
        <a href="evidence-artifacts.json">evidence-artifacts.json</a>
        <a href="calculations.json">calculations.json</a>
        <a href="verdicts.json">verdicts.json</a>
        <a href="run-manifest.json">run-manifest.json</a>
        <a href="artifact-hashes.json">artifact-hashes.json</a>
      </nav>

      <p class="disclaimer"><strong>Evidence is not authorization.</strong> This pack does not authorize deployment, spending, procurement, production traffic, or any external action.</p>
    </section>

    <section class="audit-trail" aria-labelledby="audit-heading">
      <div class="audit-heading">
        <h2 id="audit-heading">Inspect the proof chain</h2>
        <p>Seven collapsed records · open only what you need</p>
      </div>

      <details class="disclosure-row">
        <summary><span class="row-number">01</span><span>What the customer asked us to prove</span><span class="row-hint">Source + normalized claim</span></summary>
        <div class="detail-body">
          <h2>Source quote</h2>
          <p>{source_quote}</p>
          <p><strong>Normalized acceptance claim:</strong> {normalized_claim}</p>
        </div>
      </details>

      <details class="disclosure-row">
        <summary><span class="row-number">02</span><span>The frozen agreement</span><span class="row-hint">Version + canonical identity</span></summary>
        <div class="detail-body">
          <h2>Frozen contract</h2>
          <table>
            <tr><th>Contract</th><td>{contract_id} v{contract_version}</td></tr>
            <tr><th>Contract status</th><td>{contract_status}</td></tr>
            <tr><th>Frozen at</th><td>{frozen_at}</td></tr>
            <tr><th>Canonical hash</th><td><code>{contract_hash}</code></td></tr>
            <tr><th>POC use case</th><td>{use_case}</td></tr>
          </table>
        </div>
      </details>

      <details class="disclosure-row">
        <summary><span class="row-number">03</span><span>The exact test</span><span class="row-hint">Metric + rule + workload</span></summary>
        <div class="detail-body">
          <h2>Exact measurement</h2>
          <table>
            <tr><th>Criterion</th><td>{criterion_id}: {criterion_title}</td></tr>
            <tr><th>Metric and aggregation</th><td>{metric} / {aggregation}</td></tr>
            <tr><th>Acceptance rule</th><td>{rule_summary}</td></tr>
            <tr><th>Workload slice</th><td>{workload_slice}</td></tr>
            <tr><th>Workload hash</th><td><code>{workload_hash}</code></td></tr>
            <tr><th>Measurement adapter</th><td>{adapter}@{adapter_version}</td></tr>
          </table>
        </div>
      </details>

      <details class="disclosure-row">
        <summary><span class="row-number">04</span><span>Is the evidence sufficient?</span><span class="row-hint">Completeness + integrity checks</span></summary>
        <div class="detail-body">
          <h2>Evidence sufficiency</h2>
          <table>{evidence_sufficiency}</table>
        </div>
      </details>

      <details class="disclosure-row">
        <summary><span class="row-number">05</span><span>What the test observed</span><span class="row-hint">Result + deterministic calculation</span></summary>
        <div class="detail-body">
          <h2>Criterion result</h2>
          <p class="detail-verdict">{criterion_verdict}</p>
          <p>{criterion_reason}</p>
          <table>
            <tr><th>Successful cases</th><td>{success_count} / {measurement_sample_count}</td></tr>
            <tr><th>Observed rate</th><td>{observed_rate}</td></tr>
            <tr><th>Wilson lower bound</th><td>{lower_bound}</td></tr>
            <tr><th>Calculation version</th><td>{calculation_version}</td></tr>
            <tr><th>Run</th><td>{run_id} ({run_status})</td></tr>
            <tr><th>Target under test</th><td>{provider} / {endpoint_class} / {model}</td></tr>
          </table>
        </div>
      </details>

      <details class="disclosure-row">
        <summary><span class="row-number">06</span><span>What this does not prove</span><span class="row-hint">Limits + excluded claims</span></summary>
        <div class="detail-body">
          <h2>Limits / what is not proven</h2>
          <ul>{limitations}</ul>
        </div>
      </details>

      <details class="disclosure-row">
        <summary><span class="row-number">07</span><span>Human follow-up</span><span class="row-hint">Evidence handoff, not authorization</span></summary>
        <div class="detail-body">
          <h2>Exact next human action</h2>
          <p>{next_action}</p>
          <p><strong>Evidence is not authorization.</strong> A human remains responsible for the next decision.</p>
        </div>
      </details>
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
        required_threshold=_rate(criterion.rule.threshold),
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
        limitations=limitations_html,
        primary_limitation=escape(primary_limitation),
        next_action=escape(next_action),
    )
