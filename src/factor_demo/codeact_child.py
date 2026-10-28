"""Fresh external CodeAct cell, bootstrapped without a live agent or provider."""

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def models():
    from contracts import dataset_models, formula_models, legacy_models, research_models
    from pydantic import BaseModel

    return {
        k: v
        for module in [research_models, formula_models, dataset_models, legacy_models]
        for k, v in vars(module).items()
        if isinstance(v, type) and issubclass(v, BaseModel) and v is not BaseModel
    }


async def main():
    import contextlib
    import io

    config = json.loads(sys.stdin.buffer.readline())
    from sandboxing import install_linux

    install_linux(config["reads"], [config["work"]])
    import typing
    from types import SimpleNamespace

    from cell_proxy import AgentProxy, safe_doc
    from cell_wire import decode, dumps, encode
    from nooa.agentdoc import pformat, pprint
    from nooa.strategies.codeact import _ReturnResultSignal
    from session_cell import CellCompileError, execute_source

    registry = models()
    proxy = AgentProxy(config, registry)
    output = sys.stdout.buffer
    namespace = {**registry, **decode(config["bindings"], registry)}

    def complete(*args, **kwargs):
        if args:
            if len(args) != 1 or kwargs:
                raise ValueError("Use one result or keyword fields")
            raise _ReturnResultSignal(result={"result": args[0]})
        raise _ReturnResultSignal(result=kwargs)

    namespace.update(
        self=proxy,
        doc=safe_doc,
        return_result=complete,
        asyncio=asyncio,
        __name__="__cell__",
        pprint=pprint,
        pformat=pformat,
        typing=typing,
    )
    namespace["_call"] = SimpleNamespace(
        return_type=registry.get(config["return_type"], dict), method_name=config["method"]
    )
    namespace["python_cell_state"] = lambda: {
        "locals": {k: type(v).__name__ for k, v in namespace.items() if not k.startswith("_")}
    }
    while True:
        proxy.cell, proxy.sequence = config["cell"], 0
        proxy.docs, proxy.tools = config["docs"], config["tools"]
        stdout, stderr = io.StringIO(), io.StringIO()
        message = {
            "kind": "result",
            "cell": config["cell"],
            "wrapper_line_offset": 0,
            "error": None,
            "status": "completed",
        }
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                value = await execute_source(
                    config["code"], namespace, f"Cell In[{config['execution_count']}]"
                )
                try:
                    message["returned_value"] = encode(value)
                except ValueError:
                    pass
            except _ReturnResultSignal as signal:
                message["signal"] = encode(signal.result)
            except BaseException as error:  # noqa: BLE001 - isolated execution boundary
                message["error"] = "".join(traceback.format_exception(error))[-16000:]
                message["status"] = (
                    "syntax_error" if isinstance(error, CellCompileError) else "exception"
                )
        message.update(stdout=stdout.getvalue()[:32000], stderr=stderr.getvalue()[-16000:])
        output.write(dumps(message))
        output.flush()
        if message["status"] == "exception" or "signal" in message:
            break
        raw = sys.stdin.buffer.readline()
        if not raw:
            break
        config = json.loads(raw)
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
