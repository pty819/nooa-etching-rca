验证日期：2026-10-17。本地执行平台为macOS、Python3.12，文档环境与研究环境分离。

| 检查 | 本次本地结果 |
|---|---|
| Python完整回归 | 50 passed，0 failed；保留80条上游弃用/类型提示警告 |
| Vue交互回归 | 6 passed，0 failed |
| TypeScript与Vite | 类型检查与生产构建通过 |
| Ruff | src、tests、scripts检查通过 |
| Sphinx | nitpicky与warning-as-error严格构建通过 |
| Mermaid | 构建期实际渲染；最终数量由检查器在Actions日志输出 |
| 本地链接与资产 | 自动扫描无缺失；数量由Actions日志输出 |
| 源仓发布范围 | Git跟踪文件与本地配置key字面值检查通过；未提交原始对话、runs、data、凭据与缓存 |
| 浏览器 | 已检查首页中文导航、架构SVG、布局与溢出，修正首页边距 |

云端工作流与Pages验收以[Actions实际运行](https://github.com/pty819/nooa-etching-rca/actions/workflows/docs.yml)为准；首轮发布后补充已完成的公网检查。这里不预先将尚未执行的云端检查标为通过。
