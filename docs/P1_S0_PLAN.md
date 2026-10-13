# P1 / S0 阶段计划

> 状态：**已批准**（第 7 节的默认决定全部采用）。P1-1 起按第 5 节的拆分实施。
> 依据：[DEV_PLAN.md](DEV_PLAN.md) 第 5、6、8.2、9 节；main @ `e9c17f4`（#3 计划已合并）。

## 1. 目标

- **P1 核心运行时**：在不接入真实 IM 平台和 LLM 的前提下，跑通与 AstrBot 结构对齐的完整链路：消息进入 → EventBus → 9 个流水线阶段 → 插件处理 → 回复。插件可以热加载，运行状态可以在 WebUI 中观测。
- **S0 沙箱技术验证**（与 P1 并行）：用原型和实测数据确定 P4 在 Linux、Windows 上的默认隔离方案、回退顺序，以及哪些步骤需要管理员权限。

## 2. P1 范围

### 2.1 包含

| 模块 | 路径 | 内容 |
|------|------|------|
| 消息模型 | `verter/core/message/` | MVP 消息段（Plain、At、AtAll、Image、Reply、Face、Record、File、Unknown）、MessageChain、MessageEventResult |
| 平台层 | `verter/core/platform/` | Platform 基类、MessageEvent（方法命名与 AstrBot 的事件 API 对齐）、BotMessage、MessageType、平台注册与 PlatformManager（任务崩溃后退避重启） |
| 调试适配器 | `verter/core/platform/sources/` | `inmemory`（测试与基准用）；`console`（`verter run --console` 在终端收发消息） |
| EventBus | `verter/core/event_bus.py` | 有界队列 + 固定 worker；队满丢弃并计数；单事件超时；会话等待器（多轮交互）在入队前按会话拦截 |
| 流水线 | `verter/core/pipeline/` | `Stage`、`STAGES_ORDER`、`@register_stage`、洋葱模型调度；9 个阶段中 Process 只执行插件（LLM 在 P3） |
| 插件系统 | `verter/core/star/`、`verter/api/` | Star、Context、`filter.*` 装饰器与过滤器、HandlerIndex、PluginManager（`metadata.yaml`、`_conf_schema.json`、加载 / 卸载 / 热加载 / 启停）、插件 KV |
| 存储 | `verter/core/db.py` | aiosqlite + WAL；用 `PRAGMA user_version` 做版本迁移；保存插件启停状态和 KV。P3 的会话存储在此基础上扩展 |
| 内置指令 | `verter/builtin_stars/builtin_commands/` | `/help`、`/sid`，以及仅限管理员的 `/plugin`（`ls`、`on`、`off`、`reload`） |
| 可观测 | `verter/core/metrics.py`、`verter/dashboard/` | 事件计数、丢弃数、队列深度、流水线延迟直方图；日志环形缓冲；运行指标与插件管理 API |
| WebUI | `webui/src/features/` | 概览页增加实时运行指标；新增插件页（列表、启停、重载、错误详情） |
| 性能 | `benchmarks/` | 用真实代码替换 P0 的模拟基准，锁定 8.2 的预算；Linux 上启用 uvloop |
| 文档 | `docs/plugins.md`、`examples/plugins/helloworld/` | 插件开发指南；AstrBot 插件移植说明（只做 API 命名映射，不复制代码） |

### 2.2 不包含

- 真实平台适配器 OneBot、Telegram（P2）。
- LLM、会话、人格、`llm_tool` 等 LLM 钩子（P3）。
- 沙箱接入 Agent、WebUI 沙箱页、Docker 后端（P4 及以后）。
- 插件依赖自动安装（P1 只检测缺失的 `requirements.txt` 依赖，并把插件标记为失败）、插件市场、在线安装。
- 插件配置的表单编辑、日志页、多配置路由。

## 3. P1 验收标准

1. **控制台跑通**：`uv run verter run --console` 中，`/help`、`/helloworld` 有回复，未唤醒的文本被忽略，非管理员执行 `/plugin off` 被拒绝。
2. **热加载**：修改 `data/plugins/helloworld/main.py` 后，执行 `/plugin reload helloworld` 或点击 WebUI 的重载按钮，新行为立即生效，进程不重启。卸载后会调用 `terminate()`，后台任务被取消，旧模块和 handler 可被回收（用 weakref 测试验证）。
3. **故障隔离**：插件导入失败或 handler 抛异常时，运行时不崩溃，其他插件照常工作；错误信息在 `/plugin ls` 和 WebUI 中可见。
4. **EventBus**：过载时丢弃并计数，不会无限增长；单事件超时生效；会话等待器的多轮交互有测试覆盖。
5. **性能预算**：基于真实代码，锁定后作为回归阈值：
   - handler 数从 100 增加到 5000 时，未唤醒消息的分发耗时增长不超过 10%（替换 P0 模拟基准的 1.25）。
   - 通过 `inmemory` 适配器处理一条指令消息，端到端 p99 小于 1 ms。
   - 冷启动到可接收消息小于 2 秒。
   - 空载内存：P1 先测核心部分并设临时预算；计划里的 80 MB 包含 OneBot 和 Telegram，到 P2 再锁定。
6. **WebUI**：概览页显示实时指标（SSE 推送，断线自动重连）；插件页覆盖加载、空数据、错误三种状态，支持中英文、亮暗主题和键盘操作。新增视觉基线和 axe 检查，并有一条全栈 e2e 测试覆盖"在 WebUI 中重载插件"。
7. **门禁**：`just check` 在 Linux 和 Windows 上全绿；Python 覆盖率不低于 85%；代码生成无漂移；首屏 JS 预算达标（新页面同样按路由懒加载）。

## 4. S0 沙箱技术验证

### 4.1 已有依据

- **本机探测**（Ubuntu 24.04，内核 6.12，LSM 为 capability、landlock、selinux，未启用 AppArmor），apt 安装 bubblewrap 0.9.0：
  - 普通用户执行 `--unshare-all` 正常；PID 命名空间生效，`/usr` 只读。
  - 宿主机能联网，沙箱内返回 `Network is unreachable`。
  - 启动开销：中位数 3.2 ms、p95 4.3 ms（直接启动为 0.4 ms），远低于 50 ms 预算。
- **GitHub ubuntu-24.04 Runner** 默认开启 `kernel.apparmor_restrict_unprivileged_userns=1`：
  - apt 装的 bwrap 会报 `setting up uid map: Permission denied`（[Launchpad #2144531](https://bugs.launchpad.net/bugs/2144531)）。
  - 即使有 bwrap 的 AppArmor profile，`--unshare-net` 也会因为无法配置回环网卡而失败。
  - CI 的常见做法是在一次性 Runner 上执行 `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`（[runner-images #11489](https://github.com/actions/runner-images/pull/11489)）。
  - **Ubuntu 24.04 以上的真实用户机器默认也是这个状态**，所以 Linux 不能只依赖 bubblewrap。

### 4.2 实验矩阵

| 平台 | 环境 | 方案 |
|------|------|------|
| Linux | 本 VM（无 AppArmor 限制） | bubblewrap + seccomp + rlimit |
| Linux | GitHub Runner，sysctl 关闭限制 | 同上 |
| Linux | GitHub Runner，默认限制 | 回退：Landlock + seccomp + rlimit（无命名空间），并检测内核 Landlock ABI |
| Windows | windows-2025（管理员，可测一次性初始化） | 标准模式 A：AppContainer + Job Object + 工作区 ACL |
| Windows | 同上 | 标准模式 B：受限令牌 + Job Object（网络只能"建议性"屏蔽） |
| Windows | 同上 | 强隔离：初始化离线 / 在线沙箱用户 + 防火墙规则 + 受限令牌 + Job Object |

每个格子都跑 DEV_PLAN 6.6 的安全测试子集：越界写入、读取敏感文件（如 `data/config.yaml`）、外网连接、fork 炸弹、内存炸弹、超时后整棵进程树被结束、环境变量清空，并测量单次 `echo` 的额外开销。Windows 另外做兼容性矩阵：python、PowerShell 5.1 / 7、cmd、git 在每种模式下能否正常运行。

### 4.3 交付与验收

- 原型代码放在 `sandbox/crates/linux` 和 `sandbox/crates/windows`，通过实验性子命令 `verter-sandbox exec --backend <name> -- <cmd>` 调用。P0 的 NDJSON 协议不改；协议 v2 只在报告中给出设计草案。
- CI 新增 `sandbox-linux`（同时跑限制开启和关闭两种 Runner 配置）和 `sandbox-windows` 任务。
- 报告 `docs/S0_SANDBOX_REPORT.md` 需包含：
  - 实验矩阵结果和开销数据；
  - 每个系统的默认方案和回退顺序；
  - 哪些步骤需要 sudo 或管理员权限；
  - 能力降级时如何如实告知用户（`doctor` 和概览页显示实际生效的隔离能力，绝不在无隔离时静默执行）；
  - 协议 v2 的 exec / 流式输出消息草案。
- 验收：报告给出 Linux 和 Windows 的默认方案并经你确认；开销满足 Linux < 50 ms、Windows < 200 ms，不满足时给出原因和修订后的预算。

### 4.4 不包含

接入 Agent 工具、审计存储、WebUI 沙箱页（P4）；Docker 后端；macOS。

## 5. 分支与 PR 策略

都从最新的 main 切出，以 Draft 形式提交，CI 全绿后再请你审阅，合并方式沿用 squash。如果审阅排队，后一个 PR 先基于前一个分支开发，合并后再改目标为 main。

| PR | 分支 | 内容 | 依赖 |
|----|------|------|------|
| 本 PR | `cursor/p1-s0-plan-e359` | 本计划、DEV_PLAN 状态更新、补回 README | 无 |
| P1-1 | `cursor/p1-runtime-core-e359` | 消息模型、平台层、`inmemory` / `console` 适配器、EventBus、流水线与 9 个阶段、生命周期接线、uvloop；流水线和 EventBus 基准及预算 | 本 PR |
| P1-2 | `cursor/p1-plugin-system-e359` | 插件系统、HandlerIndex、`verter/api`、存储、内置指令、会话等待器、示例插件、插件文档；HandlerIndex 基准（替换模拟基准） | P1-1 |
| P1-3 | `cursor/p1-observability-e359` | 指标、日志环形缓冲、运行指标与插件 API、WebUI 概览页更新与插件页；冷启动和内存基准 | P1-2 |
| S0-1 | `cursor/s0-linux-sandbox-e359` | Linux 原型、`sandbox-linux` CI 任务 | 本 PR |
| S0-2 | `cursor/s0-windows-sandbox-e359` | Windows 原型、`sandbox-windows` CI 任务、S0 报告 | S0-1（复用实验子命令） |

S0 只改动 `sandbox/`、CI 和文档，与 P1 互不依赖，可以穿插合并。每个 PR 都附带自己的基准、测试和文档，不留到最后一起补。

## 6. 风险与阻塞

| 风险 | 影响 | 应对 |
|------|------|------|
| Ubuntu 24.04+ 的 AppArmor 限制非特权 user namespace（已在 GitHub Runner 上确认） | Linux 用户可能无法使用 bubblewrap 方案 | S0 验证 Landlock + seccomp 回退；可选提供 `verter-sandbox setup` 安装 AppArmor profile；能力如实报告 |
| Windows 隔离依赖 Win32 FFI，以及 ACL、本地用户、防火墙等系统副作用 | 实现复杂，可能修改用户系统 | 先由 S0 验证；所有副作用集中在显式的 `setup` 命令中，并可以撤销 |
| Rust 工作区目前全局禁止 `unsafe` | Windows 原型无法编写 | 只在 `sandbox/crates/windows` 放开，并要求每处 unsafe 写 `SAFETY` 注释（启用 clippy `undocumented_unsafe_blocks`）；Linux 侧通过自身重执行设置 rlimit，避免使用 unsafe |
| Python 热加载卸载不干净（残留引用、后台任务） | 内存泄漏、旧 handler 仍被调用 | 明确卸载契约；用 weakref 和任务泄漏测试把关 |
| P1 范围大 | 审阅困难 | 拆成 3 个 PR，每个都能独立合并和验收 |
| CI 共享机器上性能数据有噪声 | 预算误报 | 预算以比值为主，取多次运行的最好值；绝对值预算留出余量，结果作为 artifact 保存便于看趋势 |
| CI 时长增长（Windows check 目前约 3.5 分钟） | 反馈变慢 | 沙箱任务单独并行运行，保留缓存 |

## 7. 开工前需要你确认

以下每一项都附了默认建议。直接批准本 PR 即视为采用这些默认值。

1. **批准范围与拆分**：P1 拆为 P1-1、P1-2、P1-3，S0 并行。
2. **Linux（AppArmor 限制）**：建议自动回退到 Landlock + seccomp 并如实报告能力；另提供可选的 `verter-sandbox setup`，用一次 sudo 安装 AppArmor profile 以启用 bubblewrap。CI 上限制开启和关闭两种配置都测。
3. **Windows 策略**：
   - 建议接受"强隔离模式需要一次性管理员初始化"。
   - 标准模式允许为工作区设置 ACL；是否允许修改 Python 安装目录的 ACL，等 S0 的兼容性结果出来后再请你决定。
   - `auto` 的含义：已初始化则用强隔离；否则在 S0 证明兼容的前提下用 AppContainer；都不行就禁用沙箱工具并在 `doctor` 中说明原因。**任何情况下都不在无隔离时执行。**
4. **Rust unsafe**：只在 `sandbox/crates/windows` 允许，规则见第 6 节。
5. **插件 API 命名**：
   - 主名称：`verter.api.*`、`MessageEvent`、`Star`、`Context`、`filter.*`。
   - 为移植提供 `AstrMessageEvent` 等少量别名。
   - 不提供能拦截 `import astrbot` 的兼容层：避免名称冲突，也避免看起来像复制了 AstrBot。
   - 插件元数据以 `metadata.yaml` 为主，同时支持 `@register` 装饰器。
6. **开发环境**：在 Cloud Agent 环境配置中加入 `apt-get install bubblewrap`（本 VM 已为探测装过）。如果不改环境配置，我会在每次任务开始时安装。

## 8. 后续阶段需要提前准备（P1 / S0 不需要）

- **P2**：
  - 一个接入 NapCat（OneBot v11）的 QQ 测试号，以及可访问的反向 WebSocket 地址与 access token。
  - 一个 Telegram Bot Token 和一个测试群。
  - 请放到 Cursor Dashboard → Cloud Agents → Secrets，建议命名为 `ONEBOT_ACCESS_TOKEN`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_TEST_CHAT_ID`。CI 只使用模拟服务。
- **P3**：一个 OpenAI 兼容的 API Key、Base URL 和模型名，建议命名为 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，仅用于手动验证。
- **P5 发布前**：确定项目许可证（README 中已有 TODO）。
