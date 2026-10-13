# S0 沙箱技术验证报告

本报告对应 [P1_S0_PLAN.md](P1_S0_PLAN.md) 第 4 节。原型通过 `verter-sandbox exec` 调用，P0 的 NDJSON 协议 v1 没有改动。Docker 与 macOS 不在本阶段范围内。

## 1. 结论

| 系统 | 默认 | 回退 | 都不行 |
|------|------|------|--------|
| Linux | bubblewrap（user namespace 探测成功） | Landlock + seccomp + rlimit | 拒绝执行 |
| Windows | 已初始化、且与网络策略匹配的隔离用户 | AppContainer + Job Object | 拒绝执行 |

`auto` 在两个系统上都不会在没有隔离时执行命令。Windows 的受限令牌不能强制断网，因此只在允许网络时才能被显式选中，`auto` 不会用它来实现断网。

Linux `echo` 中位数在编写本报告的主机上低于 50 ms（见第 3 节）。Windows `echo` 中位数不能在这台 Linux 主机上测量；`sandbox-windows` 任务断言小于 200 ms，并在日志中打印 `echo overhead median via ...`。

## 2. 实验矩阵

| 平台 | 环境 | 方案 | 本阶段结果 |
|------|------|------|------------|
| Linux | 编写主机（Ubuntu 24.04，内核 6.12，LSM 为 capability,landlock,selinux，bwrap 0.9.0，user namespace 可用） | bubblewrap + seccomp + rlimit | 安全子集通过。`/bin`、`/lib`、`/lib64` 为符号链接，探测和正式挂载都按符号链接挂上，否则动态链接器缺失 |
| Linux | 同上，显式 `--backend landlock` | Landlock ABI V3 + seccomp + rlimit，无命名空间 | 安全子集通过。Landlock 未生效时拒绝执行 |
| Linux | GitHub `ubuntu-24.04`，`kernel.apparmor_restrict_unprivileged_userns=0` | bubblewrap | `sandbox-linux` 任务安装 bubblewrap 后跑同一套测试 |
| Linux | GitHub `ubuntu-24.04`，该 sysctl 为 1 | Landlock 回退 | 同一任务的 restricted 矩阵。`auto` 探测 user namespace 失败后改用 Landlock，不降级成裸执行 |
| Windows | `windows-2025`，非管理员 | AppContainer + Job Object + 工作区 ACL | `check` 与 `sandbox-windows` 都会跑。不修改 Python 安装目录的 ACL |
| Windows | `windows-2025`，`VERTER_SANDBOX_ADMIN=1` | 离线 / 在线本地用户 + 出站防火墙 + Job Object | 仅 `sandbox-windows` 执行 `setup --install`，结束时 `setup --uninstall` |
| Windows | 任意 | 受限令牌 + Job Object | 断网请求直接拒绝。允许网络时可以显式 `--backend restricted` |

每个已实现的格子都覆盖 DEV_PLAN 6.6 的子集：越界写入、读取工作区外的秘密文件、外网连接、进程数上限（fork）、内存炸弹、超时后结束进程树、清空父进程环境。Windows 另打印兼容性矩阵：`compat python|powershell|pwsh|cmd|git: exit N`。断言只有 cmd 必须成功；python、PowerShell、git 的失败被记录，不单独让任务失败。是否给 Python 安装目录加 ACL，留到看到该矩阵后再决定。

编写主机上的 Linux 安全子集已在本地跑过。Windows 格子由 CI 执行，本报告不编造 Windows 通过与否。

## 3. 开销

测量方式与测试相同：预热一次，再取 5 次墙钟的中位数。Linux 测的是 `verter-sandbox exec` 包住 `/usr/bin/echo`。Windows 测的是包住 `cmd /c echo ok`。

| 后端 | 中位数 | 预算 |
|------|--------|------|
| Linux bubblewrap | 9.77 ms（编写主机） | < 50 ms |
| Linux landlock | 7.73 ms（编写主机） | < 50 ms |
| Windows AppContainer | 编写主机无法测量。CI 日志打印实测值，并断言 < 200 ms | < 200 ms |
| Windows 隔离用户 | 同上 | < 200 ms |

10 ms 这一档主要是进程启动和挂载，不是沙箱策略计算本身。若 CI 打印的 Windows 中位数超过 200 ms，应把日志中的数字补进本表，并说明原因后再改预算，而不是放宽断言了事。

## 4. 默认方案与回退顺序

Linux `auto`：

1. 用与正式沙箱相同的只读挂载探测 bubblewrap user namespace。探测失败（例如 AppArmor 禁止非特权 user namespace）则视为不可用。
2. 探测子进程里的 Landlock。`FullyEnforced` 或 `PartiallyEnforced` 就可以作为回退。`NotEnforced` 则不可用。探测发生在子进程里，`doctor` 自己的进程不会被 Landlock 限制。
3. 两者都不可用时，`exec` 返回错误，命令不会在宿主机上运行。

bubblewrap 路径在子进程里设置 rlimit 和 seccomp（拒绝 ptrace、io_uring；断网时再拒绝 connect / sendto / sendmsg / sendmmsg），不使用 `bwrap --seccomp` 的文件描述符，因此 Linux 代码没有 `unsafe`。断网使用 `--unshare-net`。`--die-with-parent` 加上父进程在超时时结束 bwrap，用来回收进程树。

Landlock 路径对 `/usr`、`/etc`、`/dev`、`/proc` 只读，对工作区可写，并对 `/bin`、`/lib`、`/lib64` 的符号链接 inode 授予读和执行，否则动态链接器无法被打开。然后再设置 rlimit 与同一套 seccomp。

Windows `auto`（断网）：

1. 离线用户 `vsb-offline` 已由 `setup --install` 创建，且密码文件存在。
2. 否则，若 `verter.sandbox` 这个 AppContainer 配置文件可用，则用它。不授予网络能力。
3. 否则拒绝。

允许网络时，第 1 步改为在线用户 `vsb-online`（没有出站防火墙规则）。受限令牌不参与 `auto`。账户名控制在 20 个字符以内，以符合 SAM 限制。

显式 `--backend restricted` 在断网时拒绝。隔离用户未初始化时，`--backend isolated` 拒绝。

进程都是挂起创建、加入带 `KILL_ON_JOB_CLOSE` 的 Job Object 之后才恢复运行。加入失败会终止进程，避免裸跑。超时调用 `TerminateJobObject`，CLI 退出码 124。

## 5. 需要 sudo 或管理员的步骤

| 步骤 | 权限 | 说明 |
|------|------|------|
| Linux 日常 `exec` | 无 | bubblewrap 或 Landlock 都不需要 root |
| `verter-sandbox setup`（Linux，AppArmor 不是当前 LSM） | 无 | 本机即是这种状态，命令成功并说明没有安装配置文件 |
| `verter-sandbox setup --install`（Linux，AppArmor 是当前 LSM） | 一次 sudo | 写入 `/etc/apparmor.d/bwrap` 并加载。配置文件是本仓库自己的 user namespace 例外，不是从其他项目复制的 |
| Windows AppContainer `exec` | 无 | 会创建名为 `verter.sandbox` 的 AppContainer 配置文件（已存在则复用） |
| `verter-sandbox setup --install`（Windows） | 一次管理员 | `net user` 创建 `vsb-offline` / `vsb-online`（SAM 名称不超过 20 字符）。密码固定 14 个字符，避免 `net user` 因超过 LAN Manager 限制而交互询问。密码写在 `C:\ProgramData\verter-sandbox\`。用 `New-NetFirewallRule -LocalUser` 阻断离线用户的出站流量（Server 2025 的 `netsh` 不接受 `localuser=`） |
| `verter-sandbox setup --uninstall`（Windows） | 管理员 | 删除防火墙规则、两个用户和密码目录 |
| 普通 Windows `check` 任务 | 无 | 不设置 `VERTER_SANDBOX_ADMIN`，因此不创建用户 |

密码会出现在 `net user` 的命令行参数里。这是本阶段接受的限制：失败时只报告账户名，不把 `net` 的输出写进错误信息。后续如果进入正式后端，应改为不把密码放进进程命令行。

## 6. 能力如何如实报告

人类可读的 `verter-sandbox doctor`：

- Linux 打印 `auto:`、`fallback:`、`policy: exec refuses to run without isolation`。bubblewrap 可用时，协议里的 `bubblewrap.detail` 仍是可执行文件路径。
- user namespace 探测失败时，`detail` 改为探测错误，并说明将使用 Landlock（若 Landlock 可用）。不会把 Landlock 或 Windows 后端塞进协议 v1 的 `BackendKind`（该枚举仍只有 `bubblewrap`）。
- Windows 的协议 JSON 里 `backends` 为空。人类可读输出另起几行：`auto:`、`appcontainer:`、`isolated offline:`、`isolated online:` 和同一条 `policy:`。没有初始化时会写明需要管理员执行 `setup --install`。

概览页还没有沙箱面板（那是 P4）。规则先记在这里：那一页只能展示 `doctor` 报告的实际后端，不能在 `auto: none` 时显示已隔离，也不能在隔离不可用时仍提供执行按钮。

## 7. 协议 v2 草案

v1 保持 hello / hello_ack / ping / pong / shutdown。下面的消息只是草案，没有写入 `sandbox/schema/protocol.schema.json`。

客户端请求：

```json
{
  "type": "exec",
  "id": 1,
  "command": ["cmd", "/c", "echo ok"],
  "workspace": "C:\\sessions\\1",
  "backend": "auto",
  "network": false,
  "timeout_ms": 30000,
  "memory_bytes": 536870912,
  "nproc": 256
}
```

服务端按块推送标准输出和标准错误，最后给退出消息。块内容用 base64，避免 NDJSON 被二进制截断：

```json
{"type": "exec_stdout", "id": 1, "chunk_b64": "b2sNCg=="}
{"type": "exec_stderr", "id": 1, "chunk_b64": ""}
{"type": "exec_exit", "id": 1, "exit_code": 0, "timed_out": false, "backend": "appcontainer", "wall_ms": 40}
```

`backend` 取值与本报告的实现一致：`bubblewrap`、`landlock`、`appcontainer`、`restricted`、`isolated`。拒绝执行时不发 `exec_exit`，而是沿用错误通道说明原因（隔离不可用、工作区无效、参数溢出）。超时的 `exit_code` 为 124，`timed_out` 为 true。父进程环境不进入沙箱；v2 若要额外环境变量，必须在 `exec` 里显式列出，不能隐式继承。

## 8. 已知限制

- 不实现 Docker 后端，也不实现 macOS。
- 不把 Windows 或 Landlock 加入协议 v1 的 `BackendKind`。
- Windows 代码只在 `sandbox/crates/windows` 允许 `unsafe`，每处都有 `SAFETY` 注释，并且 `undocumented_unsafe_blocks` 为 deny。Linux 原型没有 `unsafe`。
- 受限令牌去掉了特权，但拦不住出站连接。
- AppContainer 能否运行 python、PowerShell 7 和 git，取决于那些程序的安装目录是否允许 AppContainer 读取。本阶段不修改那些 ACL。
- Job Object 的内存上限是提交量。内存炸弹用 512 MB 上限去分配 800 MB，给 PowerShell 自己的启动留出余量。
- 隔离用户模式是专用用户、防火墙和 Job Object。长期设计里“再套一层受限令牌”没有做进这个原型。
