# Vue 3前端、流式协议与多轮交互

## 用户看到的三种实时信息

界面分别显示模型服务实际返回的推理增量、工具/科学代码实际执行状态与输出，以及agent主动发送的中文正文。它们不是同一个事件流语义。模型没有提供推理内容时，界面不应伪造；前端也不把工具参数的一半误当成可执行调用或用户答复。

```{mermaid} diagrams/stream.mmd
:caption: 真实增量与完整工具执行的分工。reply正文是独立的tools=[]模型请求，仍受同一预算约束。
:alt: HTTP启动任务后，研究模型推理和科学worker输出进入SQLite，reply独立生成正文增量及最终附件，经可续传SSE送到Vue。
```

## 为什么单独生成reply正文

研究调用可用stream接收模型推理，但NOOA完整收集python_cell参数后才执行。agent调用 `reply` 时传入brief与真实附件路径，宿主用同一受预算限制客户端发起 `tools=[]` 的普通正文生成；只有这个请求的content增量映射成reply.delta。

这不依赖某供应商的tool_stream开关。vLLM/SGLang兼容节点在协议上可以承载这样的两类请求，但其chat template、reasoning parser、工具解析和usage行为仍需要真实节点验证。当前不能把OpenAI兼容格式的设计理解为已经实测所有自托管模型。

附件在开始正文前检查存在性与合法路径，最终commit附加图片Markdown和CSV表格。PNG/JPEG等来自真实文件；表格是已保存CSV的有界展示，不由正文模型重画。只显示图不代表视觉模型看过图，当前审查没有配置视觉输入。

## 事件契约

每条事件有全局递增 `id`、`task_id`、`agent_id`、`parent_agent_id`、`type`、`data`与时间。根ui.sqlite汇合主研究与各分支，避免多个数据库各自从1开始导致客户端去重冲突。

| 事件族 | 前端语义 |
|---|---|
| task.start/end/error/stopping | 整轮生命周期，不等于每个分支科学结论状态 |
| branch.created/state/stopping/guidance | 方向注册、分支状态与父任务指导 |
| model.start/reasoning.delta/content.delta/stream.end | 实际模型流及结束；研究content不会自动变为用户答复 |
| tool.start/output/end, agent.tool.start/end | 完整调用与实际stdout/stderr，不展示逐token工具参数 |
| reply.start/delta/commit | message_id、正文offset、临时文本和权威最终Markdown |
| validation.issue/passed, loop.guard | 可纠正证据错误或重复动作保护 |
| review.start/end/invalid, reflection.verdict/end | 审查与实际原生反思状态 |
| budget.usage/snapshot/exhausted | 共享预留、已结算和估算用量 |

客户端用task与agent前缀隔离消息、模型调用和代码cell，避免两个分支同样的cell=1或message_id互相覆盖。Unicode offset按JS字符串长度处理并去重；commit覆盖临时正文。中断的未提交片段会明确标记，不伪装成已交接结论。

## SSE恢复和消息幂等

连接使用EventSource；断线通过Last-Event-ID继续，完整刷新则从持久记录重建。服务采用text/event-stream、重试提示、心跳和禁缓存头。前端重放期间避免把历史task.start当成新的活跃任务，避免重复插入结构化历史与同一流式消息。

POST的request_id持久去重，用于防止网络重试重新启动任务。它只覆盖相同请求身份，不等于整个科学实验恰好执行一次。前端请求缓存同时绑定会话、方向和文本，避免切换分支后把相同文字误判为旧请求。

## 可见性与操作

当前方向选择器分别过滤消息、推理、工具和产物。根预算为本轮共享值；分支完成后显示完成状态，不因主研究仍运行而一直转圈。运行中可向有效方向追加指导，已结束方向在本轮结束后可单独继续。单分支停止与整轮停止是两个不同入口。

Markdown使用marked与DOMPurify；本地图片路径映射到受限artifact接口。保存表、源代码和失败可在产物/执行区域查看。前端显示的是模型给出的说明，不保证每个内部单元都有完整长篇人话报告；严格的逐次改算摘要仍属[后续设计](quality.md)。

源码：[useResearch.ts](https://github.com/pty819/nooa-etching-rca/blob/main/frontend/src/useResearch.ts)、[MarkdownView.vue](https://github.com/pty819/nooa-etching-rca/blob/main/frontend/src/components/MarkdownView.vue)、[provider_stream.py](https://github.com/pty819/nooa-etching-rca/blob/main/src/factor_demo/provider_stream.py)。
