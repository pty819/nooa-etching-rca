"""Child-side data-only tool proxy. The real agent never enters this process."""

import sys

from cell_wire import decode, dumps, encode, loads

_ACTIVE = None
_MODELS = {}
_INPUT = sys.stdin.buffer
_OUTPUT = sys.stdout.buffer


class RemoteTool:
    def __init__(self, name, description):
        self.name = name
        self.__doc__ = description

    async def __call__(self, *args, **kwargs):
        proxy = _ACTIVE
        proxy.sequence += 1
        sequence = proxy.sequence
        _OUTPUT.write(
            dumps(
                {
                    "kind": "tool",
                    "cell": proxy.cell,
                    "sequence": sequence,
                    "name": self.name,
                    "args": encode(args),
                    "kwargs": encode(kwargs),
                }
            )
        )
        _OUTPUT.flush()
        response = loads(_INPUT.readline())
        if response.get("cell") != proxy.cell or response.get("sequence") != sequence:
            raise RuntimeError("Invalid tool response identity")
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Tool refused"))
        return decode(response.get("value"), _MODELS)

    def __reduce__(self):
        return restore_tool, (self.name,)


class SkillDoc:
    def __init__(self, text):
        self.text = text


class AgentProxy:
    def __init__(self, config, models):
        global _ACTIVE, _MODELS
        self.cell = config["cell"]
        self.sequence = 0
        self.tools = config["tools"]
        self.docs = config["docs"]
        _ACTIVE, _MODELS = self, models

    def __getattr__(self, name):
        if name in self.tools:
            return RemoteTool(name, self.tools[name])
        if name in self.docs:
            return SkillDoc(self.docs[name])
        raise AttributeError("Only declared public tools and skill documents are available")

    def __reduce__(self):
        return restore_proxy, ()


def restore_proxy():
    return _ACTIVE


def restore_tool(name):
    return getattr(_ACTIVE, name)


def safe_doc(*values, concise=False, inline_depth=1):
    """Mirror native doc arguments while exposing only the proxy's public surface."""
    from nooa.agentdoc import doc

    rendered = []
    for value in values:
        if isinstance(value, AgentProxy):
            rendered.append(
                "\n".join(
                    name + "\n" + (text.splitlines()[0] if concise else text)
                    for name, text in value.tools.items()
                )
            )
        elif isinstance(value, SkillDoc):
            rendered.append(value.text.splitlines()[0] if concise else value.text)
        elif isinstance(value, RemoteTool):
            text = value.__doc__ or ""
            rendered.append(text.splitlines()[0] if concise and text else text)
        else:
            rendered.append(doc(value, concise=concise, inline_depth=inline_depth))
    return "\n\n".join(rendered)
