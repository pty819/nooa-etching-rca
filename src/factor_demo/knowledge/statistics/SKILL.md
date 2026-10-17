---
name: evidence-statistics
description: 统计验证和闭环证据审查规则
---

    按问题选择实验，不强制套流程。但必须遵守以下证据标准：
    - 分析库不受Polars限制。优先复用SciPy、scikit-learn、aeon、statsmodels等已安装成熟实现，也可自由组合或编写研究代码。aeon常用集合输入为(n_cases,n_channels,n_timepoints)，具体方法要核对实际API和tags；不要把run/通道/时间维颠倒。statsmodels的回归/时序显著性不能自动消除lot伪重复、序列相关或自适应选择偏差。库的存在不代表其所有可选extras都已安装，缺失应如实报告。
    - 检查recipe、单位、样本粒度、NaN与null、常数及异常缺失。时段和lot可能混杂；重复时间点不是独立样本。优先报告效应方向、量级、独立lot稳定性和不确定性，必要时处理多重选择。
    - setpoint、执行器输出、控制器估算及真实测量各有含义；闭环内压力、阀位、流量的共同变化可能是一条补偿链，不是多份独立证据。名称相似不代表独立取压、独立标定或相同测量原理。
    - step均值不一定等于该step固定设定值；回到时间序列核对切换、延迟、稳定区间和真实需求。异常可能只在瞬态或传感器关系中出现，不因均值恒定就排除。
    - 使用参考腔、同批/同时段对照和维修响应检查共模与机制一致性。高权重、PCA分离、同步变化仅是关联。监督筛选后的PCA只作描述；预测验证时所有筛选和预处理在训练fold内完成。
    - 自写OPLS/其他估计器必须做小型合成检查，并验证fit/transform/predict确实用相同数学变换；区分解释方差与预测能力。置换/自助法以真实独立单位为准；小lot数优先精确枚举，报告可达的最小p值，不把有限次未命中写成p=0。
    - 所有比例说明分子分母、参照状态和单位；“恢复”区分恢复比例、残余比例和超调。控制器达到设定不等于工艺已恢复。保留反向证据，不用恒定的间接估算量排除不可观测的接触/泄漏/校准问题。

    专家提出自定义公式、融合特征或要求比较区分能力时，必须进入公式比较流程，不能只交一张自制分数表：
    - create_formula_study(FormulaPlan(...)) 固定两两label、主指标、按batch_id分组的训练fold、dev和模型套件；先read_formula_study复用同口径研究。改验证口径新建study，不能拼接排名。
    - 自由写artifacts/*.py的extract(trace,steps,params)，返回每run单个数值或None。register_formula(FormulaSpec(...))登记传感器、step、窗口/频带等参数、单位、文字公式和源码快照。任意科学Python均可，不局限算子；输入不含label或outcome。step内20%以后应以steps.start_s/end_s计算阈值，说明边界。mean(a*b)不等于mean(a)*mean(b)。应先构造小反例核对语义。
    - evaluate_formula_set(study_id,["name@1",...])把每个单公式及有意义组合与所有固定模型比较；预处理由程序在fold内拟合。所有登记单公式默认需比较，失败保留，确实不适用时retire_formula_set并给科学理由。不能为抬高分数排除有效候选。
    - 完成比较后freeze_formula_selection会从原始数据重算最佳公式及模型，核对预测和分数；新公式版本使旧选择失效。ResearchReply.formula_comparison=FormulaHandoff(study_id=...,status="selected",selection_id=...)。没完成必须status="incomplete"+reason，不能省略。
    - 报告解释最佳公式具体传感器/step/时间窗、物理意义、失败方法和反证。最高仅指本次候选在固定开发集上的最高，不代表普遍最优、独立测试表现或因果根因。专家据dev多轮调整会适应开发集，必须披露。
