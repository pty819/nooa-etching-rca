"""One untrusted scientific cell. No host protocol or credential-bearing objects."""

import json
import linecache
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    # The bootstrap alone is trusted; the guard precedes both user code and pickle.
    from sandboxing import install_linux

    install_linux(config["reads"], [config["work"]])
    import cloudpickle

    namespace = {"__name__": "__research__"}
    previous = Path(config["previous"]) if config.get("previous") else None
    if previous and previous.is_file():
        with previous.open("rb") as stream:
            namespace.update(cloudpickle.load(stream))
    sources = namespace.pop("__source_cache__", {})
    sources[config["filename"]] = (
        config["code"] if "code" in config else Path(config["source"]).read_text()
    )
    for filename, text in sources.items():
        linecache.cache[filename] = (len(text), None, text.splitlines(keepends=True), filename)
    namespace.update(
        DATASET=Path(os.environ["RESEARCH_DATASET"]),
        DATASET_ID=os.environ.get("RESEARCH_DATASET_ID", ""),
    )
    sys.path.insert(0, str(Path.cwd()))
    result = 0
    try:
        executing = namespace
        if config.get("script"):
            sys.argv = [config["filename"], *config["args"]]
            sys.path.insert(0, str(Path(config["filename"]).parent))
            executing = {
                "__name__": "__main__",
                "__file__": config["filename"],
                "DATASET": namespace["DATASET"],
                "DATASET_ID": namespace["DATASET_ID"],
            }
        exec(compile(sources[config["filename"]], config["filename"], "exec"), executing)  # noqa: S102 - guarded child only
    except SystemExit as error:
        result = error.code if isinstance(error.code, int) else 1 if error.code else 0
        if error.code is not None and not isinstance(error.code, int):
            print(str(error.code), file=sys.stderr)
    except BaseException:  # noqa: BLE001 - the supervisor reads the actual exit code
        traceback.print_exc()
        result = 1
    excluded = {"__builtins__", "DATASET", "DATASET_ID"}
    state = {}
    dropped = []
    for name, value in namespace.items():
        if name.startswith("__") or name in excluded:
            continue
        try:
            cloudpickle.dumps(value)
            state[name] = value
        except Exception:  # noqa: BLE001 - checkpoint skips nonserializable locals
            dropped.append(name)
    with Path(config["checkpoint"]).open("wb") as stream:
        cloudpickle.dump({**state, "__source_cache__": sources}, stream)
    Path(config["metadata"]).write_text(
        json.dumps(
            {
                "variables": {k: type(v).__name__ for k, v in list(state.items())[-100:]},
                "dropped": dropped[-100:],
            }
        )
    )
    raise SystemExit(result)


if __name__ == "__main__":
    main()
