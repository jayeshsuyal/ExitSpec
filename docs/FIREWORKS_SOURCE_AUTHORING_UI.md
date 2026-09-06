# Source authoring: synthetic UI/API checkpoint

This milestone connects the accepted core at
`168d435b41f74eff6c56c2ec3aeec8634fa92ec2` to both installed app server
compositions. Real activation still rejects unconditionally. There is no live
worker, operator launcher, credential input or provider connection in this flow.

## Product path

After capture, open the existing human-review page and choose **Inspect source
for synthetic proposals**. The page at `/app/pocs/{poc_id}/source-authoring`:

1. Lists current meeting text, email, document and existing-contract receipts.
   Reviewed, stale, archived, closed or oversized sources cannot be selected for
   executable consent.
2. Prepares a disclosure and displays its exact source-owner redacted UTF-8 text,
   candidate provider/model, purpose, custody, byte/token/deadline/ledger limits
   and remaining consent lifetime. The page independently checks the text digest.
3. Requires two explicit attestations: permitted redacted business-text
   classification and acknowledgment of this source disclosure. The five-minute
   expiry starts at preparation and is not extended by acknowledgment.
4. Requires a separate **Run synthetic validation** action. A single background
   thread uses the fixed synthetic supervisor and core D/F guards. Its local
   fixture consists of existing source-owner requirement candidates, validated
   again after bounded IPC. This tests custody and publication, not model quality.
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
| `run` | `operation_id` | Schedules at most one synthetic attempt |
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

Every API response is `no-store`; no CORS permission is returned. Capabilities
exist only in page memory and the fixed request header, never URLs, operation
receipts, logs, cookies or persistent browser storage. On page hide the page
requests revocation with its header, aborts pending reads, clears source text,
capability and acknowledgment, and stops polling. Refresh/back restoration issues
a fresh page capability and requires fresh inspection and acknowledgment. An
unobserved page-hide request is not a guarantee of cancellation: core expiry and
publication guards remain authoritative, and an already completed publication
is preserved.

Bootstrap grants page access, not a live grant. There is no HTTP grant creation,
reset, renewal, credential input, policy/model/endpoint selection, arbitrary body
submission or network-mode option. One core ledger spans all POCs/pages: one
worker, ten claims, ten seconds between claims and nonrefunded $0.01 synthetic
reservations. These reservations are not proof of billing bounds. Capacity
exhaustion refuses new sessions/operations without evicting consumed permits.
This remains a single-user loopback trust boundary, not account authentication.

## Review and validation

`inspect_disclosure` is a read/preview integration seam on the core. It obtains
the exact source through the accepted owners, rechecks the session, record,
owner state and expiry after clock callbacks, and retains terminal invalidation
receipts with cleared source/body references. It never starts a worker. Reentrant
clock regressions cover revoke, archive and shutdown at both preview checks.

The main app now composes the same A3 source/draft/review owners already used by
the source-neutral demo. Its review lookup substitutes validated A3 material for
the matching A2 source through the existing proposal-review service. Unrelated
sources and downstream human authority are preserved. The source-neutral demo
uses a real process-local closure reservation for the new core.

The test-only Zoom correction replaces a whole-JSON search for `730` with an
exact receipt shape, fixed metadata values and separately validated random IDs.
An ID deliberately containing `730` now passes while extra fields or transcript,
PII or meeting identity in any non-ID field fail the exact contract. Product
behavior is unchanged.

API tests exercise both app compositions, all four source kinds, one ledger
across POCs, capability misuse, source/review/archive/closure/expiry invalidation,
duplicate Run/replay, mid-flight cancellation and request/response limits. Nine
Chromium cases cover both app compositions, desktop/mobile layout, page
refresh/back restoration, untrusted responses and native Zoom text with separate
authoring consent. The v0.4 release gate explicitly requires all nine with zero
skips, failures or errors. The engineering gate includes the new code lint and
JavaScript syntax checks. Optional browser skips in ordinary engineering runs
are distinct from the mandatory release collections.

## Next proposed checkpoint

After exact-head acceptance of this UI/API milestone, implement and review the
fixed live worker and explicit local operator launcher as a separate bounded
change, initially tested entirely with synthetic transport. Preserve the accepted
source/body/profile bindings, one-use ticket, total deadline, dispatch/publication
ordering, cancellation, actual-wire bounds and retained attempt ledger.

Do not activate it without separately verified model/full-schema acceptance,
tokenizer/template and total billable-token bounds, current pricing/account/rate/
logging conditions, custody/region approval, configured credentials and an
explicit owner launch budget. Account/API/token probes, private capture reads,
tokenizer downloads, provider calls, spending and real E2E require their later
explicit owner approvals. Deployment, merge, tags and release remain separate.
