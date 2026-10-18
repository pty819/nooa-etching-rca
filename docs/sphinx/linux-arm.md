# Linux与ARM64部署

系统支持Linux x86_64与aarch64，并不依赖macOS。执行后端按实际能力选择：优先使用Landlock；未编入Landlock的Linux内核使用bubblewrap的用户、挂载、PID和网络命名空间。两个后端均在生成代码执行前设置seccomp与资源上限。没有可用的受限后端时明确报错。

## 先检查部署宿主

```bash
uname -m
uv sync --python 3.12 --frozen --group dev
uv run --no-sync factor-demo doctor
```

`doctor`显示平台、架构、选中的backend和Landlock ABI。内核版本号不能代替功能检查：实测ARM NAS的7.1.8内核返回Landlock不可用，但已安装的bubblewrap 0.11.1可以正常创建所需命名空间。

无Landlock时安装发行版的bubblewrap包，例如Debian/Ubuntu的`sudo apt-get install bubblewrap`。`FACTOR_LINUX_BACKEND=auto`为默认值，也可在独立测试进程中显式选择`landlock`或`bubblewrap`。强制选择失败不会自动换成无限制执行；主机限制了用户命名空间时，doctor会报告bubblewrap的实际失败原因。

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
mkdir -p ~/.config/systemd/user
cp deploy/systemd/nooa-etching-rca.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nooa-etching-rca
systemctl --user status nooa-etching-rca
```

该unit使用`~/src/nooa-etching-rca`，默认仅监听服务器本机8765。工作目录不同应修改unit；不要移动或重启同机其他项目。若使用`.env`，在unit中显式添加`EnvironmentFile`并重新加载；私有provider JSON由项目直接读取。

从开发机浏览时可以用SSH转发，不需要改动本地现有8765服务：

```bash
ssh -N -L 18765:127.0.0.1:8765 user@your-linux-host
```

打开`http://127.0.0.1:18765`。数据、研究会话、日志和模型配置保持在各自部署目录。运行效果与实际回归记录见[验收记录](quality.md)。

## 持续验证

Runtime checks包含Linux x86_64、[原生Linux ARM64 runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)和macOS，还单独强制bubblewrap运行完整Python回归，避免只验证有Landlock的默认云端环境。真正部署平台仍需执行同一套测试、自由科学代码、真实模型多轮研究与浏览器/SSE验收；不能仅凭runner名称宣布部署成功。
