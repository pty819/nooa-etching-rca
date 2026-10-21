# Linux与ARM64部署

系统支持Linux x86_64与aarch64，并不依赖macOS。执行后端按实际能力选择：优先使用Landlock；未编入Landlock的Linux内核使用bubblewrap的用户、挂载、PID和网络命名空间。两个后端均在生成代码执行前设置seccomp与资源上限。没有可用的受限后端时明确报错。

## 先检查部署宿主

```bash
uname -m
uv sync --python 3.12 --frozen --group dev
uv run --no-sync factor-demo doctor --warm-font-cache
```

`doctor`显示平台、架构、选中的backend和Landlock ABI。内核版本号不能代替功能检查：实测ARM NAS的7.1.8内核返回Landlock不可用，但已安装的bubblewrap 0.11.1可以正常创建所需命名空间。

`--warm-font-cache`在部署阶段预热Matplotlib字体索引，首次扫描可能较慢。每个cell只复制索引JSON到自己的可写缓存，不能修改部署缓存。Linux只读挂载字体配置与fontconfig缓存；图表生成仍在受限worker内完成。user service自动预热，并为首次启动保留600秒，避免首次绘图把cell的计算时限耗在字体扫描上。

无Landlock时安装发行版的bubblewrap包，例如Debian/Ubuntu的`sudo apt-get install bubblewrap`。`FACTOR_LINUX_BACKEND=auto`为默认值，也可在独立测试进程中显式选择`landlock`或`bubblewrap`。强制选择失败不会自动换成无限制执行；主机限制了用户命名空间时，doctor会报告bubblewrap的实际失败原因。

Ubuntu的AppArmor可能还需要为`/usr/bin/bwrap`授予创建用户命名空间的权限，表现为`Failed RTM_NEWADDR: Operation not permitted`。项目提供`deploy/apparmor/bwrap`，语法依据[Ubuntu官方说明](https://documentation.ubuntu.com/release-notes/23.10/)。这是针对bubblewrap可执行文件的权限规则，cell的挂载、PID、网络和seccomp约束仍保留；不是关闭AppArmor或修改全局sysctl。强制后端CI仅在其临时runner安装此规则；NAS实测环境不需要修改系统策略。

## bubblewrap后端的具体行为

每个cell创建独立的空根目录。只挂载解释器、依赖、只读bootstrap、当前选中数据版本和本方向工作区；不将宿主根目录整体交给研究代码。`/lib`、`/lib64`等标准符号链接保留，保证usr-merge发行版的ELF解释器可运行。根目录和输入只读，持久写入限定在工作区，设备和proc为私有运行时视图。

PID命名空间与die-with-parent保证关闭执行器时清理后代；网络命名空间与seccomp共同限制科学进程的外部连接。宿主仍通过原有模型客户端访问API；模型凭据不进入生成代码进程。bootstrap检查只读根标记，随后才加载检查点或运行生成代码。隐藏路径通常返回FileNotFoundError，而Landlock/macOS可能返回PermissionError；只读挂载的写入返回EROFS。这些都是操作被拒绝，测试检查实际不可读/不可写行为。

科学Python、模型比较、分支、检查点、证据重算和Markdown/SSE接口共用这套执行器，不给ARM另做缩减算法实现。后端选择不改变272k上下文、50k输出或thinking配置。

## 构建并启动

代码与依赖应在目标架构重新安装，不能复制macOS的虚拟环境或node_modules。Node24用于前端构建；构建产物是普通静态资源。

```bash
git clone https://github.com/pty819/nooa-etching-rca.git ~/src/nooa-etching-rca
cd ~/src/nooa-etching-rca
uv sync --python 3.12 --frozen --group dev
npm ci --prefix frontend
npm run build --prefix frontend
uv run --no-sync factor-demo doctor
uv run --no-sync factor-demo generate-etch --data data/etching-v3 --runs 360
```

在部署目录配置自己的`.env`或`.secrets/providers.json`。凭据不进入仓库。确认8765端口空闲后，可以安装项目自带的user service：

```bash
mkdir -p ~/.config/systemd/user ~/.local/state/nooa-etching-rca
cp deploy/systemd/nooa-etching-rca.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nooa-etching-rca
systemctl --user status nooa-etching-rca
```

该unit将进程日志写入用户state目录的`nooa-etching-rca/workbench.log`，便于没有user journal的NAS查看。它使用`~/src/nooa-etching-rca`，默认仅监听服务器本机8765。工作目录不同应修改unit；不要移动或重启同机其他项目。若使用`.env`，在unit中显式添加`EnvironmentFile`并重新加载；私有provider JSON由项目直接读取。

从开发机浏览时可以用SSH转发，不需要改动本地现有8765服务：

```bash
ssh -N -L 18765:127.0.0.1:8765 user@your-linux-host
```

打开`http://127.0.0.1:18765`。数据、研究会话、日志和模型配置保持在各自部署目录。运行效果与实际回归记录见[验收记录](quality.md)。

## 持续验证

Runtime checks包含Linux x86_64、[原生Linux ARM64 runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)和macOS，还单独强制bubblewrap运行完整Python回归，避免只验证有Landlock的默认云端环境。真正部署平台仍需执行同一套测试、自由科学代码、真实模型多轮研究与浏览器/SSE验收；不能仅凭runner名称宣布部署成功。

## ARM NAS实测记录

2026-10-19在RK3588、aarch64、Python3.12.13、7.1.8内核上确认Landlock ABI为0，自动选择已安装的bubblewrap0.11.1。使用原生ARM依赖；未复制macOS虚拟环境。首次完整SD卡运行被读写阻塞中断，因此不记为通过；随后把同一依赖和数据放入临时内存目录验证执行链路。临时目录不是持久化部署，也不作为存储性能结论。

真实GLM按专家指令现场编写并运行Polars脚本，划出前两个lot的非参考腔子集，宿主重算为10条；MiniMax独立模型审查通过并交付。随后返回324条的公开发现主集，读取真实trace与step边界，绘制step4的20%—100%时间窗口内`he_edge_pressure_torr`曲线。该轮按可视化任务交付，状态`not_required`，没有强制进入模型比较或根因审查。两次计数均为`raw_recomputed`。

第一次绘图因字体扫描超时，记录保留；预热部署字体索引、复制到各worker缓存后，同一受限执行器实际生成PNG，真实模型重试在102.68秒内完成交付。现场还发现代理`doc()`未接受原生`concise`参数，已补齐参数与多对象调用，并在真实外层CodeAct回归中验证。

两条绘图路径分别验收：Matplotlib由受限worker的独立诊断实际绘图；真实agent重试时自主选择Pillow生成同一数据窗口的图，而不是依赖诊断脚本。第三次成功交付按专家反馈修正重叠标题、纵轴位置与刻度精度，新增`step4_he_readable.png`并保留旧图，耗时177.49秒。

| 验证范围 | 结果 |
|---|---|
| NAS临时内存目录、原生ARM、实际bubblewrap | 完整110项通过，76条依赖警告，939.17秒；新增的7项后端/缓存回归另行通过（包含新符号链接反例） |
| 本机macOS回归 | 完整110项通过；新增缓存反例包含在随后7项定向回归中 |
| `d2d8a90`的[Runtime checks 39161615971](https://github.com/pty819/nooa-etching-rca/actions/runs/39161615971) | x86默认后端、原生ARM默认后端、强制bubblewrap、macOS各111项通过；前端14项通过、构建与依赖审计通过 |
| 同提交的[Sphinx与Pages 39161615956](https://github.com/pty819/nooa-etching-rca/actions/runs/39161615956) | 严格文档构建与发布成功 |

完整NAS回归启动时收集110项；后续新增的缓存符号链接反例单独运行，因此没有把两次运行相加冒称一次111项NAS全量验收。CI在最终代码上完整收集111项。NAS的原始SD卡首次失败、字体超时和后续重试均保留记录。

以上证明Linux ARM上的自由代码、动态划分、主集回退、独立审查及图文交付可以连通，不能据此声称找到物理根因或完成多场景盲评。独立审查仍提示子集选择依据的证据链接与样本粒度说明可进一步加强。

## Kubernetes容器（无Landlock、无嵌套命名空间）

内核<5.13（无Landlock）且容器内不允许bubblewrap创建命名空间时，`auto`会记录警告并回退到`process`后端；也可显式设置`FACTOR_LINUX_BACKEND=process`。该后端不需要任何特权或命名空间：子进程独立进程组运行，施加CPU/内存/文件大小/进程数/打开文件数rlimit与宿主墙钟超时（超时杀死整个进程组），seccomp可用则安装、不可用仅记录警告。**它不做文件系统隔离，完全依赖容器**，因此推荐：

- `readOnlyRootFilesystem: true`，仅把可写工作目录（cell scratch、`TMPDIR`）挂为`emptyDir`；
- `runAsNonRoot: true`与固定`runAsUser`（若必须以root启动，可设`FACTOR_CELL_UID`/`FACTOR_CELL_GID`让cell降权）；
- `allowPrivilegeEscalation: false`、`capabilities.drop: [ALL]`、`seccompProfile: RuntimeDefault`；
- 不要把API密钥以文件形式挂进cell可读路径；cell环境变量已被清空，但同一容器内的文件对cell可读；
- 可选NetworkPolicy仅放行模型网关等必要出口（seccomp已在cell内拒绝`socket`，NetworkPolicy是额外保障）。
- 需要拒绝回退时设置`FACTOR_STRICT_BACKEND=1`。
- cell需访问数据湖时设置`FACTOR_CELL_ALLOW_NETWORK=1`（默认关闭）：seccomp不再拒绝`socket`，并开放DNS/CA配置读取；landlock/bubblewrap/process三种后端均生效。务必配合下方NetworkPolicy限制出口。

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities: {drop: [ALL]}
  seccompProfile: {type: RuntimeDefault}
env:
  - {name: FACTOR_LINUX_BACKEND, value: process}
  - {name: TMPDIR, value: /work/tmp}
volumeMounts:
  - {name: work, mountPath: /work}
# volumes: [{name: work, emptyDir: {sizeLimit: 4Gi}}]
```

数据湖出口NetworkPolicy示例（仅放行数据湖网段端口与集群DNS；按实际CIDR/端口与标签修改，模型网关等其他出口需另行放行）：

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: nooa-egress}
spec:
  podSelector: {matchLabels: {app: nooa-etching-rca}}
  policyTypes: [Egress]
  egress:
    - to: [{ipBlock: {cidr: 10.20.0.0/16}}]   # data lake
      ports: [{protocol: TCP, port: 443}]
    - to:
        - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: kube-system}}
          podSelector: {matchLabels: {k8s-app: kube-dns}}
      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}]
```
