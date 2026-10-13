# Verter-Bot

参考 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 架构的高性能多平台 LLM 机器人框架（独立实现，非 fork）。

## 技术栈

| 组件 | 技术 |
|------|------|
| 核心运行时 | Python 3.12+，asyncio（Linux 上启用 uvloop） |
| 本地沙箱 | Rust（`verter-sandbox`，支持 Linux 与 Windows） |
| WebUI | TypeScript + React + Tailwind CSS v4 + shadcn/ui |

## MVP 范围

- 平台：OneBot v11（QQ）+ Telegram
- 核心：EventBus → Pipeline（洋葱模型）→ Star 插件 / Provider（OpenAI 兼容）
- 运维：插件热加载、Provider 管理、WebUI、可被 Agent 调用的本地沙箱

## 状态

目前处于规划阶段。架构、语言选型、性能设计、沙箱安全模型、shadcn 规范、质量体系和分阶段计划见 [docs/DEV_PLAN.md](docs/DEV_PLAN.md)。

## 许可证说明

AstrBot 使用 AGPL-3.0。Verter-Bot 不复制 AstrBot 源码，只参考其架构。
