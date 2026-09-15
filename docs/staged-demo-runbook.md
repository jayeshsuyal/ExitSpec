# Staged Zoom → Fireworks demo

This run demonstrates native Zoom text intake, one Fireworks draft, human correction/confirmation, a frozen criterion, supported synthetic proof, and handoff. It does not qualify production serving, measure GPUs, or establish general drafting quality. The browser layout and controls are unchanged; demo-specific copy, mode validation and a 0/1 counter distinguish the one-attempt authorization.

## What is implemented

- `source_authoring_operator --demo` accepts a distinct, independently reviewed detached demo approval. The default production path remains closed while its serving qualification registry is empty.
- The exact pinned request uses the existing model and TEXT drafting endpoint, source/redaction validation and private TTY credential mechanism. No second STT or SDK is added.
- One attempt is consumed atomically in the approved private run directory before dispatch. The installed child separately fences dispatch replay. Failures, cancellation, process restart and uncertain outcomes do not refund the attempt. Same-user/host local files are not tamper-proof against their owner or administrator.
- Source/request/response limits remain 16/64/256 KiB, requested output ≤2,000 tokens, and a 30-second local execution deadline. The 8,192-token local preflight is not a provider-token ceiling. Provider-reported usage is separate from local counting and the $0.01 local reservation is not a guaranteed invoice ceiling.
- `consumed.json` records UNKNOWN usage and billing before any delivery is possible. A complete bounded response produces `observation.json` before draft acceptance; well-formed over-limit counters are retained unchanged and the draft is rejected. Malformed/absent usage is distinguished from reported usage. No response means UNKNOWN, never an assumed zero. Receipt failure prevents publication. No automatic invoice reconciliation or extra provider call occurs.
- Consuming the attempt closes new authoring admission only. Existing result polling, cancellation, proposals, human decisions, criterion freeze, synthetic proof and handoff remain available.

## External readiness — still to establish

Adding Fireworks credits is not established as the only remaining step. No live activity was performed during implementation.

| Item | Status before a live run | Required operator evidence/action |
| --- | --- | --- |
| Exact candidate and environment | Review and complete-gate checkpoint required | Use the final clean committed checkout and its matching fresh editable environment. A wheel-only install or prior candidate's environment is insufficient for the source/worker checks. |
| Fireworks account | NOT_READY until separately verified | Owner restores account readiness if necessary; approves effective pricing, data handling and one attempt's spend terms. No invoice-ceiling promise. |
| Zoom callback | UNKNOWN | Owner confirms the existing callback state. Any receiver start, public tunnel, configuration change or validation request requires separate authority. Do not inspect credential-bearing pages. |
| Callback forwarding | UNKNOWN in real delivery | Existing receiver requires exact callback Host/path/signature handling; expose only the separately approved callback surface, never the local app or credentials. |
| Zoom host and participants | UNKNOWN | Eligible app/host, real meeting and exactly the approved speakers; separate metadata and capture consent. Keep the reviewed absolute capture/enrollment deadline. |
| Signaling-only enrollment | UNKNOWN with provider | Real speaker metadata and keep-alives must arrive before media readiness within the existing bounds. Missing metadata refuses; no media fallback. |
| Native transcript and stop/drain | UNKNOWN with provider | Real transcript must match the strict existing envelope and participant association. Existing stop acknowledgment/drain behavior must complete. No STT fallback. |
| Live authorization | NOT_GRANTED by code approval | Explicit approval for the concrete meeting/capture, callback/tunnel actions if needed, source/data handling and one Fireworks request. Keys only through the existing private operator TTY. |

## Exact-candidate preparation (no provider request)

After independent code/test clearance, the trusted operator/reviewer prepares a private external approval using schema `exitspec.detached-demo-approval.v1` and the exact `_DemoLaunchProfile` fields. No sample is an executable approval. Review must independently anchor its SHA-256; the digest is an equality check, not a signature or proof of the approver's identity.

The profile contains the final commit, tree and complete tracked-file SHA-256 manifest; pinned tokenizer identity and verified artifact paths/hashes; approved pricing/custody/region references; finite validity times; fixed request/schema/header/redaction pins; $0.01 local request and launch reservations; and `accounting_contract=exitspec-demo-local-accounting-v1`. It also binds a stable 64-hex run ID and the pre-existing 0700 run directory's canonical absolute path, device, inode, owner UID and local hostname. The run directory must stay outside the code checkout. Replacing the directory, changing the run ID or moving approval to another directory does not renew that approval. Never delete consumption markers to retry.

Tokenizer assets remain data-only at the pinned DeepSeek revision, with tokenizers 0.23.2. Reuse the already verified local artifacts from the approved environment records. Do not download a replacement or infer server parity. Exact artifact details and detached-approval responsibilities are in [live accounting](source-authoring-live-accounting.md).

The reviewed command has this shape; replace placeholders privately from the approved records, not from chat credentials:

```sh
/path/to/matching-environment/bin/python -I -m exitspec.source_authoring_operator \
  --demo --approval-id REVIEWED_ID \
  --approval-file /private/approved/demo-approval.json \
  --approval-sha256 INDEPENDENTLY_REVIEWED_SHA256 \
  --enroll-metadata --port 8765 --output-root /private/approved/evidence
```

`--demo` must be first. Running this command is a live-launch action, not part of the offline checklist. Admission precedes private key input. Follow the existing hidden-input operator prompts (Ctrl-J submits a line); do not put credentials in arguments, environment variables, logs or messages. Metadata enrollment, browser capture consent and exact-source Fireworks acknowledgment remain separate. Stop with the existing operator stop prompt after handoff; it revokes the pairing and launch and closes the server.

## Demonstration script

1. Once the separately approved operator session is ready, create the meeting POC in the existing app, enroll the two approved numeric participants using the existing speaking prompts, and acknowledge capture in the Zoom panel.
2. Speaker one says: “Response quality must satisfy the customer. The system must select the exact requested tool.” Speaker two says: “Latency must remain visible to the customer.” These are staged requirements, not production measurements.
3. Stop capture, await the existing stop/drain states, and process the native transcript into the source receipt. Inspect the actual received/redacted text. A missing or invalid transcript stops the demo.
4. Open source authoring. Check the visible one-attempt disclosure and 0/1 counter. Acknowledge the exact source, then choose Run once. The first actual Fireworks output is unverified until it passes validation; the synthetic rehearsal does not predict its wording or quality. A refusal or timeout consumes this run and is not retried.
5. Follow the existing proposal review. Correct or reject invented/unsupported requirements, confirm only source-supported claims, and retain the exact-tool-selection criterion for supported synthetic proof. Unsupported quality and excluded latency requirements remain visible with their limitations. No invented requirement may be accepted just to finish the demonstration.
6. Complete the existing human capability plan, customer confirmation and criterion freeze. Run the existing synthetic evidence policy, inspect its typed verdict and Evidence Pack, and record the human handoff. PASS is not guaranteed: report the verdict actually produced. Synthetic results never become production or GPU evidence.
7. Retain the private consumption/observation records under approved custody, separately from the synthetic proof artifacts. An observation contains provider-reported counts, not a settled invoice. Do not claim live completion if any stage failed.

## Offline rehearsal

The focused checkpoint provides raw logs, JUnit and screenshots. `tests/test_combined_roster_operator.py` exercises both the accepted offline mode and the new demo presentation with a fake Zoom child and fake Fireworks subprocess, one real loopback app, native source receipt, exact-source consent, human confirmation/freeze, supported synthetic proof and handoff. No secret, live provider call or spend is needed. The test helper blocks provider networking. Only explicit test composition supplies fake transport; it cannot be selected through the installed operator.
