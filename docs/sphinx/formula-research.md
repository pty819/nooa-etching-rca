# 专家公式、多轮比较与最佳候选交接

## 已实现的业务闭环

专家可以在同一会话多轮提出三四种提取办法，agent编写任意Python提取代码，登记版本，计算每片晶圆的特征，并用固定的Logistic和树模型比较单公式及组合。程序保存验证协议、代码、参数、逐片预测、模型及失败记录；全部必要比较完成后，从原始对象重新拟合/提取并计算最佳候选，核对特征和预测一致，再交付中文公式说明和排名。

这一能力由`formula_lab.py`执行，不是要求模型自行写一张“最高准确度”表。自由`run_code`仍然存在；只有进入公式研究的比较受此契约约束。通用探索中的每一个内部计算单元并不自动变成受审计的公式试验，不能扩大这一实现的保证范围。

```{mermaid} diagrams/formula-flow.mmd
:caption: 从专家想法到可重算的最佳公式；失败和未完成均有明确出口。
:alt: 专家提出公式，agent登记源码版本，程序固定lot验证计划、隔离提取、训练模型和计算分数，重算最佳候选后通过交接门禁发布报告。
```

## 按本轮任务需要启用

普通画图、查看分布、解释已有结果、探索一个自定义特征，可以直接使用run_code和读取工具，不要求创建公式研究、训练模型、完成历史比较或重算最佳结果。只有本轮创建、修改或执行公式比较，或者调用begin_formula_comparison明确继续旧研究，才启用本轮的公式交接门禁；单纯read_formula_study不启用。

门禁状态不写入历史研究库或NOOA快照，每轮开始重置；历史公式、失败与模型结果完整保留。已启用的比较可以明确交付incomplete，而不必强行完成计算。即使本轮未启用，若最终报告显式声称某个冻结选择有效，程序仍核验该选择。基础文件与证据校验继续适用。

## 公式是原始序列上的程序

每个`FormulaSpec`包含名称、文字表达式、物理解释、传感器、step、参数、单位和源码路径。默认`kind=per_sample`的源码提供`extract(sample, steps, params)`，返回该对象一个数值或显式缺失`None`；时序sample是传感器表，普通表格是单行表，图片/文本是该对象的Path。学得的算子使用`kind=learned`及`fit(samples, steps, params, labels)`、`transform(state, sample, steps, params)`；宿主在每折训练部分独立拟合。完整协议见[特征隔离](feature-isolation.md)。算法可以使用任意已安装的科学Python库；没有把专家限制在均值、比值等少数算子中。标量列表可组成多列特征集，分别计算或联合建模。

例如step 2从20%时间开始的两个传感器乘积均值：

$$t_0=t_{start}+0.2(t_{end}-t_{start}),\qquad
f=\operatorname{mean}_{t\in[t_0,t_{end})}(a(t)b(t)).$$

```python
import polars as pl


def extract(trace, steps, params):
    step = steps.filter(pl.col("step_id") == 2).row(0, named=True)
    start = step["start_s"] + params["start_fraction"] * (
        step["end_s"] - step["start_s"]
    )
    window = trace.filter(
        (pl.col("step_id") == 2)
        & (pl.col("time_s") >= start)
        & (pl.col("time_s") < step["end_s"])
    )
    if window.is_empty():
        return None
    return window.select((pl.col("sensor_a") * pl.col("sensor_b")).mean()).item()
```

这个公式不同于`mean(a) * mean(b)`。实际回归夹具令a、b在两类中均为零均值，但相关方向相反：均值乘积只能得0.5的balanced accuracy，乘积均值可以得1.0。窗口测试还验证20%以后得到±1.5，而不是整步的±2。这是特意构造的语义检查，**不是生产数据准确率**。

每个per_sample提取进程只获得一个对象；时序对象只含声明step中的`time_s`、`step_id`和传感器列，步骤表只含`step_id/start_s/end_s`，文件使用随机标识。它不获得label、outcome、lot、原始run名称、其他样本或完整公开输入。learned的fit只获得该折训练对象及训练标签，transform仍逐对象隔离；验证/开发对象不进入fit。受保护case目录在临时目录下也不可读取。研究agent在自由探索阶段仍可看公开标签，所以这不防止人为把已知标签硬编码进公式；任意Python与文字表达式也没有形式化等价证明，仍需专家核对和反例检查。

## 固定比较口径

`FormulaPlan`在创建时冻结以下内容。修改label、主指标、模型套件、数据或软件环境必须新建研究，不能把分数拼在同一排名中。

| 项目 | 程序实际约束 |
|---|---|
| 对比范围 | 两个不同label、非control、公开train/dev；刻蚀数据保持单一recipe，其他模态按声明schema |
| 数据版本 | 固定DatasetRef及文件SHA256；切回主集不改变已建研究，登记、评价、冻结和交接复核 |
| 独立单位 | schema的group列映射为独立组；刻蚀为batch_id/lot，禁止train/dev共组 |
| 验证折 | 训练集StratifiedGroupKFold；持久保存每折run ID，训练/验证均须有两类 |
| 主指标 | balanced accuracy默认；可在创建时改为accuracy或macro F1 |
| 模型预算 | 每个单公式及登记组合均执行研究内相同模型套件、种子和参数 |
| 预处理 | learned算子及中位数填补在每折训练集拟合；Logistic额外在每折训练集拟合标准化 |
| 选优 | dev主指标降序、lot CV均值降序、CV标准差升序、公式数量升序、稳定试验ID |
| 环境 | Python、NumPy、Polars、SciPy、scikit-learn、joblib、cloudpickle版本及可信执行器源码哈希 |

固定模型包括LogisticRegression、RandomForestClassifier、ExtraTreesClassifier和HistGradientBoostingClassifier，默认前两种。当前登记流程使用宿主设定的固定参数，不提供任意自定义估计器或自动超参搜索；其他模型可以先在自由研究代码中探索，进入统一选优流程需扩展并验证执行器。

这里有意把模型拟合与公式程序分开：自定义代码只输出数值，独立的可信执行器拟合模型，宿主根据逐片预测重新计算分数，不接受agent提交的手填分数。训练CV提供lot稳定性信息；dev参与选择。因为专家会反复看dev结果调整公式，开发集会被适应，不能把它称为最终独立测试。

[scikit-learn的分组分层划分说明](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.StratifiedGroupKFold.html)解释了保留组边界与尽量保持类别比例的关系；[Pipeline文档](https://scikit-learn.org/stable/modules/generated/sklearn.pipeline.Pipeline.html)说明预处理与模型串接机制。实际折内拟合、类覆盖和lot重叠约束以本项目执行代码与测试为准。

## 多轮版本、失败和恢复

相同名称再次登记得到`name@2`，必须填写`change_reason`；旧代码、参数、结果和选优历史保留。所有版本默认成为必需的单公式比较；多公式组合通过`evaluate_formula_set`另外登记。某版本不适用时，必须显式排除整个特征集并记录理由；没有理由的排除被拒绝。不能为了提高分数删掉有效竞争候选。

```{mermaid} diagrams/formula-state.mmd
:caption: 比较状态与版本变更。旧选择保留为历史，但不能作为当前交接。
:alt: 公式登记后待比较，可运行成功、失败或中断；重试只补缺失模型，所有有效候选完成后重算并冻结，新版本或新组合使选择失效。
```

同一case的主/子agent共用SQLite公式登记库，但各自的自由工作区不合并。短事务领取同一特征集的模型任务，重复调用不会重复执行已成功模型，也不会与正在运行的同一比较并发。中断保留失败状态；重试检查宿主进程身份后补缺失模型。它不承诺任何自由代码副作用exactly-once。

可信快照存于case的`.formula-lab/`，不在研究worker可写目录内。可下载的`artifacts/formula-lab/`是宿主重建的公开副本，修改它不会改变规范分数。成功试验保存输入特征矩阵、逐片预测、拟合模型、模型系数/重要度、填补与标准化参数及哈希。服务仅下载joblib，不在HTTP进程加载任意pickle。

## 交接的程序门禁

1. `create_formula_study(FormulaPlan(...))`创建固定协议；后续轮先读取已有研究。
2. `register_formula(study_id, FormulaSpec(...))`冻结原始序列提取程序与解释。
3. `evaluate_formula_set(study_id, ["product@1"])`比较单公式；传多个版本则比较组合。
4. 所有有效特征集乘模型套件均成功后，`freeze_formula_selection`从原始数据重算第一名，要求特征、逐片预测及分数一致。
5. 返回`ResearchReply.formula_comparison=FormulaHandoff(study_id=..., status="selected", selection_id=...)`。未完成必须显式`status="incomplete"`并说明原因；本轮已启用比较时遗漏字段、引用失效选择或关键产物哈希不一致均不能交接。

新版本、组合、排除或试验状态变化会使当前选择失效；旧选择仍保存。无法稳定重跑的随机公式不会被确认。Predict审查获得宿主核验的公式证据包；最终Markdown中的公式、传感器、step、参数、排名和下载链接由宿主追加，即便正文模型遗漏比较表，最终提交消息仍包含规范记录。前端“公式比较”页恢复同一SQLite状态，并显示失败和排除理由。

**局限**：重跑一致说明指定数据和程序下的重复性，不代表算法正确、生产泛化或因果归因。胜者只限当前研究的已试候选；不同label对、指标或模型协议保持独立。未纳入登记的任意探索、每次内部改算的完整中文差异报告、未见真实工厂场景的效果，仍不能冒充已验收能力。

## 可执行验证

`tests/test_formula_lab.py`覆盖真实macOS隔离worker、融合语义与20%窗口、单公式/组合和四类模型、lot折、输入变更、缺失比较阻断、失败保留与显式排除、版本失效、最佳重跑、随机公式重跑拒绝、公开副本改写、规范预测改写、NOOA CodeAct实际工具调用、只读Reflexion约束和只读HTTP下载。NOOA测试使用脚本化模型响应以固定调用，不声称进行了新的独立大模型科学发现。

Vue测试检查等待与已重跑状态、版本、分数、失败和排除理由；完整系统原有回归继续执行。最新计数见[发布验证记录](quality.md)。

本轮触发回归另外覆盖：历史未完成研究不阻塞新的原生CodeAct画图任务；只读不启用；显式继续后必须交接；新轮重置；普通任务预算耗尽不携带历史公式交接；未启用时显式选优声明仍会被校验。

新增`test_learned_features.py`实际使用自定义状态类，验证训练折fit、开发分布变化不改变训练特征和最佳重算；`test_multimodal_features.py`分别对表格、图片、文本实际提取、评价和冻结。它们是确定性工程夹具，不是泛化效果证明。
