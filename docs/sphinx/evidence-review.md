# 证据门禁、审查与局部自反思

## 确定性检查到底证明什么

`EvidenceVerifier`读取当前公开输入、session哈希和候选报告。它检查发现集是否仅含train/dev、recipe是否唯一、关键输入是否改变；检查产物路径是否位于允许命名空间且不是逃逸符号链接；按EvidenceRef的where和column复核单元格或聚合，并给出actual、文件SHA-256和选择条件。

`supported/weak`假设必须引用结构化evidence_ids，引用ID必须存在；run_ids不得来自公开集合之外。主研究返回时，如果本轮仍有活跃子任务，后置条件拒绝过早最终交接。错误通过原生InvariantError回到研究策略，形成可修正反馈，而不是偷偷删除问题后放行。

```{mermaid} diagrams/evidence.mmd
:caption: 确定性验证、审查者引用修复与研究者修订分开处理。审查者格式错误不能成为科学报告返工的假理由。
:alt: 候选经过范围路径数值和引用检查，冻结证据后审查；无效审查先修复，真实阻断才触发有界研究修订。
```

检查**不能**保证保存表由正确算法从源数据计算而来、run_ids确实构成某个聚合、全部自然语言数字都有引用、单位物理含义正确，或某部件具备因果关系。任意自写OPLS、时序窗口和导出脚本仍需要可复现检查。尤其不能把“引用表中的7.92是真的”扩写为“所有压力和传热推理都正确”。

## Predict审查的输入和输出

输入包包含当前ResearchReply、已复算的数值及哈希、允许的claim/evidence ID、局部反思的实际宿主事件和明确观察范围。review不执行科学代码，所以没有重算的事项必须列为缺口或局限。

ReviewIssue分为evidence、statistics、physics、causality、scope、reproducibility；严重度为blocking或warning。每项要定位具体claim_id，列出已有反证与可执行检查。claim_id只能是一个已有假设/证据ID或summary/scope；多个引用放evidence_ids数组，不能把斜杠连接的字符串当作一个ID。

若审查者引用无效，系统保留原始review，再要求审查者修复一次。仍无效则交接为not_reviewed，而不是让研究者为审查格式反复重写报告。这个边界来自真实GLM验收中暴露的返工问题。

## 修订、状态与发布

| review_status | 含义 |
|---|---|
| passed | 当前审查没有blocking；可以保留warning |
| needs_revision | 尚有blocking且还有定向修订额度 |
| budget_exhausted | 模型预算或修订次数已耗尽，保留受限结果；reason区分来源 |
| not_reviewed | 审查被关闭，或无法形成有效审查引用；不能展示为通过 |
| discussion | 只讨论已有结果，本轮未新做科学审查 |

每个 `attempt_NN` 保存report、verification、review、outcome和对应文件副本。最多两次定向revise；研究状态与审查状态分开。初始生成尚未形成可核验主报告便耗尽预算时，宿主可交付已有分支指针与未完成说明，不拼造主报告的科学结论。

最终发布发生在状态确定之后。前端对阶段性回复标注“待审查”；最终消息携带审查状态。正文撰写器只整理给定事实，无工具且不应新增数字，但其语言生成并没有获得形式化的逐句事实保证。

## 局部Reflexion

```{mermaid} diagrams/reflexion.mmd
:caption: 原生Reflexion的局部只读组合。原生策略在耗尽后可能返回最后候选，应用必须检查实际判词与预算状态。
:alt: inspect_candidate开启只读限制，最多两次基础CodeAct执行及原生反思，保存判词后明确返回通过或未通过。
```

`inspect_candidate`组合原生Reflexion与CodeActV2，只能读已有文本证据。公开执行/写入/发布/任务变更工具均受任务局部限制，防止重试重复发送消息或重新启动研究。宿主在限制之外保存反思审计记录。

最多两次是基础执行次数上限，不是“两个额外重试再加第一次”。`TrackedReflexion`保存真实is_satisfactory、issues和suggestions，同时转接V2动态cell上下文。预算中断、原生耗尽后仍返回候选或实际满意分开记录；候选assessment写PASS不等于原生verdict通过。取消仍传播，不因预算标志被吞掉。

它仍是**局部实验功能**，没有包住整轮research或reply，也没有被证明必然改善真实根因识别率。若需要独立重新计算，应另派有明确任务与独立工作区的研究分支，而不是假装只读反思已经重跑了原始数据。

源码：[evidence.py](https://github.com/pty819/nooa-etching-rca/blob/main/src/factor_demo/evidence.py)、[review_pipeline.py](https://github.com/pty819/nooa-etching-rca/blob/main/src/factor_demo/review_pipeline.py)、[local_reflection.py](https://github.com/pty819/nooa-etching-rca/blob/main/src/factor_demo/local_reflection.py)。
