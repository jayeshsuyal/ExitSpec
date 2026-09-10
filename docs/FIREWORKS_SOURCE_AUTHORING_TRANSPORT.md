# Bounded source-authoring transport mechanism

The accepted ZF3a private live-protocol mechanism is composed into the unified
SourceNeutral runtime and exercised with fake-only tests. **Production execution remains disabled.** The installed worker and
public supervisor refuse before credential input, pipe/process creation or
networking. The production profile registry is empty; there are no CLI flags,
environment variables, JSON fields or registration APIs that select fake or
approved operation. The private mechanism is reachable in tests by monkeypatching
admission and invoking a test-only child that poisons real network access.

The existing synthetic worker and seven-field WorkerBinding remain unchanged.
Both realms use one operation claim/D/F/cleanup engine. Frozen Wave-1 Fireworks
and standalone STT transports remain unchanged. Sealed issuer/operator/web
wiring is implemented; a real approved profile/tokenizer and live qualification
are absent from the installed candidate.

## Wire and lifecycle contract

Live frames use the separate `exitspec.source-authoring-live-ipc/1`, `LIVE_PINNED`
namespace. The eight-byte big-endian prefix declares total bytes after the first
word and canonical RFC8785 metadata length. Both length words are validated before
metadata or payload is read. Metadata is at most 2,048 bytes. The exact binding
has epoch, grant, operation, generation, nonce, body/profile/launch-profile SHA256,
credential generation and code revision. Synthetic identifiers cannot be coerced
into this binding. The fixed request-profile hash is independently checked.

| Frame | Channel | Payload | Maximum total bytes |
| --- | --- | --- | --- |
| PREPARE | parent stdin | empty | 2,056 |
| READY_NO_SEND | child stdout | empty | 2,056 |
| SEND_TICKET | parent stdin | exact body, 1..65,536 | 67,592 |
| CREDENTIAL | separate inherited pipe | secret, 1..4,096 | 6,152 |
| RESULT | child stdout | response, 1..262,144 | 264,200 |

All parent frames total at most 75,800 bytes; all child frames total at most
266,256, within the existing 81,920/278,528 ceilings. Source text remains limited
to 16,384 UTF-8 bytes. Duplicate/noncanonical/unknown metadata and partial,
trailing or swapped frames refuse. Credentials require exact ASCII bearer-token
grammar and refuse whitespace, controls, CR/LF/NUL and invalid header characters.

PREPARE carries only bindings, the original monotonic deadline and inherited
credential descriptor. The child confirms READY without source or secret bytes.
The parent prepares immutable ticket bytes and separate private credential bytes
before D. D staging sends nothing. Outside owner locks, handoff writes the entire
ticket, closes stdin, rechecks cancellation, then writes and closes the credential
pipe. A single nonblocking writer uses chunks at most 4,096 bytes and a one-second
handoff limit inside the original 30-second lifetime. The child requires complete
matching ticket/EOF before reading credential, then matching credential/EOF before
transport. Generation rotation uses revocation; replacement credentials are not
accepted for a staged ticket.

One watchdog owns process polling, TERM, up to 250ms wait, KILL and up to 750ms
wait, within a one-second cleanup interval. Cancellation requests do not perform
process control under owner locks. An unobserved exit retains the slot; it is
never called successful cleanup. Partial handoff or post-D cancellation is an
unknown delivery outcome, with no resend/refund promise. The mechanism revokes
RESULT_READY on cancellation, but existing F owner validation is still required
before the shared operation engine can publish. The supervisor itself cannot publish.

## Exact request and strict initial response

The HTTPS primitive validates the existing r2 template/profile/schema/system
pins and canonical source wrapper before creating a connection. It passes the
same retained body bytes to exactly one fixed POST to
`api.fireworks.ai:443/inference/v1/chat/completions`. No caller endpoint, headers,
proxy, redirects, retries, tools, compression or streaming option is available.
Socket timeouts track remaining lifetime; the child watchdog remains the total
backstop for DNS/TLS/header stalls and trickles.

HTTP success requires status 200 and JSON content type. Duplicate framing
headers, content encoding other than identity, incompatible transfer encoding,
oversized headers/body and a mismatched declared content length refuse. Header
parsing is bounded by Python's HTTP parser and is subsequently restricted to at
most 100 headers and 16,384 combined name/value characters. The body is read in
bounded chunks and parsed as strict UTF-8 JSON without duplicate/nonfinite values.

The 262,144-byte response cap measures decoded entity bytes, not all HTTP chunk
framing or trailer bytes. HTTP framing is handled by the standard library; this
unit does not claim a whole-HTTP-wire cap. The transport retains the response's
actual socket before `getresponse()` can detach it from a closing connection,
refreshes its remaining timeout before each body read and stops at a consumed
declared entity length before touching the response's closed socket again.

The root requires exactly id, object, created, model, choices and usage, with
only optional system_fingerprint. Object must equal chat.completion and model
must equal `accounts/fireworks/models/deepseek-v4-flash-0731`. Id/fingerprint are
1..128 printable ASCII characters and are discarded after validation. Created
is a nonnegative nonboolean safe integer. Exactly one choice requires integer
index zero, assistant message and stop finish reason; only null logprobs is
optional. Message requires nonempty string content; optional reasoning_content
must be null/empty, tool_calls null/empty-list, and refusal null. Other fields
at every depth refuse.

Usage requires nonboolean safe integers prompt_tokens <=8,192,
completion_tokens <=2,000 and total_tokens <=10,192, equal to their sum. Optional
prompt_tokens_details contains exactly cached_tokens <=prompt_tokens; optional
completion_tokens_details contains exactly reasoning_tokens ==0. Null/empty
detail objects, unknown billable components, aliases and automatic profile
expansion are rejected. Extracted content still needs the existing exact inner
schema, numeric/source-anchor and owner source-binding checks before F.

The strict initial envelope is not a claim of exact model/account compatibility.
New real-provider fields require an explicitly reviewed compatibility change.
No body, secret, provider identifiers/errors or raw diagnostics are reflected
in errors or public receipts. The worker exits silently on failure.

## Evidence and remaining gates

Tests use public synthetic literals and fake HTTP/child behavior only. Controls
cover disabled installed defaults, successful fake requests, exact bytes,
malformed envelope/usage, framing/EOF/binding swaps, body and credential caps,
partial/nonblocking writes, cancellation, generation mismatch, timeouts, trickles,
startup failure and observed/unobserved cleanup. Existing synthetic operation
tests retain D/F regressions. Additional private fake admission tests exercise
issuer identity, installation, token proof, live realm D/F, source/closure races
and one actual browser journey without provider access.

Same-OS-user code remains trusted. Clearing Python references does not guarantee
memory zeroization. Declared/observed token counters do not prove all billable
tokens; reservations are not an invoice ceiling. Public documentation is not
account, region, custody or execution approval. No actual account, key, native
Zoom meeting, provider request, GPU or spending qualification has run.

The central production registry lives only in `source_authoring_launch.py` and
remains empty. The child admits its compiled profile before descriptor access.
Fixed profile/code fields match the admitted profile; epoch/grant/operation/body,
nonce, worker generation and credential generation remain per-launch/attempt
bindings validated by the parent and across every child frame. The parent checks
its issuer lease before and between body/credential handoff, outside I/O locks.

No launch factory accepts a worker, clock, schedule, tokenizer, raw token count,
credential callback or transport factory. Tests replace private admission and
transport seams. Real qualification, profile population, activation, publishing
and shipping remain outside this offline integration's authorization.
