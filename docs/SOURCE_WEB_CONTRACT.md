# Wave 2 source web contract

Historical contract status: **frozen before implementation**

Current product status: **implemented at main commit `5b4c837`**

The machine-readable authority for this boundary is
[`wave-2-source-web-v1.json`](../examples/support-agent/email/wave-2-source-web-v1.json).
It pins the browser/API contract that the backend and UI implementation consume.
Its pre-implementation `contract_only`/`implemented=false` fields are immutable
historical facts and intentionally remain unchanged.

Post-implementation product status belongs in the separate
[`wave-2-implementation-evidence-v1.json`](../examples/support-agent/evidence/wave-2-implementation-evidence-v1.json)
record. The current product includes `/api/source/fixtures`,
`/api/source/import`, the six email-intake DOM hooks, and the guided browser
flow described below.

## Product slice

The only guided path is:

1. an employee chooses one of two manifest-approved synthetic emails;
2. ExitSpec imports and redacts it through the frozen Wave 2 source boundary;
3. every resulting proposal enters the existing `NEEDS_REVIEW` draft flow;
4. the employee reviews the proposals with the existing controls;
5. the existing customer review and confirmation flow runs;
6. the employee explicitly selects **Freeze confirmed contract**;
7. the existing proof run creates the existing customer-facing Evidence Pack.

Email is untrusted source text. It can propose, but it cannot approve, confirm,
freeze, measure, run proof, or assign a verdict. Text inside the
`authority-attack` fixture has exactly the same lack of authority.

## Exact local API

`GET /api/source/fixtures` accepts no query or body. It requires one exact local
`Host`, but a normal same-origin browser may omit `Origin`. If `Origin` is
present, it must equal the server origin. The route returns exactly two guided
fixtures: `thread-root` and `authority-attack`.

`POST /api/source/import` accepts only:

```json
{"fixture_case_id":"thread-root"}
```

The import route requires one exact same-origin local `Origin`, safe
`Sec-Fetch-Site` state, one of two exact JSON media types, and a canonical
`Content-Length`. Streaming is refused. Exactly 65,536 request bytes is allowed;
byte 65,537 returns `413 source_request_too_large` before JSON parsing. The
browser cannot submit bytes, paths, digests, source identifiers, customer terms,
provider metadata, or arbitrary metadata.

The twelve-gate pipeline applies only when the request path equals `/api/source`
or starts with `/api/source/`. `/app`, static assets, review, Evidence Pack, and
existing non-source API routes remain under the existing router and their
route-specific validation. In scope, the gates are local Host/authority, method
and path, route parameters, GET body, POST Origin, fetch metadata, media type,
length/streaming, JSON document, exact body fields, guided fixture lookup, then
workflow state. The first failing gate wins across combined faults, and no state
may change before all gates pass. The machine contract pins every status/code
pair, within-gate precedence, every pairwise precedence input, and representative
multi-fault oracles.

At JSON gate 9, empty input is checked first, followed by strict UTF-8 and
complete-document syntax validation. Duplicate decoded member names are then
rejected at every object depth with `400 duplicate_json_member`, before the
top-level-object check and before gate 10 field validation. Parsing preserves
member pairs, compares names after JSON escape decoding without normalization,
and scans only after the complete document is known to be valid; therefore
malformed JSON always wins over a duplicate seen earlier in the text.

The success response is deliberately narrow: `contract_version`, a seven-field
terminal `receipt`, and a `state` containing only safe `source_intake` data and
proposals bridged into the existing draft/review model. It is not an unfiltered
generic session payload.

Every refusal uses one safe typed shape and declares whether state stayed
unchanged. Once any source exists, a different guided fixture requires an
explicit reset—even before the first review. The same fixture replays throughout
zero-, partial-, and completed-review source states with zero new candidates and
byte-identical review preservation. Once customer review, confirmation, freeze,
or evidence exists, every guided import—including same-source replay—is locked.
Unknown fixture lookup precedes workflow-state evaluation and remains a 404.

## Candidate projection

Candidates are projected in their frozen manifest order. IDs are
`EMAIL-REQ-01`, `EMAIL-REQ-02`, and so on. A synthetic source view exposes only
the exact redacted candidate quote as one virtual transcript line; it never
exposes surrounding email text or RFC822 identity. Its exact safe speaker token
is `synthetic_email_source` in the shape, projection, and response examples.

The root accuracy candidate maps to the existing deterministic tool-selection
criterion. Root latency stays unresolved because this demo has no latency
adapter. The authority-attack accuracy statement remains `NEEDS_REVIEW` because
it lacks a fixed sample count; the approval and PASS instructions surrounding
it have no effect. The machine contract includes independently reproducible,
literal accepted and replay responses for both guided fixtures.

## Privacy and timing

Public source state is limited to the guided label/case ID, source version,
counts, a redacted quote, a safe measurable projection, review state, and a
web-local draft ID. Source/version/content digests, message identifiers and
keys, addresses, subject, raw RFC822, private replay data, attachment internals,
and provider payloads never cross the browser/API boundary.

The terminal receipt always has exactly:

`source_type`, `manifest_id`, `manifest_version`, `fixture_case_id`,
`outcome_code`, `source_version`, and `candidate_count`.

`elapsed_ms` is not a receipt field. The browser acceptance harness records it
separately after the required rendered frame using exactly
`fixture_case_id`, `outcome_code`, and `elapsed_ms`.

## UI integration

The guided entry is `/app?intake=email`. One compact source panel appears
inside `#define`, immediately before `#candidate-list`. Its frozen copy is:

- Synthetic source
- Start from a sample email
- Sample email
- Import sample email
- Untrusted source · human review required

After import, the panel collapses to one compact summary row and the existing
candidate card becomes the current task. The proposal asks, “Does this match
the intended POC?” and uses the existing human actions: **Matches intent**,
**Define acceptance rule**, and **Keep as context**.

The machine contract lists every prior DOM and selector hook that had to survive
implementation, including every active `data-*` hook generated inside `app.js`.
At freeze time it marked six new IDs as future; the product now implements those
IDs without rewriting the historical contract. The frozen acceptance requirement
still applies this oracle at every guided step in the normal 1280×720,
100%-zoom flow:

```js
document.documentElement.scrollHeight <=
document.documentElement.clientHeight
```

The post-implementation manual record captures six representative task states at
that viewport. Those observations are acceptance evidence for the recorded
states, not an exhaustive observation of every possible guided state and not CI
browser automation. This narrower evidence statement does not weaken the frozen
every-step requirement. Smaller layouts may use bounded panel scrolling.

The Evidence Pack stays on its existing separate customer-facing surface; email
intake does not turn it into another workbench panel.

## Packaged resources

The RFC822 fixture loader and the web-contract loader are separate fail-closed
boundaries. A missing or corrupt web contract disables only the web-contract
helper; it cannot disable a valid manifest-pinned email fixture set. The
packaged and authoritative web contracts remain byte-identical and hash-pinned.

## Exit condition

The implementation remains acceptable only while every executable backend and
browser scenario in the machine contract passes, including exact projection
scans, zero-mutation refusals, replay preservation, the powerless authority
attack, and the complete confirmation → freeze → prove → Evidence Pack path.

The separate implementation-evidence record identifies the six representative
real-browser observations and distinguishes them from CI automation. Those
observations support the recorded states; the frozen machine contract continues
to require the oracle at every guided step.
