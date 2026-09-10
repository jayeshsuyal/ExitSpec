# Unified source authoring with offline integration evidence

The `--source-neutral` server composes native Zoom capture and source-bound
Fireworks authoring into the existing review, planning, agreement, evidence and
handoff owners. MAIN keeps its existing routes and synthetic authoring behavior.
The source-authoring production registry is empty, and the installed default is
`SYNTHETIC_NO_NETWORK`. There is no admitted profile or qualified real tokenizer
implementation in this candidate. Real account, key, Zoom entitlement, model,
billing, custody and region prerequisites have not been inspected or qualified.

A private offline test composition exercises the complete installed wiring with
fake Zoom, fake credentials and a network-disabled Fireworks-shaped child. Its
page says `OFFLINE_FAKE_FIREWORKS`; it does not claim live provider qualification.
A separately qualified future installation would use `QUALIFIED_FIREWORKS`.
No browser field selects a realm or installs a launch.

## Product path

After capture, open source inspection from the existing human-review page.
The page at `/app/pocs/{poc_id}/source-authoring`:

1. Lists current meeting text, email, document and existing-contract receipts.
   Reviewed, stale, archived, closed or oversized sources cannot be selected for
   executable consent.
2. Prepares a disclosure and displays its exact source-owner redacted UTF-8 text,
   candidate provider/model, purpose, custody, byte/token/deadline/ledger limits
   and remaining consent lifetime. The page independently checks the text digest.
3. Requires two explicit attestations: permitted redacted business-text
   classification and acknowledgment of this source disclosure. The five-minute
   expiry starts at preparation and is not extended by acknowledgment.
4. Requires a separate Run action whose label identifies the server-installed
   mode. The default synthetic fixture uses existing source-owner candidates.
   The sealed Fireworks branch uses the fixed live supervisor and never builds
   that synthetic fixture. Both use one claim/dispatch/publication/cleanup engine.
   Offline transport tests prove integration behavior, not model quality.
5. Polls current status and exposes consent revocation/cancellation. Success opens
   the existing human-review queue. Proposals remain `NEEDS_REVIEW`; the existing
   named-reviewer decision is still required. No automatic confirmation, freeze,
   proof, verdict, customer acknowledgment or closure is introduced.

Zoom capture consent is independent. Native Zoom text can enter this same source
flow; no STT, media upload or capture-triggered authoring is added. Existing A3
local authoring and frozen Wave-1 behavior remain available unchanged.

## HTTP and page authority

All endpoints are exact `POST /api/pocs/{poc_id}/source-authoring/{action}`
requests with `Content-Type: application/json`. Read operations also use POST so
the browser supplies Origin consistently; reads never claim or execute work.

| Action | Exact JSON object | Effect |
| --- | --- | --- |
| `bootstrap` | `{}` | Issues one page capability and server-owned core session |
| `sources` | `{}` | Lists current source eligibility metadata |
| `prepare` | `source_receipt_id` | Prepares the selected source's disclosure |
| `preview` | `operation_id` | Revalidates and returns exact current disclosure text |
| `authorize` | `operation_id`, literal `business_text: true`, literal `acknowledged: true`, bounded `idempotency_key` | Issues/replays the server-owned permit; no execution |
| `run` | `operation_id` | Schedules at most one attempt in the installed realm |
| `status` | `operation_id` | Revalidates current consent and returns content-free status |
| `revoke` | `operation_id` | Revokes this session's disclosure/operation |

Bootstrap is the sole capability-free request: its response supplies a fresh
256-bit secret. Every subsequent read and mutation requires that secret in
`X-ExitSpec-Authoring-Capability`. The server compares it in constant time against
a process-owned session scoped to the selected POC and core epoch. Public POC,
source, operation, disclosure and Zoom IDs/digests cannot substitute for it.

Exact loopback Host and matching Origin, single headers, bounded framing and an
exact flat JSON object are checked before bootstrap can mint a capability or
consume any of the sixteen session slots. Missing, foreign, null or duplicate
Origin, unknown fields, duplicate JSON keys, unexpected encodings, ambiguous
lengths, transfer encoding, nonfinite numbers and parameterized routes fail
closed. Requests are capped at 4 KiB with a two-second body-read bound. Responses
are capped at 256 KiB, including browser-side streaming enforcement.
The body reader uses a monotonic absolute deadline and single raw reads through
the buffered stream, so continuously arriving bytes cannot renew the bound.
The deadline is checked again after JSON validation, before runtime admission;
expiry returns a content-free timeout without minting a session or operation.

Every API response is `no-store`; no CORS permission is returned. Capabilities
exist only in page memory and the fixed request header, never URLs, operation
receipts, logs, cookies or persistent browser storage. On page hide the page
requests revocation with its header, aborts pending reads, clears source text,
capability and acknowledgment, and stops polling. Refresh/back restoration issues
a fresh page capability and requires fresh inspection and acknowledgment. An
async disclosure digest or source-list response rechecks its captured page
generation and operation before changing content. Status/cancellation failures
from superseded operations cannot clear or disable a newer disclosure. An
untrusted or failed current-status response disables consent and Run controls
and clears the displayed source until the page is reloaded. An
unobserved page-hide request is not a guarantee of cancellation: core expiry and
publication guards remain authoritative, and an already completed publication
is preserved.

Bootstrap grants page access, not a live grant. There is no HTTP grant creation,
reset, renewal, credential input, policy/model/endpoint selection, arbitrary body
submission or network-mode option. One core ledger spans all POCs/pages: one
worker, ten claims, ten seconds between claims and nonrefunded $0.01
reservations. Offline reservations are not proof of real billing bounds. Capacity
exhaustion refuses new sessions/operations without evicting consumed permits.
This remains a single-user loopback trust boundary, not account authentication.

## Review and validation

The UI bridge preserves `mode-heading` and adds `source-mode-copy` and
`source-live-missing`. Mode copy stays neutral until bootstrap validates the
exact server-derived mode/readiness shape. The installed synthetic mode has
four unique known `live_missing` reasons; admitted and offline fake modes have
none. Fixed local copy distinguishes them. Unknown or contradictory fields
fail closed; fake transport is never displayed as live qualification.
`source-selection-reason`, `source-ack-reason` and `source-run-reason` are visible
`aria-describedby` targets driven by the same predicates as their controls.
Unchanged descriptions are not rewritten on polling. Pending Run never disables
the independent cancellation control.

The optional root `data-authoring-state` is presentation only: `unverified`,
`select`, `inspect`, `acknowledged`, `starting`, `processing`, `terminal` or
`unavailable`. It contains no identifiers or authority. Page lifecycle resets
clear the mode claim and descriptions along with the existing consent state.
Review's static instructions are universal; evaluator and slot copy follows
the current validated A2/A3 membership. Action labels, values and named-reviewer
requirements retain their existing meanings.

The dedicated bridge browser collection requires exactly 210 cases with zero
skips, failures or errors, in addition to the existing 19 source-authoring and
54 other preserved mandatory cases (73 preserved cases in total). It covers bootstrap validation, accessible state
descriptions, repeated announcements, lifecycle reset, pending-Run cancellation
and A2/A3/mixed review copy in both compositions, real named decisions, slot
accounting, completion routes and provenance failures.

MAIN preserves `local_synthetic_demo` and adds a narrow `source_authoring_review`
capability to `/api/state`. It exposes only the existing GET receipt collection
and current-review projection at `/api/pocs/{poc_id}/assisted-authoring` and
`/api/pocs/{poc_id}/assisted-authoring/current-review`. Nonexact targets, wrong
methods and body framing are refused, including leading-slash aliases normalized
by the HTTP handler. These reads use the existing owners and
DTOs; they do not expose broader assisted authoring, retained-proposal or source
picker routes. MAIN's read capability does not enable the source-neutral
authoring page or capability planner. Validated A3 keeps consume no A2 evaluator
slots, and completion involving A3 material does not enter the A2 Define flow.

The proposal collection includes an `authoring_provenance` manifest with exact
schema `exitspec.review-authoring-provenance/1`. Every current item counted in
the review summary, including kept and discarded rows, has exactly
`proposal_id`, `origin`, `review_state` and `normalized_claim`. Origin is
`INTAKE_A2` or `ASSISTED_A3` and comes only from the review owner's
committed A3 registration. The owner obtains scope/source callbacks before its
review lock, revalidates selected immutable bindings and committed replacement,
and captures actual decisions and origins in one immutable snapshot. Pending
rows, counts and the complete manifest all come from that snapshot. MAIN keeps
its existing agreement-version filter; historical rows are not reintroduced.

The browser requires exact bounded unique manifest membership and agreement
with the receipt/current-review projections. Jointly omitted A3 provenance,
contradictory origins and missing/unknown/stale manifests block review while
legitimate A2-only and empty queues remain usable. Reconciliation also refuses
disappearance of previously validated A3 records. The release gate separately
requires all 18 new owner/API snapshot regressions with zero skips, including
reentrant/concurrent publication, callback lock order, actual decision overlays
and selected scope. Validated A3 records outside the current agreement scope
do not consume current A2 slots or change its completion navigation. This checks consistency of authoritative server state; it
does not authenticate a server that coherently forges every response. The
manifest assigns no evaluator support or downstream decision authority. Both
compositions always cross-check independent receipts/projections, including
when the manifest declares only A2 origins. Primitive string IDs are required
before syntax and uniqueness validation. State and the bounded primitive claim
come from the same snapshot item as origin and counts, without additional owner
lookups or disclosure of decision metadata. Each manifest state count must
equal the summary, pending state/claim bindings must equal the queue, and A3
state/claim bindings must equal the independent projection. Every load and
reconciliation rebuilds A2 kept slots and metric cues from current `INTAKE_A2`
`KEEP_FOR_CONTRACT` rows. Duplicate A2 metrics stay disabled after reload;
distinct metrics remain eligible. Discarded, historical and A3 rows consume no
A2 slots. Tests cover these reload cases and malformed or contradictory state,
claim and count data before review and after a recorded decision. Reconciliation
retains the just-acknowledged immutable selected A2 or A3 binding and previously validated
completed A2 bindings, including discarded rows and rows loaded after reload.
Missing or contradictory current rows require reload before another decision;
eligibility is rebuilt only after those consistency checks. Genuine responses
continue to permit distinct metrics and exclude discarded rows from A2 slots.
The existing
new-ID email journey requires exactly the two same-origin current-POC GET reads
while retaining all decision, contract, execution, evidence and closure checks.

`inspect_disclosure` is a read/preview integration seam on the core. It obtains
the exact source through the accepted owners, rechecks the session, record,
owner state and expiry after clock callbacks, and retains terminal invalidation
receipts with cleared source/body references. It never starts a worker. Reentrant
clock regressions cover revoke, archive and shutdown at both preview checks.

The real-clock integration also verifies expiry against the exact `issued + TTL`
value used at preparation. Subtracting two floating-point timestamps can produce
a value just above or below 300 seconds and incorrectly refuse valid consent.
There is no tolerance or lifetime extension: one representable step of drift in
either expiry is rejected, and the existing monotonic expiry guards still apply.

The main app now composes the same A3 source/draft/review owners already used by
the source-neutral demo. Its review lookup substitutes validated A3 material for
the matching A2 source through the existing proposal-review service. Unrelated
sources and downstream human authority are preserved. The source-neutral demo
shares the exact existing generic-evidence terminal closure owner through its
read-only `closure_service` composition accessor. Actual `HANDOFF_COMPLETED` and
`POC_STOPPED` records fence source authoring, including previously issued page
capabilities and consent. Bootstrap capability/session creation stays inside
that owner's short mutation reservation. A competing closure cannot record
until the reservation ends; owner locks never span worker I/O. A closure that
wins before dispatch prevents handoff, and one that wins before final publication
prevents A3/review publication without refunding a claimed attempt.

Twenty-two mandatory terminal-closure controls use genuine product evidence and
handoff/stop records without replacing the closure service or its resolver.
They include both real browser decision buttons, open execution, earlier
capabilities/consent, dispatch/publication races and exception cleanup. The
earlier standalone placeholder closure is no longer used by this composition.

The test-only Zoom correction replaces a whole-JSON search for `730` with an
exact receipt shape, fixed metadata values and separately validated random IDs.
An ID deliberately containing `730` now passes while extra fields or transcript,
PII or meeting identity in any non-ID field fail the exact contract. Product
behavior is unchanged.

API tests exercise both app compositions, all four source kinds, one ledger
across POCs, capability misuse, source/review/archive/closure/expiry invalidation,
duplicate Run/replay, mid-flight cancellation and request/response limits. Nineteen
Chromium cases cover both app compositions, desktop/mobile layout, page
refresh/back restoration, untrusted responses and native Zoom text with separate
authoring consent, deferred digests across page lifecycle changes, obsolete
status failures and delayed source lists. The v0.4 release gate explicitly
requires all nineteen with zero
skips, failures or errors. The engineering gate includes the new code lint and
JavaScript syntax checks. Optional browser skips in ordinary engineering runs
are distinct from the mandatory release collections.

## Integrated synthetic demo and decision replay

The integration preserves the current proposal navigator, per-proposal review
drafts, explicit review editor, page epochs and cancellation, and accepted
completion counters. A decision attempt captures its selected ID, origin,
claim and decision before the request; reconciliation checks that binding
before rebuilding eligibility, including when a non-first proposal is selected.

A committed proposal decision can be retried after its response is lost. The
HTTP boundary still requires membership in the current POC/agreement scope;
completed current rows reach the owner's existing exact request/key validation
and replay the same immutable receipt. Conflicting requests, keys reused for a
different proposal, stale source bindings and excluded agreement rows still
refuse. Fifty-six mandatory HTTP/browser controls cover this boundary and
commit-then-drop-response retries, with one receipt and unchanged request bytes.

The mandatory synthetic rehearsal uses actual source-neutral product pages:
document capture, explicit source-authoring disclosure/consent, synthetic worker,
named A3 review, separately entered HUMAN_DECLARED planning, confirmation,
freeze, supported deterministic proof and named terminal handoff. Unsupported
advisory and excluded scope remains unproven. Set
`EXITSPEC_SYNTHETIC_DEMO_EVIDENCE` to an external output directory to record its
screenshots and content-free request/authority summary. That document rehearsal
remains separate from the new unified native-Zoom rehearsal, and neither makes
a live provider, spend, deployment or shipping claim.

## Operator and unified browser path

`python -m exitspec.source_authoring_operator --approval-id <compiled-id>
--port <local-port> --output-root <absolute-path>` is the bounded local operator
entrypoint. With the installed empty registry it returns a fixed refusal before
TTY input, credential reads, file verification, runtime, pipe or socket effects.
There is no profile file, fake flag, secret CLI option or environment fallback.

An admitted launch is checked before terminal input and again afterward. The
controlling terminal uses bounded no-echo input and restores its state on exit.
Each response is submitted with Ctrl-J (LF); CR and credential repair are refused.
One sealed launch installs once on one SourceNeutral server. The operator pairs
Zoom on that same server and exact POC through the shared local pairing helper.
Zoom prerequisites/participant capture consent and Fireworks launch/exact-source
egress consent remain separate. Rekeying requires a new launch and new consent.

SourceNeutral renders fixed display hints for the existing native Zoom panel on
both `/sources/new` and `/capture`. Other meeting/STT compatibility endpoints are
not probed there. The existing paste input remains available. These hints do not
authorize pairing, capture or provider execution; MAIN retains its current behavior.

The unified browser test creates one POC, captures a bounded fake native Zoom
window, inspects the exact redacted source, acknowledges separately, runs one
fake Fireworks attempt and completes the existing named review → HUMAN_DECLARED
plan → customer confirmation/freeze → supported deterministic proof → named
non-authorizing handoff. `EXITSPEC_UNIFIED_DEMO_EVIDENCE` writes screenshots and
its request/provenance record to an external evidence directory.

The release wrapper preserves the earlier 358 mandatory cases, accepted 202
transport cases, and adds 248 unified admission/operator/engine/web/Zoom/browser
cases, all required to have zero skips/errors/failures. The actual final run,
commit/tree and artifact binding determine acceptance, not this collection count.

Real qualification, production-profile population, provider/Zoom activity,
credential/account inspection, downloads, spend, publishing and shipping remain
separate work requiring explicit authorization. Offline success does not prove
real-account readiness or imply missing real-account permissions.
