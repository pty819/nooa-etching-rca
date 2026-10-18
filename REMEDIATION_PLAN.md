# Audit remediation

Baseline: `55364f0`. Scope: both independently reproduced audit reports, including
remaining issues, misleading claims, portable validation and published docs.
Preserve user datasets, private providers, prior cases and the pinned NOOA source.

## Execution boundaries

- [x] Fresh, credential-free external CodeAct cell execution, closed tool RPC surface.
- [x] Default-deny filesystem/network policy; no live agent/client inherited by cells.
- [x] Trusted scientific supervisor owns cell identity, exit status and output capture.
- [x] No untrusted control FD, stale replies, oversized protocol lines or parent unpickle.
- [x] Bounded execution, clean cancellation and state restored only in untrusted children.

## Scientific evidence and delivery

- [x] Protected input/code/cell/artifact provenance and integrity checks.
- [x] Trusted raw-data recomputation and explicit exploratory/computed evidence grades.
- [x] Explicit planned/completed falsification checks with execution/evidence references.
- [x] Deterministic verified final delivery; no unconstrained second model rewriting.
- [x] Frozen held-out etching evaluation and mechanism/step truth scoring.
- [x] Multi-lot and negative-control benchmark, saturation diagnostics and honest limits.
- [x] Separately configured reviewer using the same shared budget; no false independence.

## Reliability and cleanup

- [x] Reflexion outcome labels, bounded steps and real no-progress branch handling.
- [x] Structured subprocess exits and audit failure isolation preserving original errors.
- [x] One effective strategy configuration, strict Pydantic contracts and skill inputs.
- [x] Local Host/Origin protection and no diagnostic IDs sent to model providers.
- [x] Remove environment-specific SSH import from the public core.
- [x] Separate legacy CLI and clarify time-correlated sensor noise.
- [x] Patch development dependencies; Python/frontend/platform CI.

## Verification and delivery

- [x] Convert both reports' attacks to regression tests before claiming containment.
- [x] Run appropriate full Python/Vue/build/security/docs checks.
- [x] Update architecture, requirements, diagrams and release evidence.
- [x] Inspect actual browser behavior, preserve user work, push and verify publication.

Hashes and replay prove integrity and reproducibility, not arbitrary mathematics or
causal truth. Free exploratory code remains available; trusted raw facts and formal
model metrics must have stronger, explicit validation than agent-authored tables.

## Working evidence

- tests/test_execution_boundaries.py: actual external CodeAct cannot read a harmless file outside its scratch root or reach self._llm/self._workspace; approved run_code still works. Scientific stdout cannot impersonate a result, 17MiB output is bounded, following cells remain aligned; outside-case reads fail.
- Existing variable persistence, actual scripts (including exit3), timeout/recovery and scientific file access test passed.
- tests/test_evidence_provenance.py: unrecorded CSV rejected, raw values independently recomputed, completed predicates checked, exploratory/derived output cannot pass as a raw supported fact. Subsets cannot replace raw values or add new raw files.
- tests/test_reliability_cleanup.py: audit exceptions preserve the original exception, subprocess codes are structured, nonlocal Host rejected. Canonical publication preserves all characters and never calls a rewriting model.
- Final macOS local suite: 104 passed, 76 upstream warnings (remediation-final104.log). Vue: 14 passed; npm audit: 0; strict Sphinx/24 rendered diagrams/internal links/search passed. Six neutral 180-run cases generated; missing agent reports remain report_missing, not a claimed scientific benchmark success. Final code commit `5a0603b`: Runtime checks 39138352863 succeeded on Ubuntu24.04/macOS15 plus frontend; Sphinx build and Pages deployment 39138352830 succeeded.

- Real provider acceptance: GLM -> strict fact materialization -> independently configured MiniMax Predict -> canonical SSE completed passed/delivered on round 2; the earlier not_reviewed checkpoint remains intact. MiniMax schema transport is strict JSON with Pydantic validation, not permissive list coercion.
- Real physical held-out acceptance: fixed step-4 20%-100% He-inlet operator + Logistic scored 60 test objects / 12 independent lots. Balanced accuracy and macro F1=1; mechanism recall=0 because no mechanisms were submitted. The first NaN operator failure is preserved and v2 was fixed before the first test evaluation.
- Surface-condition residual truth threshold now stays clear of the floating-point boundary at 1.2-1.0; cohort-size regression passed.

- First remote matrix: both platforms 99 passed / 2 failed. A harmless 5-second atexit hook reproduced the external-cell false failure locally; the trusted bootstrap now exits only after flushing its checkpoint/result, without SDK teardown delaying commitment. Linux scientific guard imports no longer load the whole agent SDK. The network-denial test now catches denial at socket creation as well as connection, and the branch integration deadline allows bounded CI cold startup. The final matrix passed; the initial failures remain documented rather than being hidden.

## Final verification

- Code: `5a0603b` (all model/runtime/library changes included).
- Runtime matrix: https://github.com/pty819/nooa-etching-rca/actions/runs/39138352863 — Linux and macOS each 104 passed, frontend 14 passed, build and audit successful.
- Documentation: https://github.com/pty819/nooa-etching-rca/actions/runs/39138352830 — strict build and Pages deployment successful.
- Local service reloaded only after checking idle; prior user sessions, providers and data remain. Browser inspected the canonical fact table and passed review status.
- Six-case scientific agent effectiveness is still unmeasured; generation, isolated test/truth scoring, false-positive/omission metrics and controls are implemented, but no missing report is counted as a successful blind study.
