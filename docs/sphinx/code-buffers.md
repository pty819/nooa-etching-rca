# 版本化源码、编辑与执行

源码保存在宿主 `.execution/code.sqlite` 的不可变 revision 中，而不是依赖模型记忆中的旧代码块。每个版本有 SHA-256 和 `cell://name@revision` 引用；通过只读 JSON 请求送入受限 worker。代码缓冲区不额外生成可导入的 `.py` 文件，普通 `run_code` 和保存脚本的路径仍可使用。

## 工具与版本约束

| 工具 | 行为 |
|---|---|
| `code_write(name, source, expected_revision=0)` | 新建要求 0；替换要求当前 revision，返回新版本但不执行 |
| `code_edit(name, expected_revision, old, new)` | 只替换一个非空、唯一精确匹配；版本过期或匹配歧义时原子拒绝 |
| `code_read(name, revision=0, offset=0, limit=12000)` | 按字符分页读原文；0 选择最新，返回 hash、source_ref 和 next_offset |
| `code_list()` | 列出每个缓冲区的最新 revision |
| `code_run(name, revision, timeout_s=120)` | 明确正 revision，在主科学 namespace 执行该版本一次 |
| `code_experiment(...)` | 在独立 worker 中执行明确候选/依赖版本和 checks，暂不发布产物 |

实际完整参数与工具说明见[自动生成的工具契约](_generated/tools.md)。

## 改源码不等于改变现有内存

重新定义 `score` 会使这个名称绑定到新函数，但旧别名仍可能指向旧函数，已经计算的结果也不会自动重算。删除源码中的一行不会删除 namespace 里的旧名称；`code_run` 不重放之前的 cell。要保留数据并重新计算，应显式执行新定义和依赖它的计算路径。

编译失败没有运行用户代码，现有函数仍可能是旧版本。运行异常则重置整个受影响内核，旧变量和别名随之失效；下一步必须重建输入与定义，不能假定失败只撤销了当前几行。旧函数调用栈仍可显示真实旧版本的虚拟文件名和源代码行。

## 一个隔离的修复与验收例子

以下是从外层 `python_cell` 调用公开工具的示例。它验证简单公式，不代表实际刻蚀研究结果：

```python
v1 = await self.code_write("score", "def score(x):\n    return x / 0\n")
failed = await self.code_experiment(
    "score", v1["revision"],
    'assert score(PARAMETERS["x"]) == 4', parameters={"x": 2},
)
assert failed["status"] == "failed"

v2 = await self.code_edit("score", v1["revision"], "return x / 0", "return x * 2")
repair = await self.verify_repair(failed["observation_id"], "score", v2["revision"])
assert repair["status"] == "verified"
assert repair["goal_satisfied"] is None

goal = await self.verify_goal(
    "score(2) 应等于 4", repair["experiment_id"], "assert score(2) == 4",
)
assert goal["supplied_criterion_passed"]
```

`verify_repair` 使用原失败 checks，不接受模型用无关的 `assert True` 替换它。目标验收是另一次显式重算；只有模型自己写的目标断言被执行，不代表独立认证全部研究意图。若实验生成了文件，应读取 `code_read_experiment` 核对后再显式 `code_publish`；示例只计算标量，因此无需发布空产物目录。

## 保存、发布与失效

源码历史和执行记录跨轮保留，Python 活对象不跨新研究轮次或数据切换恢复。候选在实验 worker 中定义成功，不会替换主 REPL 的正式函数。需要在主 REPL 激活时仍应明确 `code_run`，并显式重算原结果。

实验来源记录绑定候选及依赖 revision、JSON 参数、选中数据与文件哈希。源码编辑或当前输入变化使旧结果标为 `stale`；旧文件保留审计用途，但证据校验拒绝把它们当成当前结果。限制和失败状态详见[运行协议](repl-runtime.md)。讨论和只读反思可读源码/观察，但不能执行、写入或发布。
