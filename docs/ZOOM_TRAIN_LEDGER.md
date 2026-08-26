# ExitSpec Zoom-to-POC train ledger

This ledger records the sequential train. CI links point to the GitHub Actions
run that gated each merged PR. Raw private capture content and credentials are
intentionally absent.

| PR | Concern | Branch | Commit / merge commit | CI | Tests / status |
| --- | --- | --- | --- | --- | --- |
| [#129](https://github.com/jayeshsuyal/ExitSpec/pull/129) | Security and evidence boundary | `codex/zoom-train-pr1-security-evidence` | `27b5b0f` / `4149ae0` | [run 32925386029](https://github.com/jayeshsuyal/ExitSpec/actions/runs/32925386029) | Python 3.12/3.13, Browser E2E, operator CI green; merged |
| [#130](https://github.com/jayeshsuyal/ExitSpec/pull/130) | Privacy-reviewed fixture pipeline | `codex/zoom-train-pr2-privacy-fixtures` | `ff5bde5` / `d4c4c64` | [run 32926262293](https://github.com/jayeshsuyal/ExitSpec/actions/runs/32926262293) | Python 3.12/3.13, Browser E2E, operator CI green; merged |
| [#131](https://github.com/jayeshsuyal/ExitSpec/pull/131) | RTMS packet decoder | `codex/zoom-train-pr3-rtms-decoder` | `7ba4878` / `c8e7104` | [run 32927361818](https://github.com/jayeshsuyal/ExitSpec/actions/runs/32927361818) | Python 3.12/3.13, Browser E2E, operator CI green; merged |
| [#132](https://github.com/jayeshsuyal/ExitSpec/pull/132) | Session state and idempotency | `codex/zoom-train-pr4-session-idempotency` | `2150ec1` / `baa7c64` | [run 32928353177](https://github.com/jayeshsuyal/ExitSpec/actions/runs/32928353177) | Python 3.12/3.13, Browser E2E, operator CI green; merged |
| [#133](https://github.com/jayeshsuyal/ExitSpec/pull/133) | Zoom transcript proposal bridge | `codex/zoom-train-pr5-proposal-bridge` | `e367010` / `146cc0b` | [run 32929276328](https://github.com/jayeshsuyal/ExitSpec/actions/runs/32929276328) | Python 3.12/3.13, Browser E2E, operator CI green; merged |
| [#134](https://github.com/jayeshsuyal/ExitSpec/pull/134) | Guided `/app` handoff | `codex/zoom-train-pr6-guided-app` | `bbae108` + ledger `b932a79`/`3a486c6` / merge `b0230e4` | [run 32931484770](https://github.com/jayeshsuyal/ExitSpec/actions/runs/32931484770) | Full Python 3.12/3.13, distribution, operator, HTTP, and Chromium checks green; merged |
| [#135](https://github.com/jayeshsuyal/ExitSpec/pull/135) | Adversarial E2E, regression audit, demo readiness | `codex/zoom-train-pr7-adversarial-e2e` | `a2e8793` + ledger `f9e34cb`/`aee54c0` / merge `a98cfda` | [run 32932715724](https://github.com/jayeshsuyal/ExitSpec/actions/runs/32932715724) | Python 3.12/3.13, Browser E2E, operator, npm audit CI green; merged |
| [#136](https://github.com/jayeshsuyal/ExitSpec/pull/136) | Ledger bookkeeping | `codex/zoom-train-ledger-final` | `297ecf0` / `080288a` | [run 32933056045](https://github.com/jayeshsuyal/ExitSpec/actions/runs/32933056045) | Documentation-only follow-up; all CI jobs green; merged |

## Holistic release audit

The post-PR7 conductor gate is recorded in
[`ZOOM_RELEASE_AUDIT.md`](ZOOM_RELEASE_AUDIT.md). The local synthetic train
passes the complete review, regression, privacy, dependency, UI, and E2E audit.
The real Zoom release claim remains blocked on credential rotation/disablement,
explicit privacy/custody authorization, and a fresh authorized live call.

## Train risks and external gates

- The real capture remains private, untrusted, and unsealed; no raw packet or
  transcript content is in this repository.
- Before another live Zoom run, the owner must rotate or disable the exposed
  Zoom webhook credential in the Zoom portal without sending the replacement
  secret here.
- A content-free privacy/custody consent receipt is required before anyone
  opens the private capture for fixture derivation.
- The local guided handoff is synthetic-only. The complete live Zoom claim is
  intentionally not made until a fresh authorized call proves it.
- Python and Node dependency audits are now part of the post-train release
  record; both report no known vulnerabilities after the fixed pytest dev
  constraint is applied.
