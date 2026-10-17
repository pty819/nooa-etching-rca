# NOOA Etching RCA

[![Documentation](https://github.com/pty819/nooa-etching-rca/actions/workflows/docs.yml/badge.svg)](https://github.com/pty819/nooa-etching-rca/actions/workflows/docs.yml)

基于 NOOA CodeActV2 的刻蚀腔室异常研究原型：固定配方物理仿真、200路传感器、11个step、22000个派生特征，以及可自由写科学代码的多轮研究 agent。

**[阅读完整需求与设计文档](https://pty819.github.io/nooa-etching-rca/)** · [需求与验收](docs/sphinx/requirements.md) · [系统架构](docs/sphinx/architecture.md) · [Agent设计](docs/sphinx/agents.md) · [算法设计](docs/sphinx/algorithms.md) · [文档自审](docs/sphinx/quality.md)

## 系统能力

- 以原始曲线、step边界和量测为输入，分别研究正常、严重异常和维修后残留三个时段；recipe保持一致。
- 通用 Python/Polars 执行，自主选择统计、时序、OPLS/PCA或其他方法，保留代码、真实输出、失败及修订。
- 原生 LoopGuard、Context/TextSkill、证据前后置校验、Predict 审查与有界定向修订、局部只读 Reflexion。
- 原生 QueueManager/Channel 与独立研究进程、工作区、NOOA存储和恢复组合；默认两个活跃分支。
- 主/子研究、摘要、审查及正文共用 AdmissionBroker 和持久 token 预算。
- Vue 3 + TypeScript + Vite；实际推理与执行输出、独立 Markdown 正文流、图片与CSV表格、SSE恢复和定向专家指导。

当前配置：272000总上下文、50000输出上限、177600输入token摘要阈值。普通工具完整收集参数后执行，不从部分工具参数中截取用户答复。

## 运行要求与复现

完整自由代码研究当前要求 **macOS sandbox-exec、Python3.12/3.13、uv和Node**。文档可在Linux构建，不需要API密钥；这不代表研究执行后端已支持Linux。

```bash
uv sync --python 3.12
npm ci --prefix frontend
npm run build --prefix frontend
cp .env.example .env
# 在本机编辑.env，填写自己的模型服务信息；不要提交真实key。
uv run factor-demo generate-etch --data data/etching-v2 --runs 90
uv run factor-demo serve --host 127.0.0.1 --port 8765
```

打开 <http://127.0.0.1:8765>，创建研究并选择custom provider。生成器拒绝覆盖已有数据。CLI及单分支恢复说明见[操作指南](docs/sphinx/operations.md)。

## 构建Sphinx文档

```bash
npm ci --prefix docs
make docs-check
python3 -m http.server 8877 --directory docs/_build/html
```

文档使用独立 `.venv-docs`、锁定Python/Node依赖和构建期Mermaid SVG，不导入研究运行时、不调用模型。GitHub Actions对PR严格构建，对main部署GitHub Pages；Pages只提供静态文档。

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

当前worker限制不构成恶意agent的完整主机隔离，服务默认只监听本机。不是已标定数字孪生、生产设备规格或现场操作系统。

本项目MIT许可；NOOA及其他依赖保留各自许可，见[第三方归属](THIRD_PARTY_NOTICES.md)。
