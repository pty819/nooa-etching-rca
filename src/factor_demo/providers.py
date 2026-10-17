"""Private provider configuration; never print or trace credentials."""

from __future__ import annotations

import json
import os
import subprocess
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


def import_hermes(host: str) -> list[dict]:
    # The remote output is captured directly to a private file, never to the terminal.
    script = r'''
from pathlib import Path
import json,yaml
p=Path('/root/.hermes')
c=yaml.safe_load((p/'config.yaml').read_text())
env={}
for line in (p/'.env').read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1)
        env[k.strip()]=v.strip().strip('"').strip("'")
main=c['model']; fallback=c.get('fallback_providers',[])
out={}
if main.get('provider')=='minimax-cn':
    out['minimax']={'name':'minimax','model':main['default'],
      'base_url':main['base_url'],'api_style':'anthropic',
      'api_key':env['MINIMAX_CN_API_KEY']}
for item in fallback:
    if item.get('provider')=='zai':
        out['glm']={'name':'glm','model':item['model'],
          'base_url':item['base_url'],'api_style':'openai',
          'api_key':env['GLM_API_KEY']}
print(json.dumps(out))
'''
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "python3", "-"],
        input=script, text=True, capture_output=True, timeout=40, check=True,
    )
    providers = json.loads(result.stdout)
    if not providers or any(not p["api_key"] for p in providers.values()):
        raise ValueError("No usable Hermes provider configuration")
    PRIVATE_CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(PRIVATE_CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(providers, f)
    PRIVATE_CONFIG.chmod(0o600)
    return [{k: v for k, v in p.items() if k != "api_key"} for p in providers.values()]


def load_provider(name: str) -> Provider:
    load_dotenv(override=False)
    if name == "custom":
        return Provider(name, os.environ["FACTOR_MODEL"], os.environ["FACTOR_API_BASE"],
                        os.getenv("FACTOR_API_STYLE", "openai"), os.environ["FACTOR_API_KEY"])
    data = json.loads(PRIVATE_CONFIG.read_text())
    return Provider(**data[name])


def probe(provider: Provider) -> dict:
    from .runtime import RuntimeSettings, build_client

    settings = RuntimeSettings.load(Path("configs/runtime.json"))
    client = build_client(provider, settings)
    response = client.call([{"role": "user", "content": "Reply OK."}])
    usage = response.usage
    return {"provider": provider.name, "model": provider.model, "ok": bool(response.content),
            "runtime": settings.public_record(provider),
            "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
            if usage else {}}
