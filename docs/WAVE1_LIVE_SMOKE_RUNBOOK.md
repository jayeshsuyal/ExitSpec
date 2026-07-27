# Wave 1 Fireworks live-smoke runbook

## Status and purpose

This runbook prepares one separately approved, funded, synthetic Fireworks
assisted-authoring action at the exact reviewed ExitSpec revision below. It is
an operator procedure, not authorization to run the action.

**This runbook does not close Wave 1.** A successful smoke produces one missing
piece of evidence. The conductor must still reconcile that evidence with the
frozen acceptance manifest, deterministic failure matrix, authority/privacy
gates, and reviewed revision before recording a Wave 1 decision.

Never use real customer data. Never reuse a key that has appeared in chat,
source control, logs, screenshots, or another operator's shell.

## Required people and approval record

Use two named roles:

- **Approver:** confirms the frozen disclosure, funding, data policy, and maximum
  spend, then authorizes exactly one product action.
- **Operator:** performs the preflight, runs that one action, captures only
  allowed evidence, and revokes the dedicated key.

Before creating or loading a credential, record outside the repository:

- operator and approver names;
- approval timestamp in UTC;
- exact revision and manifest version;
- the statement: “I authorize one bounded synthetic Fireworks action under the
  frozen Wave 1 disclosure, with up to two provider attempts and no more than
  `$0.01` estimated request cost”;
- the approved execution window; and
- the approved evidence location.

Approval expires when the execution window ends, the revision or disclosure
changes, the server restarts after a possible send, or any abort condition
occurs. A later action needs a new approval.

## Frozen execution card

| Field | Required value |
| --- | --- |
| Git revision | `c2d4bd1eb8585ddd2cc32459259916353e4bf637` |
| Manifest | `exitspec-wave-1-fireworks-assisted-authoring` version `1.0.0`, `FROZEN` |
| Manifest file | [`wave-1-acceptance-v1.json`](../examples/support-agent/fireworks/wave-1-acceptance-v1.json) |
| Policy identity | `5dcd98965bc158ed915f4bf9207a8b43e647e2d069126da970ea36bac008aa71` |
| Disclosure identity at this revision | `wave1_provider_disclosure_0a558b4a6461df684397b8f17ca4d211752b246ed241323de0534c178805ea09` |
| Provider / API surface | `fireworks` / `chat_completions` |
| Model | `accounts/fireworks/models/deepseek-v4-flash` |
| Exact endpoint | `https://api.fireworks.ai/inference/v1/chat/completions` |
| Service tier | `standard` |
| Credential source | Server environment variable `FIREWORKS_API_KEY`, read only with `--enable-fireworks` |
| Source fixture SHA-256 | `159c7729450b1ace0646f25943850b5561a3307558eb41f9a0fd48628a436a94` |
| Approved case | `measurable-integer-threshold` |
| Redacted request payload SHA-256 | `417e3f118997ca43e317f46b69cce72537c2a5b2a1e258f5d2e93a329717dc6d` |
| Redaction configuration SHA-256 | `ff66c680e81956fad57d019ea9a517fbf305c1adf752432f6914dbc9d4ca4422` |
| Input / output ceilings | 6,000 estimated input tokens / 2,000 output tokens |
| Runtime ceilings | 30-second timeout, at most 2 attempts, at most 10-second `Retry-After` |
| Spend ceilings | `$0.01` per product action; `$0.10` process-local reservation ceiling |
| Live-smoke latency gate | At most 10,000 ms for the successful smoke observation |
| Acknowledgement | Exact-disclosure-bound, server-validated, one use, 300-second TTL |

The approved synthetic source is:

> Customer: The agent must reach 95% exact tool-selection accuracy across 200
> approved cases.

The payload digest binds the complete redacted structured request, including its
messages, response schema, model, timeout, token bounds, and budget. Do not copy
the request body into the evidence pack.

The frozen 2026-07-27 standard pricing snapshot is `$0.14` input, `$0.028`
cached input, and `$0.28` output per million tokens. At both token ceilings, the
snapshot calculation is `$0.0014`; ExitSpec still enforces the more conservative
`$0.01` action ceiling. Pricing is an estimate until reconciled with provider
billing.

The frozen provider-policy snapshot says:

- no persistent prompt or generation retention by default for open models
  without explicit opt-in;
- prompt cache may remain in volatile memory for several minutes while active;
- service metadata, including token counts, may be logged;
- advanced-feature opt-in is false; and
- region is not asserted by the cited provider documentation.

See the complete boundaries in
[`PROVIDER_SPEC.md`](PROVIDER_SPEC.md) and [`SECURITY.md`](SECURITY.md).

## Abort conditions

Stop before authorization and classify the smoke as `BLOCKED` or `FAIL` using
the outcome table below if any condition is true:

- `HEAD` is not the exact revision above, or the execution worktree is not
  clean. Read this runbook from its reviewed documentation branch while the
  smoke itself runs from a separate clean checkout of the pinned revision.
- Any preflight test fails.
- The provider account is suspended, unfunded, unable to cover the `$0.10`
  process guardrail, or cannot access the pinned model.
- The current provider pricing or data-handling terms materially differ from
  the frozen snapshots.
- Storage, advanced-feature opt-in, or another account setting conflicts with
  the disclosure.
- The key is not fresh and dedicated, has previously been exposed, or cannot be
  revoked immediately after the smoke.
- The named approver, exact wording, execution window, or evidence location is
  missing.
- The browser is not using `http://127.0.0.1:8765/app`.
- The panel does not show `Ready`, the model/destination/caps differ, or the
  disclosure changes after acknowledgement.
- Any real customer source, customer term, raw audio, or arbitrary pasted input
  could be confused with the code-pinned synthetic request.
- Other account traffic prevents one narrow dashboard window from being
  reconciled.
- A credential, request/response body, generated proposal, or provider request
  ID appears in a log, screenshot, receipt, or intended evidence artifact.

Do not “fix and continue” after an abort. Correct the condition and obtain a new
approval for a new window.

## Preflight

### 1. Verify the reviewed revision

From the repository root, run:

```bash
git rev-parse HEAD
git status --short
```

The first command must print
`c2d4bd1eb8585ddd2cc32459259916353e4bf637`. The second must print nothing.
Do not pull, rebase, switch branches, or edit code during the execution window.
Prepare the repository's isolated `.venv` and install its development
dependencies before the approved window; the commands below assume that
environment is ready.

### 2. Re-run the local, provider-free proof

Run these tests **before** putting a key in the server environment:

```bash
.venv/bin/python -m pytest -q \
  tests/test_wave1_acceptance_manifest.py \
  tests/test_wave1_runtime.py \
  tests/test_authorized_fireworks.py \
  tests/test_wave1_fireworks_failure_matrix.py \
  tests/test_wave1_provider_execution_web.py \
  tests/test_wave1_provider_execution_cli.py
node --check src/exitspec/static/app.js
```

These tests use deterministic fake provider connections. They must not receive
or require a live credential. Record the command, named test files, UTC time,
and result—not merely “tests passed.”

### 3. Check the provider account before creating the key

In the Fireworks dashboard:

1. Confirm the account is active and can cover the `$0.10` process guardrail.
2. Confirm the pinned model is available to the account.
3. Compare current pricing and data-handling terms with the frozen execution
   card. Abort on a material difference.
4. Confirm no account setting opts this request into persistent storage or an
   undisclosed advanced feature.
5. Choose a quiet UTC window and record aggregate request, token, and billed-cost
   baselines. Do not record an account balance, credential, request ID, or raw
   content in the evidence pack.

An unfunded or suspended account is an allowed `BLOCKED` result, not permission
to send a cheaper, different-model, or different-endpoint request.

### 4. Create and load one fresh dedicated key

Create a dedicated key immediately before the approved window. Do not paste it
into a command, command-line argument, file, `.env`, note, screenshot, or chat.
In a fresh `zsh` terminal, read it without echoing:

```bash
read -r -s "FIREWORKS_API_KEY?Fresh dedicated Fireworks key: "
export FIREWORKS_API_KEY
printf "\n"
PYTHONPATH=src .venv/bin/python -m exitspec.cli serve \
  --host 127.0.0.1 --port 8765 --enable-fireworks
```

The terminal must report:

```text
Synthetic-only Fireworks assist enabled. Every send still requires explicit disclosure acknowledgement.
```

If it reports `Key missing`, stop the server, unset and revoke the key, and mark
the attempt `FAIL` without clicking the provider action.

## Execute exactly one product action

1. Open `http://127.0.0.1:8765/app` in one browser tab. Do not use recording
   mode, multiple tabs, direct POST requests, or browser automation.
2. Open **Meeting source**, then find **Draft with Fireworks**.
3. Confirm the panel says **Ready** and shows:
   - model `accounts/fireworks/models/deepseek-v4-flash`;
   - destination `api.fireworks.ai`;
   - `$0.01 max/action` and `$0.10 process guardrail left`;
   - the frozen data-policy summary.
4. Expand **Policy details** and verify the exact endpoint, 30-second timeout,
   at most two attempts, volatile prompt-cache notice, metadata notice, and
   unasserted region.
5. Read the consent text. It must say:
   “I authorize one bounded synthetic action (up to 2 provider attempts).”
6. The approver gives the final go/no-go for this disclosure and window. The
   operator checks the acknowledgement once.
7. Click **Draft with Fireworks** exactly once. This is one product action; its
   bounded retry policy may make at most two provider attempts for timeout,
   `429`, or `503`.
8. Wait for a terminal status. Do not edit notes, reset the workflow, restart the
   server, close the tab, approve a proposal, or click the action again.
9. On success, verify exactly one source-linked candidate appears and remains
   `NEEDS_REVIEW`. It must represent a measurable 95% threshold over 200 cases.
   It must not approve, confirm, freeze, measure, or assign a verdict.

If the browser says the connection was interrupted while the same server is
still running, do not create a new authorization or restart. The product retains
the same idempotency keys for safe replay, but the operator must first get the
approver's explicit confirmation to click the same action again solely to
retrieve that existing operation. If the server may have restarted, treat the
external outcome as unknown and follow incident handling; do not retry.

## Capture the content-free terminal receipt

After the operation is terminal and while the same server is running, this local
read-only command prints only the last receipt:

```bash
curl -fsS http://127.0.0.1:8765/api/provider/fireworks/disclosure \
  | .venv/bin/python -c 'import json,sys; r=json.load(sys.stdin)["runtime"]["last_execution"]["receipt"]; print(json.dumps(r,indent=2,sort_keys=True))'
```

The receipt must contain exactly:

```text
provider
model
endpoint
attempts
latency_ms
input_tokens
output_tokens
total_tokens
estimated_cost_usd
pricing_version
outcome_code
```

Unknown metadata must be `null`, not invented. The receipt must not contain:

- credentials or headers;
- request or response bodies;
- raw or redacted transcript text;
- proposal text; or
- a provider request ID.

Do not save the full `/api/state`, execution response, browser network capture,
or provider response. Those surfaces can contain generated proposal text and are
not evidence artifacts.

## Reconcile with the Fireworks dashboard

Use the pre-recorded UTC window and dashboard baseline. Record only:

- UTC window start and end;
- pinned model;
- aggregate provider request count in that window;
- aggregate input, cached-input, and output token counts when available;
- aggregate billed cost or account usage delta; and
- whether the dashboard and local receipt reconcile.

One product action may appear as one or two provider attempts. The dashboard
count must not exceed the receipt's `attempts`, and the receipt must not exceed
two. The local `estimated_cost_usd` and provider-billed amount may differ because
of rounding or billing implementation, but both must remain at or below
`$0.01`; explain any non-zero difference without copying raw content.

Do not copy provider request IDs, payload previews, response text, account
balance, credentials, headers, or unrestricted dashboard screenshots. A
dashboard confirms account-side request and billing facts; it does not prove
ExitSpec's local authorization or authority boundaries.

If unrelated traffic makes the window ambiguous, the result is `BLOCKED`. Do not
run another request merely to obtain a cleaner window.

## Outcome decision

### `PASS` — live smoke only

Record `PASS` for this smoke only when all are true:

- the exact revision, manifest, policy identity, disclosure, model, endpoint,
  payload digests, and caps match;
- the terminal operation is `succeeded_needs_review` with outcome code
  `success`;
- `provider_call_attempted` is true and `attempts` is one or two;
- exactly one expected proposal is created, source-linked, locally validated,
  and still `NEEDS_REVIEW`;
- no agreement, confirmation, freeze, measurement, or verdict is created;
- the receipt has exactly the allowed fields and no forbidden content;
- observed latency is no more than 10,000 ms;
- estimated and reconciled billed cost are each no more than `$0.01`;
- dashboard request/token/cost facts reconcile with the receipt; and
- the key is revoked and cleanup is recorded.

This is not a Wave 1 `PASS`. It is evidence for the later wave-gate decision.

### `BLOCKED`

Record `BLOCKED` when an external condition prevents a valid smoke, including:

- verified suspended or unfunded account (`account_unavailable`);
- approval or a fresh revocable key is unavailable;
- current provider policy/pricing no longer matches the frozen disclosure;
- other account traffic makes dashboard reconciliation impossible; or
- a crash after send leaves the external result unknown.

Record whether no call was attempted or the terminal receipt reports an attempt.
`BLOCKED` does not close Wave 1.

### `FAIL`

Record `FAIL` for every other non-conforming observation, including:

- authentication, configuration, redirect, timeout, retry-exhaustion, service,
  malformed-response, schema, source-link, budget, or review-boundary failure;
- an unexpected model, endpoint, payload, disclosure, attempt count, cost, or
  receipt field;
- missing local validation or a proposal that is not `NEEDS_REVIEW`;
- latency above 10,000 ms;
- extra or unexplained provider requests; or
- any secret, content, authority, or privacy violation.

A terminal failure does not authorize another action. Diagnose with sanitized
facts, correct the issue, and request a separately approved future smoke.

## Cleanup and key rotation

Immediately after receipt capture and dashboard reconciliation:

1. Stop ExitSpec with **Ctrl+C**.
2. Remove the credential from the shell:

   ```bash
   unset FIREWORKS_API_KEY
   ```

3. Revoke the dedicated key in the Fireworks dashboard, even after a failed or
   blocked action.
4. Close the dedicated shell and browser tab.
5. Confirm no key, raw request/response, proposal, or provider request ID was
   copied into the repository, shell history, clipboard manager, screenshots,
   evidence location, or chat.
6. Record key revocation time and the person who verified it. Record only a
   redacted key label if the dashboard requires an identifier.

Do not retain the key for debugging or a second smoke.

## Incident handling

Stop the server and revoke the key immediately if any of these occur:

- credential exposure or suspected exposure;
- unexpected destination, redirect, model, or payload;
- possible real-customer content egress;
- more than two provider attempts or more than `$0.01` cost;
- raw content or a provider request ID in an evidence surface;
- provider output that attempts to approve, confirm, freeze, measure, or decide;
- unexplained dashboard activity; or
- process/server loss after a possible send.

Do not paste raw content into an issue. Preserve only the approved content-free
receipt, UTC window, revision/manifest identities, aggregate dashboard facts,
typed outcome, and a description of the boundary crossed. Notify the security
owner and conductor, mark the smoke `FAIL` or `BLOCKED`, and prohibit another
provider action until the incident is reviewed.

Exactly-once behavior is guaranteed only within one running process. If the
process dies after network send, the provider-side outcome may be unknown. Never
restart and repeat under the original approval.

## Engineering Evidence Pack checklist

The evidence record should contain:

- [ ] **Claim:** one exact, bounded, synthetic Fireworks action was attempted.
- [ ] **Scope identity:** revision, manifest ID/version, policy identity,
      disclosure identity, fixture hash, case ID, payload digest, redaction
      configuration digest, model, and endpoint.
- [ ] **Authorization:** named approver/operator, exact authorization wording,
      UTC approval and execution window.
- [ ] **Automated proof:** named preflight tests, command, revision, UTC time,
      and result.
- [ ] **Manual proof:** panel values reviewed, acknowledgement checked, one
      product action initiated, and final proposal state inspected.
- [ ] **Terminal outcome:** `PASS`, `FAIL`, or `BLOCKED`, plus typed outcome,
      safe next action, provider-call-attempted fact, and attempt count.
- [ ] **Receipt:** only the eleven allowed content-free fields.
- [ ] **Dashboard reconciliation:** aggregate request count, tokens, billed cost,
      UTC window, and reconciliation conclusion—no raw content or request ID.
- [ ] **Authority proof:** every accepted proposal remained `NEEDS_REVIEW`; zero
      provider-created agreements, confirmations, freezes, measurements, or
      verdicts.
- [ ] **Privacy proof:** synthetic-only source; no credential, headers, bodies,
      transcript, proposal text, or provider request ID in retained artifacts.
- [ ] **Cleanup:** server stopped, environment cleared, dedicated key revoked,
      and revocation independently verified.
- [ ] **Limits:** one smoke does not replace deterministic tests, prove provider
      retention behavior, authenticate a customer, authorize production, or
      establish the complete Wave 1 exit gate.
- [ ] **Rollback:** Fireworks remains disabled by default; omit
      `--enable-fireworks` to preserve the deterministic local path.

The conductor—not the operator—makes the final Wave 1 decision against the
binary gate in the
[`Engineering Playbook`](ENGINEERING_PLAYBOOK.md#wave-1--explicit-assisted-authoring-and-live-fireworks)
and records any remaining gap in [`ROADMAP.md`](ROADMAP.md).
