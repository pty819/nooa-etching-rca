# 动态数据集、作用域和多模态代码研究

## 已实现的闭环与边界

CodeActV2可以根据当前问题现场编写任意科学Python，产生子集、切窗、派生列、聚合或多父集融合。宿主把结果登记为不可变DatasetRef，记录父引用、源码摘要、参数、理由、schema、样本身份和文件哈希。当前范围按agent保存，下一轮及进程恢复继续使用同一版本；可以逐级返回先前范围，也可以返回该agent的绑定主集。

这套机制不要求每个任务都比较模型。只画图和描述性表格可以声明`ResearchReply.intent="visualization"`，通过基础数据/证据检查后直接交付；存在假设、活动公式比较或正式选择时不能走这个轻量出口。普通探索和研究结论分别使用exploration/report，正式选优用comparison。

本次按用户明确缩小的范围，**没有接入外部取数服务或知识图谱**。`request_data`只通过统一的AcquisitionProvider协议请求条目，默认明确返回unavailable。宿主未来可注入实现；本次仅用内存测试provider验证契约。已经由用户放在项目data目录的本地输入可通过UI/API登记为新数据，不属于外部拉数。

```{mermaid} diagrams/dataset-runtime.mmd
:caption: 任意代码负责变换，宿主负责版本、血缘、作用域和下游绑定。
:alt: 本地输入通过适配器形成主数据版本，CodeAct动态代码派生不可变子集，独立agent选择栈决定执行器、证据与模型比较的数据范围。
```

## 数据版本与模态适配

`DatasetSchema`声明模态、索引文件、对象ID、标签、独立分组、split、资产路径、时间轴、step边界、可选recipe/控制腔和单位。没有这些概念的普通表格或图文数据不必伪造刻蚀元数据。每个索引ID必须唯一且非空；明确的test/holdout条目不进入发现集。

| 模态 | 输入方式 | 科学Python中的样本 |
|---|---|---|
| table | CSV/Parquet/JSON/JSONL，或带索引的目录 | Polars单行/表格，由任务代码自由组织 |
| timeseries | 样本索引 + 曲线资产 + 可选step表 | 选定通道的Polars时间序列 |
| image | 图片文件/目录，或带标签和group的资产索引 | 单个只读图片Path，可用Pillow等已安装库处理 |
| text | 文本文件/目录，或带资产索引 | 单个只读文件Path |
| mixed | 资产索引指向文件或每样本资产目录 | 一个只读资产Path/目录；代码决定组合方式 |

默认自动识别适合简单输入。复杂模态和角色映射通过dataset.json或API的data_schema显式声明。资产接入不等于已经配备所有媒体的理解模型；可用解码器与算法仍以安装环境和实际代码为准。

数据内容存于case的`.datasets/objects/`，相同字节按SHA256复用；版本树由这些对象构成。源码作为`_transform.py`随派生版本保存，参数和理由进入manifest。relation=subset要求样本身份来自声明的父集；transform/merge允许代码产生新的表示或聚合身份；version沿用父版本family并递增版本号。任意变换的数学/物理正确性不由哈希自动证明。

现有刻蚀输入继续兼容，包括可选参考腔。无参考腔时导出空的同schema控制特征表；声明存在控制腔却缺必要表仍报错。

## 当前范围与恢复

worker内置只读路径变量`DATASET`和引用`DATASET_ID`。所有新代码应从DATASET读当前版本。切换范围会等待当前代码结束、关闭科学worker并重建变量环境，避免沿用旧DataFrame；文件和NOOA对话保留。`run_script`的独立进程也获得这两个变量。

原始主集下为旧脚本保留兼容路径。切换到子集后，旧`../input`路径不能绕过作用域重新读取主集。科学worker不能改写版本目录。根agent和分支拥有独立的选择栈；分支绑定某个DatasetRef后，只能选择其后代，返回root也仅返回该分支绑定的根。

```{mermaid} diagrams/dataset-scope.mmd
:caption: 范围切换持久化，Python变量不跨范围保留。返回操作不会删除版本。
:alt: 主集进入子集和嵌套子集后，可以按栈回退或直接返回绑定主集；进程恢复从SQLite读取同一当前引用。
```

## CodeActV2工具用法

| 工具 | 行为 |
|---|---|
| inspect_dataset | 查看当前/指定版本、schema、预览；可按需读取划分源码 |
| list_datasets | 分页查询已有版本，不切换范围 |
| read_dataset_lineage | 查看父图、变换与来源记录 |
| derive_dataset | 登记已由任意Python生成的目录；默认切换到新子集 |
| select_dataset | 选择已有版本并把原范围压栈 |
| return_dataset | 回到parent或绑定root |
| spawn_research(dataset_ref=...) | 子研究显式绑定版本，默认继承父agent当前版本 |
| request_data | 统一取数抽象；未配置实现时返回unavailable，不访问外部数据/KG |

例如，模型根据用户问题自行写出按lot保留一片的代码：

```python
# Inside a saved workspace script, executed via run_script.
from pathlib import Path
import polars as pl

runs = pl.read_parquet(DATASET / "runs.parquet")
child = runs.sort("run_id").unique("batch_id", keep="first")
out = Path("artifacts/one-wafer-per-lot")
out.mkdir(parents=True, exist_ok=True)
child.write_parquet(out / "runs.parquet")
```

然后在外层python_cell登记结果，而不是把where条件交给固定筛选器：

```python
parent = await self.inspect_dataset()
child = await self.derive_dataset(DatasetDerivation(
    title="每lot一片",
    parents=[parent["id"]],
    output_dir="artifacts/one-wafer-per-lot",
    source="artifacts/build-subset.py",
    reason="专家要求先检查独立lot的稳定性",
    data_schema=DatasetSchema(**parent["schema"]),
    relation="subset",
))
# The next worker sees the child through DATASET.
await self.run_code("import polars as pl; print(pl.read_parquet(DATASET/'runs.parquet').shape)")
await self.return_dataset("parent")
```

子集可以保留父集的相对资产路径，宿主从声明的父版本继承缺失资产；多父集存在同名不同内容资产时要求代码明确物化，防止静默选错来源。新的step窗、融合表或聚合结果应由代码写出相应的资产/schema，并记录参数与理由。

## 与证据及公式比较的共同契约

ResearchReply.dataset_ref标明报告范围；没有显式声明时，研究门禁绑定当前版本。EvidenceRef.dataset_ref可单独指向另一已登记版本，例如返回主集后引用子集证据。身份检查按对应版本执行，不再把所有表都强塞进最初runs.parquet的集合。用户应区分不同范围的结论，不能把子集效应推广到全体。

FormulaPlan.dataset_ref默认取当前范围，在创建时冻结；保存子集文件而未登记/选择不会改变正式评价的输入。之后返回主集、新增资产或产生新版本都不会改写旧计划。新软件环境下的重算仍需符合冻结环境协议，不能用同名文件替换版本。

CLI的autoresearch入口也支持一般本地表格/资产输入；Web新建会话从data目录选择，数据范围页显示当前版本、血缘、源码和预览，运行中可通过对话指导agent切换；空闲时可直接选择/回退。持久SSE重放数据事件，客户端拒绝同会话过期响应。

## 统一取数接口的预留

AcquisitionRequest承载source、criteria、reason、parent_refs、lead_refs及request_id；AcquisitionResult区分supplied/pending/unavailable，提供新增身份、来源和已供应的本地资产。只有宿主注入的provider可以返回实际条目。相同request_id不重复登记已供应结果。默认实现不存在，因此不会声称已经完成取数。

此处没有HTTP/SQL/MCP/KG连接器，没有读取用户知识图谱，也没有用模拟知识记录冒充真实来源。未来实现只需把新的条目交给相同的数据版本登记过程，再由CodeActV2生成关联/合并代码；不能原地覆盖已冻结输入。

## 已验证范围

可执行回归覆盖32→16→8→parent→root、进程恢复、原生CodeAct生成/执行划分脚本、readonly输入、子集引用、分支独立范围、固定计划绑定、CSV/图文资产、本地导入、来源记录、抽象provider与未配置状态、版本哈希改写和响应竞态。确切总数见[验证记录](quality.md)。测试夹具和脚本化模型响应验证框架行为，不等同于陌生生产数据上的自主科学发现。
