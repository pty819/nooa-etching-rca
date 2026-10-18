# 任意特征程序的样本与训练折隔离

## 修复的统计边界

早期实现向单个无标签提取进程提供全部train/dev曲线。虽然可信Pipeline在训练fold内填补、标准化和拟合分类器，特征源码仍能在模块级读取所有曲线来学习群体阈值或PCA。只改dev曲线就能改变训练特征，说明无标签输入本身不能保证预测验证没有泄漏。

当前提供两种自由Python契约。它们都基于冻结的DatasetRef，程序隔离边界覆盖特征生成阶段。

```{mermaid} diagrams/feature-isolation.mmd
:caption: 逐样本提取与可学习算子都保留任意代码，训练/验证边界由宿主执行。
:alt: 纯算子每个进程仅获得一个无标签样本；学得算子仅在fold训练数据拟合，再在单样本进程变换，最后可信Pipeline拟合分类器并计算验证分数。
```

## per_sample：一个进程一个样本

`FormulaSpec.kind="per_sample"`默认。源码定义`extract(sample, steps, params)`，返回一个有限数值或None。时间序列/表格的sample是仅含声明通道/特征的Polars DataFrame；图片、文本和组合资产的sample是只读Path。steps包含该样本对应的step_id/start_s/end_s，可为空。

每个样本的模块加载与执行都在独立进程中，只暴露该样本和必要边界。即使源码用glob读取input目录，也无法读其他训练/验证样本。代码依然可以自由使用已安装科学库、定义函数/类，不提供固定算子白名单。

## learned：在每个训练fold内拟合

`kind="learned"`提供两个函数：

```python
def fit(samples, steps, params, labels=None):
    # samples and steps are dictionaries keyed by opaque sample identities.
    # Only the current fold's TRAINING samples and labels are present.
    return self_contained_state


def transform(state, sample, steps, params):
    # One sample, learned state and no validation/dev labels.
    return scalar_or_none
```

宿主分别对每个lot CV fold及最终train→dev划分拟合状态。变换进程只见自己的样本、已学状态和源码，不可重新读取其他样本。自定义类/闭包和成熟库估计器由cloudpickle在隔离进程内保存和加载；可信HTTP/编排进程不加载任意pickle。状态必须自包含，不依赖已经清理的临时训练目录。

因此同一run在不同fold中可以有不同特征值；保存的矩阵包含fold标记，可信模型执行器按对应fold选择输入。不能用在全部数据上学得的一张全局特征表代替。

## 比较、缓存与重跑

不同模型共享同一冻结特征协议与lot折。已验证的特征缓存按研究/公式版本/fold复用，文件摘要检查后才能使用。正式冻结会绕过特征缓存，从原始输入重新拟合和提取，再要求特征矩阵、逐片预测和分数一致。失败与排除仍保留。

最佳试验保存矩阵、预测、拟合模型、预处理参数、特征定义与最终训练状态；来源脚本、参数、训练身份和DatasetRef都能追踪。内容寻址快照避免重复存储相同产物，写出的可下载副本只在内容变化时更新；导出不持有SQLite写事务。

## 反例与限制

回归使用任意Python先学习`a`的群体均值，再计算`mean(a > 群体均值)`。当前协议下仅改变dev曲线不会改变训练特征；每个CV fit只见训练输入，单样本transform只见一个样本。自定义状态类实际通过保存/加载，模型完成比较和重跑。

这些检查不构成任意源码的数学正确性证明，也不防止人为将已知标签硬编码为特征。专家反复查看dev后调整公式仍会适应开发集；最高分只限已试候选，不能称为独立最终测试或物理根因。外层CodeAct与科学worker都在独立受限进程执行；系统约束不能证明任意算子的数学正确性。
