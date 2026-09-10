# Detached approval and local token accounting

Production execution remains disabled. `_QUALIFIED_SERVING_CONTRACTS` is empty.
This change implements a non-circular code approval binding and an actual local
DeepSeek V4 tokenizer. It does not establish Fireworks serving-template or billing
parity, account/custody approval, or draft quality. An optional dependency extra
now supports a complete local tokenizer installation; that does not activate it.
The r2 request profile, messages body, schema, endpoint and transport are unchanged.

## Freeze, review, then approve

A compiled profile containing its own commit and every tracked file hash would
require its containing code to refer to its own final hash. The new arrangement
keeps serving qualification in code and puts code measurements in a detached JSON
record created **after** the code is committed. No tracked file needs to change.

The record has exactly two keys: `schema_version` (the literal
`exitspec.detached-source-approval.v1`) and `profile`. The profile contains exactly
the fields of `_QualifiedLaunchProfile` in `source_authoring_launch.py`, including
the exact HEAD, tree, full tracked path/SHA-256 manifest, tokenizer artifact
paths/hashes, fixed policy hashes/limits, approval references, and finite float
validity timestamps. Manifest entries are JSON arrays of two strings. Its
`launch_profile_sha256` is the existing domain-separated profile digest excluding
only that field. The operator's independent anchor is SHA-256 of the **whole
record's bytes**, including that profile digest.

The trusted local reviewer must inspect the frozen candidate, qualification
references and complete record, then communicate the expected record digest
independently. Generating a record or hashing the candidate does not grant
approval. A digest from inside the record, a neighboring untrusted checksum file,
or an automatically generated command is not an independent approval anchor.
SHA-256 establishes equality to reviewed bytes, not a signature or approver identity.
This does not protect against an already malicious same-user host or modified
Python/runtime dependencies.

After serving qualification exists, the fixed operator takes `--approval-id`,
`--approval-file` (an absolute nonsymlink path outside the tracked worktree), and
`--approval-sha256` (the independently obtained lowercase SHA-256), plus the
existing local port/output-root options. It does not accept endpoint/model/secret
or fake-mode switches. Today the empty compiled qualification list refuses before
argument-dependent file reads or terminal input.

Records are limited to 1 MiB and one second of reading, must be regular files,
and reject duplicate, unknown, missing, nonfinite and malformed fields. Code
verification retains the five-second shared deadline, bounded Git output, clean
HEAD/tree, exact tracked-set equality, every tracked SHA-256, and bounded artifact
hashing. Record substitution is checked before terminal input, again before the
credential prompt, after that prompt, and in the fixed child before ordinary IPC
or secret reads. The parent passes only the record path and independent digest
to the fixed child; ordinary body/credential pipes keep their existing bindings.

## Exact local encoding

Model data comes from the official
[DeepSeek-V4-Flash-0731 revision](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/tree/7872f01b1d1fe23eabc4c98b48bffcef5a386062).
Only these data files are consumed locally; no model weights or remote executable
code are downloaded by the implementation.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| tokenizer.json | 6,367,146 | `8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf` |
| tokenizer_config.json | 801 | `6ac8c8dc065ed118161d02dd532749ae3f52c243deac27872134fae2f50d8547` |

The model repository's [MIT license](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/blob/7872f01b1d1fe23eabc4c98b48bffcef5a386062/LICENSE)
is preserved with acquired test artifacts. The implementation independently
expresses the bounded two-message subset of the official
[chat encoder](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/blob/7872f01b1d1fe23eabc4c98b48bffcef5a386062/encoding/encoding_dsv4.py):
BOS, exact system content, User marker, exact user content, Assistant marker,
then the closing thinking marker for chat mode. It inserts neither an EOS nor
another schema copy. Both the explicit schema in the system string and the
canonical source wrapper are counted without repairing escapes or whitespace.

The data config disables automatic BOS/EOS addition. `Tokenizer.encode` is called
with `add_special_tokens=False`, and the exact decoded prompt must round-trip.
All 1,283 registered added-token strings are refused if present in message content
so source text cannot introduce structural control markers. Ordinary markup,
Unicode, punctuation, whitespace and numbers remain literal data. The real local
boundary probe accepted 8,192 tokens and refused 8,193; the source byte cap remains
16,384. No character-count heuristic or caller-supplied integer replaces this count.

Only Hugging Face `tokenizers==0.23.2` core is used, loaded lazily from verified
local data with `from_str`; no Hub API or automatic fallback runs. Fixed vocabulary
size and known ASCII, Unicode, role-marker and no-thinking token-ID vectors are
checked on each load. The approved macOS ARM64 CPython ABI3 wheel is 3,101,593 bytes,
SHA-256 `986670e43691469dcee610ea0f846f91a8f84e91fc6f7a48d4c064414c0ec2bf`.
Its 32 RECORD entries were verified; it has no `.pth`, install scripts or bundled
LICENSE file. The upstream release points to the
[Apache-2.0 license at source commit 88a4498](https://github.com/huggingface/tokenizers/blob/88a4498ad4ea1a9487b0a9b0ff881383fd5a06a3/LICENSE).

### Optional installation

The default installation keeps tokenizer dependencies optional. To include the
complete declared tokenizer runtime, use a fresh supported Python environment:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[source-authoring-tokenizer]'
.venv/bin/python -m pip check
```

This installs dependencies normally; it does not acquire model data, read keys,
contact a model/account, or grant launch authority. Supply the reviewed local
model data separately. The installed default still refuses before an approval
record or credential is read because compiled serving qualification is empty.

The reviewed [macOS ARM64 / CPython 3.12 dependency closure](../requirements/source-authoring-tokenizer-macos-arm64-py312.txt)
records all 16 exact versions and platform-specific wheel hashes, including the
Hub dependency required by Tokenizers. For a reviewed offline installation, acquire
and inspect those exact wheels first, then install the hashed requirements from
that local wheelhouse with normal dependency resolution. ExitSpec's base
requirements must also be satisfied; the 16-package file covers the tokenizer
closure only. Do not use its macOS wheel hashes as a universal lock. A normal
unconstrained installation may resolve different transitive versions and must be
checked against the intended qualification record.

The complete tokenizer closure was installed from verified wheels into a fresh
environment with no copied/inherited site-packages. `pip check`, 584 installed
file comparisons, isolated import origins and actual token-ID vectors passed
with no runtime network/process attempts. The installed package files occupied
34,198,620 bytes in that environment, excluding bootstrap pip. Hub/Xet APIs are
not used. The earlier no-deps core experiment remains historical compatibility
evidence; it is not the completed installation. Full application/child and final
gate results remain bound to the exact packaging candidate's external evidence.

## Provider qualification still required

Fireworks documents [DeepSeek V4 reasoning_effort=none](https://docs.fireworks.ai/api-reference/post-chatcompletions)
and [structured-output constraints](https://docs.fireworks.ai/structured-responses/structured-response-formatting).
The latter do not automatically put a schema into the prompt; this request already
includes its schema explicitly. Neither document proves that this particular
serverless deployment renders the same local chat template or counts all billable
tokens identically. No estimated overhead or invented parity margin is accepted.

`prompt_token_ids` is a documented alternative that bypasses server tokenization,
but it is not used here. Switching would require a reviewed request-profile and
source-to-wire binding change, exactly one input representation, structured-output
compatibility, and continued privacy treatment of token IDs as source-bearing data.
A worker-side rewrite of an already approved body would violate the current contract.

Before qualifying the compiled serving entry, establish exact model/template and
runtime compatibility, provider usage/billing behavior, current account pricing,
custody/region conditions and explicit execution authorization. The 8,192/2,000
limits, $0.01 claim reservation, ten claims/$0.10 launch reservation and 30-second
attempt lifetime are unchanged. Reservations are not a verified invoice ceiling.
Native Zoom text still feeds explicit Fireworks TEXT drafting, followed by named
human review, confirmation, freeze, proof and handoff. No duplicate STT, agent/tool
loop, new clarification flow, or changes to existing independent STT are introduced.

## Evaluate drafting separately from mechanism correctness

Deterministic/fake transport tests prove admission, limits, bindings and lifecycle
behavior. Real local tokenizer tests prove local encoding behavior. Neither
measures generated draft quality. A later explicitly authorized model evaluation
must keep the model/version, prompt, source set and reviewer instructions fixed,
use synthetic or separately approved sources, and report raw denominators:

- Source accuracy: count claims with exact, unambiguous supporting source spans;
  record omissions and semantic distortion even when a quote exists.
- Invented requirements: count unsupported requirements, numeric facts and scope;
  record severity and reject unsupported material before confirmation.
- Reviewer correction effort: measure edits, rejected proposals, correction time
  and unresolved items per source under a consistent review procedure.

Predeclare acceptance criteria and summarize reviewer disagreements. Preserve
failed/refused outputs in appropriately authorized evaluation custody. No quality
score, acceptance threshold achievement, or reduced review effort is claimed by
this offline candidate.
