# 一手资料与归属

## 实现事实的首要依据

当前仓库源码、版本化配置、依赖锁和测试是实现事实的依据。自动生成的[模块索引](_generated/modules.md)与[配置契约](_generated/contracts.md)连到对应源码。NOOA依赖固定为 [NVIDIA-NeMo/labs-OO-Agents的bff0ef4提交](https://github.com/NVIDIA-NeMo/labs-OO-Agents/tree/bff0ef449f9bd74ddbb19486489f95c0edc73831)，其原生策略、存储和控制器机制不归为本项目原创。

外部资料用于说明机制或方法来源，不替代本项目实际验证。不同来源的适用范围如下。

## 设备与物理机制

| 来源 | 本项目如何使用 | 不能据此声称 |
|---|---|---|
| [Lam Akara](https://www.lamresearch.com/product/akara/)与[官方技术介绍](https://newsroom.lamresearch.com/everything-about-akara) | 物种、源响应、通量与能量需要分开考虑 | 已实现DirectDrive/TEMPO/SNAP专有机制或厂商性能 |
| [Lam Kiyo](https://www.lamresearch.com/product/kiyo-product-family/) | ESC、径向均匀性及边缘控制值得单独建模 | 当前模型就是Kiyo，或获得了厂内结构/配方 |
| [Applied Materials Centris Sym3 Y](https://www.appliedmaterials.com/us/en/product-library/centris-sym3-y-etch.html) | 高流导排气、副产物与腔壁状态的机制重要性 | 公共文案给出了可直接标定的速率系数 |
| [Applied Materials Sym3 Z](https://www.appliedmaterials.com/us/en/product-library/sym3z-etch.html) | 能量控制与偏置变量分离的工程动机 | 已实现专有PVT2或真实IEDF |

配方、气相/壁面库存、控制器参数、热接触与噪声均是本项目明确假设。产品资料页面可能改版；方法设计应引用机制与来源，不能根据页面当前营销措辞补造数字。

## 自动特征与研究循环

| 一手工作 | 借鉴方向 | 当前实现状态 |
|---|---|---|
| [CAAFE，NeurIPS 2023](https://papers.neurips.cc/paper_files/paper/2023/hash/8c2df4c35cdbee764ebb9e9d0acd5197-Abstract-Conference.html)，[作者代码](https://github.com/noahho/CAAFE) | 语义、代码特征变换与执行反馈 | 没有复刻整套算法或宣称其工业RCA效果 |
| [LLM-FE](https://arxiv.org/abs/2503.14434)，[作者代码](https://github.com/nikhilsab/LLMFE) | 程序候选、经验与继续搜索 | 本项目保留自身NOOA编排与证据契约 |
| [RD-Agent](https://arxiv.org/abs/2505.15155)，[微软代码](https://github.com/microsoft/RD-Agent) | 假设、实现、执行反馈的持续循环 | 金融验证不能直接迁移为设备根因验证 |
| [OpenFE，ICML 2023](https://proceedings.mlr.press/v202/zhang23ay.html) | 不依赖LLM的自动特征基线 | 可作为后续对照，未在本项目完成系统benchmark |

OPLS/PCA、lot置换与因果边界在本项目中是需要明确实现和验证的统计选择，不因引用一篇自动特征论文就自动正确。旧研究的自写估计器错误及修正见[案例复盘](case-study.md)。

## 科学分析库

- [aeon官方文档](https://www.aeon-toolkit.org/en/stable/)：时序距离、集合变换与学习方法；具体API与可选依赖应按锁定版本检查。
- [statsmodels官方文档](https://www.statsmodels.org/stable/)：回归、统计推断、诊断与时序方法；统计检验本身不等于设备因果识别。

## 文档和发布

- [Sphinx严格构建选项](https://www.sphinx-doc.org/en/master/man/sphinx-build.html)：使用nitpicky与fail-on-warning。
- [Sphinx配置](https://www.sphinx-doc.org/en/master/usage/configuration.html)：中文界面、主题、静态资产与构建行为。
- [sphinxcontrib-mermaid官方仓库](https://github.com/mgaitan/sphinxcontrib-mermaid)：Mermaid指令与SVG输出。
- [Mermaid CLI官方仓库](https://github.com/mermaid-js/mermaid-cli)：构建阶段真实渲染与图语法检查。
- [GitHub Pages自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)：独立构建产物与部署job的权限模型。
- [GitHub Pages发布源配置](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)：workflow发布源与主分支部署。

文档使用Sphinx/Furo/MyST及Mermaid，应用使用NOOA、Vue和科学Python依赖，各上游许可按其原始项目保留；公共仓库不包含上游vendor checkout或node_modules。
