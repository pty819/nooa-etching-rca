# REPL, editing and correction migration

User-authorized phases. No service deployment or new CI workflows.

| Phase | Change | Regression gate |
|---|---|---|
| A | Persistent isolated orchestration/scientific workers, shared namespace, no pickle; syntax failure preserves state, runtime failure resets epoch | PID/object identity, generators/locks, globals rebinding, redefine-after-call, await/return; crash/timeout/cancellation; existing sandbox/output tests |
| B | Precise source/dependency experiments in isolated scratch workers; staged artifacts and result validity | CAS edit never executes; failing formula checks/runtime cannot mutate main kernel or published artifacts; source edits mark results stale |
| C | Durable parent/child observations, repair and verification links, dynamic context and reflection evidence | Actual next model request contains source/error/state; inner failure survives outer failure; unrelated success cannot resolve failure |

Each phase is a separate PR. Run focused real-worker regressions, then the full suite and existing platform CI. Merge only the checked exact head. Scripted model tests prove context delivery, not autonomous model intelligence; report real-provider validation separately.

Cross-user-message live kernels are not implied: web/branch worker lifetime currently ends after each request. Until a service-lifetime session manager is implemented, disclose new kernels and require explicit input rebuilding. Never silently replay prior side effects.

## Phase A execution contract

Both layers retain one namespace/event loop for successive cells in a generation call. Functions resolve globals in that same namespace. Source compilation precedes execution. Syntax errors preserve state; runtime exceptions, cancellation, timeout and protocol failure invalidate the affected kernel. Next execution has a new epoch. Scripts run in a separate temporary kernel. Completed tool effects and written files do not roll back.

Scientific stdout/stderr are per-cell files streamed by the host, separate from request-correlated completion frames; printing JSON cannot complete a cell. Completion data still originates in the untrusted Python process and is not proof of mathematical validity or protection against malicious same-process introspection. Host evidence checks remain required. Hard CPU limits apply cumulatively over a kernel lifetime; each cell also has a wall-clock deadline.

macOS RLIMIT_NPROC is opt-in because it counts the entire login uid; explicit limits remain honored. This carries forward the necessary process-cap fix also proposed in PR #8, without its dependency upgrade.
