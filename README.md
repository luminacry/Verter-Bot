# Verter-Bot

参考 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 架构的高性能多平台 LLM 机器人框架（独立实现，非 fork）。

> **TODO（许可证）：项目许可证尚未确定。** 仓库目前不包含 LICENSE 文件，确定之前请勿对外分发。AstrBot 使用 AGPL-3.0；本项目不复制其源码，只参考架构设计。

## 当前进度

P0（工程基建与质量门禁）已完成。P1（核心运行时）与 S0（沙箱技术验证）的阶段计划见 [docs/P1_S0_PLAN.md](docs/P1_S0_PLAN.md)（待审批），总体计划见 [docs/DEV_PLAN.md](docs/DEV_PLAN.md)。

| 组件 | 技术 | P0 已有内容 |
|------|------|-------------|
| 核心运行时 `verter/` | Python 3.12+、asyncio | 配置（沙箱默认本地、仅管理员、禁止联网）、CLI、生命周期、WebUI 后端 API（登录、系统信息） |
| 沙箱执行器 `sandbox/` | Rust 1.99 | `verter-sandbox`：NDJSON 握手、`doctor`、协议 JSON Schema |
| WebUI `webui/` | React 19 + shadcn/ui（Base UI） | 登录页、概览页、亮 / 暗主题、中 / 英文 |

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
```

`uv run verter doctor` 会检查配置、沙箱执行器和 WebUI 构建产物。

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
