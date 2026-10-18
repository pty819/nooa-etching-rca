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
- [ ] Inspect actual browser behavior, preserve user work, push and verify publication.

Hashes and replay prove integrity and reproducibility, not arbitrary mathematics or
causal truth. Free exploratory code remains available; trusted raw facts and formal
model metrics must have stronger, explicit validation than agent-authored tables.

## Working evidence

- tests/test_execution_boundaries.py: actual external CodeAct cannot read a harmless file outside its scratch root or reach self._llm/self._workspace; approved run_code still works. Scientific stdout cannot impersonate a result, 17MiB output is bounded, following cells remain aligned; outside-case reads fail.
- Existing variable persistence, actual scripts (including exit3), timeout/recovery and scientific file access test passed.
- tests/test_evidence_provenance.py: unrecorded CSV rejected, raw values independently recomputed, completed predicates checked, exploratory/derived output cannot pass as a raw supported fact. Subsets cannot replace raw values or add new raw files.
- tests/test_reliability_cleanup.py: audit exceptions preserve the original exception, subprocess codes are structured, nonlocal Host rejected. Canonical publication preserves all characters and never calls a rewriting model.
- Complete macOS suite: 99 passed, 76 upstream warnings (remediation-full-final.log). New gateway regressions are tested separately. Vue: 14 passed; npm audit: 0; strict Sphinx/24 rendered diagrams/internal links/search passed. Six neutral 180-run cases generated; missing agent reports remain report_missing, not a claimed scientific benchmark success. Linux/macOS CI and public delivery still need their actual workflow outcomes.
