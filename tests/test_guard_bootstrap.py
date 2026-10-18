"""Linux policy startup imports only attributed guard primitives, not the agent SDK."""

import json
import subprocess
import sys

from factor_demo.sandboxing import child_environment, prepare_guard_modules


def test_guard_bootstrap_is_small_and_never_loads_agent_or_model_client(tmp_path):
    kit = tmp_path / "runtime"
    kit.mkdir()
    prepare_guard_modules(kit)
    code = f"import sys,json;sys.path.insert(0,{str(kit)!r});from _guard_config import LandlockRule;from _guard_rules import apply_landlock;print(json.dumps([x for x in sys.modules if x=='nooa' or x.startswith('nooa.') or x=='litellm']))"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], check=True, capture_output=True, text=True, timeout=20
    )
    assert json.loads(result.stdout) == []
    assert "SPDX-License-Identifier: Apache-2.0" in (kit / "_guard_rules.py").read_text()
    assert child_environment(tmp_path)["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
