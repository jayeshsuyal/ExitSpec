# POC Workspace Specification

- Status: Accepted
- Version: 1.0
- Date: 2026-07-27
- Governing acceptance contract:
  [`poc-workspace-acceptance-v1.json`](../examples/product/poc-workspace-acceptance-v1.json)

## Decision

ExitSpec is a POC-first product. A POC is the stable primary product object; email,
meeting transcripts, notes, and documents are source records attached to that
POC. Named humans may also add requirements with a rationale, but a human
addition is not disguised as imported source. Sources may propose requirements, but they never become separate
channel-specific POCs and never receive approval, confirmation, freeze,
measurement, or verdict authority.

The product navigation therefore begins with a POC dashboard at `/app`. A user
creates or resumes a POC, adds one or more sources, reviews source-linked
requirements, obtains exact-version customer confirmation, freezes the agreed
contract, runs approved measurements, and publishes an Evidence Pack.

```text
workspace
    -> POC
        -> sources
        -> requirement candidates and human decisions
        -> agreement versions and customer decisions
        -> frozen contract
        -> proof runs
        -> Evidence Packs
```

Email and meetings are intake methods, not top-level product categories.

## User outcome

A field, solutions, deployment, customer-success, or technical presales engineer
can:

1. see every POC that needs attention;
2. resume the exact next human action without reconstructing state;
3. create a POC from the way requirements actually arrived;
4. add later sources to the same POC without losing provenance;
5. show the customer one exact reviewable agreement;
6. keep agreement state distinct from evidence state; and
7. hand off an inspectable Evidence Pack.

The dashboard is an action surface, not an analytics dashboard.

## Product aggregate

### POC

The POC is the aggregate root for the product workspace. Its stable identity
does not depend on a source channel, contract version, proof run, or verdict.

The initial workspace projection requires:

```text
poc_id
display_name
customer_label
use_case
owner
created_at
updated_at
archive_state
active_contract_id
active_contract_version
source_summary
derived_phase
next_human_action
blockers
latest_evidence_summary
```

`display_name`, `customer_label`, `use_case`, and `owner` are product metadata.
They do not replace customer-visible contract fields or authenticated identity.

### Sources

A POC may contain zero or more source records. The provider-neutral source types
are:

- `email`;
- `meeting_transcript`;
- `note`;
- `document`.

The current synthetic RFC822 path remains the first implemented source adapter.
A pasted or deterministic synthetic transcript is the next meeting-source
adapter. Live mailbox, meeting, and raw-audio transports retain their separate
security and privacy gates.

Every machine-derived executable requirement must retain exact allowed
provenance. A human-added requirement must retain its named author and rationale.
`human_added` is a requirement provenance classification, not a source type.

### Requirements and agreement versions

Source import creates or proposes `NEEDS_REVIEW` candidates. Named employee
decisions create the reviewed requirement set. Only selected approved
requirements assemble the customer-visible agreement.

A POC may have many agreement versions but only one active draft or review
version. Historical confirmed and frozen versions remain immutable.

### Proof runs and Evidence Packs

A frozen contract may have multiple proof runs. Every run points to one exact
frozen contract version and its approved adapter policy. An Evidence Pack is
published for one consistency-checked run. The dashboard may summarize the
latest valid result, but it must never rewrite or hide historical verdicts.

The read-only `/app/evidence` library lists every pack retained by the bounded
process-local run authorities, newest first. It independently reverifies a pack
before releasing its link, labels non-current runs as historical, and projects
handoff state only for the exact evidence binding that was reviewed. It does
not scan artifact directories, recalculate verdicts, or create a second artifact
authority. A failed integrity check makes the complete library unavailable
rather than publishing a partial or unverified list.

Agreement lifecycle and evidence verdict remain separate domain concepts:

```text
agreement: DRAFT | IN_REVIEW | APPROVED | FROZEN | SUPERSEDED
evidence:  PASS | FAIL | BLOCKED | NOT_PROVEN | NOT_RUN
```

`NOT_RUN` is a workspace presentation state, not a new verdict authority.

## Derived workspace phase

The dashboard phase is a read-only projection. It is never stored as authority
and cannot advance the contract or evidence lifecycle.

| Phase | Meaning | Typical next action |
| --- | --- | --- |
| Define | Source, review, confirmation, or freeze work remains | Add source, review proposals, send review, or freeze |
| Prove | An exact frozen version is ready for or undergoing measurement | Choose an approved run or resolve a run blocker |
| Decide | A run has a terminal evidence result or handoff task | Inspect, rerun, or share the Evidence Pack |

If underlying states disagree, the projection must expose a blocker. It must not
guess a more advanced phase.

## Dashboard information architecture

### Global shell

The global navigation contains:

- ExitSpec;
- POCs;
- the current user or local-demo identity.

Destinations and actions appear only when their underlying product authority
exists. The read-only demo therefore does not show a dead **Evidence Packs**
destination or unavailable **New POC** control. Once local POC creation lands,
**New POC** becomes the one persistent primary action. Once an Evidence Pack
library exists, its destination may enter the global navigation.

Templates, integrations, activity feeds, billing, CRM, model catalogs, and
organization administration are excluded from the first dashboard.

### Dashboard content

The normal `/app` dashboard contains, in this order:

1. a compact page header with **POCs**;
2. at most one **Next up** card for the highest-priority active POC;
3. one POC list; and
4. three bounded filters: **Active**, **Needs attention**, and **Completed**.

The current POC is not repeated in the default active list. The list is for
other matching POCs; its total still includes the current POC so the dashboard
does not conceal workspace state.

The POC list exposes only:

- POC and customer label;
- derived phase;
- exact next action or blocker;
- owner;
- last updated time; and
- latest evidence result, when one exists.

The first dashboard excludes vanity totals, charts, leaderboards, provider
spend, token counts, conversion funnels, activity timelines, and decorative
status cards.

### Attention ordering

“Needs attention” is deterministic. It is true only when a POC has an available
human action, a customer change request, an expired or revoked review, a
measurement blocker, invalid evidence, or a failed release-relevant gate.

The continue card is selected by:

1. explicit current user ownership;
2. needs-attention state;
3. oldest unresolved action timestamp; and
4. stable POC ID as the final tie-breaker.

No model ranks or silently reprioritizes customer work.

### Empty and failure states

- No POCs: show one sentence. Offer **New POC** only when local creation
  authority is implemented.
- Filter has no matches: preserve the filter and offer **Show active POCs**.
- POC summary unavailable: show a bounded error row and no invented status.
- Evidence unavailable: show `Not run` or the exact non-`PASS` state, never a
  neutral green success treatment.

## Create-POC flow

The flow is short, resumable, and source-aware.

### Step 1 — Identify the POC

Required:

- display name;
- customer label;
- use case; and
- owner.

This step creates a local draft POC only. It creates no agreement, customer
review, evidence, or provider authorization.

### Step 2 — Choose the starting source

The source choices are:

- **Email**
- **Meeting or transcript**
- **Notes or document**
- **Start manually**

The interface states which choices are currently available. It must not render a
future live connector as connected or usable.

### Step 3 — Bring in the source

The first implementation reuses the manifest-approved synthetic email catalog.
The meeting path begins with pasted or deterministic synthetic transcript text,
not raw audio. Notes reuse the existing redaction-first capture path. Manual
requirements are explicitly `human_added`.

### Step 4 — Review proposals

The user reviews every source-linked proposal as:

- Matches intent;
- Define or correct the acceptance rule; or
- Keep as context.

No source or model action may make this decision.

### Step 5 — Continue in the existing agreement spine

```text
reviewed requirements
    -> customer-visible agreement
    -> exact-version customer decision
    -> explicit freeze
    -> approved proof run
    -> Evidence Pack
```

The create flow ends when the user reaches the POC workbench. It does not hide
the agreement, proof, or evidence steps inside an “AI generation” animation.

## Multi-source rules

1. One POC may contain multiple source types.
2. Source import is append-only at the POC boundary; exact replay is idempotent.
3. Reusing one source identity with conflicting content fails closed.
4. A new source creates review-only candidates. It does not edit an agreement.
5. Before customer review begins, approved new candidates may be assembled into
   the active draft through an explicit employee action.
6. After a customer review link exists, accepting a material new requirement
   creates a new agreement version and invalidates the old pending capability.
7. After customer confirmation, a material new requirement requires a new
   agreement version and a new customer decision.
8. After freeze, no source may mutate the frozen contract or its runs. A material
   change creates a successor agreement version inside the same POC.
9. Conflicting source statements remain visible and unresolved until a named
   employee decides how to represent them.
10. Source deletion or retention expiry cannot erase provenance required by a
    retained agreement or Evidence Pack without an explicit retention policy and
    visible unavailable-evidence state.

“Material” means any change to customer-visible or execution-bound content,
including requirement wording, threshold, sample count, workload, adapter
policy, owners, non-goals, or retention terms.

## Route contract

The target route map is:

| Route | Purpose |
| --- | --- |
| `/app` | POC dashboard |
| `/app/pocs/new` | Create a POC |
| `/app/pocs/{poc_id}` | Guided POC workbench |
| `/app/pocs/{poc_id}/sources` | Source history and add-source entry |
| `/review/{token}` | Exact-version customer review |
| `/artifacts/{run_path}` | Static Evidence Pack and artifacts |

Compatibility requirements:

- `/app?intake=email` continues to open the seeded synthetic-email workbench
  until the new creation flow reaches parity;
- `/app?mode=recording` continues to support the prepared deterministic take;
- current review links and artifact URLs remain valid; and
- route migration must not conflate a POC ID with a contract ID or run path.

## Workbench contract

Inside one POC, the interface keeps the existing chain of custody:

```text
Source -> Agreement -> Customer -> Freeze -> Evidence -> Handoff
```

The user-facing journey language is:

```text
Define -> Confirm -> Prove
```

`Define` contains source capture, proposal review, and measurable-rule
definition. `Confirm` contains agreement preparation, customer decision, and
freeze. `Prove` contains execution, verdict, Evidence Pack, and handoff.
Existing domain actions and the internal `Define`/`Prove`/`Decide` projection
remain unchanged; they are not shown as a competing journey.

Every normal state emphasizes:

- one current task;
- one primary action in a consistent location;
- relevant source and work;
- agreement state;
- evidence state;
- blockers; and
- next action.

Technical details remain inspectable but visually secondary. Customer review and
Evidence Pack surfaces remain separate from the employee workbench.

## Visual contract

The accepted product palette restores the graphite/orange system:

| Token | Value | Use |
| --- | --- | --- |
| Canvas | `#0E141B` | Application background |
| Navigation | `#121A23` | Global shell and fixed navigation |
| Panel | `#18222D` | Primary work surfaces |
| Raised | `#1D2936` | Selected or elevated bounded surfaces |
| Primary text | `#F1F4F7` | Main content |
| Secondary text | `#C4CED9` | Supporting content |
| Muted text | `#96A4B4` | Metadata |
| Border | `#2A3747` | Normal separators |
| Strong border | `#46576B` | Focused structure |
| Action orange | `#E87849` | Current task and primary action |
| Success green | `#73C99C` | Proven success only |

The product uses no pure black, blue/periwinkle primary accent, gradients,
glassmorphism, neon, giant landing hero, chip soup, or decorative dashboard
clutter.

Orange is restrained to:

- the primary action;
- active progress;
- needs-attention emphasis;
- focus; and
- a small number of critical boundaries.

Orange is not a success colour. Evidence `PASS` uses success green. Agreement
status and evidence verdict must remain visually and textually distinct.

## Layout and accessibility

At 1280×720 and 100% zoom, the normal seeded dashboard and each normal workbench
step must fit inside the professional application shell without
workflow-length body scrolling.

At narrower widths or zoom:

- content reflows;
- body scrolling remains available when needed for accessibility;
- finite panels may scroll independently;
- the page has no horizontal body overflow at 320 CSS pixels;
- keyboard focus is always visible; and
- colour is never the only status signal.

The dashboard list remains bounded through pagination or finite virtualized
loading before real multi-POC scale. Infinite body scroll is not accepted.

## Safety and authority

The workspace shell does not create new authority.

- Opening a POC cannot import a source.
- Opening a source cannot approve a requirement.
- Creating a POC cannot create customer confirmation.
- A dashboard phase cannot freeze a contract.
- A latest-result summary cannot assign or change a verdict.
- A model cannot rank customer priority or advance workflow state.
- A `PASS` cannot authorize deployment, spend, procurement, or traffic.

Every write remains an explicit action against the underlying source,
requirement, confirmation, contract, run, or evidence service.

## Persistence and identity boundary

The first workspace implementation may use a clearly labeled local,
process-scoped POC registry so the deterministic demo remains available. It must
not claim authenticated identity, durable customer decisions, tenant isolation,
or production persistence.

Before live customer source or customer-bound delivery:

- authenticated workspace identity;
- contract-scoped authorization;
- durable append-only confirmation history;
- durable POC and contract storage;
- tenant isolation;
- revocation and expiry;
- backup and restore;
- retention and deletion; and
- unavailable-store behavior

must pass the existing real-customer trust gate.

## Acceptance gates

The versioned machine-readable gate is
[`poc-workspace-acceptance-v1.json`](../examples/product/poc-workspace-acceptance-v1.json).
The capability is not implementation-complete until every required gate passes.

The minimum human acceptance script is:

1. open `/app` and understand that POCs—not inboxes or meetings—are the primary
   objects;
2. identify the next action for a seeded POC without opening it;
3. choose **New POC** and create a local draft;
4. choose Email and enter the existing bounded synthetic-email flow;
5. return to the dashboard and resume the same POC;
6. add a synthetic meeting transcript as another source without creating a
   second POC;
7. prove that the new source cannot mutate a confirmed or frozen version;
8. complete the existing confirmation, freeze, proof, and Evidence Pack loop;
9. rerun one frozen contract and verify that `/app/evidence` preserves both
   immutable run-scoped packs;
10. distinguish agreement status, evidence verdict, and human handoff state;
    and
11. complete the normal seeded path without infinite body scrolling.

## Non-goals

This specification does not claim:

- live Gmail, Outlook, IMAP, Graph, or mailbox ingestion;
- live Zoom, Meet, Teams, or calendar integration;
- speech-to-text or raw-audio handling;
- authenticated hosted identity or tenant authorization;
- durable production POC storage;
- CRM replacement;
- autonomous POC creation or customer confirmation;
- arbitrary metric execution;
- model-driven customer prioritization;
- hosted measurement;
- automatic email delivery; or
- production deployment authorization.

## Planned PR train

No implementation PR begins until this specification and its acceptance contract
are reviewed together.

1. **Workspace contract and fixtures** — freeze object, route, state, visual, and
   acceptance semantics.
2. **Dashboard projection** — read-only POC registry and deterministic next
   action.
3. **Dashboard shell** — `/app`, filters, continue card, and bounded POC list.
4. **Create-POC identity** — local draft creation with no source or authority
   side effects.
5. **Source chooser** — Email, Meeting or transcript, Notes or document, and
   Start manually with honest availability.
6. **Email bridge** — move the existing synthetic email loop under one seeded
   POC without weakening its frozen Wave 2 contract.
7. **Synthetic transcript source** — add a second source type through the
   provider-neutral source model, without raw audio.
8. **Multi-source versioning** — require explicit new agreement versions and new
   customer decisions for material changes.
9. **Evidence Pack library** — list and open immutable run-scoped packs.
10. **Visual restoration and accessibility** — graphite/orange tokens, keyboard
    and responsive gates, and no-scroll acceptance.
11. **Hardening and implementation evidence** — adversarial browser, state,
    packaging, and failure evidence.

Production persistence and authenticated customer confirmation remain a
separate C4 train governed by the durable confirmation ledger ADR and the
real-customer trust gate.
