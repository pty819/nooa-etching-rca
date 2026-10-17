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
| 执行 | `research_workspace.py`, `research_worker.py` | 持久科学解释器、限制、超时、stdout/stderr与错误 |
| 请求 | `runtime.py`, `budget.py`, `provider_stream.py` | 模型配置、原生准入、token账本、真实流观察 |
| 数据 | `etch_recipe.py`, `etch_model.py`, `etch_chemistry.py`, `etch_features.py` | 固定工艺、降阶动力学、原始观测及派生特征 |

NOOA提供CodeActV2、Predict/Reflexion、Context/TextSkill、SQLiteStorageManager、QueueManager及AdmissionBroker。项目补的是设备场景知识、证据schema、进程和文件边界、跨进程状态桥接、UI身份与交付语义。没有另写一套通用agent运行时。

## 一轮任务的数据与控制流

入口先核对会话，建立本轮共享预算、公开工具和独立工作区；恢复NOOA快照后重新安装运行态对象、技能与上下文。模型生成的外层 `python_cell` 调用应用公开方法。真正的数据计算进入 `run_code` 或 `write_file` + `run_script`，在那里使用 `../input` 与 `artifacts/`。

候选返回后，应用先核验并冻结证据，再调用原生Predict审查。需要时进入 `revise`，保留每次旧结果。最终结构化答复、审查结果和Markdown先保存，之后经独立正文生成流交付。正文生成若因模型预算被拒绝，应用直接发送已保存报告，不虚构逐token进展。

## 两个不同的代码环境

外层NOOA执行环境负责调用公开工具与调度；它的cwd不等于研究workspace。在外层直接 `open('artifacts/...')` 或 `savefig` 可能写到错误位置。科学worker是持久Python进程，cwd才是本方向workspace，可读取同一公开输入，且不继承API凭据。

`run_code`调用之间可复用该worker中的变量；退出、超时或恢复会重建worker，内存数组不会自动恢复。`run_script`启动脚本子进程，不能假定它与交互解释器共享Python变量。恢复依赖脚本、文件和NOOA事件，而不是隐式内存快照。

## 状态为什么分开保存

`conversation.sqlite`保存NOOA事件和快照；根 `ui.sqlite`保存浏览器可重放事件、inbox与请求ID；`branches.sqlite`保存任务注册与生命周期；`budget.sqlite`保存本轮token预留和结算。它们是不同职责的持久层，不是一个跨数据库原子事务。

因此系统承诺可恢复文件与可追溯状态，**不承诺强杀时所有副作用exactly-once**。进程身份核对、mission匹配、旧报告过滤和显式恢复用于降低误关联风险；运行日志仍需保留来解释崩溃边界。

完整符号与实际公开方法见[源码索引](_generated/modules.md)。
