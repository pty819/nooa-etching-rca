# Agent、工具与上下文设计

## 为什么以CodeActV2为主干

本场景需要模型根据中间证据决定下一步，临时编写统计或物理关系代码，并处理执行失败。仅让模型从预定义的几个筛选器中选择，无法覆盖原始曲线检查、估计器实现、批次置换和新因子构造。因此主研究使用原生CodeActV2与通用科学worker，算法自由度保留在研究层。

CodeActV2沿用NOOA代码执行、事件、配置校验与工具机制。项目没有把旧版CodeActStrategy换一个名字，也没有使用自行解析Markdown代码块代替原生调用。方法可用 `_strategy=` 按本轮有效策略覆盖，避免通过修改共享策略对象影响同时运行的其他方法。

```{mermaid} diagrams/agent-loop.mmd
:caption: 主研究循环。确定性门禁和审查在候选交接处运行，正常科学探索仍由agent自由决定。
:alt: 当前任务经CodeActV2调用通用工具与独立分支，候选返回后核验、审查及有界修订，LoopGuard和预算约束探索。
```

## 研究方法和策略组合

| 方法 | 策略 | 作用与限制 |
|---|---|---|
| `research` | CodeActV2 | 自主研究，生成ResearchReply；运行前后有领域钩子 |
| `revise` | CodeActV2 | 针对当前报告与审查问题修订，返回完整候选，不重跑无关实验 |
| `chat` | CodeActV2只读工具 | Web讨论，可读产物、解释和reply，不执行研究或改文件 |
| `discuss` | Predict历史兼容 | CLI只讨论入口，不携带科学执行工具 |
| `review` | PredictStrategy | 审查给定报告和已复算证据，不独立运行实验 |
| `inspect_candidate` | 项目封装原生Reflexion | 只读局部实验；具体状态和原生判词单独返回 |

研究策略默认没有固定总迭代数，但受整轮超时、模型次数/token预算、重复调用保护和单元超时约束。领域校验允许三次重试；这是执行/返回纠错次数，不是无限研究授权。最终审查最多两次定向修订，与这个重试计数不同。

## 公开工具

| 工具 | 输入与输出语义 |
|---|---|
| `run_code(code, timeout_s)` | 在持久科学worker执行任意Python；返回ok、输出、耗时等，并保存实际代码 |
| `write_file(path, content)` | 只写workspace内文本/源码；PNG等二进制应在科学代码里保存 |
| `run_script(path, args, timeout_s)` | 运行已保存Python脚本，参数按字面传递，检查退出码 |
| `list_artifacts`, `read_artifact` | 列出真实产物或读取有界文本；不是凭空声明文件存在 |
| `reply(message_id, brief, image_paths, table_paths)` | 给出精确事实和局限，独立无工具正文请求流式输出中文Markdown |
| `spawn_research`, `list_research`, `wait_research` | 动态拆分问题、查询和等待独立方向 |
| `read_branch_result`, `guide_research` | 读取当前mission对应结果，或投递保留来源的父任务指导 |
| `cancel_research`, `resume_research` | 取消独立分支或用新mission显式恢复同一方向 |
| `inspect_candidate` | 只读审查候选；禁止计算、写研究文件、reply和任务变更 |

`run_code`默认120秒，上限300秒；执行超时会清理worker及后代，下一次需要重建内存状态。外层CodeAct单元超时330秒，局部Reflexion基础单元为60秒且最多六次CodeAct迭代。脚本失败须先检查返回值，不能仅凭工具调用正常返回就宣称实验成立。

## Prompt的职责分配

### 分析库按问题自由选择

Polars是表格读写、筛选和分组的首选，并不是整个研究过程只能使用的库。默认研究依赖包括下面这些能力；选择、组合或使用其他已安装库均由agent根据问题决定，不要求挨个调用。

| 库 | 可用于本场景的任务示例 |
|---|---|
| Polars / pandas | 宽表与目录整理、分组/连接、库间数据适配 |
| NumPy | 数组、线性代数、数值因子和时序窗口 |
| SciPy | 统计检验、信号处理、优化和科学数值计算 |
| scikit-learn | 预处理、PCA、建模、Pipeline和分组交叉验证 |
| aeon | 时序距离、变换、分类、聚类与相关时序方法 |
| statsmodels | 回归、统计推断、残差诊断与时序模型 |
| matplotlib | 保存真实研究图，交付PNG等产物 |

SciPy、aeon和statsmodels是显式项目依赖；实际版本由[锁文件参考](_generated/contracts.md)统一记录，而不要求用户手工拼兼容版本。aeon/Numba对NumPy和SciPy有上限约束，安装会选择整体兼容组合，不能把单独最新版本当作最佳环境。

aeon集合输入通常有case/channel/time三个维度；具体方法必须核对当前API和tags，不能把run和通道轴颠倒。statsmodels的显著性也不会自动解决lot伪重复、序列相关或自适应选择偏差。优先使用成熟实现，但不能把普通PLS冒充OPLS；确需自写算法时仍要做数值一致性检查。

worker为Numba JIT和joblib临时文件指定自身workspace下的缓存路径，继续保留文件/网络限制。核心依赖不等于安装了所有可选extras；遇到缺包先如实说明，由宿主补齐并锁定，不在隔离worker里联网安装。

Agent类文档表达行为契约：以证据产生和修订假设、区分因果层级、使用公开工具、返回结构化引用，并正确理解外层调度环境与科学workspace的区别。领域规则由 `physics` 和 `statistics` 两个TextSkill承载，原生SkillRegistry按实例注册并激活。

物理技能强调固定配方、时段两两对比、设定值与联锁/反馈含义，以及不能用不可观测代理排除真实接触问题。统计技能强调lot独立性、监督选择泄漏、OPLS一致变换、有限置换p值下界、恢复率分母和闭环证据依赖。两者不是固定算法脚本，而是研究和审查必须遵守的解释边界。

## Context与自动摘要

```{mermaid} diagrams/context-budget.mmd
:caption: 原生上下文压缩与动态块。阈值是输入token数，不是输出长度，也不是累计会话token。
:alt: 272000总窗口预留50000输出，可用输入222000，177600触发原生摘要；完整历史仍在SQLite，稳定技能和动态任务分块注入。
```

稳定约束用前缀Context；当前任务、最近十条专家反馈、最多十个近期假设及有界分支状态独立组织。较旧方向通过工具查询，而不是把所有报告无限重复塞进prompt。快照恢复后重新注入运行对象和技能，防止nosnapshot对象缺失或旧Context串用。

TokenBudgetSummarizer保留最近10条事件，目标摘要约8000字符。272000减50000给出222000输入预算，默认阈值是其80%，即177600。摘要调用同样经过预算包装；原始事件仍存NOOA SQLite，摘要不是删除审计证据。

历史运行曾实际触发一次原生摘要；这不能证明所有供应商的272k窗口都已经填满并通过压力测试。GLM当前配置使用thinking开启、effort=max；MiniMax Anthropic路由使用独立thinking budget，不虚构原生effort档位。

## 中文交接的现状

阶段性 `reply` 由agent主动调用；每次内部计算自动留日志，但不自动生成一篇长报告。候选报告有假设、数学证据、替代解释与反证，最终正文经独立撰写器整理，撰写器无工具且不得增加数字或改变证据等级。

尚未将“每次改算必须列传感器全名、step、旧值、新值、修改原因、结论变化”全部变成硬字段。传感器与特征名目前可包含在报告和证据中，但详细程度存在模型自由度。此缺口在[文档自审](quality.md)列为后续工作，不能用prompt要求代替已实现schema。
