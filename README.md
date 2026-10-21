# NOOA Etching RCA

[![Documentation](https://github.com/pty819/nooa-etching-rca/actions/workflows/docs.yml/badge.svg)](https://github.com/pty819/nooa-etching-rca/actions/workflows/docs.yml)

基于 NOOA CodeActV2 的工业过程与多模态数据研究原型。核心按数据schema和专家问题执行，可用于不同工艺的数据探索；仓库附带一个固定配方刻蚀仿真案例（200路传感器、11个step、22000个派生特征）。

**[阅读完整需求与设计文档](https://pty819.github.io/nooa-etching-rca/)** · [需求与验收](docs/sphinx/requirements.md) · [系统架构](docs/sphinx/architecture.md) · [Agent设计](docs/sphinx/agents.md) · [算法设计](docs/sphinx/algorithms.md) · [文档自审](docs/sphinx/quality.md)

## 系统能力

- 按实际数据声明对象、标签、分组、曲线、step、配方和单位；不预设工艺、标签数量、传感器清单或步骤。附带刻蚀案例使用三个业务时段和固定recipe。
- 通用 Python 执行，可自由组合 Polars、NumPy、SciPy、scikit-learn、aeon、statsmodels、pandas、matplotlib及其他已安装库；Polars仅为表格首选。自主选择统计、时序、OPLS/PCA或其他方法，保留代码、真实输出、失败及修订。
- [专家公式研究](docs/sphinx/formula-research.md)：原始序列任意Python算子、跨传感器融合、多轮版本、固定lot验证、Logistic/三类树模型、最佳重算与中文排名交接；Vue独立比较页。
- 原生 LoopGuard、Context/TextSkill、证据前后置校验、Predict 审查与有界定向修订、局部只读 Reflexion。
- 原生 QueueManager/Channel 与独立研究进程、工作区、NOOA存储和恢复组合；默认两个活跃分支。
- 主/子研究、摘要、独立模型审查共用 AdmissionBroker 和持久 token 预算；正文直接推送已核验规范文本。
- Vue 3 + TypeScript + Vite；实际推理与执行输出、独立 Markdown 正文流、图片与CSV表格、SSE恢复和定向专家指导。

当前配置：272000总上下文、50000输出上限、177600输入token摘要阈值。普通工具完整收集参数后执行，不从部分工具参数中截取用户答复。

## 动态数据集

支持CodeActV2任意代码派生、不可变版本/血缘、独立agent范围与恢复、子集/主集切换、表格/曲线/图文资产接入。证据与公式计划绑定DatasetRef；学得特征在训练fold内拟合。Vue提供数据范围面板。外部取数仅预留统一抽象接口，默认不可用；本次没有数据源或知识图谱连接器。见[动态数据集设计](docs/sphinx/dynamic-datasets.md)与[特征隔离](docs/sphinx/feature-isolation.md)。

## 运行要求与复现

自由代码执行使用 **macOS sandbox-exec或Linux（Landlock／bubblewrap＋seccomp），Python3.12/3.13、uv和Node24**。缺少受限执行机制时拒绝执行。Linux/macOS运行回归由独立Runtime checks工作流检查；文档构建不替代运行验收。

```bash
uv sync --python 3.12
npm ci --prefix frontend
npm run build --prefix frontend
cp .env.example .env
# 在本机编辑.env，填写自己的模型服务信息；不要提交真实key。
uv run factor-demo generate-etch --data data/etching-v3 --runs 360
uv run --env-file .env factor-demo serve --host 127.0.0.1 --port 8765
```

打开 <http://127.0.0.1:8765>，创建研究并选择custom provider。生成器拒绝覆盖已有数据。CLI及单分支恢复说明见[操作指南](docs/sphinx/operations.md)。

ARM64与未启用Landlock的Linux部署见[Linux部署指南](docs/sphinx/linux-arm.md)，可用 `factor-demo doctor` 查看实际执行后端。内核<5.13且禁止嵌套命名空间的Kubernetes容器会自动回退到无特权的`process`后端（rlimit+尽力seccomp，文件系统隔离依赖容器），推荐的Pod配置见同一指南。

## 构建Sphinx文档

```bash
npm ci --prefix docs
make docs-check
python3 -m http.server 8877 --directory docs/_build/html
```

文档使用独立 `.venv-docs`、锁定Python/Node依赖和构建期Mermaid SVG，不导入研究运行时、不调用模型。GitHub Actions对PR严格构建，对main部署GitHub Pages；Pages只提供静态文档。

修复设计、证据等级和收尾test评分见[审核问题修复](docs/sphinx/audit-remediation.md)。旧版生成与演示入口移至显式 `factor-demo legacy` 命令。

## 验证与解释边界

```bash
uv run pytest -q
uv run ruff check src tests scripts
npm test --prefix frontend
npm run build --prefix frontend
make docs-check
```

模型输出是可复核异常证据和机制候选。保存表数值通过检查不等于任意自写算法全部正确，Predict审查通过不等于物理根因证实。Reflexion仍为局部实验功能。历史研究找中了边缘背氦异常，但没有独立确认全部隐藏接触退化；旧报告仍有已披露数字口径问题。详见[科学案例复盘](docs/sphinx/case-study.md)。

## 公开范围与许可

仓库提供源码、生成器、测试、配置与详细设计；不上传已有私有对话、数据集、研究数据库/trace、模型key或本地缓存。公开生成器包含故障构造逻辑，因此严格盲评应只给agent公开发现集并隔离提示作者与真值，不可给它全仓权限后仍宣称盲评。

外层CodeAct与科学代码均在无凭据受限子进程运行，保留自由编写科学代码的能力。服务默认只监听本机。不是已标定数字孪生、生产设备规格或现场操作系统。

本项目MIT许可；NOOA及其他依赖保留各自许可，见[第三方归属](THIRD_PARTY_NOTICES.md)。
