# Local end-to-end product contract

Status: implemented local product contract
Scope: local synthetic demonstration
External mailbox and meeting-platform connectors: excluded

## Outcome

The local ExitSpec product must complete one honest source-to-proof loop:

```text
choose source
  -> create draft POC
  -> capture a synthetic source
  -> review source-linked requirement proposals
  -> create the customer agreement
  -> record exact-version customer confirmation
  -> freeze the confirmed contract
  -> run one approved measurement adapter
  -> independently verify the resulting artifacts
  -> show one typed verdict and customer Evidence Pack
  -> record an evidence-bound human handoff or stop decision
  -> move the closed POC to Completed
```

The browser may coordinate explicit employee actions. It may not become a
parallel source, approval, confirmation, contract, execution, verdict, or
artifact authority.

## Product objects

### POC

A POC is the stable workspace object. Email, meeting, document, and existing
contract are starting-source choices, not different kinds of POC.

The first local create operation requires:

- display name;
- customer label;
- use case;
- owner; and
- starting-source choice.

Creation produces a local draft POC only. It creates no source, proposal,
approval, customer decision, frozen contract, run, verdict, or Evidence Pack.
The operation is bounded and idempotent. Conflicting reuse of an idempotency key
fails closed.

### Source

Every implemented intake path must terminate in the existing provider-neutral
source envelope boundary. One POC may contain multiple sources.

The local source choices are:

| Choice | First implemented capture path | Honest unavailable boundary |
| --- | --- | --- |
| Email | Pasted bounded synthetic email text | Live mailbox/OAuth |
| Meeting | Pasted synthetic transcript or one consent-bound short browser recording; fixed fixture by default, optional Fireworks STT | Zoom/Meet/Teams, customer audio, or production recording |
| Document | Bounded UTF-8 synthetic text document | Arbitrary binary extraction |
| Existing contract | Strict ExitSpec contract import | Generic third-party schema conversion |

No source may approve a requirement, confirm an agreement, freeze a contract,
run a measurement, or set a verdict.

### Agreement and evidence

Agreement lifecycle and evidence status remain independent:

```text
agreement: DRAFT -> IN_REVIEW -> APPROVED -> FROZEN
evidence:  NOT_RUN -> running operation -> PASS | FAIL | BLOCKED | NOT_PROVEN
```

`NOT_RUN` is presentation state, not a verdict. `PASS` is evidence, not
authorization for deployment, spend, procurement, or production traffic.

## Browser route contract

```text
/app                              professional POC work queue
/app/pocs/new                     source-first local draft creation
/app/pocs/{poc_id}/sources/new    bounded source capture
/app/pocs/{poc_id}/review         human proposal triage
/app/pocs/{poc_id}/define         measurable-rule definition
/app/pocs/{poc_id}/agreement      customer draft, confirmation, and freeze
/app/pocs/{poc_id}                frozen-contract proof and closure
/review/{token}                   exact-version customer review
/artifacts/{run_path}             verified static Evidence Pack boundary
```

Compatibility entries remain valid:

```text
/app?intake=email
/app?mode=recording
```

The dashboard is a read-only projection. Its authoritative continuation card,
POC rows, blocker, next action, and evidence summary never mutate domain state.

## API contract

### POC coordination

```text
POST /api/pocs
GET  /api/pocs/{poc_id}
GET  /api/workspace?filter={Active|Needs attention|Completed}
GET  /api/workspace/pocs/{poc_id}/closure
POST /api/workspace/pocs/{poc_id}/closure
```

Closure accepts exactly one authoritative terminal binding. Completed handoff
requires the verified Evidence Pack binding. A durable `BLOCKED` run exposes a
separate terminal-run binding and permits only `POC_STOPPED`; it never invents
an Evidence Pack or verdict. Once either human closure decision is recorded,
all POC-scoped lifecycle writes return `409 POC_LIFECYCLE_CLOSED` while reads
and exact closure replay remain available.

`POST /api/pocs` accepts only the required draft identity, one supported
starting-source choice, and an idempotency key. It returns a draft POC identity
and next intake route. It does not import content.

### Source capture

```text
GET  /api/pocs/{poc_id}/sources
POST /api/pocs/{poc_id}/sources/email-text
POST /api/pocs/{poc_id}/sources/meeting
POST /api/pocs/{poc_id}/sources/document
POST /api/pocs/{poc_id}/sources/contract
```

Each write has a source-specific strict schema and bounded body. A generic
caller-controlled adapter name is forbidden. Capture returns a typed receipt
and source-linked proposals that remain `NEEDS_REVIEW`.

### Agreement actions

Existing review, customer-review, confirmation, revision, and freeze
authorities remain the only writers. New routes may delegate to those
authorities; they may not reproduce their rules.

### Measurement operation

```text
POST /api/pocs/{poc_id}/runs
GET  /api/pocs/{poc_id}/runs/{run_id}
GET  /api/pocs/{poc_id}/runs/latest
GET  /api/pocs/{poc_id}/evidence
```

The browser cannot submit a contract path, confirmation path, workload path,
prompt path, endpoint, model, provider, request count, concurrency, output
directory, credential, or adapter. Those values come from the exact frozen POC
version and server-owned configuration.

The start operation is bounded and idempotent. Public operation state is:

```text
NOT_STARTED
VALIDATING
RUNNING
COMPLETED
BLOCKED
NOT_PROVEN
```

`COMPLETED` does not imply `PASS`. A customer Evidence Pack URL appears only
after persisted artifacts survive independent reload, integrity validation,
and deterministic verdict recomputation.

`BLOCKED` may be intentionally closed as `POC_STOPPED` against the exact
durable runner receipt. It cannot be recorded as `HANDOFF_COMPLETED` because
no Evidence Pack exists to hand off.

The first dynamic evaluator is one bounded inference-performance criterion:
client-observed p95 time to first token plus its mandatory error-rate
guardrail. The local agreement screen may explicitly select:

```text
POST /api/reference/inference/v1/chat/completions
model: exitspec/reference-stream-v1
```

This deterministic OpenAI-compatible streaming target exists only to exercise
the real probe, calculation, Evidence Pack, and handoff loop without a funded
provider or GPU. It is not an inference engine and does not prove production
performance. The probe still performs one preflight, ten warmups, and 100
measured streamed requests.

Only reviewed definitions supported by this evaluator may become executable.
Every reviewed claim excluded from the contract is retained in the frozen
non-goals and customer Evidence Pack as explicitly `NOT_PROVEN`; ExitSpec never
invents a metric, observation, or recommendation for it.

## UI contract

### Dashboard

At 1280×720, `/app` is one fixed professional application shell:

- compact global navigation;
- one authoritative **Continue working** card;
- one finite POC work queue;
- direct POC navigation with no duplicate selection preview;
- one visible `Define -> Confirm -> Prove` position;
- agreement state distinct from evidence state;
- one exact next action;
- one consistently placed primary action; and
- no giant hero, KPI-card soup, decorative charts, or body-level infinite
  scroll.

`continue_working` controls the deterministic continuation card. Filters change
the finite POC table without changing that authoritative next decision. At
narrow widths, the continuation card and table reflow without horizontal body
overflow.

### Source-first creation

`/app/pocs/new` asks how requirements are arriving before it shows the
source-specific form:

```text
Email
Meeting
Notes or document
Existing ExitSpec contract
```

Only capabilities backed by a real local route are enabled. The Meeting route
may expose the fixed browser recording or explicit experimental Fireworks STT
mode. Mailbox, Zoom, Google Meet, and customer-audio integrations remain
unavailable with an exact explanation.

### Workbench

Every workbench state keeps:

- one current task;
- one primary action;
- relevant source and requirement work;
- agreement state;
- execution/evidence state;
- blockers; and
- the next human action.

Technical identities remain inspectable but secondary.

## Delivery train

| PR | Deliverable | Depends on |
| --- | --- | --- |
| 1 | Professional continuation dashboard | Current workspace projection |
| 2 | Typed local draft POC creation service | This contract |
| 3 | Source-first create UI and API integration | PR 2 |
| 4 | Unified local source route boundary | PR 2 |
| 5 | Email, meeting-text, document, and contract capture | PR 4 |
| 6 | Multi-source workbench projection | PR 5 |
| 7 | Confirm/freeze continuity for created POCs | PR 6 |
| 8 | Browser-safe performance operation coordinator | Existing CLI runner |
| 9 | Run/status/evidence UI integration | PRs 7 and 8 |
| 10 | Browser E2E, adversarial audit, demo fixtures, and runbook | PRs 1–9 |

PRs 1, 2, and 8 may proceed in parallel. PR 10 cannot begin until every
preceding authority and projection has landed.

## Local exit gate

The local E2E demonstration is complete only when all of the following are
true:

1. an employee selects a supported starting source;
2. a retry creates no duplicate POC or source;
3. source content produces only source-linked `NEEDS_REVIEW` proposals;
4. no proposal advances without explicit human review;
5. customer confirmation binds the exact visible agreement;
6. freeze preserves the confirmed version and canonical digest;
7. run creation accepts no caller-controlled execution inputs;
8. status survives safe replay and never converts failure into `PASS`;
9. the UI exposes no verdict before verified terminal evidence exists;
10. the Evidence Pack survives independent reload and recomputation;
11. excluded or unsupported reviewed claims appear as `NOT_PROVEN`, not as
    silently dropped or fabricated evidence;
12. the dashboard and normal workbench fit at 1280×720;
13. the complete synthetic flow is repeatable from a clean process using the
    recorded runbook.

External Gmail, Outlook, Zoom, Google Meet, authenticated identity, durable
multi-tenant storage, and production traffic authorization remain separate
future gates.
