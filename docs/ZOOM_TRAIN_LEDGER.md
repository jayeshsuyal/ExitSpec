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
| PR6 pending | Guided `/app` handoff | `codex/zoom-train-pr6-guided-app` | `bbae108` | pending | Full Python, distribution, operator, HTTP, and Chromium checks green; not yet opened |
| PR7 pending | Adversarial E2E, regression audit, demo readiness | pending | pending | pending | Pending PR6 merge |

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
