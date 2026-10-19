"""Regressions for recheck2 findings N1–N4, N7–N9 (harmless local probes only)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from factor_demo.research_workspace import PythonWorkspace

pytestmark = pytest.mark.skipif(
    sys.platform not in {"darwin", "linux"}, reason="enforced execution backend required"
)


def _case(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    (run / "input").mkdir(parents=True)
    (run / "workspace" / "artifacts").mkdir(parents=True)
    (run / "input" / "x.txt").write_text("data")
    return run


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Landlock PoC")
def test_n1_host_refuses_research_cell_symlink_write_escape(tmp_path):
    run = _case(tmp_path)
    outside = tmp_path / "OUTSIDE"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("ORIGINAL\n")

    async def exercise():
        ws = PythonWorkspace(run)
        try:
            planted = await ws.execute(
                f"""
import os
os.makedirs('research_cells', exist_ok=True)
os.symlink({str(victim)!r}, 'research_cells/cell_0002.py')
print('planted')
""",
                60,
            )
            assert planted["ok"]
            second = await ws.execute("print('PAYLOAD')", 60)
            assert second["ok"]
            assert victim.read_text() == "ORIGINAL\n"
            assert not (run / "workspace/research_cells/cell_0002.py").is_symlink()
            swapped = await ws.execute(
                f"""
import os, shutil
shutil.rmtree('research_cells')
os.symlink({str(outside)!r}, 'research_cells')
print('swapped')
""",
                60,
            )
            assert swapped["ok"]
            fourth = await ws.execute("x=1", 60)
            assert fourth["ok"]
            assert "cell_0004.py" not in os.listdir(outside)
            assert (run / "workspace/research_cells/cell_0004.py").is_file()
        finally:
            await ws.close()

    asyncio.run(exercise())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Landlock PoC")
def test_n2_setsid_grandchild_is_reaped_with_cell(tmp_path):
    run = _case(tmp_path)
    beat = run / "workspace" / "heartbeat.txt"

    async def exercise():
        ws = PythonWorkspace(run)
        try:
            result = await ws.execute(
                f"""
import os, time
path = {str(beat)!r}
open(path, 'w').write('0')
pid = os.fork()
if pid == 0:
    os.setsid()
    try:
        for fd in (1, 2):
            os.close(fd)
    except OSError:
        pass
    for i in range(1, 20):
        open(path, 'w').write(str(i))
        time.sleep(0.5)
    os._exit(0)
print('child', pid)
""",
                60,
            )
            assert result["ok"]
            first = beat.read_text().strip() if beat.exists() else "0"
            await asyncio.sleep(2.0)
            second = beat.read_text().strip() if beat.exists() else first
            assert first == second or int(second) <= int(first) + 1, (first, second)
        finally:
            await ws.close()

    asyncio.run(exercise())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Landlock PoC")
def test_n3_state_joblib_symlink_is_not_copied_by_host(tmp_path):
    from factor_demo.path_guard import copy_regular_nofollow

    run = _case(tmp_path)
    secret = tmp_path / "HOST_ONLY" / "credentials.env"
    secret.parent.mkdir()
    secret.write_text("API_KEY=sk-host-secret\n")

    async def exercise():
        ws = PythonWorkspace(run)
        try:
            planted = await ws.execute(
                f"""
import os
try:
    open({str(secret)!r}).read()
    raise SystemExit('direct read should fail')
except OSError:
    pass
os.symlink({str(secret)!r}, 'state.joblib')
print('planted')
""",
                60,
            )
            assert planted["ok"]
        finally:
            await ws.close()

    asyncio.run(exercise())
    state = run / "workspace" / "state.joblib"
    assert state.is_symlink()
    dest = tmp_path / "copied.joblib"
    with pytest.raises(RuntimeError, match="symbolic link|escapes"):
        copy_regular_nofollow(state, dest, required_root=(run / "workspace").resolve())
    assert not dest.exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="seccomp PoC")
def test_n4_seccomp_denies_socketpair_and_iouring(tmp_path):
    run = _case(tmp_path)

    async def exercise():
        ws = PythonWorkspace(run)
        try:
            result = await ws.execute(
                """
import socket, ctypes
out = []
try:
    socket.socketpair()
    out.append('socketpair=OK')
except OSError as e:
    out.append('socketpair=DENIED:' + type(e).__name__)
libc = ctypes.CDLL(None, use_errno=True)
ret = libc.syscall(425, 8, None)
err = ctypes.get_errno()
out.append(f'iouring={ret}:errno={err}')
print('||'.join(out))
""",
                60,
            )
            assert result["ok"], result["output"]
            line = [x for x in result["output"].splitlines() if "socketpair=" in x][-1]
            assert "socketpair=DENIED" in line
            assert "iouring=-1" in line or "errno=1" in line or "errno=38" in line
        finally:
            await ws.close()

    asyncio.run(exercise())


def test_n7_etch_generator_wires_knowledge_profile_and_fixed_recipe(tmp_path):
    from nooa.unifiedllm import FakeLLMClient

    from factor_demo.autoresearch_agent import AutoresearchAgent
    from factor_demo.dataset_adapter import detect_schema
    from factor_demo.etch_dataset import generate_etching
    from factor_demo.research_context import install_research_context
    from factor_demo.research_workspace import prepare_workspace

    data = tmp_path / "etch"
    generate_etching(data, runs=45, seed=1, profile="legacy_v2")
    schema = json.loads((data / "dataset.json").read_text())["schema"]
    assert schema["knowledge_profile"] == "etch"
    assert schema["fixed_recipe"] is True
    detected = detect_schema(data)
    assert detected.knowledge_profile == "etch"
    assert detected.fixed_recipe is True

    run = tmp_path / "session"
    prepare_workspace(data, run)
    assert (run / "input" / "dataset.json").is_file()

    async def exercise():
        agent = AutoresearchAgent(PythonWorkspace(run), llm=FakeLLMClient())
        try:
            install_research_context(agent, "probe")
            assert "domain.domain_rules" in agent._knowledge_registry.activated()
            assert agent._datasets.schema().fixed_recipe is True
        finally:
            await agent.aclose()

    asyncio.run(exercise())


def test_n8_font_seed_refuses_symlink_destination(tmp_path, monkeypatch):
    from factor_demo import sandboxing
    from factor_demo.path_guard import ensure_real_dir

    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "fontlist-v390.json").write_text('{"_version":390}')
    monkeypatch.setenv("FACTOR_FONT_CACHE_DIR", str(seed))
    cache = tmp_path / "cache"
    ensure_real_dir(cache)
    target = cache / "mpl"
    target.symlink_to(tmp_path / "outside-mpl")
    (tmp_path / "outside-mpl").mkdir()
    # ensure_real_dir inside seed_worker_fonts replaces the symlink trap
    sandboxing.seed_worker_fonts(cache)
    assert target.is_dir() and not target.is_symlink()
    assert (target / "fontlist-v390.json").is_file()
    assert not list((tmp_path / "outside-mpl").glob("fontlist*"))


def test_n9_domain_rules_attr_cleared_when_profile_removed(tmp_path):
    from nooa.unifiedllm import FakeLLMClient
    from test_capabilities import public_case

    from factor_demo.autoresearch_agent import AutoresearchAgent
    from factor_demo.dataset_models import DatasetSchema
    from factor_demo.research_context import install_research_context, refresh_domain_context

    case = public_case(tmp_path / "case")
    schema = DatasetSchema(
        index_file="runs.parquet",
        id_column="run_id",
        split_column="split",
        recipe_column="recipe_id",
        fixed_recipe=True,
        knowledge_profile="etch",
    )
    (case / "input" / "dataset.json").write_text(json.dumps({"schema": schema.model_dump()}))

    async def exercise():
        agent = AutoresearchAgent(PythonWorkspace(case), llm=FakeLLMClient())
        try:
            install_research_context(agent, "x")
            assert hasattr(agent, "domain_rules")
            cleared = DatasetSchema(
                index_file="runs.parquet", id_column="run_id", split_column="split"
            )
            # Rewrite registry-backed schema by replacing dataset.json and session
            (case / "input" / "dataset.json").write_text(
                json.dumps({"schema": cleared.model_dump()})
            )
            # Force a registry rewrite: re-bootstrap via new session on empty root is heavy;
            # instead patch schema() return for this unit check.
            agent._datasets.schema = lambda identity="": cleared  # type: ignore[method-assign]
            refresh_domain_context(agent)
            assert not hasattr(agent, "domain_rules")
            assert "domain.domain_rules" not in agent._knowledge_registry.activated()
        finally:
            await agent.aclose()

    asyncio.run(exercise())


def test_m2_supported_rejects_exploratory_citations(tmp_path):
    from nooa.unifiedllm import FakeLLMClient
    from test_capabilities import public_case

    from factor_demo.autoresearch_agent import AutoresearchAgent
    from factor_demo.evidence import EvidenceError, EvidenceVerifier
    from factor_demo.research_models import (
        EvidenceCheck,
        EvidenceRef,
        Hypothesis,
        RawEvidenceSpec,
        ResearchReply,
    )
    from factor_demo.trusted_evidence import materialize_raw

    root = public_case(tmp_path / "case")

    async def exercise():
        worker = PythonWorkspace(root)
        agent = AutoresearchAgent(worker, llm=FakeLLMClient())
        try:
            await agent.run_code(
                "from pathlib import Path\n"
                "Path('artifacts/exploration.csv').write_text('value\\n999999\\n')"
            )
            exploratory = EvidenceRef(
                id="e1",
                claim="fabricated delta",
                artifact="artifacts/exploration.csv",
                column="value",
                value=999999,
                unit="K",
            )
            raw = EvidenceRef.model_validate(
                materialize_raw(
                    root,
                    RawEvidenceSpec(
                        id="e2",
                        claim="row value",
                        file="runs.parquet",
                        column="value",
                        where={"run_id": "r1"},
                        unit="V",
                    ),
                )
            )
            check = EvidenceCheck(
                description="raw above zero",
                status="completed",
                evidence_id="e2",
                operator="gt",
                threshold=0,
            )
            with pytest.raises(EvidenceError, match="recomputed evidence|exploratory"):
                EvidenceVerifier(root).verify(
                    ResearchReply(
                        summary="probe",
                        evidence=[exploratory, raw],
                        hypotheses=[
                            Hypothesis(
                                id="h1",
                                title="candidate",
                                status="supported",
                                mathematical_evidence="e1 e2",
                                evidence_ids=["e1", "e2"],
                                evidence_files=[],
                                alternative_explanation="y",
                                falsification_test="x",
                                checks=[check],
                            )
                        ],
                    )
                )
        finally:
            await worker.close()
            await agent.aclose()

    asyncio.run(exercise())


def test_m3_formula_ranking_prefers_cv_over_dev():
    from factor_demo.formula_lab import FormulaLab

    lab = FormulaLab.__new__(FormulaLab)
    study = {
        "sets": [{"id": "a", "excluded": False}, {"id": "b", "excluded": False}],
        "trials": [
            {
                "id": "t-dev",
                "set_id": "a",
                "status": "succeeded",
                "dev_score": 1.0,
                "cv_mean": 0.5,
                "cv_std": 0.1,
                "formulas": ["x@1"],
            },
            {
                "id": "t-cv",
                "set_id": "b",
                "status": "succeeded",
                "dev_score": 0.6,
                "cv_mean": 0.9,
                "cv_std": 0.05,
                "formulas": ["y@1"],
            },
        ],
    }
    ranked = lab.ranking(study)
    assert ranked[0]["id"] == "t-cv"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Landlock/bwrap PoC")
def test_n2_fork_chain_cannot_outlive_cell(tmp_path):
    """Rapid setsid fork-exit chain must stop when the cell returns (PID ns / reaper)."""
    run = _case(tmp_path)
    beat = run / "workspace" / "heartbeat-chain.txt"

    async def exercise():
        ws = PythonWorkspace(run)
        try:
            result = await ws.execute(
                f"""
import os, time
path = {str(beat)!r}
open(path, 'w').write('0')
if os.fork() == 0:
    os.setsid()
    for fd in (0, 1, 2):
        try:
            os.close(fd)
        except OSError:
            pass
    end = time.time() + 8
    n = 0
    while time.time() < end:
        n += 1
        if n % 2000 == 0:
            open(path, 'w').write(f'{{time.time():.1f}}')
        pid = os.fork()
        if pid > 0:
            os._exit(0)
    os._exit(0)
time.sleep(0.2)
print('spawned')
""",
                60,
            )
            assert result["ok"], result.get("output")
            first = beat.read_text().strip() if beat.exists() else "0"
            await asyncio.sleep(2.0)
            second = beat.read_text().strip() if beat.exists() else first
            assert first == second, (first, second)
        finally:
            await ws.close()

    asyncio.run(exercise())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="seccomp PoC")
def test_n2_cell_cannot_sigqueue_reaper_or_host(tmp_path):
    """rt_sigqueueinfo must not kill the supervisor or an unrelated same-uid process."""
    import subprocess
    import time

    run = _case(tmp_path)
    beat = run / "workspace" / "heartbeat-sig.txt"
    victim = subprocess.Popen(["sleep", "120"])
    try:

        async def exercise():
            ws = PythonWorkspace(run)
            try:
                result = await ws.execute(
                    f"""
import os, time, ctypes, struct
path = {str(beat)!r}
open(path, 'w').write('0')
if os.fork() == 0:
    os.setsid()
    for fd in (0, 1, 2):
        try:
            os.close(fd)
        except OSError:
            pass
    for i in range(1, 20):
        open(path, 'w').write(str(i))
        time.sleep(0.4)
    os._exit(0)
time.sleep(0.2)
import platform
libc = ctypes.CDLL(None, use_errno=True)
nr = 129 if platform.machine() == 'x86_64' else 138
buf = ctypes.create_string_buffer(128)
struct.pack_into('iii', buf, 0, 9, 0, -1)
ctypes.set_errno(0)
r_parent = libc.syscall(nr, os.getppid(), 9, buf)
e_parent = ctypes.get_errno()
ctypes.set_errno(0)
r_host = libc.syscall(nr, {victim.pid}, 9, buf)
e_host = ctypes.get_errno()
print(f'sigqueue_parent={{r_parent}}:{{e_parent}} sigqueue_host={{r_host}}:{{e_host}}')
""",
                    60,
                )
                assert result["ok"], result.get("output")
                line = [x for x in result["output"].splitlines() if "sigqueue_parent=" in x][-1]
                assert "sigqueue_parent=-1:" in line
                assert "sigqueue_host=-1:" in line
                first = beat.read_text().strip() if beat.exists() else "0"
                await asyncio.sleep(2.0)
                second = beat.read_text().strip() if beat.exists() else first
                assert first == second or int(second) <= int(first) + 1, (first, second)
            finally:
                await ws.close()

        asyncio.run(exercise())
        time.sleep(0.2)
        assert victim.poll() is None, "host process must survive sandboxed rt_sigqueueinfo"
    finally:
        if victim.poll() is None:
            victim.kill()
            victim.wait(timeout=5)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="seccomp PoC")
def test_n4_seccomp_denies_unshare_and_clone3(tmp_path):
    run = _case(tmp_path)

    async def exercise():
        ws = PythonWorkspace(run)
        try:
            result = await ws.execute(
                """
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
out = []
ctypes.set_errno(0)
r = libc.unshare(0x10000000)  # CLONE_NEWUSER
out.append(f'unshare={r}:errno={ctypes.get_errno()}')
ctypes.set_errno(0)
r = libc.syscall(435, None)  # clone3
out.append(f'clone3={r}:errno={ctypes.get_errno()}')
print('||'.join(out))
""",
                60,
            )
            assert result["ok"], result["output"]
            line = [x for x in result["output"].splitlines() if "unshare=" in x][-1]
            assert "unshare=-1:" in line
            assert "clone3=-1:" in line
            assert "errno=38" in line or "errno=1" in line
        finally:
            await ws.close()

    asyncio.run(exercise())
