# 执行、数据与凭据边界

本章描述本地研究程序如何约束生成代码，防止误读真值、改写其他任务或让计算结果串线。这是项目自身的运行边界，不涉及对外部系统操作。

```{mermaid} diagrams/trust.mmd
:caption: 编排持有模型凭据；两类生成代码都在独立受限子进程执行，科学输出由宿主监督。
:alt: 可信主进程运行NOOA和工具代理，外层CodeAct和科学Python分别在无凭据进程中运行，隐藏测试和真值仅供收尾评分。
```

## 外层 CodeAct 与科学代码

NOOA仍负责CodeActV2的推理、工具策略、历史和自动摘要；`SecureCodeActV2`将外层cell改为全新启动的独立进程。它只拿到声明的工具代理、参数和结构化契约，没有live agent、模型客户端、凭据或父进程对象。工具请求采用限长JSON，宿主独立核对方法白名单、参数类型、cell身份和调用序号。它不能通过反序列化让宿主加载任意Python对象。

科学代码也逐cell启动全新进程，保留自由Python及已安装科学库。变量检查点只在下一次受限子进程中反序列化，宿主不反序列化。脚本使用真实进程退出码；stdout/stderr仅作为输出文本，无法声明执行成功。一次输出最多保留262144字符，其余继续排空并标记截断；不会留下下一cell误收的协议响应。超时、取消和结束清理整个进程组。

macOS使用deny-default的sandbox-exec规则；Linux使用Landlock或bubblewrap的受限挂载视图，再设置seccomp和资源上限；缺少Landlock时会实际验证命名空间后端，而不是依据内核版本猜测。仅允许解释器/依赖、引导代码、选中只读数据版本与本方向workspace；`factor_demo`应用源码从运行库授权中排除。Linux引导仅复制固定NOOA版本的底层约束模块并保留许可头，不在每个科学进程导入整套agent SDK；子进程显式使用本地模型成本表，避免无关初始化。未具备相应系统机制时拒绝执行，不自动回退到无限制模式。Linux实际运行验收以Runtime checks工作流为准，不能由文档构建代替。

读写规则不以`/Users`作为唯一边界。科学进程不继承控制FD、模型key、原始会话目录或test/truth路径；通信控制、源码副本、输入哈希和执行账本由宿主保存。外层代理仍可调用已公开的工具，所以每个工具必须自己检查数据范围、产物路径和任务权限。进程约束与回放均不证明任意算法的数学正确性。

## 输入、产物与证据

发现数据只包含train/dev。动态数据集是不可变版本，worker每次仅获得当前版本的DATASET，不能用旧`../input`绕回全集。子集必须保持原值、原结构和选中资产闭包；改值/加文件必须显式登记为transform。产物根目录自身及下载路径均拒绝逃逸符号链接。

成功cell的输入、源码与变更产物哈希进入私有SQLite账本，并保存相应产物副本。该来源证明只赋予探索性可复现等级；raw数值与正式模型指标另由宿主重算，详见[证据设计](evidence-review.md)。

## 本地服务与凭据

服务默认监听127.0.0.1。请求Host必须为localhost、127.0.0.1或[::1]，有Origin时还需与本机来源匹配。CLI与网页共用内核文件锁，运行中不能从另一入口改数据范围、启动第二轮或冻结评估；进程退出自动释放锁。

`.env`和`.secrets/providers.json`仅在本地可信编排中读取。公开源码不含远程SSH取key的个人环境脚本，不向供应商附加内部agent/mission标识请求头。模型服务仍会接收到本轮任务、已选上下文和必要工具结果。公开仓库只包含源码、测试、非秘密配置、锁文件与文档；原始对话、生成数据、运行数据库、真实key和缓存不发布。

## 评估隔离的限度

隐藏test和仿真参考存放在宿主私有快照，所有agent工具均不提供评分入口。收尾评分先冻结报告和已选方法，只允许对同一定义重试。生成器公开，所以拿到全仓权限的人仍能了解故障构造；不能把它当成第三方独立科学发现证明。公开机制ID只是分类词表，不包含哪个场景对应哪个答案。

## Host path integrity and process reaping

- Host writes into the sandbox workspace (`research_cells`, JSON outputs, formula/`state.joblib` copies, font cache seeds) refuse symbolic links (`O_NOFOLLOW` / realpath containment). A cell cannot redirect host I/O outside the workspace by planting symlinks.
- On Linux, cell supervisors become child subreapers and `reap()` SIGKILLs the process group **and** every `/proc` descendant, so `setsid()` grandchildren do not survive a finished cell. Bubblewrap additionally uses a private PID namespace (`--unshare-all --die-with-parent`).
- Linux seccomp denies `socket`/`socketpair`, `io_uring_*`, ptrace/process-vm, and cross-process signals. `fork`/`exec` remain for scientific helpers (e.g. subprocess); without sockets or io_uring they cannot open network channels. Prefer bubblewrap when a private network/PID namespace is required.
- macOS applies `RLIMIT_AS` / `RLIMIT_CPU` in the child bootstrap alongside `(deny default)` sandbox-exec. Full PID-namespace isolation is Linux/bubblewrap-only.

