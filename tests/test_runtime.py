import asyncio
import json

import httpx
import pytest

from factor_demo.providers import Provider
from factor_demo.runtime import (
    RuntimeSettings,
    build_client,
    context_truncation,
    install_summarizer,
)


def test_context_reserves_output_and_summary_headroom():
    settings = RuntimeSettings()
    assert settings.input_budget == 222_000
    assert settings.compression_threshold == 177_600
    with pytest.raises(ValueError, match="smaller"):
        RuntimeSettings(max_output_tokens=272_000)
    with pytest.raises(ValueError, match="usable input"):
        RuntimeSettings(summary_threshold=225_000)


def test_glm_cannot_silently_disable_thinking():
    p = Provider("glm", "glm-5.3-flash", "https://example.invalid/v1", "openai", "test-only")
    with pytest.raises(ValueError, match="cannot disable"):
        build_client(p, RuntimeSettings(thinking=False))


def test_real_nooa_wire_v2_output_cap_and_reasoning(monkeypatch):
    """Exercise real NOOA + SDK request construction, with a fake HTTP response."""
    captured = []

    async def send(client, request, **kwargs):
        assert request.url.host == "wire.invalid"
        body = json.loads(request.content)
        captured.append(body)
        code = "return_result({'selected_experiment_id':'exp_001','summary':'ok','limitations':[]})"
        return httpx.Response(200, request=request, json={"id": "test-1", "object": "chat.completion",
            "created": 0, "model": "glm-5.3-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "cell-1", "type": "function", "function": {
                    "name": "python_cell", "arguments": json.dumps({"code": code})}}]},
                "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})

    monkeypatch.setattr(httpx.AsyncClient, "send", send)

    async def exercise():
        from factor_demo.agent import FactorResearchAgent

        p = Provider("glm", "glm-5.3-flash", "https://wire.invalid/v1", "openai", "test-only")
        settings = RuntimeSettings()
        client = build_client(p, settings)
        assert client.context_window == 272_000
        assert client.get_context_limits().usable_input_tokens == 222_000
        agent = FactorResearchAgent(None, llm=client, truncation=context_truncation(settings))
        assert agent.runtime.truncation_config.max_context_tokens == 222_000
        summarizer = install_summarizer(agent, settings)
        assert summarizer.config.max_tokens == 177_600
        try:
            result = await agent.research("Protocol unit test only")
            assert result.selected_experiment_id == "exp_001"
        finally:
            await agent.aclose()

    asyncio.run(exercise())
    assert len(captured) == 1
    body = captured[0]
    assert body.get("max_tokens", body.get("max_completion_tokens")) == 50_000
    assert "context_window" not in body  # local budget metadata must not leak to the API
    assert body["reasoning_effort"] == "max"
    assert body["thinking"] == {"type": "enabled", "clear_thinking": False}
    assert [t["function"]["name"] for t in body["tools"]] == ["python_cell"]
