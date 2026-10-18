"""Strict JSON transport for gateways with unreliable Anthropic schema-tool encoding."""

import json


def install_json_transport(client):
    original = client.acall

    async def acall(messages, tools=None, output_model=None, **kwargs):
        if output_model is None:
            return await original(messages, tools=tools, **kwargs)
        if tools:
            raise ValueError("Structured gateway output cannot also execute tools")
        instructions = (
            "Return exactly one JSON object matching this JSON Schema. "
            "Use JSON arrays [] for array fields, including nested evidence_ids; "
            'never XML, {"item":...}, a string, or null in place of an array. '
            "Do not call a tool or include commentary.\n"
            + json.dumps(output_model.model_json_schema(), ensure_ascii=False)
        )
        response = await original(
            [*messages, {"role": "user", "content": instructions}], tools=None, **kwargs
        )
        text = response.content.strip()
        if text.startswith("```json\n") and text.endswith("\n```"):
            text = text[8:-4]
        # No permissive list coercion: invalid review fields remain invalid and
        # native Predict owns the bounded retry through the same budget client.
        response.parsed = output_model.model_validate(json.loads(text))
        return response

    client.acall = acall
    return client
