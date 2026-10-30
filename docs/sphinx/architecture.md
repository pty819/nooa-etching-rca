# 系统架构与执行边界

## 系统上下文

研究服务运行在持有模型凭据的本地主机。浏览器使用HTTP发消息，通过SSE观察后台任务。FastAPI服务管理会话入口、请求去重和worker生命周期，不直接承载长时间的科学计算。每次研究轮次由独立 `web_worker` 执行；CLI可直接进入同一 `research_turn`。

```{mermaid} diagrams/context.mmd
:caption: 运行拓扑。分支各有自己的研究host、科学worker与NOOA存储；模型请求共用本轮Broker。
:alt: Vue经FastAPI连接独立研究host，主agent协调独立分支，共用预算和根事件数据库，科学worker只读公开输入并写自身工作区。
```

浏览器、HTTP服务和研究host具有不同生命周期。浏览器断开只停止观察；不会取消研究。正常关闭HTTP服务会取消活跃任务并清理后代进程。仅HTTP服务崩溃而研究host存活时，新服务验证进程身份后接管观察；不能把一次页面刷新理解为重启整个agent。

## 分层与模块职责

```{mermaid} diagrams/layers.mmd
:caption: 应用分层及依赖方向。数据生成器与自主研究是独立路径，后者不需要获得生成器真值。
:alt: 界面、HTTP服务、研究编排、agent契约、NOOA原生框架、工作区和审计层的依赖关系。
```

| 层 | 主要模块 | 责任 |
|---|---|---|
| 展示 | `frontend/src/App.vue`, `useResearch.ts` | 会话和方向切换、消息、工具、产物、预算与审查状态 |
| HTTP | `web.py`, `web_worker.py` | 目录限定、消息路由、SSE、任务启动/停止/接管 |
| 编排 | `autoresearch.py`, `research_branches.py` | 本轮对象组合、上下文恢复、分支生命周期、最终发布 |
| 研究 | `autoresearch_agent.py`, `strategy_factory.py` | 公开研究方法、工具、V2/审查/反思策略 |
| 证据 | `research_models.py`, `evidence.py`, `review_pipeline.py` | 输出契约、确定性检查、审查与不可变修订 |
| 执行 | `native_sandbox.py`, `execution_observer.py`, `research_workspace.py`, `persistent_kernel.py` | 原生外层能力配置与执行中间件；科学内核另行管理 |
| 源码与实验 | `code_buffers.py`, `code_experiments.py` | 精确revision、冻结输入实验、检查与显式产物发布 |
| 执行观察 | `execution_observations.py`, `execution_provenance.py` | 父子观察、输入/源码/产物来源、原路径复验和目标检查 |
| 请求 | `runtime.py`, `budget.py`, `provider_stream.py` | 模型配置、原生准入、token账本、真实流观察 |
| 数据 | `etch_recipe.py`, `etch_model.py`, `etch_chemistry.py`, `etch_features.py` | 固定工艺、降阶动力学、原始观测及派生特征 |

NOOA提供CodeActV2、Predict/Reflexion、Context/TextSkill、SQLiteStorageManager、QueueManager及AdmissionBroker。项目补的是设备场景知识、证据schema、进程和文件边界、跨进程工具与执行证据桥接、UI身份与交付语义。没有另写一套通用agent运行时。

## 一轮任务的数据与控制流

入口先核对会话，建立本轮共享预算、公开工具和独立工作区；恢复NOOA快照后重新安装运行态对象、技能与上下文。模型生成的外层 `python_cell` 调用应用公开方法。数据查看和连续计算可用 `run_code` / `code_run`；版本候选和公式试错可用 `code_experiment`，保存的脚本可用 `write_file` + `run_script`。科学环境使用只读 `DATASET`、`DATASET_ID` 与各自可写目录；切换数据范围会重建主科学内核，旧变量不再复用。实验先在暂存目录检查，再由宿主显式发布。

候选返回后，应用先核验并冻结证据，再调用原生Predict审查。需要时进入 `revise`，保留每次旧结果。最终结构化答复、审查结果和Markdown先保存，之后原样分段推送规范Markdown。纯可视化通过文件/数值检查后直接交付，不强制科学审查。正文推送失败时保留规范报告及交付状态，不再调用模型重写。

## 两个不同的代码环境

外层NOOA执行环境负责调用公开工具与调度；它的cwd不等于研究workspace。在外层直接 `open('artifacts/...')` 或 `savefig` 可能写到错误位置。科学worker是持久Python进程，cwd才是本方向workspace，可读取本方向选中的不可变数据版本，且不继承API凭据。

同一生成调用的外层 cell 保留自己的 namespace 和事件循环；主科学 `run_code` / `code_run` 则在当前研究轮的另一个内核中复用活对象。二者不直接共享Python对象。科学内核编译失败保留原内存，运行异常、超时、取消、崩溃或清理失败使其失效；外层异常保留或恢复行为遵循NOOA原生执行器。新请求和数据切换也不恢复旧活对象。

`run_script` 使用独立临时内核；`code_experiment` 从显式源码revision、JSON参数与冻结输入建立另一个可丢弃worker。其失败不清空主科学内存，也不能覆盖正式产物。源码版本、执行来源、父子观察、修复关系跨轮保留，继续计算必须显式重建所需输入，不隐式重放副作用。

## 状态为什么分开保存

`conversation.sqlite`保存NOOA事件和快照；根 `ui.sqlite`保存浏览器可重放事件、inbox与请求ID；`branches.sqlite`保存任务注册与生命周期；`budget.sqlite`保存本轮token预留和结算；`.datasets/registry.sqlite`保存不可变数据定义和方向独立的选择栈；`.formula-lab/registry.sqlite`保存公式版本及试验。此外，`.execution/code.sqlite` 保存源码revision，`ledger.sqlite` 记录科学来源，`experiments.sqlite` 保存实验manifest，`observations.sqlite` 保存跨层观察及修复关系。它们是不同职责的持久层，不是一个跨数据库原子事务。

因此系统承诺可恢复文件与可追溯状态，**不承诺强杀时所有副作用exactly-once**。进程身份核对、mission匹配、旧报告过滤和显式恢复用于降低误关联风险；运行日志仍需保留来解释崩溃边界。

完整符号与实际公开方法见[源码索引](_generated/modules.md)。

## 数据范围与科学协议

[动态数据集](dynamic-datasets.md)把现场生成的划分代码、父版本、schema、理由和输出哈希绑定为DatasetRef。主/子方向各自选择范围；分支只能使用派生时分配的根及后代。公式计划和数值证据显式绑定版本，避免返回主集后错用旧统计。文件对象和修订快照采用内容寻址，工作区改写不影响已冻结文件。

[特征隔离](feature-isolation.md)区分逐对象算子与学得的变换，fold训练与开发数据走独立入口。外部拉数仅保留类型化可注入接口，未配置时明确不可用；本次没有连接知识图谱或实际外部数据源。

## 审核后的执行与交付路径

可信主进程仅持有NOOA编排、模型客户端、预算和明确工具实现。模型生成的外层CodeAct使用官方SandboxedExecutor及parent-agent proxy，原生fork worker共享当前生成调用的REPL；项目只在公开execute_python中间件上记录外层观察，不替换执行器。科学Python仍使用独立启动的无凭据长驻内核，脚本和实验另有临时实例。宿主核对完成协议、来源账本和父子观察，不能仅因进程持续或返回零证明科学正确。只读Predict reviewer可使用另一已配置模型，全部请求仍受同一预算约束。最终Markdown由核验结果确定性渲染与推送。留出test/仿真参考仅由单独收尾入口评分。细节与测试映射见[审核问题修复](audit-remediation.md)。
