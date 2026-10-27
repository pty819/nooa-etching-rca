# CodeAct execution feedback

Every `run_code`, `run_script` and file-write execution produces a host-owned
execution observation even if the outer cell assigns the return value without
printing it. The observation contains the execution reference, actual status,
bounded output and dataset reference. Outer-cell success does not imply that
all scientific executions succeeded. Original outer exception tracebacks cross
the process boundary as text, never as live exception objects.

`self.list_executions(offset=0, limit=20)` lists the trusted ledger newest first.
`self.read_execution(execution_ref, offset=0, limit=12000)` reads immutable source,
stdout, stderr and combined output from the supervisor archive. Each field has
its own character count and continuation offset. This also works after a new
research turn. Worker-editable files in `research_cells/` are convenience copies,
not the authoritative readback. Collection remains bounded (256 Ki characters
per stream); stdout retains its beginning and stderr its tail. Output previews
retain both ends, and explicitly report truncation.

The current execution and most recent failure are included in dynamic research
context so conversation compression does not remove the immediate repair target.
Namespaces last for one research turn and reset on a new turn; archive recovery
does not replay code or restore live Python objects. Timeouts, cancellation and
dataset changes also reset the scientific checkpoint.
