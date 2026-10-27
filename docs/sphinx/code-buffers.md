# Versioned code buffers

Research code can be edited without creating a script file. Buffers are stored
as immutable text revisions in the supervisor's `.execution/code.sqlite`, and
sent to guarded workers in an immutable JSON request. Execution records retain
the exact source and its hash. This is durable source storage, not RAM-only
storage; it does not produce a `.py` or a standalone source-text file for the
buffer. Ordinary `run_code` and `run_script` remain supported.

Use these methods from a CodeAct `python_cell`:

```python
v1 = await self.code_write('features', 'def score(x):\n    return x / 0\n')
await self.code_run('features', v1['revision'])
result = await self.run_code('score(2)')
# Host observations show the error even without print(result).
v2 = await self.code_edit('features', expected_revision=1,
                          old='return x / 0', new='return x / 2')
await self.code_run('features', v2['revision'])
await self.run_code('print(score(2))')
```

`code_write` requires `expected_revision=0` to create a buffer and the current
revision to replace it. `code_edit` requires one unique nonempty exact match;
a stale revision or ambiguous match fails atomically. Old revisions are retained.
`code_list` lists latest versions. `code_read(name, revision=0, offset=0,
limit=12000)` pages exact source by character; zero selects the latest revision.
`code_run` always requires an explicit positive revision.

Edits do not execute code. Running a revision executes exactly that source once
in the scientific namespace; it does not replay previous cells. Old function
references remain old, removed definitions remain until explicitly deleted,
and side effects run again only when the agent explicitly executes code.
Errors in old functions retain the corresponding virtual source name and line
through worker checkpoints, for example `cell://features@1`.

Buffer revisions and execution archives survive new turns. Live Python state
still resets on a new research turn or dataset change. Re-execution must be
explicit. Discussion and reflection modes cannot write/edit/run buffers.
