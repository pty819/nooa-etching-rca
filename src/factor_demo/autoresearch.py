"""Persistent research with evidence gates, bounded review, and isolated branches."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

from .research_workspace import PythonWorkspace, prepare_workspace
from .synthetic import write_json

INITIAL_MESSAGE = """自主研究固定recipe刻蚀公开数据，分别比较1↔2、2↔3、1↔3。自由选择算法和写代码，
形成有数学证据、反证和可区分检查的假设。可以动态派生独立子研究，读取其结果后综合，避免重复证据。
数值保存CSV/Parquet并提供EvidenceRef，supported/weak引用evidence_ids。保留失败和不确定性，不凑十个根因。
达到可复核检查点返回ResearchReply，宿主会审查并可能要求定向修订。"""


async def research_turn(
    run: Path,
    provider_name="glm",
    message="",
    mode="research",
    settings_path=Path("configs/runtime.json"),
    timeout=3600,
    data=None,
    speaker="user",
    events=None,
    live=False,
    on_agent=None,
    policy=None,
    admission_path=None,
):
    if data is not None:
        prepare_workspace(data, run)
    run = run.resolve()
    if not (run / "session.json").exists():
        raise ValueError("No autoresearch session; create one first")
    from nooa.storage.sqlite import SQLiteStorageManager
    from nooa.tracing import enable_tracing, exporters

    from .autoresearch_agent import AutoresearchAgent
    from .budget import MissionBudget
    from .capabilities import Capabilities
    from .live_events import LiveEvents, MarkdownReplies
    from .provider_stream import observe_agent, observe_client
    from .providers import load_provider
    from .reply_writer import StreamingReplyWriter
    from .research_branches import ResearchManager
    from .research_context import install_research_context
    from .review_pipeline import research_with_review
    from .runtime import RuntimeSettings, build_client, context_truncation, install_summarizer

    policy = policy or Capabilities.load()
    settings = RuntimeSettings.load(settings_path)
    provider = load_provider(provider_name)
    owned_events = events is None
    events = events or LiveEvents(run, uuid.uuid4().hex)
    budget = MissionBudget(events.root, events.task_id, policy, admission_path)
    replies = MarkdownReplies(events)
    replies.publication = "discussion" if mode != "research" else "progress"
    workspace = PythonWorkspace(
        run, events=events, research_enabled=mode not in {"chat", "discuss"}
    )
    storage = SQLiteStorageManager(run / "conversation.sqlite")
    enable_tracing(exporters=[exporters.journal_file(run / "traces")])
    base = build_client(provider, settings)
    base._http.httpx_async.headers["X-Research-Agent"] = events.agent_id
    base._http.httpx_async.headers["X-Research-Mission"] = events.task_id
    if live:
        observe_client(base, events)
    llm = budget.client(base, events, settings)
    writer = StreamingReplyWriter(llm, replies) if live else None
    agent = AutoresearchAgent(
        workspace,
        replies=replies,
        reply_writer=writer,
        policy=policy,
        llm=llm,
        storage=storage,
        truncation=context_truncation(settings),
    )
    storage.restore_latest_snapshot(agent)
    agent._formula_lab.begin_turn()
    manager = ResearchManager(
        agent,
        events.root,
        events.task_id,
        events.agent_id,
        policy,
        budget,
        provider_name,
        settings_path,
    )
    agent._manager = manager
    message = message or INITIAL_MESSAGE
    ledger = (
        json.loads((run / "latest_reply.json").read_text())
        if (run / "latest_reply.json").exists()
        else {}
    )
    feedback_path = run / "expert_feedback.jsonl"
    feedback = (
        [json.loads(x)["message"] for x in feedback_path.read_text().splitlines()]
        if feedback_path.exists()
        else []
    )
    install_research_context(agent, message, feedback, ledger)
    agent.context["branch_state"] = __import__("nooa").Context(
        expr="self._manager.context_summary()"
    )
    agent.context["live_reply_contract"] = (
        "调用reply(message_id,brief,真实图表路径)主动发进度。最终ResearchReply包含结构化EvidenceRef；审查和最终发布由宿主执行。"
    )
    if live:
        observe_agent(agent, events)
    install_summarizer(agent, settings)
    persisted_round = max(
        (
            int(p.name.removeprefix("round_"))
            for p in (run / "rounds").glob("round_*")
            if p.name.removeprefix("round_").isdigit()
        ),
        default=0,
    )
    agent.round_number = max(agent.round_number, persisted_round) + 1
    turn = agent.round_number
    folder = run / "rounds" / f"round_{turn:03d}"
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "message.md").write_text(message)
    write_json(run / "runtime.json", settings.public_record(provider))
    write_json(folder / "capabilities.json", policy.model_dump())
    write_json(
        run / "provider.json",
        {
            "provider": provider.name,
            "model": provider.model,
            "base_url": provider.base_url,
            "nooa_commit": "1454b78c8f0a85c97eab1c6a91cc8cfc95b34de5",
        },
    )
    with (run / "conversation.jsonl").open("a") as out:
        out.write(
            json.dumps(
                {
                    "round": turn,
                    "speaker": speaker,
                    "mode": mode,
                    "message": message,
                    "time": time.time(),
                    "turn_id": events.task_id,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    write_json(
        run / "status.json",
        {"round": turn, "status": "running", "mode": mode, "mission": events.task_id},
    )
    events.emit("turn.start", round=turn, mode=mode, capabilities=policy.model_dump())
    if owned_events:
        events.emit("task.start", mode=mode)
        events.emit("user.message", message=message)
    main_task = asyncio.current_task()

    async def control():
        while True:
            for item in events.take_guidance_records():
                manager.guidance.put(item)
                if item["source"] == "user":
                    feedback.append(item["message"])
                    with feedback_path.open("a") as out:
                        out.write(json.dumps(item, ensure_ascii=False) + "\n")
                    agent.context["expert_feedback"] = json.dumps(
                        {"guidance_as_data": feedback[-10:]}, ensure_ascii=False
                    )
                events.emit("steering.applied", message=item["message"], source=item["source"])
            if (run / "requests" / f"{events.task_id}.cancel").exists() or (
                events.root / "requests" / f"{events.task_id}.cancel"
            ).exists():
                main_task.cancel()
                return
            await asyncio.sleep(0.2)

    background = asyncio.create_task(control())
    external = None
    completed = False
    try:
        if on_agent:
            external = await on_agent(agent, workspace)
        async with asyncio.timeout(timeout):
            if mode == "research":
                reply, review = await research_with_review(agent, message, folder, policy, llm)
            else:
                if mode == "chat":
                    from .strategy_factory import research_strategy

                    reply = await agent.chat(message, _strategy=research_strategy(policy))
                else:
                    reply = await getattr(agent, mode)(message)
                review = {"status": "discussion", "revision": 0, "review": None}
            payload = {
                **reply.model_dump(),
                "review_status": review["status"],
                "mission": events.task_id,
                "revision": review["revision"],
            }
            if mode == "research":
                formula_result = agent._formula_lab.handoff(reply.formula_comparison)
                payload["formula_result"] = formula_result
                payload["formula_markdown"] = agent._formula_lab.markdown(formula_result)
            write_json(folder / "reply.json", payload)
            write_json(run / "latest_reply.json", payload)
            write_json(folder / "review.json", review)
            write_json(run / "latest_review.json", review)
            (folder / "reply.md").write_text(render_reply(payload))
            if mode == "research":
                replies.publication = "final"
                replies.review_status = review["status"]
                # Appended by the host on commit even if the text writer omits the table.
                replies.final_appendix = payload.get("formula_markdown", "")
                final_text = render_reply(payload, include_formula=False)
                import polars as pl
                from nooa.unifiedllm.admission import AdmissionCallCapError

                from .budget import BudgetExceeded
                from .live_events import public_artifact_path

                images = [
                    p
                    for p in reply.artifacts
                    if Path(p).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                ][:2]
                tables = []
                for name in dict.fromkeys(e.artifact for e in reply.evidence):
                    path = public_artifact_path(run, name)
                    if path.suffix.lower() == ".csv" and pl.read_csv(path, n_rows=0).width <= 30:
                        tables.append(name)
                    if len(tables) == 2:
                        break
                if writer:
                    try:
                        await writer.send(
                            f"round-{turn}-final",
                            "以下是本轮已经保存、验证和审查的最终报告。严格保持数值、假设状态和局限，不新增结论。审查状态为"
                            + review["status"]
                            + "。\n"
                            + final_text,
                            images,
                            tables,
                        )
                    except (BudgetExceeded, AdmissionCallCapError):
                        replies.send(f"round-{turn}-final", final_text, images, tables)
                        events.emit(
                            "budget.delivery_fallback",
                            message="正文生成预算已耗尽，直接交付已保存的结构化报告",
                        )
                else:
                    replies.send(f"round-{turn}-final", final_text, images, tables)
                freeze_round_artifacts(run, folder)
            elif not replies.committed:
                replies.send(f"round-{turn}-result", render_reply(payload))
            with (run / "conversation.jsonl").open("a") as out:
                out.write(
                    json.dumps(
                        {"round": turn, "speaker": "agent", "turn_id": events.task_id, **payload},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            write_json(
                run / "status.json",
                {
                    "round": turn,
                    "status": "checkpoint",
                    "mode": mode,
                    "review_status": review["status"],
                    "mission": events.task_id,
                },
            )
            events.emit("turn.checkpoint", round=turn, review_status=review["status"])
            completed = True
            return payload
    except BaseException as error:
        write_json(
            run / "status.json",
            {
                "round": turn,
                "status": "interrupted",
                "error_type": type(error).__name__,
                "mission": events.task_id,
            },
        )
        events.emit("turn.error", error_type=type(error).__name__)
        raise
    finally:
        background.cancel()
        await asyncio.gather(background, return_exceptions=True)
        if external:
            external.cancel()
            await asyncio.gather(external, return_exceptions=True)
        await manager.shutdown(wait=False)
        try:
            storage.save_snapshot(agent)
        finally:
            await workspace.close()
            await agent.aclose()
            await llm.aclose()
            storage.close()
            snapshot = budget.snapshot()
            write_json(folder / "budget.json", snapshot)
            events.emit("budget.snapshot", **snapshot)
            budget.close()
        from opentelemetry import trace

        trace.get_tracer_provider().force_flush()
        from .research_audit import audit_session

        audit_session(run)
        if owned_events:
            events.emit("task.end", status="checkpoint" if completed else "interrupted")
            events.close()


def render_reply(reply: dict, include_formula=True) -> str:
    lines = [reply["summary"], ""]
    for h in reply["hypotheses"]:
        lines += [
            f"### {h['id']} · {h['title']} ({h['status']})",
            "",
            h["mathematical_evidence"],
            "",
            "替代解释：" + h["alternative_explanation"],
            "",
            "反证检查：" + h["falsification_test"],
            "",
            "证据文件：" + ", ".join(h["evidence_files"]),
            "",
        ]
    if reply["questions_for_expert"]:
        lines += ["专家待审：", "", *["- " + x for x in reply["questions_for_expert"]], ""]
    if reply["next_experiments"]:
        lines += ["后续实验：", "", *["- " + x for x in reply["next_experiments"]], ""]
    if include_formula and reply.get("formula_markdown"):
        lines += [reply["formula_markdown"], ""]
    return "\n".join(lines)


def freeze_round_artifacts(run: Path, folder: Path) -> dict:
    """Keep evidence for earlier claims even when the scientist revises a script."""
    snapshot = folder / "artifacts"
    if not snapshot.exists():
        shutil.copytree(
            run / "workspace/artifacts",
            snapshot,
            ignore=__import__("factor_demo.evidence", fromlist=["snapshot_ignore"]).snapshot_ignore,
        )
    hashes = {
        str(p.relative_to(snapshot)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(snapshot.rglob("*"))
        if p.is_file()
    }
    write_json(folder / "artifacts-manifest.json", hashes)
    return hashes
