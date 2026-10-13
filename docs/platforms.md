# 平台适配

P2 接入真实 IM。本页说明配置方式。CI 只用 `tests/fakes/` 里的模拟客户端，不依赖真实账号。

## OneBot v11（QQ / NapCat）

适配器类型：`onebot_v11`。只实现 WebSocket，不实现 HTTP POST。

主测对象是 [NapCat](https://github.com/NapNeko/NapCatQQ)。其它 OneBot v11 实现只要走标准事件和 `send_msg` 即可。

### 反向 WebSocket（推荐）

Verter-Bot 监听，NapCat 作为客户端连过来。

```yaml
platform:
  - id: qq_main
    type: onebot_v11
    enable: true
    ws_reverse_host: "127.0.0.1"
    ws_reverse_port: 6199
    access_token: "ENV:ONEBOT_ACCESS_TOKEN"
```

在 NapCat 里把反向 WS 填成 `ws://127.0.0.1:6199/`（或 `/ws`），token 与上面一致。`ENV:NAME` 会在启动时读环境变量。

握手时可用查询参数 `access_token` 或请求头 `Authorization: Bearer <token>`。

### 正向 WebSocket

Verter-Bot 去连 OneBot 实现暴露的 WS 服务：

```yaml
platform:
  - id: qq_forward
    type: onebot_v11
    enable: true
    ws_forward_url: "ws://127.0.0.1:3001"
    access_token: "ENV:ONEBOT_ACCESS_TOKEN"
```

反向与正向可以只配一种。连接断开后由 `PlatformManager` 退避重启。

### 消息段

| OneBot | Verter |
|--------|--------|
| `text` | `Plain` |
| `at`（`qq=all`） | `AtAll` |
| `at` | `At` |
| `image` | `Image` |
| `record` | `Record` |
| `reply` | `Reply` |
| `face` | `Face` |
| 其它 | `Unknown`（保留原生字段） |

群聊仍遵守唤醒前缀：没有 `@` 或指令前缀的消息不会进插件。
