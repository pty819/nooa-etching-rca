# 证据门禁、审查与局部自反思

## 确定性检查到底证明什么

`EvidenceVerifier`读取当前公开输入、session哈希和候选报告。它检查发现集是否仅含train/dev、recipe是否唯一、关键输入是否改变；检查产物路径是否位于允许命名空间且不是逃逸符号链接；按EvidenceRef的where和column复核单元格或聚合，并给出actual、文件SHA-256和选择条件。

`supported/weak`假设必须引用结构化evidence_ids，引用ID必须存在；run_ids不得来自公开集合之外。主研究返回时，如果本轮仍有活跃子任务，后置条件拒绝过早最终交接。错误通过原生InvariantError回到研究策略，形成可修正反馈，而不是偷偷删除问题后放行。

```{mermaid} diagrams/evidence.mmd
:caption: 确定性验证、审查者引用修复与研究者修订分开处理。审查者格式错误不能成为科学报告返工的假理由。
:alt: 候选经过范围路径数值和引用检查，冻结证据后审查；无效审查先修复，真实阻断才触发有界研究修订。
```

## 来源、重算与完成的反证

每个EvidenceRef绑定DatasetRef及宿主记录的成功cell或current已检查实验的输入/源码/产物来源。实验发布后若源码/输入过期或文件被改写，`artifact_proof`拒绝其当前证据引用；未记录CSV不能被当成执行结果。来源等级明确区分：`exploratory_reproducible`仅表示该代码产生了该表；`derived_recomputed`只核对派生输入；`raw_recomputed`由宿主从原始版本按声明的文件、列、过滤、step时间比例及聚合独立计算；`formula_recomputed`从保护的正式模型预测核对指标。

`materialize_raw_evidence`支持原始计数、单值、mean/median/std等以及乘积/step窗口。它不执行传入源码，单位可与传感器目录核对。任意自写OPLS或复杂算子仍通过自由Python探索与正式特征流程表达，不假装宿主能证明任意程序语义。

Hypothesis.checks明确planned/completed、evidence_id、比较关系和阈值；completed由宿主针对独立重算证据执行。supported需要完成且通过的检查，rejected需要已完成的失败条件，未执行的反证只能保留计划或弱结论。falsification_test和alternative_explanation保留解释用途，文本本身不能证明实验执行。

这些检查仍**不能**保证全部自然语言数字都有引用、任意算法都正确，或某部件具备因果关系。正文将研究者说明与确定性事实表分开显示。

## 自由代码的检查与报告门禁不同

`code_experiment`的checked只代表提交的checks执行完成；`verify_repair`验证同一输入上的原失败路径，`verify_goal`只验证一个具名断言。这些状态不会替代EvidenceRef的来源/数值核对、正式公式模型指标复算、Predict审查或独立test评分，也不会自动让报告中的所有自然语言主张通过。完整的候选→复验→目标→显式发布流程见[执行运行协议](repl-runtime.md)。

## Predict审查的输入和输出

审查使用另一个已配置模型的只读Predict agent；缺少不同模型时明确not_reviewed，不冒充独立审查。全部审查请求仍计入同一共享预算。输入包包含当前ResearchReply、已复算的数值及哈希、允许的claim/evidence ID、局部反思的实际宿主事件和明确观察范围。review不执行科学代码，所以没有重算的事项必须列为缺口或局限。

ReviewIssue分为evidence、statistics、physics、causality、scope、reproducibility；严重度为blocking或warning。每项要定位具体claim_id，列出已有反证与可执行检查。claim_id只能是一个已有假设/证据ID或summary/scope；多个引用放evidence_ids数组，不能把斜杠连接的字符串当作一个ID。

若审查者引用无效，系统保留原始review，再要求审查者修复一次。仍无效则交接为not_reviewed，而不是让研究者为审查格式反复重写报告。这个边界来自真实GLM验收中暴露的返工问题。

## 修订、状态与发布

| review_status | 含义 |
|---|---|
| passed | 当前审查没有blocking；可以保留warning |
| needs_revision | 尚有blocking且还有定向修订额度 |
| budget_exhausted | 实际模型预算耗尽，保留受限检查点 |
| revision_limit | 定向修订次数用完仍有阻断，不是预算耗尽 |
| not_required | 当前可视化/讨论任务无需研究审查 |
| not_reviewed | 审查被关闭，或无法形成有效审查引用；不能展示为通过 |
| discussion | 只讨论已有结果，本轮未新做科学审查 |

每个 `attempt_NN` 保存report、verification、review、outcome和对应文件副本。最多两次定向revise；研究状态与审查状态分开。初始生成尚未形成可核验主报告便耗尽预算时，宿主可交付已有分支指针与未完成说明，不拼造主报告的科学结论。

最终发布发生在状态确定之后。前端对阶段性回复标注“待审查”；最终消息携带审查状态。规范正文由宿主原样分段推送，不再交给第二个模型重写。审查未通过或未完成时发布受限检查点并降低支持状态。

## 局部Reflexion

```{mermaid} diagrams/reflexion.mmd
:caption: 原生Reflexion的局部只读组合。原生策略在耗尽后可能返回最后候选，应用必须检查实际判词与预算状态。
:alt: inspect_candidate开启只读限制，最多两次基础CodeAct执行及原生反思，保存判词后明确返回通过或未通过。
```

`inspect_candidate`将同一宿主 `execution_context()` 的快照作为 `execution_evidence` 参数交给原生Reflexion与CodeActV2组合，包含近期父子观察、未解决失败和原路径/目标验证记录；可用 `read_observation` 读取完整归档，只能读已有文本证据。公开执行/写入/发布/任务变更工具均受任务局部限制，防止重试重复发送消息或重新启动研究。宿主在限制之外保存反思审计记录。

最多两次是基础执行次数上限，不是“两个额外重试再加第一次”。`TrackedReflexion`保存真实is_satisfactory、issues和suggestions，同时转接V2动态cell上下文。预算中断、原生耗尽后仍返回候选或实际满意分开记录；候选assessment写PASS不等于原生verdict通过。取消仍传播，不因预算标志被吞掉。

它仍是**局部实验功能**，没有包住整轮research或reply，也没有被证明必然改善真实根因识别率。若需要独立重新计算，应另派有明确任务与独立工作区的研究分支，而不是假装只读反思已经重跑了原始数据。

源码：[evidence.py](https://github.com/pty819/nooa-etching-rca/blob/main/src/factor_demo/evidence.py)、[review_pipeline.py](https://github.com/pty819/nooa-etching-rca/blob/main/src/factor_demo/review_pipeline.py)、[local_reflection.py](https://github.com/pty819/nooa-etching-rca/blob/main/src/factor_demo/local_reflection.py)。
