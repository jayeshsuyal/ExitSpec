# Local native Zoom operator integration

This is opt-in local operator tooling, not hosted readiness. No funded or live
meeting verification is recorded here. The guided fixture remains separate.
Fake-network tests prove interface connections and human workflow boundaries;
they do not prove Zoom account access or a complete meeting transcript. Fireworks
source egress always needs separate consent.

## Operator bootstrap

After exact-candidate review and separate live-run authorization, run
`python -m exitspec.zoom_live_operator` from a clean committed source checkout
with the declared Python and Node dependencies installed. It starts `/app`, then
prompts locally for the created POC ID, exact meeting UUID, up to two approved
numeric participant IDs, callback host/path/port, owner consent/rotation receipt,
window and spending ceiling. Secrets use hidden terminal input. Never paste them
into chat or command arguments. The approved provider app must already be
installed and configured separately. Start RTMS in that app only after pairing
and browser capture consent. This runner does not configure accounts or initiate
OAuth installation and is not bundled hosted infrastructure.

The same OS user and installed repository are trusted. Browser requests cannot
pair, set credentials, choose executable paths or bind streams. The parent spawns
the fixed Node runner with private pipes, no inherited environment or unrelated
descriptors, no shell and no secrets in argv. Child stderr is discarded. A clean
matching Git revision is checked immediately before child launch, outside workflow
locks. Do not edit code during a live run; changed live code needs fresh evidence.

The child authenticates the exact raw webhook signature/timestamp and meeting
before offering one stream. Parent binding is restricted to that offer. Further
events bind the private child generation, stream and monotonic sequence. Unknown
participants and replacement streams fail closed. Native packets lack stream
identity; the parent supplies the authenticated connection binding and decodes
actual bytes. A constructed normalized dataclass or supplied digest is not proof.

Callbacks require exact configured Host/path, POST JSON and absent browser Origin.
Browser mutations separately require exact loopback Host/Origin and bounded JSON.
Neither Origin nor a provenance digest authenticates the operator. WebSockets use
TLS Zoom endpoints without redirects/userinfo. Initial roster delivery is not
guaranteed by the retrieved Zoom reference; the operator supplies the approved
numeric roster before capture.

## Lifecycle and bounded source publication

`PAIRED` and `WAITING` are not connected. `LISTENING` requires successful signaling
and transcript handshakes. Interruptions/reconnects are visible. Injected test
transport is explicitly simulated (`FAKE_ZOOM_RTMS`); no synthetic fallback can
represent live success.

Restored browser pages discard cached capture state and consent, invalidate old
requests, and fetch current operator state before enabling controls again.

Stop separates `STOP_REQUESTED`, matching provider acknowledgement, `DRAINING`,
normal media socket close and `CAPTURE_READY`. Packets pending before close are
admitted in arrival order. Webhook stop or silence alone cannot finalize capture.
During a requested stop, an early stop webhook preserves the original acknowledgement
deadline and pending packets; HTTP and WebSocket ordering is not assumed.
Ambiguous close, empty capture, overflow and deadlines fail without a source.
The result is **BOUNDED_WINDOW_NOT_COMPLETE_MEETING**: the protocol does not
establish cross-socket meeting completeness. Late transcript after the drain
marker fails before publication rather than silently disappearing.

Limits: 128 KiB IPC frames, eight queued parent commands, 64 KiB native packets,
1 MiB accepted native bytes, 256 unique segments and 16,000 source characters.
Child socket traffic separately caps 4,096 frames/8 MiB, 256 transcript deliveries,
three reconnect attempts and 1,024 IPC events. Callback HTTP caps four connections,
512 requests and 256 replay digests. Parent action keys cap at 64; one paired
capture is retained. Handshake/stop/drain deadlines and the approved 60–900 second
window revoke stalled sessions. These bounds do not meter provider billing: the
owner must verify rates and choose a window within the approved spending ceiling.

Reset, closure, archive, replacement, expiry and shutdown revoke the child. Native
publication uses the common source → draft lock order and closure reservation.
No provider I/O holds shared workflow locks. Exact redelivery cannot duplicate
source/proposal attachment. The common redaction spine prepares storage; raw text
is cleared after publication, failure or revocation. Only the redacted source
remains in process-local intake. Diagnostic recording/chaos hooks belong only to
the diagnostic harness; normal `live-child.mjs` supplies neither.
Every diagnostic connect/reconnect path also checks explicit network authorization;
disabled networking still permits authenticated diagnostic webhook recording.

Each fresh signaling connection sends handshake sequence `1`, following the
[signaling handshake reference](https://developers.zoom.us/docs/rtms/event-reference/#signaling-handshake-request).
Reconnects establish a fresh connection with that same starting sequence; no
random-nonce semantics are inferred. A replacement media connection that closes
before its handshake consumes the same bounded retry budget, with a fresh handshake
deadline on each attempt.

## Receipts and evidence

`GET /api/pocs/{poc_id}/zoom-live-receipt` returns content-free code revision,
source/session IDs, source-binding and redacted-source digests, transport mode,
scope, stop/drain facts and declared spending/time limits after publication.
It excludes transcript, names, provider IDs, credentials and capabilities and
does not assert measurement validity. Receipts remain process-local; save only
this sanitized projection for a separately approved smoke.

Human proposal review, customer confirmation, freeze and supported evaluation
remain the existing workflow. Native fake-network Chromium tests carry different
spoken thresholds through that path into a deterministic reference Evidence Pack.
PASS concerns those valid local reference measurements, not live Zoom completeness
or production infrastructure. Existing BLOCKED/NOT_PROVEN evidence rules remain.
The v0.4 release gate now separately requires all three native Chromium cases with zero
failures/errors/skips, in addition to the historical mandatory groups.

## Supporting repairs

The transitive `qs` override pins 6.16.0, fixing
[GHSA-x5fp-wj9c-mxmx](https://github.com/ljharb/qs/security/advisories/GHSA-x5fp-wj9c-mxmx)
and [GHSA-4mjr-xmp4-gh2g](https://github.com/ljharb/qs/security/advisories/GHSA-4mjr-xmp4-gh2g).
Express 4.22.2 and body-parser 1.20.6 remain. Query parsing is reachable, although
the published exploit prerequisites were not observed. Remove the override once
compatible upstream constraints admit the patched version.

Playwright 1.62.0 availability probes reproduced shutdown warnings on baseline and
candidate. Only those probes now use `asyncio.run` with the async context manager;
the executable existence check, missing-browser failure and no-skip semantics are
unchanged. No warnings are globally suppressed.
