"""Private provider configuration; never print or trace credentials."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PRIVATE_CONFIG = Path(".secrets/providers.json")


@dataclass
class Provider:
    name: str
    model: str
    base_url: str
    api_style: str
    api_key: str = field(repr=False)


def load_provider(name: str) -> Provider:
    load_dotenv(override=False)
    if name == "custom":
        return Provider(
            name,
            os.environ["FACTOR_MODEL"],
            os.environ["FACTOR_API_BASE"],
            os.getenv("FACTOR_API_STYLE", "openai"),
            os.environ["FACTOR_API_KEY"],
        )
    data = json.loads(PRIVATE_CONFIG.read_text())
    return Provider(**data[name])


def probe(provider: Provider) -> dict:
    from .runtime import RuntimeSettings, build_client

    settings = RuntimeSettings.load(Path("configs/runtime.json"))
    client = build_client(provider, settings)
    response = client.call([{"role": "user", "content": "Reply OK."}])
    usage = response.usage
    return {
        "provider": provider.name,
        "model": provider.model,
        "ok": bool(response.content),
        "runtime": settings.public_record(provider),
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
        if usage
        else {},
    }
