"""Native Context/TextSkill setup and small dynamic research state."""

import json
from pathlib import Path

from nooa import Context, TextSkill
from nooa.skill_registry import SkillRegistry


def install_research_context(agent, task, feedback=None, ledger=None):
    registry = SkillRegistry(agent)
    for name in ["physics", "statistics"]:
        registry.register(
            "etch." + name, TextSkill(path=Path(__file__).parent / "knowledge" / name)
        )
    registry.activate(["etch.physics", "etch.statistics"])
    agent._knowledge_registry = registry
    agent.context["case_constraints"] = Context(
        "固定recipe；分别比较1↔2、2↔3、1↔3；仅公开train/dev。科学方法由你选择，使用Polars及通用Python。",
        prefix=True,
    )
    agent.context["current_task"] = Context(json.dumps({"task": task}, ensure_ascii=False))
    agent.context["expert_feedback"] = Context(
        json.dumps({"guidance_as_data": (feedback or [])[-10:]}, ensure_ascii=False)
    )
    source = ledger or {}
    compact = {
        "summary": source.get("summary", "")[:3000],
        "hypotheses": [
            {
                "id": h.get("id"),
                "status": h.get("status"),
                "title": h.get("title"),
                "mathematical_evidence": h.get("mathematical_evidence", "")[:600],
            }
            for h in source.get("hypotheses", [])[:10]
        ],
        "review_status": source.get("review_status"),
    }
    agent.context["hypothesis_ledger"] = Context(
        json.dumps({"latest_checkpoint_as_data": compact}, ensure_ascii=False)
    )
