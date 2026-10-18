"""Persistent research with evidence gates, bounded review, and isolated branches."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
import uuid
from pathlib import Path

from .io_utils import write_json
from .research_workspace import PythonWorkspace, prepare_workspace

INITIAL_MESSAGE = """根据用户目标与当前数据schema自主研究，不预设工艺类型、标签数量、传感器或步骤。
需要分组比较时按用户指定的对比关系与独立样本粒度开展；只在数据明确声明时使用固定配方约束。自由选择算法和写代码，
形成有数学证据、反证和可区分检查的假设。可以动态派生独立子研究，读取其结果后综合，避免重复证据。
数值保存CSV/Parquet并提供EvidenceRef，supported/weak引用evidence_ids。保留失败和不确定性，不凑十个根因。
达到可复核检查点返回ResearchReply，宿主会审查并可能要求定向修订。"""


async def research_turn(run: Path, *args, **kwargs):
    from .case_lease import case_lease

    with case_lease(run.resolve()):
        return await _research_turn(run, *args, **kwargs)


async def _research_turn(
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
    if live:
        observe_client(base, events)
    llm = budget.client(base, events, settings)
    writer = StreamingReplyWriter(llm, replies) if live else None
    from .reviewer import EvidenceReviewer, choose_provider

    reviewer = None
    review_llm = None
    review_provider = (
        choose_provider(provider, settings)
        if policy.review_enabled and mode == "research"
        else None
    )
    if review_provider:
        review_base = build_client(review_provider, settings)
        if live:
            observe_client(review_base, events)
        review_llm = budget.client(review_base, events, settings)
        reviewer = EvidenceReviewer(llm=review_llm)
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
                reply, review = await research_with_review(
                    agent, message, folder, policy, llm, reviewer
                )
            else:
                if mode == "chat":
                    from .strategy_factory import research_strategy

                    reply = await agent.chat(message, _strategy=research_strategy(policy))
                else:
                    reply = await getattr(agent, mode)(message)
                review = {"status": "discussion", "revision": 0, "review": None}
            if agent._datasets and not reply.dataset_ref:
                reply.dataset_ref = agent._datasets.current()["id"]
            payload = {
                **reply.model_dump(),
                "review_status": review["status"],
                "mission": events.task_id,
                "revision": review["revision"],
                "delivery_status": "pending",
                "evidence_verification": review.get("verification"),
                "reviewer_model": review_provider.model if review_provider else None,
                "reviewer_independent_model": bool(
                    review_provider and review_provider.model != provider.model
                ),
            }
            if review["status"] not in {"passed", "not_required", "discussion"}:
                for hypothesis in payload["hypotheses"]:
                    if hypothesis["status"] == "supported":
                        hypothesis["status"] = "weak"
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
                replies.publication = (
                    "final" if review["status"] in {"passed", "not_required"} else "checkpoint"
                )
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
                if writer and reply.intent != "visualization":
                    try:
                        await writer.send(
                            f"round-{turn}-final",
                            final_text,
                            images,
                            tables,
                        )
                    except Exception as delivery_error:  # noqa: BLE001 - saved result is authoritative; cancellation is BaseException
                        payload["delivery_status"] = "fallback"
                        payload["delivery_error_type"] = type(delivery_error).__name__
                        replies.send(f"round-{turn}-final", final_text, images, tables)
                        events.emit(
                            "budget.delivery_fallback"
                            if isinstance(delivery_error, (BudgetExceeded, AdmissionCallCapError))
                            else "reply.delivery_fallback",
                            error_type=type(delivery_error).__name__,
                            message="正文推送未完成，直接交付已保存的规范报告",
                        )
                else:
                    replies.send(f"round-{turn}-final", final_text, images, tables)
                freeze_round_artifacts(run, folder)
            elif not replies.committed:
                replies.send(f"round-{turn}-result", render_reply(payload))
            if payload["delivery_status"] == "pending":
                payload["delivery_status"] = "delivered"
            write_json(folder / "reply.json", payload)
            write_json(run / "latest_reply.json", payload)
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
            if reviewer:
                await reviewer.aclose()
            if review_llm:
                await review_llm.aclose()
            await llm.aclose()
            storage.close()
            snapshot = budget.snapshot()
            write_json(folder / "budget.json", snapshot)
            events.emit("budget.snapshot", **snapshot)
            budget.close()
        from opentelemetry import trace

        with contextlib.suppress(Exception):
            trace.get_tracer_provider().force_flush()
        from .research_audit import audit_session

        try:
            audit_session(run)
        except Exception as audit_error:  # noqa: BLE001 - preserve the research exception
            with contextlib.suppress(Exception):
                write_json(folder / "audit-error.json", {"error_type": type(audit_error).__name__})
                events.emit(
                    "audit.warning",
                    error_type=type(audit_error).__name__,
                    message="研究结果已保留，审计未完成",
                )
        finally:
            if owned_events:
                with contextlib.suppress(Exception):
                    events.emit("task.end", status="checkpoint" if completed else "interrupted")
                with contextlib.suppress(Exception):
                    events.close()


def render_reply(reply: dict, include_formula=True) -> str:
    lines = ["### 研究者说明（解释与因果判断待专家核实）", "", reply["summary"], ""]
    if reply.get("dataset_ref"):
        lines += ["> 数据范围：`" + reply["dataset_ref"] + "`。", ""]
    verified = reply.get("evidence_verification") or {}
    if verified.get("checks"):
        lines += ["### 可核对数值", "", "| 引用 | 数值 | 来源验证 |", "|---|---|---|"]
        for item in verified["checks"]:
            proof = item.get("provenance", {})
            grade = {
                "raw_recomputed": "原始输入重新计算",
                "formula_recomputed": "规范预测重新计分",
                "derived_recomputed": "派生数据重新计算",
                "exploratory_reproducible": "已关联执行记录，计算语义待核实",
            }.get(proof.get("grade"), "旧记录，来源待核实")
            lines.append(f"| {item['id']} | {item['actual']} {item['unit']} | {grade} |")
        lines.append("")
    for h in reply["hypotheses"]:
        status = {
            "supported": "已通过声明的数值检查，机制待确认",
            "weak": "仍需补证",
            "rejected": "当前被反证",
            "unresolved": "尚不能判断",
        }[h["status"]]
        lines += [
            f"### {h['id']} · {h['title']} ({status})",
            "",
            h["mathematical_evidence"],
            "",
            "替代解释：" + h["alternative_explanation"],
            "",
            "反证方案（执行状态以登记检查为准）：" + h["falsification_test"],
            "",
            "证据文件：" + ", ".join(h["evidence_files"]),
            "",
        ]
    declared = verified.get("hypothesis_checks", {})
    if declared:
        lines += ["### 登记检查的执行结果", ""]
        for hypothesis_id, checks in declared.items():
            if not checks:
                lines.append(f"- {hypothesis_id}：尚无已完成的登记检查，反证文字仅为方案。")
            for check in checks:
                state = "满足声明条件" if check["passed"] else "不满足声明条件"
                lines.append(
                    f"- {hypothesis_id} · {check['description']}：{check['value']} {check['operator']} {check['threshold']}，{state}。"
                )
        lines.append("")
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
        from .artifact_store import snapshot_tree

        snapshot_tree(run / "workspace/artifacts", snapshot, run / ".artifact-store")
    hashes = {
        str(p.relative_to(snapshot)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(snapshot.rglob("*"))
        if p.is_file()
    }
    write_json(folder / "artifacts-manifest.json", hashes)
    return hashes
