# 后续开发路线（P2 之后）

> 状态：已确认。依据：[DEV_PLAN.md](DEV_PLAN.md)、[P2_P4_PLAN.md](P2_P4_PLAN.md)。
> 相对 P2_P4_PLAN 的变更：顺序改为 P2 → P3 → P4；OneBot 覆盖 NapCat 的通知、请求事件与全部接口。

## 顺序与理由

1. P2 平台适配（OneBot 补全、Telegram、平台页）
2. P3 模型与会话
3. P4 沙箱产品化与 Agent 工具
4. P5 发布

不接 LLM 时机器人只能回复固定指令；沙箱的价值在于给 Agent 调用，因此 P4 排在 P3 之后。

## 阶段 0：收尾

- 建立 git 分支 `p2-onebot`，承接 OneBot 首版。
- 修复 OneBot 首版缺陷：
  - 接口返回 `status: failed` 或 `retcode != 0` 时抛错，不再当作成功。
  - 反向 WS 允许多个连接；每个连接的待响应请求独立，旧连接断开不影响新连接。
  - 正向 WS 重连退避不泄漏任务，退避从 `reconnect_interval_s` 起翻倍，上限 30 秒。
  - 引用回复通过 `get_msg` 补全被引用消息的发送者，使“引用机器人的消息”能唤醒。
- `test_dashboard_serves_on_an_ephemeral_port` 在本机失败已确认是环境问题：系统代理（`127.0.0.1:7897`）开启时，`httpx 0.28.1` 未应用注册表里的 `ProxyOverride`，把 `127.0.0.1` 也转发给了代理，代理回了 502。直连（原始 socket、aiohttp）返回正常，CI 无代理不受影响，不改代码。
- 待定（需单独确认）：JWT 注销与改密后失效；反向代理后的登录限流。

## 阶段 1：OneBot 补全（P2-1）

状态：代码部分已完成；真实 NapCat 手工验证待测试号就位。

- 消息段：video、file、forward / node、json、music、contact、poke、dice、rps、mface，收发双向。
- 事件：notice（撤回、戳一戳、群成员增减、禁言、管理员变动、表情回应、精华等）与 request（好友、加群）进入流水线，插件可订阅。
- 接口：`Context.call_action(platform_id, action, **params)` 透传 NapCat 全部接口；常用接口（撤回、禁言、踢人、群成员）提供带类型的封装。
- 心跳与 lifecycle 元事件判断连接是否在线，供平台页使用。
- 验收：模拟对端覆盖新增消息段、notice、request、`call_action`；`onebot_v11.py` 覆盖率不低于 85%；真实 NapCat 手工验证（需要测试号，不阻塞合入）。

## 阶段 2：Telegram（P2-2）

状态：代码部分已完成；真实 Bot 手工验证待 token 就位。

aiohttp 长轮询 `getUpdates`，私聊与群聊；按官方限速控制发送间隔；失败重试；文档说明群组隐私模式；模拟 Bot API 测试。

## 阶段 3：平台页与性能预算（P2-3）

状态：平台列表 API 与按 id 重启已完成（`/api/platforms`）。WebUI 平台页延后，优先做 OneBot 人格与跨群对话，见 [P3_ONEBOT_PERSONA_PLAN.md](P3_ONEBOT_PERSONA_PLAN.md)。空载 RSS 预算仍待补。

## 阶段 4：模型与会话（P3）

OpenAI 兼容 Provider（流式、工具调用、多 Key 轮换、重试、并发上限）；会话与人格（上下文隔离、LRU、合并写入 SQLite）；Agent 工具循环（模拟 LLM 测试）；WebUI 提供商、会话、人格页。

## 阶段 5：沙箱产品化（P4）

按 P2_P4_PLAN 第 4 节；另加 Agent 工具 `shell_exec`、`python_exec`、`fs_*`（依赖 P3）。

## 阶段 6：发布（P5）

Docker 镜像、Windows 便携包、平台 wheel、稳定性测试、部署文档。对外发布前必须确定许可证。

## 需要准备

- NapCat 测试号（阶段 1 手工验证）。
- OpenAI 兼容 API Key、Base URL、模型名（阶段 4 手工验证）。
- 本机安装 Rust 与 just（阶段 5 及完整 `just check`）。
