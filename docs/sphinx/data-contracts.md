# 数据模型、文件布局与接口契约

## 从波形到证据的血缘

```{mermaid} diagrams/data-lineage.mmd
:caption: 默认90目标run场景的数据血缘。99项输入哈希是历史验收实例的数量，不是所有新数据集的固定常数。
:alt: 固定配方和硬件状态产生曲线、量测与隐藏状态；公开发现集进入agent，test和真值仅供宿主核验，证据按修订冻结。
```

## 输入文件

| 文件 | 粒度和核心字段 | 使用规则 |
|---|---|---|
| `runs.parquet` | 每run一行：run_id、period_id、batch_id、split、is_control、recipe_sha256、trace_path | run是加工，lot是主要独立批次单位；参考腔不能混入目标腔计数 |
| `steps.parquet` | 每run每step：step_id、step_name、start_s、end_s、samples、各设定值 | 按实际边界切片；不能用标签臆测step |
| `traces/*.parquet` | time_s、step_id及200观测通道 | 默认0.5秒采样；有限值检查及稳态裁窗在研究代码里明确实现 |
| `features.parquet` | 目标run特征宽表，前置元数据后为22000特征列 | 不把标签/批次/结果量测当传感器因子 |
| `control_features.parquet` | 同期参考腔相同schema | 用于共模与硬件指纹对照；不能假定两腔原值完全相同 |
| `outcomes.parquet` | 每run的中心/边缘深度、均匀性等 | 可作发现阶段结果关联；不能作为输入特征伪装预测能力 |
| `catalog.json`, `sensors.json` | 特征和传感器语义、单位、role、step_name | 必须先核对语义；setpoint、controller_output与measurement不同 |
| `recipe.json`, `case.json` | 固定工艺与业务时段含义 | recipe一致与业务标签均不等于训练划分 |

特征名规范为 `sensor__step_NN__statistic`。例如 `he_edge_pressure_torr__step_04__mean` 指第一次加工step的边缘背氦压力均值；它不等于“稳态均值”，因为默认提取器包含该step内全部采样点。

## 结构化研究交接

`ResearchReply`包含 `summary`、`hypotheses`、`evidence`、`artifacts`、`next_experiments`、`questions_for_expert`。`Hypothesis`有独立的id、title、status、mathematical_evidence、evidence_files、evidence_ids、alternative_explanation和falsification_test。

假设状态只允许 `supported / weak / rejected / unresolved`。`supported`表示当前报告声明的证据支持强度，不等于通过独立物理维修验证。系统审查状态另存为 `review_status`，不能复用同一个字段表达这两件事。

```{mermaid} diagrams/contracts.mmd
:caption: 报告、假设、结构化证据和审查问题的关系。图中只列关键字段，完整schema从源码生成。
:alt: ResearchReply包含假设和证据，假设通过ID引用证据；ReviewResult包含问题，问题指向单个claim和多个证据ID。
```

```json
{
  "id": "he-p2-pressure",
  "claim": "示例：某保存表中时段2边缘压力均值",
  "artifact": "artifacts/pressure_by_period.csv",
  "column": "pressure_torr",
  "where": {"period_id": 2},
  "statistic": "value",
  "value": 7.92,
  "unit": "Torr",
  "run_ids": [],
  "recipe_id": "icp_si_fixed_v2"
}
```

这是schema示例，不代表仓库附带了该结果表。`value`要求选择恰好一行；`mean/count/sum/std`等是明确聚合。PNG放在 `artifacts` 作为附件，不能当成CSV列证据；要引用图片大小或哈希，应先保存为表格中的元数据。

`run_ids`被检查是否属于公开run集合；目前不独立重算“这些run确实形成该聚合”的完整成员关系。单位字段保留供审查，验证器不做通用量纲推导。完整字段由[AST契约参考](_generated/contracts.md)生成。

## 会话布局

```text
runs/<case>/
  input/                         # 仅公开发现集
  workspace/artifacts/           # 主研究产物
  workspace/research_cells/       # 实际执行源代码
  conversation.sqlite            # NOOA事件与快照
  ui.sqlite                      # 全局SSE、inbox、request_id
  branches.sqlite                # 分支注册与进程身份
  budget.sqlite                  # mission级token账本
  rounds/round_NNN/revisions/attempt_NN/
  branches/research-<id>/
    input -> 同一公开输入
    workspace/                   # 独立文件与执行worker
    conversation.sqlite
    rounds/
    traces/
```

`input`共享不意味着workspace共享。主研究读取分支公开产物时使用 `branches/<id>/artifacts/...`；种子材料复制到分支自己的 `artifacts/seed/`。每次候选修订冻结已有产物和被引用的分支文件，防止后续同名文件变化悄悄改变旧结论。

## HTTP接口

| 方法与路径 | 行为 |
|---|---|
| `GET /api/config` | 可用provider、数据集、公开能力配置；不返回密钥 |
| `GET /api/sessions` | 研究会话列表及活跃状态 |
| `POST /api/sessions` | 根据已存在数据集导出新研究会话 |
| `GET /api/sessions/{name}` | 历史、产物、分支与最后事件序号 |
| `POST /api/sessions/{name}/messages` | 空闲时开始一轮；运行时投递指定方向指导 |
| `POST /api/sessions/{name}/stop` | 停止整轮及子任务 |
| `GET /api/sessions/{name}/branches` | 持久分支列表 |
| `POST /api/sessions/{name}/branches/{identity}/stop` | 单独停止分支 |
| `GET /api/sessions/{name}/events?after=N` | SSE，兼容Last-Event-ID续传 |
| `GET /api/sessions/{name}/artifacts/{path}` | 受限产物下载与图片显示 |

写请求要求 `X-Research-Client: 1`，并检查存在的Origin。该机制降低跨站写入风险，不是账户登录或公网访问控制。消息包括 `message`、`agent_id`、`provider`、`mode`、`request_id`；同一会话的request_id持久去重。活动分支恢复独占该轮时，向不运行的其他方向发送指导返回409，避免消息被错误消费。

HTTP讨论模式映射为只读CodeActV2 `chat`；CLI历史兼容 `discuss` 仍使用Predict。两者均不应执行新研究代码。源码：[web.py](https://github.com/pty819/nooa-etching-rca/blob/main/src/factor_demo/web.py)、[live_events.py](https://github.com/pty819/nooa-etching-rca/blob/main/src/factor_demo/live_events.py)。
