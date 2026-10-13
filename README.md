# Verter-Bot

High-performance multi-platform LLM bot framework, structured after [AstrBot](https://github.com/AstrBotDevs/AstrBot) (independent implementation, not a fork).

## MVP scope

- **Platforms:** OneBot v11 (QQ) + Telegram
- **Core:** EventBus → Pipeline (onion stages) → Stars (plugins) → Provider (OpenAI-compatible)
- **Ops:** Plugin hot-reload, Provider manager, simple WebUI Dashboard

## Status

Planning complete. See [docs/DEV_PLAN.md](docs/DEV_PLAN.md) for architecture, package layout, performance rules, and phased milestones.

## Phases (summary)

| Phase | Focus |
|-------|--------|
| 0 | Scaffold, config, lifecycle, console adapter |
| 1 | Pipeline + Stars + hot-reload |
| 2 | OneBot v11 + Telegram adapters |
| 3 | LLM Provider + conversation history |
| 4 | Dashboard WebUI |
| 5 | Tests, Docker, hardening |

## License note

AstrBot is AGPL-3.0. Verter-Bot must not copy AstrBot source; only architectural patterns are referenced.
