# Remote branch cleanup audit

Audit date: 2026-08-30
Compared against `origin/main` at
`9d07903312ecb8db162bc9d601c063540142c549`.

The table records every non-main branch present on the remote before the v0.4
release-candidate PR. `CONTAINED_IN_MAIN` means the branch tip is an ancestor
of the audited main commit. Only those tips are eligible for deletion. Every
`NOT_CONTAINED` branch is retained because its tip is not fully contained in
main; branch age and naming are not deletion criteria.

| Remote branch | Tip SHA | Containment | Action |
| --- | --- | --- | --- |
| `codex/confirmation-invariant-suite` | `51894559e9b1b0488700148da853769dc1c75e8b` | `NOT_CONTAINED` | Retain |
| `codex/confirmation-ledger-adr` | `5eb5ed617b1b295a17aeb34773af34e1d6e8eaaa` | `NOT_CONTAINED` | Retain |
| `codex/confirmation-ledger-schema` | `018642b835d81f682011ebb952f670e6c0a13a5e` | `NOT_CONTAINED` | Retain |
| `codex/confirmation-lifecycle-types` | `2f554a998604def0dbdaadd350eaeeeb2e6acad1` | `NOT_CONTAINED` | Retain |
| `codex/confirmation-nul-policy` | `4824287e8fafb36e424bef4002f38a72cc0dc7bb` | `NOT_CONTAINED` | Retain |
| `codex/confirmation-sqlite-schema` | `6f39d664c1e5b944f446a203966fec85f963962c` | `NOT_CONTAINED` | Retain |
| `codex/confirmation-store-port` | `b6e529e410ba6b0b2db1546f0993f9244b9f4444` | `NOT_CONTAINED` | Retain |
| `codex/demo-loop` | `dc936dd74c327dff70696adb777b239dd4089cfd` | `CONTAINED_IN_MAIN` | Delete after release PR is merged |
| `codex/pr13-engineering-playbook` | `b086d23589634bdbacf2100363f8077854797087` | `NOT_CONTAINED` | Retain |
| `codex/pr14-pr-contract-template` | `9718fb3afa5863ff0316be635da5f5886fc2787d` | `NOT_CONTAINED` | Retain |
| `codex/pr15-bug-intake-template` | `ae79b972fecf9be786cf32b206b6afc9c6a2e98b` | `NOT_CONTAINED` | Retain |
| `codex/pr16-engineering-gate` | `35554ccf231279a283ee9119c1515622c7485613` | `NOT_CONTAINED` | Retain |
| `codex/pr17-ci-engineering-gate` | `682169ec06879d928382ee20bc321284623d8d1e` | `NOT_CONTAINED` | Retain |
| `codex/product-loop-checkpoint` | `242f3fcb42ff93c10f8032755c668e4ed9bd1c2e` | `CONTAINED_IN_MAIN` | Delete after release PR is merged |
| `codex/sqlite-invitation-store` | `85e0bb839407772dea1355acd090e33559152843` | `NOT_CONTAINED` | Retain |
| `codex/sqlite-invitation-writer` | `f75a52a97cc5eb1c895bd0d6b06efbeb0ba27183` | `NOT_CONTAINED` | Retain |

No branch is deleted by this audit alone. The two eligible tips are to be
deleted only after the repaired browser gate is green on the merged main
commit; the remaining fourteen remote branches are unmerged by containment
and must remain available for their owners.
