"""Execute cells directly in one namespace, including await and top-level return."""

import ast
import inspect
import linecache


class CellCompileError(Exception):
    """Compilation failed before any user code executed."""


class CellReturn(BaseException):
    def __init__(self, value):
        self.value = value


class Returns(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        return node

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Return(self, node):
        return ast.copy_location(
            ast.Raise(
                exc=ast.Call(
                    func=ast.Name(id="__cell_return__", ctx=ast.Load()),
                    args=[node.value or ast.Constant(None)],
                    keywords=[],
                ),
                cause=None,
            ),
            node,
        )


async def execute_source(source, namespace, filename):
    """Compile before mutation. Runtime errors retain no promised rollback."""
    try:
        tree = ast.parse(source, filename=filename)
        tree = Returns().visit(tree)
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = tree.body[-1]
            tree.body[-1] = ast.copy_location(
                ast.Assign(
                    targets=[ast.Name(id="__cell_value__", ctx=ast.Store())], value=last.value
                ),
                last,
            )
        ast.fix_missing_locations(tree)
        compiled = compile(tree, filename, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except SyntaxError as error:
        raise CellCompileError(str(error)) from error
    linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
    namespace["__cell_return__"] = CellReturn
    namespace.pop("__cell_value__", None)
    try:
        result = eval(compiled, namespace)
        if inspect.isawaitable(result):
            await result
    except CellReturn as returned:
        return returned.value
    finally:
        namespace.pop("__cell_return__", None)
    return namespace.pop("__cell_value__", None)
