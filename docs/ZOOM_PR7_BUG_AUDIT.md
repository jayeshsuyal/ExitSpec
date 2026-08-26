# PR7 adversarial bug audit

Status: pre-merge audit completed for the local synthetic path. No new product-severity finding remains open.

## Finding log

| ID | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| PR7-001 | Test | The first acceptance assertion expected closure immediately after the Evidence Pack, but the existing workspace intentionally exposes `RECORD_DECISION_HANDOFF` until a separate human closure decision is recorded. | Fixed in the PR7 test; the lifecycle boundary is now asserted explicitly. |
| PR7-002 | None | Duplicate start/stop/process actions could have created a second source or POC. | No defect reproduced; the integrated test asserts one POC, one source, and replay responses. |
| PR7-003 | None | A proposal or Zoom handoff projection could have exposed transcript text or granted downstream authority. | No defect reproduced; browser/API projections remain content-minimizing and all confirmation, freeze, measurement, and verdict flags remain false. |

## Adversarial coverage

| Threat | Regression coverage |
| --- | --- |
| No transcript; duplicate delivery; out-of-order packets | `tests/test_zoom_session_runtime.py` |
| Malformed payload; duplicate keys; size/version/timestamp bounds | `tests/test_zoom_rtms_decoder.py` |
| Reconnect failure; timeout; processing failure; crash recovery | `tests/test_zoom_session_runtime.py` |
| Unsupported or vague metric; source-bound review | `tests/test_zoom_proposal_bridge.py`, `tests/test_zoom_pr7_adversarial_e2e.py` |
| Provider/model failure and review-only provider output | `tests/test_wave1_fireworks_failure_matrix.py`, `tests/test_wave1_runtime.py` |
| Stale customer decision and replayed confirmation/freeze | `tests/test_confirmation_invariants.py`, `tests/test_poc_performance_lifecycle_web_transport.py` |
| Secret/content leakage and UI projection bounds | `tests/test_zoom_guided_handoff.py`, `tests/test_zoom_session_runtime.py`, `tests/test_browser_new_id_flow.py` |
| Complete Zoom-source path to deterministic Evidence Pack | `tests/test_zoom_pr7_adversarial_e2e.py` |

## Boundary not claimed

This audit covers only the local synthetic Zoom handoff and existing process-local POC workflow. It does not certify a fresh real Zoom call, Zoom CRC/setup attestation, or the private 2026-08-25 capture as a golden fixture. Those require credential rotation, explicit privacy/custody approval, and a new authorized live run.
