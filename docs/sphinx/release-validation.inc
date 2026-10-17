验证日期：2026-10-17。本地执行平台为macOS；研究回归使用Python3.12，独立文档环境使用Python3.13；GitHub文档构建使用Python3.12。

| 检查 | 本次本地结果 |
|---|---|
| Python完整回归 | 59 passed，0 failed；保留80条上游弃用/类型提示警告 |
| Vue交互回归 | 8 passed，0 failed |
| TypeScript与Vite | 类型检查与生产构建通过 |
| 扩展科学库 | aeon/statsmodels纳入显式依赖；真实worker完成DTW、三维归一化、回归、PCA、SciPy找峰、图表保存及JIT缓存检查 |
| 专家公式流程 | 实际worker与原生CodeAct调用通过；三公式+一组合在现有刻蚀公开数据上完成8次模型比较并重算一致，保留NaN失败与修订；不是新的独立诊断 |
| Ruff | src、tests、scripts检查通过 |
| Sphinx | nitpicky与warning-as-error严格构建通过 |
| Mermaid | 21张图全部实际渲染为SVG；检查其引用、XML与viewBox |
| 本地链接与资产 | 24个HTML页面，全部站内链接/资源引用无缺失；动态数量见Actions日志，不把外部链接或重复属性算作已验收本地链接 |
| 源仓发布范围 | Git跟踪文件与本地配置key字面值检查通过；未提交原始对话、runs、data、凭据与缓存 |
| 浏览器 | 已检查公网首页中文导航、架构SVG、布局与溢出，修正首页边距 |
| 中文搜索回归 | 修复Sphinx的JS构造器不匹配，补jieba及领域词典；生成脚本实际执行通过，四个中文词在索引中，本地浏览器检索“背氦”已返回相关章节 |
| 首轮云端构建/部署 | [运行39005898488](https://github.com/pty819/nooa-etching-rca/actions/runs/39005898488)的build与deploy均success |
| 公网HTTP | 首页、架构、agent、算法、自审页及首页CSS/SVG均200 |

以上云端结果明确对应首次部署；中文搜索缺陷是在首次部署后浏览器实测发现的，随后加入修复和可执行回归。最新提交的云端状态以[Actions实际运行](https://github.com/pty819/nooa-etching-rca/actions/workflows/docs.yml)为准，读者也可直接检索“背氦”验证索引与浏览器逻辑。
