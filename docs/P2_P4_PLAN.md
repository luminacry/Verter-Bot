# P2 / P4 阶段计划

> 状态：**待批准**（第 7 节列出默认决定；批准本 PR 即视为采用）。P1 与 S0 已全部合入 main。
> 依据：[DEV_PLAN.md](DEV_PLAN.md) 第 5、6、8.2、9 节；[S0_SANDBOX_REPORT.md](S0_SANDBOX_REPORT.md)；main @ `08f4409`（#3 计划，#4–#6 P1，#7–#8 S0）。

## 1. 目标

- **P2 平台适配**：在已有 `Platform` / `PlatformManager` 上接入真实 IM。控制台和 `inmemory` 继续作为调试与测试适配器。本阶段仍不接入 LLM：群聊 / 私聊里只有已唤醒的插件指令会回复。
- **P4 沙箱产品化**（与 P2 并行）：把 S0 已经锁定的 Linux / Windows 默认方案接到常驻 runner、协议 v2、会话工作区、审计和 WebUI 沙箱页。Agent 工具（`shell_exec` 等）依赖 P3 的工具循环，本阶段不做。

P3（模型与会话）和 P5（发布）不在本计划范围内，所需准备列在第 8 节。

## 2. P2 范围

### 2.1 包含

| 模块 | 路径 | 内容 |
|------|------|------|
| OneBot v11 | `verter/core/platform/sources/onebot_v11.py` | 反向 WebSocket（aiohttp 服务端，NapCat 主测）与正向 WebSocket（aiohttp 客户端）。事件与 `MessageChain` 互转；断线由 `PlatformManager` 退避重启 |
| Telegram | `verter/core/platform/sources/telegram.py` | Bot API 长轮询（aiohttp，不用官方 SDK）。私聊与群聊；隐私模式在文档中说明 |
| 注册 | `verter/core/platform/register.py` | `_BUILTIN` 增加 `onebot_v11`、`telegram`；按配置懒加载 |
| 配置 | `verter/core/config.py` | 适配器私有键仍走 `PlatformConfig.settings`（`extra="allow"`），由适配器 `parse_settings` 校验 |
| 模拟服务 | `tests/fakes/`（或同等位置） | 模拟 OneBot 客户端与 Telegram Bot API，CI 与 `just dev` 不依赖真实账号 |
| 可观测 | `verter/dashboard/` | 平台列表 / 状态 / 重启次数 / 最后错误的 API；概览页可展示适配器是否在跑 |
| WebUI | `webui/src/features/platforms/` | 平台页：列表、启停或重启、加载 / 空数据 / 错误三态，中英文、亮暗主题、键盘操作 |
| 性能 | `benchmarks/` | 空载内存预算改为含 OneBot + Telegram（无插件）RSS < 80 MB，作为回归阈值 |

`PlatformManager` 已在 P1-1 实现（崩溃退避重启、`stats()`）。P2 补适配器与页面，不重写管理器。

### 2.2 不包含

- LLM、会话、人格、工具循环（P3）。
- 其它平台（Discord、Slack 等）。
- Telegram webhook、OneBot HTTP POST / 正向 HTTP。
- 从 WebUI 编辑 `config.yaml` 里的 token 与监听地址（仍用配置文件；页面只展示运行状态并允许重启）。
- 插件市场、在线安装。

## 3. P2 验收标准

1. **OneBot**：模拟客户端能以反向 WS 和正向 WS 各走通一条私聊与一条群聊（文本、At、图片段往返）。NapCat 手工验证留到第 8 节的密钥就位之后，不阻塞合入。
2. **Telegram**：模拟 Bot API 能走通私聊与群聊；`getUpdates` 失败后自动重试；隐私模式导致收不到普通群消息时，文档写明需要关闭隐私或使用指令。
3. **断线**：适配器 `run()` 抛错后，`PlatformManager` 退避重启，WebUI 显示 `ERROR` / 重启次数 / `last_error`。
4. **唤醒语义不变**：未带唤醒前缀的群消息仍被忽略；`/help`、`/helloworld` 有回复。
5. **性能**：空载 RSS（启用 OneBot + Telegram 模拟连接、无第三方插件）< 80 MB；既有流水线 p99 与分发层预算不回退。
6. **WebUI**：平台页覆盖三态；新增视觉基线与 axe；一条 e2e 覆盖“看到适配器状态并触发重启”。
7. **门禁**：`just check` 在 Linux 和 Windows 上全绿；Python 覆盖率不低于 85%；代码生成无漂移；首屏 JS 预算达标。

## 4. P4 沙箱产品化

S0 已锁定默认方案，见 [S0_SANDBOX_REPORT.md](S0_SANDBOX_REPORT.md) 第 1、4 节。本阶段把原型变成运行时能力。Docker 与 macOS 仍然不做。

### 4.1 采用的 S0 默认值（不再重开）

| 项 | 决定 |
|----|------|
| Linux `auto` | bubblewrap（user namespace 探测成功）→ Landlock + seccomp + rlimit → 拒绝执行 |
| Windows `auto` | 已初始化且与网络策略匹配的隔离用户 → AppContainer + Job Object → 拒绝执行 |
| 无隔离 | 任何情况下都不执行 |
| 受限令牌 | 仅允许网络时可由 `--backend restricted` 显式选用；`auto` 不用它来断网 |
| 协议 v1 `BackendKind` | 保持仅 `bubblewrap`，Windows / Landlock 放到协议 v2 |
| Python 安装目录 ACL | **不修改**（S0 兼容性矩阵未强制 python 成功） |
| 开销预算 | Linux `echo` < 50 ms，Windows `echo` < 200 ms，CI 继续断言 |

### 4.2 包含

| 模块 | 路径 | 内容 |
|------|------|------|
| 协议 v2 | `sandbox/schema/protocol.schema.json`、生成的 Python / Rust 模型 | 按 S0 报告第 7 节：`exec` / `exec_stdout` / `exec_stderr` / `exec_exit`；`BackendKind` 增加 `landlock`、`appcontainer`、`isolated`、`restricted`；hello 协商版本 |
| 客户端 | `verter/core/computer/runner.py` | `SandboxClient.exec`：流式收集输出、截断、超时、把拒绝执行映射为错误而不是裸跑 |
| 启动器 | `verter/core/computer/` | `ComputerBooter`：按配置拉起常驻 `verter-sandbox serve`，准备 `data/workspaces/<会话哈希>/`，注入策略（网络、限额、权限） |
| 权限 | 现有 `ComputerConfig.allows` | 默认仅管理员；白名单 / 全员可配；未授权调用拒绝并审计 |
| 审计 | `verter/core/db.py` | 记录会话、发起人、命令、耗时、退出码、截断后的输出 |
| doctor | `verter-sandbox doctor` + dashboard | 协议 JSON 报出 Windows / Landlock 后端；概览或沙箱页只展示实际生效的能力 |
| WebUI | `webui/src/features/sandbox/` | 沙箱页：策略、doctor 能力、审计列表；`auto: none` 时不提供执行按钮 |
| Windows setup | `sandbox/crates/windows` | 产品化时不再把密码放进 `net user` 命令行（S0 已知限制）；账户名仍 ≤ 20 字符，密码仍 14 字符以满足 `net user` 复杂度 |

CLI 实验子命令 `verter-sandbox exec` 保留给 CI 与 `doctor`；运行时走 NDJSON。

### 4.3 不包含

- Agent 工具 `shell_exec` / `python_exec` / `fs_*`（P3 之后）。
- Docker 后端。
- macOS。
- 在沙箱页里对非管理员会话直接执行命令的调试控制台（若做，也必须走同一套权限与审计）。

### 4.4 P4 验收

1. 从 Python `SandboxClient` 在 Linux 与 Windows 上各执行一次 `echo`，走协议 v2，输出完整，后端名称与 S0 默认一致。
2. 隔离不可用时 `exec` 返回错误，命令不在宿主机运行；WebUI 不显示“已隔离”。
3. 越界写、读工作区外文件、外网（断网策略下）、超时杀树，在现有 `sandbox-linux` / `sandbox-windows` 任务上继续通过。
4. 未授权会话的调用被拒绝并写入审计表。
5. 沙箱页三态、中英文、亮暗、axe、视觉基线。
6. `just check` 全绿；既有开销断言不放宽。

## 5. 分支与 PR 策略

都从最新的 main 切出，以 Draft 提交，CI 全绿后再请你审阅，合并方式沿用 squash。后一个 PR 可先基于前一个分支，合并后再改目标为 main，并 `rebase --onto` 新的 squash SHA（不要只改 base，否则会把已 squash 的提交再打一遍）。

| PR | 分支 | 内容 | 依赖 |
|----|------|------|------|
| 本 PR | `cursor/p2-p4-plan-e359` | 本计划、DEV_PLAN 状态、README | 无 |
| P2-1 | `cursor/p2-onebot-e359` | OneBot v11 反向 + 正向 WS、消息互转、模拟客户端测试 | 本 PR |
| P2-2 | `cursor/p2-telegram-e359` | Telegram 长轮询、消息互转、模拟 Bot API | 本 PR（可与 P2-1 并行） |
| P2-3 | `cursor/p2-platforms-webui-e359` | 平台 API、WebUI 平台页、空载内存预算 | P2-1 与 P2-2 |
| P4-1 | `cursor/p4-protocol-client-e359` | 协议 v2、`SandboxClient.exec`、工作区、`ComputerBooter` | 本 PR（与 P2 并行） |
| P4-2 | `cursor/p4-sandbox-webui-e359` | 审计表、doctor API、WebUI 沙箱页、Windows 密码不再出现在 argv | P4-1 |

P2 与 P4 互不依赖，可以穿插合并。每个 PR 带自己的测试和文档。

## 6. 风险与阻塞

| 风险 | 影响 | 应对 |
|------|------|------|
| OneBot 实现差异 | 只在某个实现上能用 | 以 NapCat 为主测；只依赖 OneBot v11 标准事件与 `send_msg`；模拟客户端按标准报文写 |
| Telegram 群组隐私模式 | 收不到非指令消息 | 文档说明；唤醒前缀与 BotFather 指令列表对齐 |
| 平台限速 | 发送被拒 | 适配器内对 Telegram 按官方 FAQ 做最小间隔；失败记入 `last_error`，不把框架打崩 |
| 协议 v2 与 v1 并存 | 旧 `hello` 客户端 | runner 按 `protocol_version` 选择；P4 起核心只说 v2 |
| Windows 密码曾出现在进程命令行 | 本机其它进程可见 | P4-2 改为不把密码放进 argv；过渡期 `setup --uninstall` 仍能清掉 S0 创建的用户 |
| AppContainer 跑不了 python / git | Agent 工具以后受限 | 本阶段不改安装目录 ACL；沙箱页如实显示兼容性，不假装能跑 |
| WebUI 首屏变大 | JS 预算失败 | 平台页、沙箱页继续路由懒加载 |
| 真实账号密钥未配置 | 无法做 NapCat / Telegram 手工验收 | CI 只用模拟服务；手工验收单独列为第 8 节，不阻塞合入 |

## 7. 开工前需要你确认

以下每一项都附了默认建议。直接批准本 PR 即视为采用这些默认值。

1. **批准范围与拆分**：P2 拆为 P2-1 / P2-2 / P2-3；P4 拆为 P4-1 / P4-2（无 Agent 工具）。P2 与 P4 并行。Docker 仍不做。不新增 LICENSE。
2. **OneBot 传输**：实现反向 WS 与正向 WS；不实现 HTTP。主测 NapCat。
3. **Telegram 传输**：只做长轮询，不做 webhook。
4. **WebUI 平台页**：展示状态并允许重启单个适配器；token 与端口仍只在 `config.yaml` 里改。
5. **S0 默认方案**：第 4.1 节全部采用，包括不修改 Python 安装目录 ACL。
6. **协议**：P4 引入 v2（S0 报告第 7 节草案）；v1 仅保留握手 / ping / shutdown，核心不再经 CLI `exec` 调运行时。
7. **Agent 工具**：明确推迟到 P3 之后的后续切片，不塞进 P4-1 / P4-2。

## 8. 后续阶段需要提前准备（P2 / P4 合入不依赖）

- **手工验收 P2**（可选，不阻塞 CI）：
  - NapCat（或其它 OneBot v11）的反向 WS 地址与 `access_token`。
  - Telegram Bot Token、一个私聊和一个测试群。
  - 放到 Cursor Dashboard → Cloud Agents → Secrets，建议命名 `ONEBOT_ACCESS_TOKEN`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_TEST_CHAT_ID`。
- **P3**：OpenAI 兼容的 API Key、Base URL、模型名，建议 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，仅用于手工验证。CI 用模拟 LLM。
- **P5 发布前**：确定项目许可证（README 中已有 TODO）。
