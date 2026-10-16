# P3：OneBot 人格与跨群对话

> 状态：待审。依据：[ROADMAP.md](ROADMAP.md)、NapCat 接口文档（https://napneko.github.io/develop/api/doc）。
> 范围：只针对 `onebot_v11`。Telegram 维持现状，不扩展。WebUI 的人格页和会话页放到后续。

## 1. 会话作用域

三层，从小到大：

| 层 | key | 内容 | 默认 |
|----|-----|------|------|
| 群上下文 | `group:<group_id>` | 近期群消息环形缓冲，多人共享 | 仅本群可见 |
| 群内个人 | `member:<group_id>:<user_id>` | 该用户在该群的对话历史 | 仅本群可见 |
| 用户全局记忆 | `user:<user_id>` | 昵称、偏好等显式允许跨群的事实 | 默认关闭，需 opt-in |

私聊没有群上下文，key 为 `dm:<user_id>`，与群内个人互不可见。

**隔离是默认值**：A 群的消息原文不会进入 B 群的上下文。跨群共享只能显式开启，两种方式：

- 配置 `linked_groups: [[g1, g2], [g3]]`，同一组内的群共享群上下文。组外仍隔离。
- 用户级别 opt-in：用户主动开启后，其 `user:<user_id>` 记忆才会被任何会话读取。

可以跨的：昵称、自述偏好、显式记下的事实。不能跨的：群聊原文、别人的发言、未开启共享的群上下文。

key 从 `MessageEvent` 推导：`get_group_id()` 非空即为群，否则为私聊；`get_sender_id()` 为 user_id。不依赖 `unified_msg_origin` 字符串拆分，避免平台间格式差异。

## 2. 人格（Persona）

字段：`name`、`system_prompt`、`greeting`（首次进入会话时发送）、`tools`（可用工具白名单，空列表表示不调用工具）、`style`（温度、最大回复长度等，透传给 Provider）。

绑定优先级，从高到低：会话（群内个人或私聊）> 群 > 平台实例 > 全局默认。每层可以不设，向下回退。

指令：`/persona list`、`/persona set <name>`、`/persona reset`。只有管理员可以切换：群里是群主、群管理员或 `admins_id` 里的 bot 管理员；私聊里只有 bot 管理员。群成员身份用 NapCat 的 `get_group_member_info` 查 `role` 字段。

可选联动：切换人格时调 `set_group_card` 同步群名片为人格名称；人格配置了 `voice` 时，回复改走 `send_group_ai_record`（角色列表来自 `get_ai_characters`）。两者默认关闭。

## 3. 群上下文感知

未唤醒的群消息也要记录。在 `WakingCheckStage` 判定不唤醒之前，把消息追加到该群的环形缓冲：条数上限（默认 50）和 token 上限（默认 2000）取先到者。唤醒时把缓冲作为上下文注入，每条带发送者昵称和群名片。

启动或重连时调 `get_group_msg_history` 回填群缓冲，私聊对应 `get_friend_msg_history`。只回填一次，避免每次重连都拉全量历史。

引用：消息含 reply 段时调 `get_msg` 取被引用内容，作为额外上下文。合并转发：收到时调 `get_forward_msg` 展开；回复超过长度阈值时改用 `send_forward_msg` 发送。

防自回环：`message_sent` 事件和 `report_self_message` 为真时上报的自身消息不进入唤醒判定，也不写入缓冲。现有的 `ignore_bot_self_message` 只比对发送者 id，覆盖不到这两种上报，需要在适配器里单独过滤。

## 4. 交互细节（按价值取舍）

- 戳一戳：`notice_type=notify, sub_type=poke` 且目标是机器人时视为唤醒，可选回应 `send_poke`。
- 表情回应：开始处理时调 `set_msg_emoji_like` 表示收到，处理完撤回。
- 已读：回复后调 `mark_msg_as_read`。
- 长回复：超过阈值改用合并转发（见第 3 节）。
- @ 与引用：回复时带 reply 段引用原消息；群里另外 @ 发送者。两者可以同时存在。

不做：群文件、群管理（禁言、踢人）、小程序卡片。这些已有 `call_action` 可以调，不需要框架层封装。

## 5. 最小 LLM Provider

人格需要模型。做一个 OpenAI 兼容的最小实现：

- 流式输出，超时，失败按指数退避重试（最多 3 次），并发上限。
- Key 走 `ENV:`，支持多个 Key 轮换。
- 上下文组装：system prompt（来自人格）+ 群缓冲 + 会话历史 + 用户记忆（如已 opt-in）+ 当前消息。总 token 超过预算（默认 4000）时，从最早的会话历史开始截断，system prompt 和当前消息不截。

测试一律用模拟 LLM（本地 aiohttp 服务器返回预设流式响应），不调用真实接口。

## 6. 存储

复用 `verter/core/db.py` 的迁移机制，新增一个版本：

- `persona`：人格定义（name 主键，其余字段 JSON）。
- `persona_binding`：绑定（scope、scope_key、persona_name）。
- `conversation`：会话历史（scope_key、role、content、时间戳）。
- `memory`：用户全局记忆（user_id、key、value），仅 opt-in 用户有数据。

写入合并：会话历史按会话累积，每轮对话结束或达到条数阈值时一次性落盘，不逐条写。

清理：会话历史超过保留期（默认 30 天）或单会话超过条数上限时删除最早的。群缓冲只在内存，不落盘。

## 7. 验收

模拟 NapCat 对端测试，覆盖：

- 三层作用域的隔离：A 群内容不出现在 B 群回复里。
- `linked_groups` 开启后组内共享、组外仍隔离。
- 人格绑定优先级与 `/persona` 权限（非管理员被拒）。
- 未唤醒消息进缓冲、唤醒时注入、超限截断。
- 自身消息不触发唤醒。
- Provider：流式、超时重试、并发上限、token 预算截断。

新代码覆盖率不低于 85%，空载 RSS 预算不回退。

需要真实 NapCat 测试号手工验证（不阻塞合入）：群历史回填、合并转发收发、戳一戳唤醒、表情回应、AI 语音。
