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
    config = json.loads(sys.stdin.buffer.readline())
    from sandboxing import install_linux

    install_linux(config["reads"], [config["work"]])
    import cloudpickle
    from cell_proxy import AgentProxy, safe_doc
    from cell_wire import decode, dumps, encode
    from nooa.events import _NO_RETURN
    from nooa.runtime.sandbox.cell_core import run_cell_source
    from nooa.strategies.codeact import _ReturnResultSignal

    registry = models()
    proxy = AgentProxy(config, registry)
    output = sys.stdout.buffer
    namespace = {}
    previous = Path(config["previous"]) if config.get("previous") else None
    if previous and previous.is_file():
        with previous.open("rb") as stream:
            namespace.update(cloudpickle.load(stream))

    def complete(*args, **kwargs):
        if args:
            if len(args) != 1 or kwargs:
                raise ValueError("Use one result or keyword fields")
            raise _ReturnResultSignal(result={"result": args[0]})
        raise _ReturnResultSignal(result=kwargs)

    namespace.update(registry)
    namespace.update(decode(config["bindings"], registry))
    namespace.update(
        self=proxy, doc=safe_doc, return_result=complete, asyncio=asyncio, __name__="__cell__"
    )
    import typing

    from nooa.agentdoc import pformat, pprint

    namespace.update(pprint=pprint, pformat=pformat, typing=typing)
    from types import SimpleNamespace

    namespace["_call"] = SimpleNamespace(
        return_type=registry.get(config["return_type"], dict), method_name=config["method"]
    )
    namespace["python_cell_state"] = lambda: {
        "locals": {k: type(v).__name__ for k, v in namespace.items() if not k.startswith("_")}
    }
    result = await run_cell_source(
        config["code"], namespace, execution_count=config["execution_count"]
    )
    excluded = (
        set(registry)
        | {"self", "doc", "return_result", "asyncio", "__builtins__"}
        | set(config["bindings"])
    )
    state = {}
    for name, value in namespace.items():
        if name.startswith("_") or name in excluded:
            continue
        try:
            cloudpickle.dumps(value)
        except Exception as error:  # noqa: BLE001 - nonserializable locals are not checkpointed
            print(f"Checkpoint omitted {name}: {type(error).__name__}", file=sys.stderr)
            continue
        state[name] = value
    with Path(config["checkpoint"]).open("wb") as stream:  # noqa: ASYNC230 - one isolated cell process
        cloudpickle.dump(state, stream)
    message = {
        "kind": "result",
        "cell": config["cell"],
        "stdout": result.stdout[:32000],
        "stderr": result.stderr[:16000],
        "error": "".join(traceback.format_exception(result.error))[-16000:]
        if result.error
        else None,
        "wrapper_line_offset": result.wrapper_line_offset,
    }
    if result.signal is not None:
        message["signal"] = encode(result.signal.result)
    elif result.returned_value is not _NO_RETURN:
        try:
            message["returned_value"] = encode(result.returned_value)
        except ValueError:
            pass
    output.write(dumps(message))
    output.flush()
    # Checkpoint and JSON result are fully flushed. The fresh child owns no
    # model transport; SDK atexit hooks must not turn a committed cell into a
    # timeout or trigger the same approved tools again. The host reaps its group.
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
