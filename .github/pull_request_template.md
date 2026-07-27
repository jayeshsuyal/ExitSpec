## Decision

<!-- One sentence: what claim becomes true after this merges? -->

## User outcome

<!-- Who benefits, what can they now do, and what will they observe? -->

## Scope

- <!-- Included behavior -->

## Non-goals

<!-- State what this PR intentionally does not implement or prove. -->

- <!-- Excluded behavior -->

## Risk and authority

- **Change risk (C0–C4):** C_
- **Authority boundary affected:** <!-- source, authoring, review, confirmation, contract, measurement, verdict, evidence, persistence, provider/network, or none -->
- **Authority owner before/after:** <!-- Name any change; write "unchanged" when none. -->
- **Invariants exercised:** <!-- e.g. INV-03, INV-05; write "none" only for C0 changes. -->

## Exit gate

<!-- Exactly one binary, observable condition that proves the Decision—not a list of implementation tasks. -->

- [ ] **PASS when:** <!-- Binary condition -->

## Failure matrix

<!-- List relevant failures, especially for C2–C4. State "Not applicable — <reason>" only when this PR introduces no failure behavior. -->

| Failure | Expected user-visible outcome | State mutation | Retry / side effects | Evidence or test |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

## Evidence

<!-- Name commands/tests and link only sanitized artifacts from the revision under review. -->

- **Automated:** <!-- Focused and baseline checks with results -->
- **Manual:** <!-- Reproduction or inspection steps with results -->
- **Artifacts:** <!-- Sanitized screenshots, receipts, manifests, or "none" -->

## Security and privacy

- **Data handled:** <!-- Classification and source -->
- **Egress / persistence:** <!-- Destination, retention, or "none" -->
- **Secrets and sanitization:** <!-- Credential source and safe logging behavior -->

## Rollback

<!-- How can this be disabled or reverted without corrupting accepted history? Include in-flight and irreversible side effects when relevant. -->

## Follow-ups

<!-- Deferred work must not be required for the Decision or safety claim above to be honest. -->

- <!-- Deferred item, or "None" -->

## Merge checklist

- [ ] Focused tests for the changed behavior pass.
- [ ] Baseline repository checks pass, or each non-applicable check is explained.
- [ ] Regression and relevant adversarial/failure cases pass.
- [ ] Documentation and capability/security claims match the implementation.
- [ ] Evidence and diff contain no secrets, customer data, private transcripts, or raw audio.
- [ ] Current, experimental, synthetic, local, estimated, and planned capabilities are labeled honestly.
- [ ] The reviewed diff is scoped, clean, and free of generated artifacts, conflict markers, and whitespace errors.
