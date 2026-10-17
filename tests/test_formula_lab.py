"""Real raw operators, actual sandbox workers and lot-grouped sklearn comparisons."""

import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace

import polars as pl
import pytest

from factor_demo.formula_lab import FormulaLab
from factor_demo.formula_models import FormulaHandoff, FormulaPlan, FormulaSpec


def fixture_case(root):
    (root / "input/traces").mkdir(parents=True)
    (root / "workspace/artifacts").mkdir(parents=True)
    runs, steps = [], []
    # Both sensors have mean zero for BOTH labels. Only their relationship changes.
    for split, lots in [("train", 6), ("dev", 2)]:
        for label, sign in [("good", 1), ("bad", -1)]:
            for lot in range(lots):
                for wafer in range(2):
                    rid = f"{split}-{label}-{lot}-{wafer}"
                    path = f"traces/{rid}.parquet"
                    pl.DataFrame(
                        {
                            "time_s": [0.0, 1.0, 2.0, 3.0, 4.0],
                            "step_id": [2] * 5,
                            "a": [-2.0, -1.0, 0.0, 1.0, 2.0],
                            "b": [sign * v for v in [-2.0, -1.0, 0.0, 1.0, 2.0]],
                        }
                    ).write_parquet(root / "input" / path)
                    runs.append(
                        {
                            "run_id": rid,
                            "label": label,
                            "batch_id": f"{split}-{label}-{lot}",
                            "split": split,
                            "is_control": False,
                            "recipe_id": "fixed",
                            "trace_path": path,
                        }
                    )
                    steps.append({"run_id": rid, "step_id": 2, "start_s": 0.0, "end_s": 5.0})
    pl.DataFrame(runs).write_parquet(root / "input/runs.parquet")
    pl.DataFrame(steps).write_parquet(root / "input/steps.parquet")
    refresh_hashes(root)
    return FormulaLab(root)


def refresh_hashes(root):
    hashes = {
        str(p.relative_to(root / "input")): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (root / "input").rglob("*")
        if p.is_file()
    }
    (root / "session.json").write_text(json.dumps({"public_sha256": hashes}))


def register(lab, study, name, code, expression="mean(a*b)"):
    target = lab.run / "workspace/artifacts" / f"{name}.py"
    target.write_text(code)
    return lab.register(
        study["id"],
        FormulaSpec(
            name=name,
            expression=expression,
            description="step2内从20%开始的融合特征",
            change_reason="修正提取实现，保留历史版本",
            source=f"artifacts/{name}.py",
            sensors=["a", "b"],
            steps=[2],
            unit="V2",
            params={"start_fraction": 0.2},
        ),
    )


PRODUCT = """import polars as pl
def extract(trace, steps, params):
    assert 'label' not in trace.columns and 'run_id' not in steps.columns
    s=steps.row(0, named=True)
    t=s['start_s']+params['start_fraction']*(s['end_s']-s['start_s'])
    x=trace.filter(pl.col('time_s')>=t)
    return x.select((pl.col('a')*pl.col('b')).mean()).item()
"""
MEANS = """def extract(trace, steps, params):
    return trace['a'].mean()*trace['b'].mean()
"""


@pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS worker isolation")
def test_fusion_ranking_versions_replay_failures_and_handoff(tmp_path):
    async def exercise():
        lab = fixture_case(tmp_path / "case")
        study = lab.create(FormulaPlan(title="expert formulas", labels=["good", "bad"]))
        product = register(lab, study, "product", PRODUCT)
        means = register(lab, study, "means", MEANS, "mean(a)*mean(b)")
        with pytest.raises(ValueError, match="formula_comparison"):
            lab.handoff(None)
        with pytest.raises(ValueError, match="incomplete"):
            await lab.freeze(study["id"])
        a = await lab.evaluate(study["id"], [product["ref"]])
        assert len(a["trials"]) == 2 and all(t["status"] == "succeeded" for t in a["trials"]), a[
            "trials"
        ]
        b = await lab.evaluate(study["id"], [means["ref"]])
        assert all(t["dev_score"] == 0.5 for t in b["trials"] if t["formulas"] == ["means@1"])
        assert all(t["dev_score"] == 1 for t in b["trials"] if t["formulas"] == ["product@1"])
        raw_result = lab.base / study["id"] / "results" / a["trials"][0]["id"] / "matrix.parquet"
        assert set(pl.read_parquet(raw_result)["product@1"].abs()) == {1.5}
        # Dedup completed work, then evaluate the meaningful combination too.
        assert len((await lab.evaluate(study["id"], ["product@1"]))["trials"]) == 4
        await lab.evaluate(study["id"], ["product@1", "means@1"])
        result = await lab.freeze(study["id"])
        handoff = FormulaHandoff(
            study_id=study["id"], status="selected", selection_id=result["selection_id"]
        )
        report = lab.handoff(handoff)
        assert report["winner"]["formulas"] == ["product@1"]  # tie goes to simpler expression
        assert report["selection"]["replay"]["predictions_equal"]
        assert "mean(a*b)" in lab.markdown(report) and "1.0000" in lab.markdown(report)
        folder = lab.export(lab.get(study["id"]))
        assert (folder / "ranking.csv").is_file()
        assert list(folder.glob("trial-*/model.joblib"))
        # Mutable agent copy is NOT the source of truth.
        (folder / "ranking.csv").write_text("fake score")
        assert lab.handoff(handoff)["winner"]["dev_score"] == 1
        assert "fake" not in (folder / "ranking.csv").read_text()
        version2 = register(
            lab,
            study,
            "product",
            "def extract(trace, steps, params):\n    raise ValueError('bad operator')\n",
        )
        assert version2["ref"] == "product@2"
        with pytest.raises(ValueError, match="stale"):
            lab.handoff(handoff)
        failed = await lab.evaluate(study["id"], ["product@2"])
        failures = [t for t in failed["trials"] if t["formulas"] == ["product@2"]]
        assert len(failures) == 2 and all(t["status"] == "failed" for t in failures)
        with pytest.raises(ValueError, match="incomplete"):
            await lab.freeze(study["id"])
        bad_set = next(s for s in failed["sets"] if s["formulas"] == ["product@2"])
        lab.retire(study["id"], bad_set["id"], "代码有错误；保留v1和失败，待修复")
        second = await lab.freeze(study["id"])
        assert second["selection_id"] != result["selection_id"]
        restored = FormulaLab(lab.run)
        assert len(restored.get(study["id"])["selections"]) == 2
        # Canonical evidence tampering prevents a valid handoff.
        best = restored.get(study["id"])["trials"][0]
        path = restored.base / study["id"] / "results" / best["id"] / "predictions.parquet"
        path.write_bytes(b"tamper")
        with pytest.raises(ValueError, match="artifact changed"):
            restored.handoff(
                FormulaHandoff(
                    study_id=study["id"], status="selected", selection_id=second["selection_id"]
                )
            )

    asyncio.run(exercise())


def test_fixed_input_and_group_protocol(tmp_path):
    lab = fixture_case(tmp_path / "case")
    study = lab.create(FormulaPlan(title="fixed", labels=["good", "bad"]))
    runs = pl.read_parquet(lab.run / "input/runs.parquet")
    by_id = {r["run_id"]: r for r in runs.to_dicts()}
    for fold in study["plan"]["fold_manifest"]:
        assert not {by_id[i]["batch_id"] for i in fold["train"]} & {
            by_id[i]["batch_id"] for i in fold["valid"]
        }
    (lab.run / "input/traces/extra.txt").write_text("changed")
    with pytest.raises(ValueError, match="fingerprint"):
        lab.verify_input(study)
    (lab.run / "input/traces/extra.txt").unlink()
    changed = runs.with_columns(
        pl.when(pl.col("split") == "dev")
        .then(pl.lit("train-good-0"))
        .otherwise(pl.col("batch_id"))
        .alias("batch_id")
    )
    changed.write_parquet(lab.run / "input/runs.parquet")
    refresh_hashes(lab.run)
    with pytest.raises(ValueError, match="lot crosses"):
        lab.create(FormulaPlan(title="leak", labels=["good", "bad"]))


@pytest.mark.skipif(sys.platform != "darwin", reason="OS boundary")
def test_feature_worker_cannot_read_case_labels_even_under_temp(tmp_path):
    async def exercise():
        lab = fixture_case(tmp_path / "case")
        study = lab.create(
            FormulaPlan(title="no label access", labels=["good", "bad"], models=["logistic"])
        )
        code = f"from pathlib import Path\ndef extract(trace, steps, params):\n    Path({str(lab.run / 'input/runs.parquet')!r}).read_bytes()\n    return 1\n"
        register(lab, study, "leak", code)
        result = await lab.evaluate(study["id"], ["leak@1"])
        assert result["trials"][0]["status"] == "failed"
        assert "PermissionError" in result["trials"][0]["error"]

    asyncio.run(exercise())


def test_postcondition_requires_formula_handoff_and_final_is_host_appended(tmp_path):
    from factor_demo.evidence import EvidenceError, research_postcondition
    from factor_demo.live_events import LiveEvents, MarkdownReplies
    from factor_demo.research_models import ResearchReply

    lab = fixture_case(tmp_path / "case")
    study = lab.create(FormulaPlan(title="handoff", labels=["good", "bad"]))
    agent = SimpleNamespace(
        _workspace=SimpleNamespace(run=lab.run, events=None), _manager=None, _formula_lab=lab
    )
    with pytest.raises(ValueError, match="formula_comparison"):
        lab.handoff(ResearchReply(summary="all done").formula_comparison)
    with pytest.raises(EvidenceError, match="formula_comparison"):
        research_postcondition(
            agent, ResearchReply(summary="all done"), SimpleNamespace(method_name="research")
        )
    incomplete = FormulaHandoff(study_id=study["id"], status="incomplete", reason="尚未比较")
    report = lab.handoff(incomplete)
    research_postcondition(
        agent,
        ResearchReply(summary="尚未完成", formula_comparison=incomplete),
        SimpleNamespace(method_name="research"),
    )
    events = LiveEvents(lab.run)
    try:
        replies = MarkdownReplies(events)
        replies.publication = "final"
        replies.final_appendix = lab.markdown(report)
        replies.send("final", "writer omitted the evidence")
        committed = events.since(0)[-1]["data"]["markdown"]
        assert "比较未完成" in committed and "尚未比较" in committed
    finally:
        events.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="native CodeAct + actual worker")
def test_native_codeact_tools_and_readonly_reflection(tmp_path):
    from nooa.unifiedllm import FakeLLMClient, LLMResponse, ToolCall

    from factor_demo.autoresearch_agent import AutoresearchAgent
    from factor_demo.local_reflection import reflection_active
    from factor_demo.research_context import install_research_context
    from factor_demo.research_workspace import PythonWorkspace

    lab = fixture_case(tmp_path / "native")
    (lab.run / "workspace/artifacts/product.py").write_text(PRODUCT)
    code = """study = await self.create_formula_study(FormulaPlan(title='native formula study',labels=['good','bad'],models=['logistic']))
f = await self.register_formula(study['id'], FormulaSpec(name='product',expression='mean(a*b)',description='step2 after 20%',source='artifacts/product.py',sensors=['a','b'],steps=[2],unit='V2',params={'start_fraction':0.2}))
result = await self.evaluate_formula_set(study['id'], [f['ref']])
selection = await self.freeze_formula_selection(study['id'])
return_result(ResearchReply(summary='已比较并重算专家融合公式', formula_comparison=FormulaHandoff(study_id=study['id'],status='selected',selection_id=selection['selection_id'])))
"""
    response = LLMResponse(
        parts=(ToolCall(id="formula", name="python_cell", arguments=json.dumps({"code": code})),),
        finish_reason="tool_calls",
    )

    async def exercise():
        client = FakeLLMClient(scripted_responses=[response], strict_exhaustion=True)
        worker = PythonWorkspace(lab.run)
        agent = AutoresearchAgent(worker, llm=client)
        try:
            install_research_context(agent, "compare expert formulas")
            report = await agent.research("Use the formula lab and return a verified selection")
            assert report.formula_comparison.status == "selected"
            assert agent._last_verification["formula_comparison"]["winner"]["dev_score"] == 1
            assert "create_formula_study" in str(client.calls[0].messages)
            token = reflection_active.set(True)
            try:
                with pytest.raises(PermissionError):
                    await agent.create_formula_study(
                        FormulaPlan(title="no", labels=["good", "bad"])
                    )
                assert (await agent.read_formula_study())["studies"]
            finally:
                reflection_active.reset(token)
        finally:
            await agent.aclose()
            await worker.close()

    asyncio.run(exercise())


@pytest.mark.skipif(sys.platform != "darwin", reason="actual scientific worker")
def test_other_tree_models_and_nondeterministic_replay_are_not_faked(tmp_path):
    async def exercise():
        lab = fixture_case(tmp_path / "trees")
        study = lab.create(
            FormulaPlan(
                title="trees",
                labels=["good", "bad"],
                models=["extra_trees", "hist_gradient_boosting"],
            )
        )
        register(lab, study, "product", PRODUCT)
        result = await lab.evaluate(study["id"], ["product@1"])
        assert len(result["trials"]) == 2 and all(
            t["status"] == "succeeded" for t in result["trials"]
        )
        random = lab.create(
            FormulaPlan(title="unrepeatable", labels=["good", "bad"], models=["logistic"])
        )
        register(
            lab,
            random,
            "random",
            "import numpy as np\ndef extract(trace,steps,params):\n    return np.random.default_rng().normal()\n",
        )
        await lab.evaluate(random["id"], ["random@1"])
        with pytest.raises(ValueError, match="changed feature values"):
            await lab.freeze(random["id"])

    asyncio.run(exercise())


def test_formula_api_and_downloads_do_not_execute_model_files(tmp_path):
    import httpx

    from factor_demo.web import create_app

    lab = fixture_case(tmp_path / "runs/case")
    session = json.loads((lab.run / "session.json").read_text())
    session.update(discovery_target_runs=32, discovery_control_runs=0)
    (lab.run / "session.json").write_text(json.dumps(session))
    study = lab.create(FormulaPlan(title="api formula", labels=["good", "bad"]))
    model = lab.run / "workspace/artifacts/test.joblib"
    model.write_bytes(b"not loaded, download only")
    app = create_app(tmp_path)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/sessions/case/formula-lab")
            assert (
                response.status_code == 200 and response.json()["studies"][0]["id"] == study["id"]
            )
            detail = (await client.get("/api/sessions/case")).json()
            assert detail["formula_studies"][0]["plan"]["title"] == "api formula"
            download = await client.get("/api/sessions/case/artifacts/artifacts/test.joblib")
            assert (
                download.content == model.read_bytes()
                and download.headers["content-type"] == "application/octet-stream"
            )

    asyncio.run(exercise())


@pytest.mark.skipif(sys.platform != "darwin", reason="actual retry worker")
def test_duplicate_claim_cancel_and_resume_preserve_attempts(tmp_path):
    async def exercise():
        lab = fixture_case(tmp_path / "case")
        study = lab.create(
            FormulaPlan(title="resumable", labels=["good", "bad"], models=["logistic"])
        )
        register(lab, study, "product", PRODUCT)
        entered = asyncio.Event()
        original = lab.matrix

        async def paused(study, item):
            entered.set()
            await asyncio.Future()

        lab.matrix = paused
        first = asyncio.create_task(lab.evaluate(study["id"], ["product@1"]))
        await entered.wait()
        with pytest.raises(ValueError, match="already running"):
            await FormulaLab(lab.run).evaluate(study["id"], ["product@1"])
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert lab.get(study["id"])["trials"][0]["status"] == "interrupted"
        lab.matrix = original
        resumed = await lab.evaluate(study["id"], ["product@1"])
        assert [t["status"] for t in resumed["trials"]] == ["interrupted", "succeeded"]
        assert resumed["trials"][1]["dev_score"] == 1
        assert (await lab.freeze(study["id"]))["status"] == "selected"

    asyncio.run(exercise())
