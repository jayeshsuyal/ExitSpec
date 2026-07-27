# Provider execution boundary

ExitSpec providers may draft or transform structured information. They cannot
approve a requirement, freeze a contract, or assign `PASS`, `FAIL`, `BLOCKED`,
or `NOT_PROVEN`. Those remain separate human-authority and deterministic
decision boundaries.

## Interfaces

- `StructuredJSONRequest[T]` pins a model, messages, a top-level object JSON
  schema, a typed-conversion callback, timeout, optional token bounds, and an
  optional USD budget ceiling. Message content and schema data are hidden from
  `repr`.
- `ProviderTransport` is injected. The provider package contains no implicit
  network client and does not read credentials from the environment.
- `FireworksProvider.execute()` builds an OpenAI-compatible chat-completions
  request and returns `StructuredJSONResult[T]` only after JSON parsing and the
  caller's local validator both succeed.
- `ProviderReceipt` records provider, exact model, endpoint, total adapter
  latency, attempt count, token counts when returned, provider request ID,
  injected pricing version, and estimated cost when input/output usage exists.

The outbound request intent is:

```json
{
  "model": "accounts/fireworks/models/pinned-model",
  "messages": [{"role": "user", "content": "..."}],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "exit_spec_draft",
      "schema": {
        "type": "object",
        "properties": {"claim": {"type": "string"}},
        "required": ["claim"],
        "additionalProperties": false
      }
    }
  },
  "temperature": 0
}
```

This Chat Completions adapter deliberately sends neither a top-level `store`
field nor a `strict` member inside the `json_schema` wrapper because neither is
part of Fireworks' documented Chat Completions wire shape. Output constraints
come from the supplied JSON Schema—for example, callers may set
`additionalProperties: false`—and every response must still pass the mandatory
local validator. Provider-generated JSON remains untrusted input.

Fireworks' Responses API is a different, stateful surface and defaults to
storing responses. Any future ExitSpec Responses API adapter **must explicitly
send `store: false`**. This V1 adapter cannot call the Responses API: its
credential destination and path are restricted to Chat Completions.

### Local JSON Schema enforcement

`StructuredJSONRequest` validates the supplied schema itself against JSON
Schema Draft 2020-12 during construction. A provider response must then satisfy
that immutable schema snapshot before the typed-conversion callback can run.
Consequently, an identity or otherwise permissive callback cannot admit missing
required properties, wrong types, forbidden additional properties, or an
authority-shaped object that the schema does not allow.

Only fragment-local `$ref` and `$dynamicRef` values beginning with `#` are
accepted. Relative-file, absolute-URI, scheme-relative, and other non-local
references fail during request construction. Runtime validation also uses an
empty no-fetch registry, so an unresolved reference cannot silently initiate
network retrieval. Schema and instance validation failures use fixed messages;
neither the schema, reference URI, provider output, nor validator diagnostic is
included in the exception.

JSON Schema `format` values are annotations in V1. ExitSpec does not install a
`FormatChecker`, so semantic checks such as email or hostname validation remain
explicitly out of scope unless a future contract adds a pinned format policy.

### Credential destination restriction

V1 fails closed on the credential destination. The endpoint string must equal
`https://api.fireworks.ai/inference/v1/chat/completions` byte for byte. Leading
or trailing spaces, tabs, carriage returns, newlines, alternate ports, lookalike
hosts, credentials, paths, queries, and fragments are rejected during
construction—before an injected transport can receive an Authorization header.
After validation, the adapter stores the constant rather than the caller's
string. Supporting an enterprise proxy later requires a separate explicit
host-allowlist design; an arbitrary endpoint override must not silently inherit
the Fireworks API key.

### Manifest-bound egress acknowledgement and permit

The provider-neutral authorization contract takes policy from the frozen
Wave-1 manifest, not from request input. That immutable policy supplies:

- provider, model, and exact HTTPS endpoint;
- the exact approved synthetic payload digest;
- source fixture hash and case identity;
- redaction policy and configuration identity;
- disclosed provider data-policy and pricing snapshots;
- timeout, token, attempt, and retry ceilings; and
- the maximum request cost.

The exact redacted `StructuredJSONRequest` includes messages, response schema,
model, timeout, token bounds, and request budget. The authorizer verifies it
against the trusted policy and derives a domain-separated RFC 8785/SHA-256
binding. The authorizer—not caller input—owns the clock and random token
material. Explicit acknowledgement creates a capability that expires after five
minutes and can authorize at most once.

Authorization does not merely consume a token beside caller-supplied bytes. It
recomputes the binding from the exact `StructuredJSONRequest` and trusted policy
presented for authorization, then returns a one-use permit that privately
carries that same request. A future live transport must accept this permit and
take the request from it exactly once; it must not accept a second, separately
supplied request. Taking the request rechecks the server clock and permanently
invalidates an expired permit, preventing authorization just before expiry from
being held for a later send.

Public acknowledgement and permit records never serialize the token verifier,
nonce, or raw request. Invalid acknowledgement, malformed input, policy or
request mismatch, expiry, and replay all fail closed with the stable, sanitized
`egress_not_authorized` code.

### Permit-only pinned HTTPS seam

`AuthorizedFireworksExecutor` is the only composition intended for future live
server wiring. It accepts a sealed `AuthorizedProviderRequest`, takes its
detached request exactly once, revalidates the frozen Wave-1 policy, and injects
the manifest's model pricing, attempt ceiling, and `Retry-After` ceiling into
`FireworksProvider`. It constructs `PinnedFireworksHTTPSTransport` itself; an
arbitrary `ProviderTransport` cannot be substituted at this boundary. It does
not read the environment; a server credential must be supplied explicitly, and
missing, whitespace-bearing, or oversized values fail before transport.
Successful results and typed errors are copied at this boundary with
`provider_request_id` removed, matching the frozen receipt contract.

`PinnedFireworksHTTPSTransport` accepts only the exact POST endpoint and strict
header/body contract. It connects only to `api.fireworks.ai:443`, sends one
first-hop request, never follows redirects, bounds the response body, requires
strict UTF-8, and closes on every outcome. Any `3xx` response becomes
`redirect_rejected` with only the status retained; `Location`, headers, bodies,
and credentials are excluded from errors and representations.

All transport tests inject fake connections. No credential loader, server route,
browser action, or live Fireworks evidence exists. Wave 1 remains blocked until
the complete frozen failure matrix and one explicitly approved bounded live
smoke pass.

## Error and retry contract

`ProviderError.code` is stable and machine-readable. Categories distinguish
authentication, ordinary client rejection, rate limiting, service
unavailability, other service errors, timeout, transport failure, malformed
response envelopes, invalid model output, budget refusal, and exhausted
retries. Redirect refusal is a distinct non-retryable category.

Only transport timeouts, HTTP `429`, and HTTP `503` are retried. Attempts are
bounded. Exponential fallback delays are capped; numeric or HTTP-date
`Retry-After` values are also capped. Malformed output, schema-validation
failure, ordinary `4xx`, and other errors are not retried. Error strings and
representations never include request content, response bodies, headers, or API
keys. Redirects are never retried or followed.

## Budget behavior

Pricing is an explicit model-to-`TokenPricing` mapping supplied by the caller.
The adapter never guesses or downloads prices.

- Before execution, it rejects a request when injected pricing plus known input
  and maximum-output token bounds already exceed the ceiling.
- After execution, it estimates cost from returned input/output token counts and
  rejects an over-budget result while attaching a content-safe receipt to the
  error.
- When pricing or token information is absent, cost is unknown and the adapter
  does not pretend the ceiling was proven. Callers can treat that absence as a
  separate policy failure if their workflow requires hard cost evidence.
- Failed or timed-out attempts that return no usage cannot be priced. The
  receipt's estimate covers reported token usage, not invisible provider work.

## What a receipt proves

Assuming the configured transport faithfully reports the response, a receipt
records which provider boundary ran, the pinned model requested, where it was
sent, how many attempts the adapter made, elapsed adapter time, identifiers and
usage returned by the provider, and the calculation made from the injected
pricing version.

## What a receipt does not prove

A receipt does **not** prove that the model output is correct, faithful, safe,
customer-approved, reproducible, deployment-ready, or within a budget when
pricing/usage is missing. It does not authenticate provider-reported token
counts, independently attest the remote model version, freeze an ExitSpec
contract, or determine an acceptance verdict. A later evidence layer must bind
the receipt to the approved contract, workload, measurements, and deterministic
decision logic.
