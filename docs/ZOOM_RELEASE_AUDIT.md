# Post-train holistic Zoom-to-POC release audit

Status: local synthetic release audit complete on the post-train audit branch.
This record is intentionally separate from the seven implementation PRs. It
does not claim a fresh live Zoom run or promote the private diagnostic capture.

## Release verdict

- **Local synthetic Zoom-to-POC path:** PASS. The bounded lifecycle reaches one
  existing Zoom-sourced draft, human proposal review, customer confirmation,
  contract freeze, deterministic supported evaluation, and one Evidence Pack.
- **Real live Zoom-to-POC release claim:** NOT SHIPPED. A fresh authorized live
  call is still required. The owner must first rotate or disable the exposed
  Zoom credential and provide the explicit privacy/custody authorization needed
  before any private capture is opened.
- **Private 2026-08-25 capture:** remains ignored, owner-only, untrusted,
  incomplete, and unsealed. No raw bytes were read, decoded, quoted, committed,
  or published during this audit.

## Per-PR conductor review

For each merged PR, the complete changed-file diff and the directly affected
callers/consumers were reviewed, including interfaces, schemas, frozen IDs,
state transitions, persistence, failure behavior, security/privacy boundaries,
UI/API compatibility, migrations, rollback behavior, and tests.

| PR | Adjacent paths reviewed | Findings and disposition |
| --- | --- | --- |
| [#129](https://github.com/jayeshsuyal/ExitSpec/pull/129) | Evidence boundary, legacy capture sealer, operator harness, setup/runtime docs | No P0/P1/P2 defect. Legacy incomplete captures still fail closed; setup attestation cannot authorize runtime, fixture publication, confirmation, freeze, proof, or verdict. |
| [#130](https://github.com/jayeshsuyal/ExitSpec/pull/130) | Ignored private workspace, consent loader, immutable fixture writer, provenance receipts | No P0/P1/P2 defect. The pipeline has no raw-capture reader and requires explicit consent plus a second-person receipt before publication. |
| [#131](https://github.com/jayeshsuyal/ExitSpec/pull/131) | Decoder callers, normalized segment model, provenance and digest binding | No P0/P1/P2 defect. Unsupported versions, duplicate keys, malformed input, depth/size/timestamp violations, and ambiguous identity fail closed without echoing input. |
| [#132](https://github.com/jayeshsuyal/ExitSpec/pull/132) | Session runtime, checkpoint recovery, guided handoff, proposal bridge | No P0/P1/P2 defect. Replay, duplicate delivery, out-of-order packets, reconnect, timeout, no-transcript stop, processing failure, and conflicting results remain bounded and idempotent. |
| [#133](https://github.com/jayeshsuyal/ExitSpec/pull/133) | Existing source-intake/proposal services, POC creation, review, definition, confirmation, freeze, proof | No P0/P1/P2 defect. Stable session-derived keys prevent duplicate POCs/sources; output is review-only and provider-neutral. |
| [#134](https://github.com/jayeshsuyal/ExitSpec/pull/134) | `web.py` dispatch, source-intake API, existing email/manual paths, static workbench, closure/evidence routes | No P0/P1/P2 defect. Same-origin and closed-POC gates remain active; desktop and mobile review found no horizontal overflow or theme break. |
| [#135](https://github.com/jayeshsuyal/ExitSpec/pull/135) | Full adversarial E2E, existing customer/evaluator/Evidence Pack flows, runbook/checklist | PR7-001 was fixed before merge. No new P0/P1/P2 defect; all specified replay, failure, provider, stale-decision, leakage, and unsupported-metric cases are covered. |
| [#136](https://github.com/jayeshsuyal/ExitSpec/pull/136) | Ledger only | Bookkeeping-only follow-up; no runtime or contract change. |

### P0/P1/P2 finding log

- P0: none.
- P1: none.
- P2: one dependency finding was discovered by the holistic audit: the
  `pytest>=8,<9` declaration allowed vulnerable pytest 8.4.2
  (`PYSEC-2026-1845`, fixed in 9.0.3). It is fixed on this audit branch by
  constraining pytest to `>=9.0.3,<10`, adding the Python audit tool to the dev
  extra, and running the same audit in the engineering gate.

Accepted lower-risk limitations are explicit and rollback-safe: the Zoom
handoff remains process-local and synthetic-only; no production multi-tenant
transport or durable cross-process session store was added; the repository has
no configured mypy/pyright checker (compileall, Pydantic validation, focused
tests, and CI Python matrices were run); and nine whole-repository Ruff findings
remain outside this train and outside its changed files. None expands authority
or weakens evidence semantics.

## Verification record

### Local tests and static checks

- Full Python behavior suite: passed with pytest 9.1.1, with loopback access for
  the existing ephemeral test servers.
- Distribution suite: passed.
- Browser E2E: passed, including the Zoom handoff and existing email/manual,
  customer review, freeze, proof, and Evidence Pack flows.
- Focused adversarial Zoom suite: passed for PR7 E2E, evidence boundary,
  privacy pipeline, decoder, session runtime, proposal bridge, and HTTP handoff.
- All repository JavaScript syntax checks: passed.
- Zoom fixture operator check/test: 11 passed.
- Node dependency audit: `npm audit --audit-level=high --offline` found 0
  vulnerabilities.
- Python dependency audit: `pip-audit --local --skip-editable` found no known
  vulnerabilities after updating the local audit environment to pip 26.2.1;
  the editable local `exitspec` package is intentionally skipped because it is
  not published on PyPI.
- Train-specific Ruff checks: passed. Whole-repository Ruff still reports only
  pre-existing findings outside the train's changed files.
- Python compileall and `pip check`: passed.
- Git diff whitespace check: passed.

### UI and browser review

The local app was inspected at 1280x720 and at 390x844. The existing dark
charcoal/orange theme remains consistent; the source-aware flow keeps the
current task and next action visible; the Zoom states are bounded as
`Consent -> Start -> Draft -> Review`; and the narrow view reflows vertically
with no horizontal overflow. Browser console warnings/errors were absent.

### GitHub review

PRs #129 through #136 are merged to `main`; each recorded test, browser, and
operator CI job is green, with no open review threads or comments. The final
PR7 run is
[32932715724](https://github.com/jayeshsuyal/ExitSpec/actions/runs/32932715724)
and the ledger follow-up run is
[32933056045](https://github.com/jayeshsuyal/ExitSpec/actions/runs/32933056045).
The failed-log views were empty. A full log download was attempted but GitHub's
API returned a transient connectivity error; no merge decision was based only
on the status badge.

### Privacy, retention, and secret audit

- `.zoom-fixture-private/` is ignored and the private capture directory is
  owner-only (`0700`); no private-capture path is tracked.
- No raw transcript bytes, tokens, URLs, meeting IDs, participant IDs, or
  credentials were added to git, browser projections, tests, evidence, or this
  report.
- The setup/runtime boundary still keeps one-time attestation separate from
  per-meeting evidence, and the missing endpoint-validation artifacts continue
  to produce the legacy incomplete outcome.
- The exposed Zoom credential remains an external owner action. It was not
  printed, copied, rotated, or replaced by this audit.

## Rollback and release gate

The audit-branch dependency change is additive and rollback-safe: reverting
the audit PR restores the prior dev constraint but would also restore the
known pytest vulnerability, so the preferred rollback is to keep the fixed
constraint and repair forward. Runtime PRs remain independently revertible in
their original order; no published history was rewritten.

The local synthetic acceptance gate is satisfied. The real live release gate
remains blocked until the owner completes credential rotation/disablement and
privacy/custody authorization, then a fresh authorized Zoom meeting proves the
complete final path. No live Zoom completion claim is made before that event.
