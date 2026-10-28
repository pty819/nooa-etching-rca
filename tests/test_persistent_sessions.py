"""Live-object semantics and explicit failure invalidation in real guarded workers."""

import asyncio
from types import SimpleNamespace

from test_capabilities import public_case

from factor_demo.research_workspace import PythonWorkspace
from factor_demo.secure_codeact import ExternalCell


def test_scientific_live_objects_rebinding_and_definition_replacement(tmp_path):
    async def run():
        worker = PythonWorkspace(public_case(tmp_path / "case"))
        try:
            first = await worker.execute(
                "import os, threading\nimport pandas as pd\n"
                "pid=os.getpid()\ndf=pd.DataFrame({'v':[1,2]})\nalias=df\n"
                "lock=threading.Lock()\ngen=(i for i in range(3))\nx=1\n"
                "def f():\n return x\nprint(next(gen))"
            )
            assert first["ok"], first["output"]
            second = await worker.execute(
                "x=2\nassert f()==2\nassert os.getpid()==pid\nassert alias is df\n"
                "assert lock.acquire(False)\nassert next(gen)==1\n"
                "df.loc[0,'v']=9\nassert alias.v.tolist()==[9,2]\n"
                "print(f())\ndef f():\n return x*10"
            )
            assert second["ok"], second["output"]
            third = await worker.execute("x=3\nassert f()==30\nprint(next(gen))")
            assert third["ok"], third["output"]
            assert third["epoch"] == first["epoch"]
            assert not list((worker.work / ".cell-state").glob("*.pickle"))
        finally:
            await worker.close()

    asyncio.run(run())


def test_syntax_preserves_kernel_but_partial_runtime_mutation_resets_it(tmp_path):
    async def run():
        worker = PythonWorkspace(public_case(tmp_path / "case"))
        try:
            first = await worker.execute("good=[1]")
            syntax = await worker.execute("def bad(:")
            assert (
                syntax["runtime_status"] == "syntax_error" and syntax["state_effect"] == "unchanged"
            )
            assert (await worker.execute("assert good==[1]"))["ok"]
            failed = await worker.execute(
                "good.append(99)\nbad=12\nraise ValueError('bad mutation')"
            )
            assert failed["state_effect"] == "reset" and failed["variables"] == {}
            after = await worker.execute(
                "assert 'good' not in globals()\nassert 'bad' not in globals()"
            )
            assert after["ok"], after["output"]
            assert after["epoch"] > first["epoch"]
            assert worker.read_execution(failed["execution_ref"])["ok"] is False
        finally:
            await worker.close()

    asyncio.run(run())


def test_timeout_crash_and_script_never_leave_partial_state(tmp_path):
    async def run():
        worker = PythonWorkspace(public_case(tmp_path / "case"))
        try:
            await worker.execute("keep=7")
            timed = await worker.execute("import time\npartial=3\ntime.sleep(4)", timeout_s=0.2)
            assert timed["runtime_status"] == "timeout" and timed["state_effect"] == "reset"
            assert (await worker.execute("assert 'partial' not in globals()\nkeep=8"))["ok"]
            script = worker.work / "artifacts/test.py"
            script.write_text('keep=100\nraise ValueError("script failure")')
            result = await worker.execute("script", script=script)
            assert not result["ok"]
            assert (await worker.execute("assert keep==8"))["ok"]
            crash = await worker.execute("import os\nos._exit(0)")
            assert crash["runtime_status"] == "crashed" and not crash["ok"]
            assert (await worker.execute("assert 'keep' not in globals()"))["ok"]
        finally:
            await worker.close()

    asyncio.run(run())


def test_outer_repl_keeps_objects_and_cleans_failed_epoch():
    async def run():
        call = SimpleNamespace(bound_parameters=dict, return_type=dict, method_name="research")
        outer = ExternalCell(SimpleNamespace(), call, timeout=10)
        try:
            a = await outer.run_cell(
                "import os\npid=os.getpid()\nx=1\ndef f():\n return x\ng=(i for i in range(3))",
                execution_count=1,
            )
            assert a.error is None, a.error
            b = await outer.run_cell(
                "x=2\nassert f()==2\nassert os.getpid()==pid\nassert next(g)==0\nprint(f())\ndef f():\n return x*10",
                execution_count=2,
            )
            assert b.error is None, b.error
            assert (
                await outer.run_cell("x=3\nassert f()==30\nreturn 42", execution_count=3)
            ).returned_value == 42
            syntax = await outer.run_cell("def bad(:", execution_count=4)
            assert syntax.error and outer.process is not None
            assert (await outer.run_cell("assert x==3", execution_count=5)).error is None
            failed = await outer.run_cell('x=99\nraise ValueError("bad")', execution_count=6)
            assert failed.error and outer.process is None
            assert (
                await outer.run_cell("assert 'x' not in globals()", execution_count=7)
            ).error is None
        finally:
            await outer.aclose()

    asyncio.run(run())


def test_runtime_syntax_error_is_not_misclassified_as_compile_failure(tmp_path):
    async def run():
        worker = PythonWorkspace(public_case(tmp_path / "case"))
        try:
            await worker.execute("values=[]")
            failed = await worker.execute(
                "values.append(4)\nraise SyntaxError('runtime exception')"
            )
            assert failed["runtime_status"] == "exception"
            assert failed["state_effect"] == "reset"
            assert (await worker.execute("assert 'values' not in globals()"))["ok"]
        finally:
            await worker.close()

    asyncio.run(run())


def test_cancelled_cell_is_archived_and_its_epoch_cannot_be_reused(tmp_path):
    async def run():
        observed = asyncio.Event()

        class Events:
            def emit(self, kind, **data):
                if kind == "tool.output" and "RUNNING" in data.get("text", ""):
                    observed.set()

        worker = PythonWorkspace(public_case(tmp_path / "case"))
        worker.events = Events()
        task = asyncio.create_task(
            worker.execute("import time\nbad=[1]\nprint('RUNNING',flush=True)\ntime.sleep(20)")
        )
        try:
            await asyncio.wait_for(observed.wait(), 10)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            failed = worker.read_execution(worker.latest_failure["execution_ref"])
            assert not failed["ok"]
            assert (await worker.execute("assert 'bad' not in globals()"))["ok"]
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await worker.close()

    asyncio.run(run())


def test_macos_process_limit_requires_explicit_operator_setting(monkeypatch):
    import resource

    from factor_demo import sandboxing

    limits = []
    monkeypatch.setattr(sandboxing.sys, "platform", "darwin")
    monkeypatch.delenv("FACTOR_CELL_NPROC", raising=False)
    monkeypatch.setattr(resource, "getrlimit", lambda key: (4096, 4096))
    monkeypatch.setattr(resource, "setrlimit", lambda key, value: limits.append(value))
    sandboxing._apply_nproc_rlimit()
    assert limits == []
    monkeypatch.setenv("FACTOR_CELL_NPROC", "600")
    sandboxing._apply_nproc_rlimit()
    assert limits == [(600, 600)]
