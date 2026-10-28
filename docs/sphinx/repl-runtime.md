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

On Linux, successful cell completion includes a host-side /proc sweep that stops and kills subprocess descendants (including setsid/fork chains), preserving only the pre-source kernel bootstrap chain. Cleanup failure invalidates the kernel. Python objects remain live; background subprocesses do not survive cell boundaries.

## Phase B experiment contract

`code_write`/`code_edit` remain compare-and-swap source edits and never execute.
`code_run` still deliberately executes in the live namespace. For candidate work,
use `code_experiment(name, revision, checks, dependencies, parameters)` with positive
exact revisions, ordered dependency buffers and JSON parameters. A fresh guarded
worker receives a byte-hashed copy of the selected DATASET, no main-kernel objects
or workspace files. Dependencies, candidate and explicit assertion checks share
only that disposable worker; a total wall deadline covers all steps. Every worker
is closed before returning, including cancellation. Failed output stays in the
private experiment tree, never the main artifact directory.

The host saves exact source, hashes, input files, parameters, checks and execution
observations. `checked` means the supplied Python checks completed successfully;
it is not independent mathematical or goal verification. The Python worker is
untrusted, as in Phase A. `code_read_experiment` returns retained diagnostics.
`code_publish` explicitly copies the checked artifact set through the supervisor
to a new immutable `artifacts/experiment-<id>/` directory. It refuses source/data
changes and staged artifact tampering. Publishing does not activate functions in
the main kernel. Source revision or input changes make history `stale`, and the
host evidence verifier refuses stale published experiments. Old files remain for
audit; they do not become evidence for the new code. This is conservative whole
input/source invalidation, not an automatic object dependency graph.

Regression gate: runtime/check/timeout failures preserve main PID, object identity
and artifact bytes; two candidate revisions retain separate traces; source edits
and dataset-byte changes invalidate results and publication; staged/published
symlink/tampering is refused; declared tools work through the actual CodeAct proxy.
