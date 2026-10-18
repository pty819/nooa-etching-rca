"""Versioned data, arbitrary derivations, scopes and deferred acquisition."""

import asyncio
import base64

import polars as pl
import pytest
from test_formula_lab import fixture_case

from factor_demo.dataset_models import (
    AcquisitionRequest,
    AcquisitionResult,
    DatasetDerivation,
    DatasetSchema,
)
from factor_demo.datasets import DatasetSession, prepare_generic_workspace
from factor_demo.research_workspace import PythonWorkspace


def child(session, title="child", every=2):
    parent = session.current()
    out = session.run / "workspace" / title
    out.mkdir()
    index = pl.read_parquet(session.path() / parent["schema"]["index_file"])
    index.gather_every(every).write_parquet(out / "runs.parquet")
    code = session.run / "workspace" / f"{title}.py"
    code.write_text("# generated filtering code\n")
    return session.derive(
        DatasetDerivation(
            title=title,
            parents=[parent["id"]],
            output_dir=title,
            source=code.name,
            reason="retain every second sample for exploration",
            data_schema=DatasetSchema.model_validate(parent["schema"]),
            params={"every": every},
        )
    )


def test_nested_scope_recovery_lineage_and_immutable_inputs(tmp_path):
    lab = fixture_case(tmp_path / "case")
    s = DatasetSession(lab.run)
    root = s.current()
    a = child(s)
    s.select(a["id"])
    b = child(s, "nested")
    s.select(b["id"])
    assert [root["rows"], a["rows"], b["rows"]] == [32, 16, 8]
    restored = DatasetSession(lab.run)
    assert restored.current()["id"] == b["id"]
    assert restored.select(back="parent")["id"] == a["id"]
    assert restored.select(back="root")["id"] == root["id"]
    assert len(s.registry.lineage(b["id"])["nodes"]) == 3
    assert pl.read_parquet(s.path(root["id"]) / "runs.parquet").height == 32
    s.registry.path(a["id"], verify=True)
    assert (s.path(a["id"]) / "traces").is_dir()
    assert s.registry.get(a["id"])["transform_sha256"]


def test_multimodal_local_imports_and_identity_validation(tmp_path):
    table = tmp_path / "table.csv"
    table.write_text("label,x\na,1\nb,2\n")
    doc = prepare_generic_workspace(table, tmp_path / "table-case")
    assert doc["schema"]["modality"] == "table" and doc["rows"] == 2
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "note.txt").write_text("an observed maintenance note")
    (assets / "pixel.png").write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/rX8AAAAASUVORK5CYII="
        )
    )
    media = prepare_generic_workspace(assets, tmp_path / "media-case")
    assert media["schema"]["modality"] == "mixed" and media["rows"] == 2
    s = DatasetSession(tmp_path / "media-case")
    index = pl.read_parquet(s.path() / "samples.parquet")
    assert all((s.path() / v).exists() for v in index["asset_path"])
    (s.path() / "samples.parquet").write_bytes(b"tamper")
    with pytest.raises(ValueError, match="hash changed"):
        s.registry.path(media["id"], verify=True)


def test_acquisition_is_an_injectable_boundary_not_a_fake_connector(tmp_path):
    source = tmp_path / "supplied"
    source.mkdir()
    pl.DataFrame({"sample_id": ["new-1"], "x": [7]}).write_parquet(source / "samples.parquet")
    lab = fixture_case(tmp_path / "case")
    session = DatasetSession(lab.run)
    old = session.current()
    request = AcquisitionRequest(
        source="future-unified-provider",
        criteria={"item_ids": ["new-1"]},
        reason="expert requests a new item",
        parent_refs=[old["id"]],
        request_id="one",
    )
    assert asyncio.run(session.acquire(request))["status"] == "unavailable"

    class Provider:
        calls = 0

        async def acquire(self, request):
            self.calls += 1
            return AcquisitionResult(
                status="supplied",
                local_path=str(source),
                data_schema=DatasetSchema(),
                new_entry_ids=["new-1"],
                provenance={"revision": "fixture-v1"},
            )

    provider = Provider()
    session.provider = provider
    request = request.model_copy(update={"request_id": "two"})
    result = asyncio.run(session.acquire(request))
    again = asyncio.run(session.acquire(request))
    assert result == again and provider.calls == 1
    assert session.registry.get(result["dataset_ref"])["parents"] == [old["id"]]
    assert session.current()["id"] == old["id"]
    session.registry.path(old["id"], verify=True)


def test_selected_worker_scope_is_readonly_and_does_not_fall_back_to_main(tmp_path):
    lab = fixture_case(tmp_path / "case")
    s = DatasetSession(lab.run)
    a = child(s)
    s.select(a["id"])

    async def exercise():
        worker = PythonWorkspace(lab.run)
        worker.dataset_session = s
        try:
            result = await worker.execute(
                "import polars as pl\nprint(DATASET_ID,pl.read_parquet(DATASET/'runs.parquet').height)\nfrom pathlib import Path\ntry: Path('../input/runs.parquet').read_bytes()\nexcept PermissionError: print('MAIN_DENIED')\ntry: (DATASET/'runs.parquet').write_bytes(b'bad')\nexcept PermissionError: print('WRITE_DENIED')"
            )
            assert (
                result["ok"]
                and "16" in result["output"]
                and "MAIN_DENIED" in result["output"]
                and "WRITE_DENIED" in result["output"]
            )
        finally:
            await worker.close()

    asyncio.run(exercise())


def test_formal_plan_pins_selected_subset_and_branch_scope_is_independent(tmp_path):
    from factor_demo.formula_lab import FormulaLab
    from factor_demo.formula_models import FormulaPlan
    from factor_demo.research_branches import prepare_branch

    lab = fixture_case(tmp_path / "case")
    session = DatasetSession(lab.run)
    main = session.current()
    subset = child(session)
    session.select(subset["id"])
    formal = FormulaLab(lab.run)
    formal.data_session = session
    plan = formal.create(
        FormulaPlan(title="subset only", labels=["good", "bad"], models=["logistic"])
    )
    assert plan["plan"]["dataset_ref"] == subset["id"]
    assert len(plan["plan"]["train"]) + len(plan["plan"]["dev"]) == 16
    session.select(back="root")
    formal.verify_input(formal.get(plan["id"]))
    assert formal.input_path(formal.get(plan["id"])) == session.path(subset["id"])
    branch, _ = prepare_branch(
        lab.run, "research-bound", "main", "question", "branch", [], subset["id"]
    )
    other = DatasetSession(branch)
    assert other.current()["id"] == subset["id"]
    assert session.current()["id"] == main["id"]


def test_dataset_evidence_uses_explicit_child_scope_and_registered_supplement(tmp_path):
    from factor_demo.evidence import EvidenceError, EvidenceVerifier
    from factor_demo.research_models import EvidenceRef, ResearchReply

    lab = fixture_case(tmp_path / "case")
    s = DatasetSession(lab.run)
    root = s.current()
    a = child(s)
    s.select(a["id"])
    main_ids = pl.read_parquet(s.path(root["id"]) / "runs.parquet")["run_id"].to_list()
    child_ids = set(pl.read_parquet(s.path(a["id"]) / "runs.parquet")["run_id"])
    outsider = next(i for i in main_ids if i not in child_ids)
    s.select(root["id"])

    async def produce():
        worker = PythonWorkspace(lab.run)
        worker.dataset_session = s
        try:
            result = await worker.execute(
                "from pathlib import Path\nPath('artifacts/value.csv').write_text("
                + repr("run_id,value\n" + outsider + ",3\n")
                + ")"
            )
            assert result["ok"]
        finally:
            await worker.close()

    asyncio.run(produce())
    s.select(a["id"])
    ref = EvidenceRef(
        id="e",
        claim="saved value",
        artifact="artifacts/value.csv",
        column="value",
        value=3,
        unit="V",
    )
    with pytest.raises(EvidenceError, match="unknown runs"):
        EvidenceVerifier(lab.run).verify(ResearchReply(summary="child", evidence=[ref]))
    ref.dataset_ref = root["id"]
    verified = EvidenceVerifier(lab.run).verify(
        ResearchReply(summary="explicit parent evidence", evidence=[ref])
    )
    assert verified["checks"][0]["dataset_ref"] == root["id"]


def test_supplied_csv_api_scope_switch_and_old_versions_survive(tmp_path):
    import httpx

    from factor_demo.web import create_app

    (tmp_path / "data").mkdir()
    (tmp_path / "data/table.csv").write_text(
        "id,label,group,split,x\n1,a,g1,train,1\n2,b,g2,dev,2\n3,b,g3,test,3\n"
    )
    app = create_app(tmp_path)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"X-Research-Client": "1"},
        ) as client:
            config = (await client.get("/api/config")).json()
            assert "table.csv" in config["datasets"]
            created = await client.post(
                "/api/sessions", json={"title": "table", "dataset": "table.csv"}
            )
            assert created.status_code == 200, created.text
            name = created.json()["id"]
            state = (await client.get(f"/api/sessions/{name}/datasets")).json()
            assert state["current"]["rows"] == 2
            root = state["scope"]["root"]
            (tmp_path / "data/extra.csv").write_text("sample_id,x\nnew,9\n")
            imported = await client.post(
                f"/api/sessions/{name}/datasets/import",
                json={"dataset": "extra.csv", "reason": "user supplied another measurement"},
            )
            assert imported.status_code == 200, imported.text
            new = imported.json()["dataset"]["id"]
            selected = await client.post(
                f"/api/sessions/{name}/datasets/select", json={"dataset_ref": new}
            )
            assert selected.json()["current"]["rows"] == 1
            returned = await client.post(
                f"/api/sessions/{name}/datasets/select", json={"back": "root"}
            )
            assert returned.json()["current"]["id"] == root
            response = await client.get(
                f"/api/sessions/{name}/datasets/{root}/files/samples.parquet"
            )
            assert response.status_code == 200
            assert (
                await client.post("/api/sessions", json={"dataset": "../.secrets/providers.json"})
            ).status_code == 400

    asyncio.run(exercise())


def test_native_codeact_generates_subset_script_and_recovers_scope(tmp_path):
    import json

    from nooa.storage.sqlite import SQLiteStorageManager
    from nooa.unifiedllm import FakeLLMClient, LLMResponse, ToolCall

    from factor_demo.autoresearch_agent import AutoresearchAgent
    from factor_demo.research_context import install_research_context

    lab = fixture_case(tmp_path / "case")
    script = """from pathlib import Path
import polars as pl
x=pl.read_parquet(DATASET/'runs.parquet').gather_every(2)
p=Path('artifacts/subset');p.mkdir(parents=True,exist_ok=True)
x.write_parquet(p/'runs.parquet')
"""
    code = f"""base=await self.inspect_dataset()
print(await self.write_file('artifacts/subset.py',{script!r}))
print(await self.run_script('artifacts/subset.py'))
child=await self.derive_dataset(DatasetDerivation(title='CodeAct subset',parents=[base['id']],output_dir='artifacts/subset',source='artifacts/subset.py',reason='expert asks for alternating samples',data_schema=DatasetSchema(**base['schema'])))
print(await self.run_code(\"import polars as pl\\nassert pl.read_parquet(DATASET/'runs.parquet').height==16\\nprint(DATASET_ID)\"))
return_result(ResearchReply(summary='已生成子集并切换到该范围',intent='visualization'))
"""
    response = LLMResponse(
        parts=(ToolCall(id="subset", name="python_cell", arguments=json.dumps({"code": code})),),
        finish_reason="tool_calls",
    )

    async def exercise():
        storage = SQLiteStorageManager(lab.run / "conversation.sqlite")
        worker = PythonWorkspace(lab.run)
        agent = AutoresearchAgent(
            worker,
            llm=FakeLLMClient(scripted_responses=[response], strict_exhaustion=True),
            storage=storage,
        )
        try:
            install_research_context(agent, "根据需求现场写划分代码")
            reply = await agent.research("每隔一条取一个样本，保存代码和子集，不做模型比较")
            selected = agent._datasets.current()["id"]
            assert reply.dataset_ref == selected and agent._datasets.current()["rows"] == 16
            storage.save_snapshot(agent)
        finally:
            await agent.aclose()
            await worker.close()
            storage.close()
        storage = SQLiteStorageManager(lab.run / "conversation.sqlite")
        worker = PythonWorkspace(lab.run)
        restored = AutoresearchAgent(worker, llm=FakeLLMClient(), storage=storage)
        try:
            assert storage.restore_latest_snapshot(restored)
            assert restored._datasets.current()["id"] == selected
            parent = await restored.return_dataset("parent")
            assert parent["rows"] == 32
            result = await restored.run_code(
                "import polars as pl\nprint(pl.read_parquet(DATASET/'runs.parquet').height)"
            )
            assert result["ok"] and result["output"].strip() == "32"
        finally:
            await restored.aclose()
            await worker.close()
            storage.close()

    asyncio.run(exercise())


def test_explicit_versions_keep_family_and_branch_cannot_select_parent_root(tmp_path):
    from factor_demo.research_branches import prepare_branch

    lab = fixture_case(tmp_path / "case")
    s = DatasetSession(lab.run)
    main = s.current()
    a = child(s)
    s.select(a["id"])
    output = lab.run / "workspace/version"
    output.mkdir()
    pl.read_parquet(s.path() / "runs.parquet").write_parquet(output / "runs.parquet")
    code = lab.run / "workspace/version.py"
    code.write_text("# explicit revised data definition")
    revised = s.derive(
        DatasetDerivation(
            title="v2",
            parents=[a["id"]],
            output_dir="version",
            source=code.name,
            reason="expert changed the data definition",
            relation="version",
            data_schema=s.schema(),
        )
    )
    assert revised["family"] == a["family"] and revised["version"] == a["version"] + 1
    branch, _ = prepare_branch(lab.run, "research-scope", "main", "question", "child", [], a["id"])
    scoped = DatasetSession(branch)
    with pytest.raises(PermissionError):
        scoped.select(main["id"])
    from factor_demo.evidence import EvidenceError, EvidenceVerifier

    with pytest.raises(EvidenceError, match="assigned scope"):
        EvidenceVerifier(branch, dataset_ref=main["id"])
    assert scoped.select(revised["id"])["id"] == revised["id"]


def test_import_rejects_symlinks_and_schema_paths_cannot_escape(tmp_path):
    from pydantic import ValidationError

    from factor_demo.dataset_adapter import discovery_copy

    for field in ["index_file", "steps_file"]:
        for value in ["../escape.parquet", "/absolute.parquet", "nested/../../escape"]:
            with pytest.raises(ValidationError):
                DatasetSchema(**{field: value})
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("not supplied")
    (source / "linked.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        discovery_copy(source, tmp_path / "target")
    assert not (tmp_path / "target/assets/linked.txt").exists()


def test_merge_inherits_step_boundaries_from_all_declared_parents(tmp_path):
    lab = fixture_case(tmp_path / "case")
    s = DatasetSession(lab.run)
    main = s.current()
    schema = s.schema()
    index = pl.read_parquet(s.path() / schema.index_file)
    pieces = []
    for number, frame in enumerate([index.head(16), index.tail(16)]):
        directory = lab.run / "workspace" / f"piece-{number}"
        directory.mkdir()
        frame.write_parquet(directory / schema.index_file)
        script = lab.run / "workspace" / f"piece-{number}.py"
        script.write_text("# disjoint parent selection")
        pieces.append(
            s.derive(
                DatasetDerivation(
                    title=f"piece-{number}",
                    parents=[main["id"]],
                    output_dir=directory.name,
                    source=script.name,
                    reason="disjoint source",
                    data_schema=schema,
                )
            )
        )
    merged = lab.run / "workspace/merged"
    merged.mkdir()
    index.write_parquet(merged / schema.index_file)
    script = lab.run / "workspace/merge.py"
    script.write_text("# union of two parents")
    result = s.derive(
        DatasetDerivation(
            title="merged",
            parents=[p["id"] for p in pieces],
            output_dir="merged",
            source=script.name,
            reason="join source lots",
            relation="merge",
            data_schema=schema,
        )
    )
    steps = pl.read_parquet(s.path(result["id"]) / schema.steps_file)
    assert set(steps["run_id"]) == set(index["run_id"])
    assert result["rows"] == 32 and len(result["parents"]) == 2


def test_merge_rejects_conflicting_parent_asset_directories(tmp_path):
    seed = tmp_path / "seed.csv"
    seed.write_text("sample_id,value\nbase,1\n")
    prepare_generic_workspace(seed, tmp_path / "case")
    s = DatasetSession(tmp_path / "case")
    parents = []
    schema = DatasetSchema(modality="mixed", asset_column="asset_path")
    for name, content in [("one", "first observation"), ("two", "different observation")]:
        source = tmp_path / name
        (source / "assets/object").mkdir(parents=True)
        (source / "assets/object/note.txt").write_text(content)
        pl.DataFrame({"sample_id": [name], "asset_path": ["assets/object"]}).write_parquet(
            source / "samples.parquet"
        )
        parents.append(s.registry.register(source, schema, name))
    work = s.run / "workspace"
    output = work / "merged"
    output.mkdir()
    pl.DataFrame(
        {"sample_id": ["one", "two"], "asset_path": ["assets/object", "assets/object"]}
    ).write_parquet(output / "samples.parquet")
    (work / "merge.py").write_text("# merge two supplied asset collections")
    with pytest.raises(ValueError, match="Ambiguous parent asset"):
        s.derive(
            DatasetDerivation(
                title="ambiguous merge",
                parents=[p["id"] for p in parents],
                output_dir="merged",
                source="merge.py",
                reason="retain both sources",
                relation="merge",
                data_schema=schema,
            )
        )
