# Verter-Bot

参考 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 架构的高性能多平台 LLM 机器人框架（独立实现，非 fork）。

> **TODO（许可证）：项目许可证尚未确定。** 仓库目前不包含 LICENSE 文件，确定之前请勿对外分发。AstrBot 使用 AGPL-3.0；本项目不复制其源码，只参考架构设计。

## 当前进度

P0、P1、S0 已合入 main。P1 覆盖消息流水线、插件系统和 WebUI 观测；S0 锁定 Linux / Windows 沙箱默认方案（无隔离时拒绝执行）。P2（平台适配）进行中：OneBot v11 适配器（P2-1）已有首版，Telegram（P2-2）与 WebUI 平台页（P2-3）尚未开始。P2 / P4 计划见 [docs/P2_P4_PLAN.md](docs/P2_P4_PLAN.md)，插件写法见 [docs/plugins.md](docs/plugins.md)，总体计划见 [docs/DEV_PLAN.md](docs/DEV_PLAN.md)。

| 组件 | 技术 | 已有内容 |
|------|------|----------|
| 核心运行时 `verter/` | Python 3.12+、asyncio、aiohttp | 配置（沙箱默认本地、仅管理员、禁止联网）、CLI、生命周期、消息流水线、插件系统、WebUI 后端 API；平台适配器：`console`、`inmemory`、`onebot_v11`（P2-1 首版） |
| 沙箱执行器 `sandbox/` | Rust 1.99 | `verter-sandbox`：NDJSON 握手、`doctor`、协议 JSON Schema；Linux `exec`（bubblewrap，Landlock 回退）与可选 `setup`；Windows `exec`（AppContainer，已初始化时用隔离用户）与管理员 `setup` |
| WebUI `webui/` | React 19 + shadcn/ui（Base UI） | 登录页、概览页（实时指标）、插件页、亮 / 暗主题、中 / 英文 |

## 环境要求

- Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)
- Node.js 22+ 与 pnpm 10
- Rust：rustup 会按 `sandbox/rust-toolchain.toml` 自动安装 1.99.0
- [just](https://just.systems)；Windows 上使用 PowerShell 7（`pwsh`）运行
- 可选：Docker，用于运行与 CI 一致的 Playwright 测试

## 快速开始

```bash
just setup            # 安装 Python、WebUI、Rust 依赖
just build-sandbox    # 构建 verter-sandbox
just build-webui      # 构建 WebUI
uv run verter init    # 生成 data/config.yaml 并打印管理员密码
uv run verter run     # 访问 http://127.0.0.1:6185
uv run verter run --console   # 在当前终端里和机器人对话
```

`uv run verter doctor` 会检查配置、沙箱执行器和 WebUI 构建产物。

## 接入 QQ（OneBot v11 / NapCat）

适配器支持两种 WebSocket 连接方式，在 `data/config.yaml` 的 `platform` 中添加一项：

```yaml
platform:
  # 反向 WS：Verter 监听，NapCat 作为客户端连进来（推荐）
  - id: qq_main
    type: onebot_v11
    mode: ws_reverse
    ws_reverse_host: 127.0.0.1
    ws_reverse_port: 6199
    ws_reverse_path: /
    access_token: "换成你的密钥"

  # 正向 WS：Verter 主动连接 NapCat 的 WS 服务端
  # - id: qq_forward
  #   type: onebot_v11
  #   mode: ws_forward
  #   ws_forward_url: ws://127.0.0.1:3001
  #   access_token: "换成你的密钥"
  #   reconnect_interval_s: 5
```

NapCat 侧对应配置：

- 反向 WS：在 NapCat 网络配置中新建“WebSocket 客户端”，`url` 填 `ws://127.0.0.1:6199/`，`token` 与 `access_token` 一致。
- 正向 WS：新建“WebSocket 服务端”，端口与 `ws_forward_url` 一致，`token` 与 `access_token` 一致。
- 两种方式都必须把 `messagePostFormat` 设为 `array`；`string`（CQ 码）格式的消息会被忽略并记录警告。

`ws_reverse_host` 默认只监听本机。改为 `0.0.0.0` 对外开放时务必设置 `access_token`，否则任何能连上端口的人都可以冒充 NapCat 推送消息、接收机器人回复。

当前能力与限制：

- 私聊、群聊消息的接收与回复。消息段双向支持 text、at（含 @全体）、image、record、video、face、reply、file、json、forward、node（仅发送）、music、contact、poke、dice、rps、mface，其余类型保留为 `Unknown` 并原样回传。
- 引用回复会通过 `get_msg` 补全被引用消息的发送者，引用机器人的消息可以唤醒机器人。
- notice（撤回、戳一戳、群成员变动、禁言等）与 request（好友 / 加群请求）作为 `MessageType.NOTICE` / `MessageType.REQUEST` 事件进入流水线，不受唤醒规则限制，只投递给订阅了这两种类型的插件。
- 插件可以用 `context.call_action(platform_id, action, params)` 调用 NapCat 的任意接口（撤回、禁言、踢人、群文件、合并转发等），接口失败时抛出 `OneBotActionError`。
- 心跳与 lifecycle 元事件用于判断 QQ 账号是否在线。
- 断线后自动重连（正向 WS 退避从 `reconnect_interval_s` 起翻倍，上限 30 秒；适配器崩溃由 `PlatformManager` 退避重启）。反向 WS 允许多个连接，回复走最新的连接。
- 不支持 HTTP 传输。
- 仅在模拟 NapCat 对端上测试过，尚未用真实 NapCat 实例验证。

## 开发命令

| 命令 | 作用 |
|------|------|
| `just dev` | 同时启动后端（:6185）和 Vite 开发服务器（:5173，`/api` 代理到后端） |
| `just check` | CI 在 Linux 和 Windows 上运行的全部门禁 |
| `just codegen` | 重新生成 OpenAPI 客户端和沙箱协议模型 |
| `just bench` | 运行基准测试并检查性能预算 |
| `just e2e-docker` | 在固定版本的 Playwright 镜像中运行端到端、无障碍和视觉回归测试（与 CI 一致） |
| `just update-snapshots` | 在同一镜像中重新生成视觉回归基线 |
| `just deny` | 检查 Rust 依赖的许可证、安全公告和来源 |

## 质量门禁

`just check` 包含：

- 格式与 lint：ruff、rustfmt、clippy（pedantic）、ESLint（strictTypeChecked 加 shadcn 规范规则）、Prettier
- 类型检查：pyright（`verter/` 使用 strict）、TypeScript strict
- 测试：pytest（覆盖率不低于 85%）、cargo test、Vitest
- 代码生成漂移检查；WebUI 首屏 JS 预算（每屏不超过 250 KB gzip）

CI 还会额外运行：Python 3.13 测试、Playwright 镜像内的 e2e / axe / 视觉回归、基准测试预算、cargo-deny。
