# Roadmap

## Seven-day implementation plan

| Day | Focus | Exit condition |
| --- | --- | --- |
| 1 | Contract schemas, state machines, deterministic verdict kernel | A reviewed contract freezes and produces `PASS`, `FAIL`, and `NOT_PROVEN` in local tests. |
| 2 | Transcript draft flow and approval UX/API | Every extracted criterion is source-linked or explicitly human-added. |
| 3 | Endpoint runner, schema/tool checks, errors, cost primitives | A fixed fixture creates a manifest and redacted evidence directory. |
| 4 | GuideLLM performance adapter and latency statistics | Changing duration or threshold changes verdict for the correct reason. |
| 5 | Evidence ledger, reports, and evidence drill-down | Every displayed number reaches its calculation and raw artifact. |
| 6 | One live provider plus adversarial failure paths | 401, 429, timeout, malformed response, partial run, and PII tests are typed correctly. |
| 7 | UI polish, CI, recording, public evidence pack | A fresh developer runs the sample in about five minutes and understands the demo in 90 seconds. |

The schedule is a build sprint, not a claim of complete subject-matter mastery. Learning gates apply at each milestone.

## Current implementation

- **Brick 1 — truth kernel:** complete locally. A frozen contract, deterministic measurement, evidence artifacts, confidence calculation, and four terminal verdict paths are tested.
- **Brick 2 — Define:** complete locally. A synthetic transcript produces source-linked drafts, explicit approval/rejection records, an approved contract, and a static review artifact. It intentionally rejects an ambiguous request.
- **Next — Prove against a real endpoint:** add redaction primitives before any non-synthetic text is persisted, then add an OpenAI-compatible endpoint client with typed failure handling. Fireworks is the leading first provider candidate once Jayesh approves credentials and a spend ceiling.

## First 20 issues in dependency order

1. Define strict domain enums and Pydantic schemas.
2. Define contract state transitions and invalid-transition tests.
3. Define canonical JSON serialization and SHA-256 contract digest.
4. Implement contract freeze and revision behavior.
5. Create synthetic discovery transcript and 200-case tool fixture.
6. Define versioned measurement-adapter protocol.
7. Implement deterministic exact-tool-selection adapter.
8. Implement Wilson lower-bound calculation and unit tests.
9. Implement criterion verdict engine and terminal-state tests.
10. Implement overall must-have verdict aggregation and precedence tests.
11. Create run manifest and artifact metadata schemas.
12. Implement run-scoped evidence writer and artifact hashing.
13. Generate JSON verdicts and static HTML decision packet.
14. Add deterministic `NOT_PROVEN`, `PASS`, `FAIL`, and `BLOCKED` demo scenarios.
15. Add secret/PII redaction primitives and positive-control tests.
16. Add OpenAI-compatible endpoint client and typed 401/429/timeout handling.
17. Add JSON-schema-validity and token/cost adapters.
18. Wrap GuideLLM and parse structured benchmark output.
19. Add FastAPI contract/run endpoints and event stream.
20. Add React `Define -> Prove -> Decide` sample workflow, CI, recording, and external feedback plan.

## Decisions requiring Jayesh’s approval before public implementation claims

1. The final public name and any trademark/name availability check for “ExitSpec.”
2. Whether proportion rules use a two-sided 95% Wilson interval, a one-sided bound, or a different approved procedure.
3. The exact overall-verdict precedence when `BLOCKED` and `NOT_PROVEN` coexist.
4. The first hosted provider and the spend ceiling for live testing.
5. The exact PII detector and wording of public privacy claims.
6. Whether the first public UI remains a compact three-screen demo or expands beyond it.
7. Whether and when to create a GitHub repository, deploy, or collect external practitioner feedback.
