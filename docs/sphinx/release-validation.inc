验证日期：2026-10-18。本地Python3.12与macOS受限执行后端；文档环境Python3.13。以下为审核修复后的执行记录，历史数据和旧报告没有被覆盖。

| 检查 | 实际结果与范围 |
|---|---|
| Python完整回归 | 99 passed，0 failed；76条上游类型/弃用提示保留；其后新增的网关参数回归见下行 |
| 网关、持出评分与规范正文专项 | 专项8 passed，后续网关与编排13 passed；MiniMax Anthropic thinking参数实际HTTP构造、内部路由ID不发送、冻结test模型与重算、规范文本原样交付 |
| Vue交互回归 | 14 passed；新增收尾评估显示、运行中禁用和旧响应丢弃 |
| TypeScript/Vite | 类型检查与生产构建通过 |
| 前端依赖 | 完整npm audit零漏洞；Vitest5.0.5及锁文件更新 |
| 新执行边界 | 真实CodeAct不能访问目录外无害探针文件或self._llm/self._workspace；批准工具仍可用；17MiB输出被限长，伪造成功文本和后续cell不串线 |
| 证据与反证 | 未记录CSV拒绝；原始输入独立重算；已完成predicate实际执行；派生/自由产物不能升级成原始事实；子集不能改值或夹带未选资产 |
| 状态与恢复 | 修订上限、Reflexion不满意、预算用尽分开；结构化非零退出码；审计错误不覆盖原异常；CLI/UI共享操作锁 |
| 真实物理持出链路 | 固定step4后80%边缘He流量算子+Logistic：60片test/12个独立lot，balanced accuracy与macro F1均1.0；因未提交机制候选，机制召回为0。v1因NaN失败保留，v2在首次test评分前修正后冻结；不称为agent盲评或根因成功 |
| 新默认物理数据 | 360目标+72参考、200通道、11步；全部432条原始曲线的recipe与指令波形一致；质量守恒最大误差9.44e-16 |
| 饱和诊断 | 新默认集三组label对比的dev各60片/12lot，仍有3193、1299、2915个单特征完全分离；明确不能用分类满分区分真因和伪相关 |
| 多场景评测准备 | 6个180目标片场景生成完成；含共同漂移与稳定无故障，两者真值机制集合为空。报告尚缺，评分入口明确report_missing，没有伪造agent盲评结果 |
| 真实GLM | 在新执行器实际读取324行发现数据（216train、108dev），一次缩进失败后修正；宿主计数重算为324并交付，未读取test |
| 独立模型审查 | 真实接入发现MiniMax thinking/缓存键参数不兼容并修复，加入HTTP回归；首次审查格式错误，保留not_reviewed而不记passed；MiniMax采用严格JSON传输后，单独审查返回1 warning/0 blocking；第二轮完整GLM→MiniMax链路实际passed/delivered，仍只验证324行计数，不宣称RCA效果 |
| 真值阈值回归 | 3项评估回归通过；表面状态残留变化的参考判定在1/3/5/30片cohort下保持一致，避免浮点均值落在原阈值边界 |
| Ruff | 源码、测试和scripts检查与格式检查通过 |
| 文档 | 严格Sphinx构建、24张实际Mermaid SVG、中文索引执行与站内资源/链接检查通过；具体数量随源码生成索引改变 |
| 浏览器 | 实际重新载入本地服务，核对受限检查点、原始输入重算等级、收尾评估入口及非强制说明 |

Runtime checks新增Ubuntu24.04与macOS15的真实pytest，以及Node24前端测试、构建与审计。此处本地结果不能替代远端平台验收；最新状态以[Runtime checks](https://github.com/pty819/nooa-etching-rca/actions/workflows/tests.yml)和[文档发布](https://github.com/pty819/nooa-etching-rca/actions/workflows/docs.yml)的对应提交为准。
