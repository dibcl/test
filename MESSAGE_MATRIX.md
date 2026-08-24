# Guest 管理消息矩阵（当前证据）

更新时间：2026-08-24  
范围：已有 Windows 日志与静态二进制；未向 Host 发送测试消息。

## 模块身份

| 组件 | 本地模块号 | 证据 |
|---|---:|---|
| Vmbooster | `0x80000001`（有符号 `-2147483647`） | `Register` 调用点直接常量 |
| VmBoosterMonitor | `0x80000002`（有符号 `-2147483646`） | 内嵌 Register 调用点直接常量 |
| VmQoEAgent | `0x80000011`（有符号 `-2147483631`） | `Register` 调用点直接常量 |
| Host 管理目标 | `0x80000000`（有符号 `-2147483648`） | Vmbooster 上行日志 |
| Host 辅助目标 | `6` | IP/OS 报告上行日志 |
| QoE/数据目标 | `10` | VmQoEAgent 上行日志 |

## Vmbooster 核心消息

| int_msgid | 方向 | dst_mod | 周期/时机 | payload | 结论 |
|---:|---|---:|---|---|---|
| `4002` | Guest→Host | `0x80000000` | 启动后约每 30 秒 | JSON：`msgtype, agentversion, vmid, agentstatus, computername, issysprep` | heartbeat/Agent 在线状态，确定 |
| `8008` | Guest→Host | `0x80000000` | 启动/重连 | `msgtype:'8008'` | VM info 请求，确定 |
| `8009` | Host→Guest（业务 msgtype） | Vmbooster | `8008` 后 | VM UUID 等 | VM info 响应，确定 |
| `8059` | Guest→Host | `0x80000000` | 启动 | 23 字节文本 | 会话/RDP 状态，高概率；payload 仍待精确确认 |
| `0x8102bf` | Guest→Host | `6` | 启动及网络变化 | IP/gateway/netmask/MAC/DNS/DHCP | IP/网络报告，确定 |
| `0x8102c1` | Host→Guest | Vmbooster | 对上条的响应 | 同长度路由响应 | IP 报告确认，确定 |
| `0x8102c5` | Guest→Host | `6` | 启动 | `OsName=Microsoft Windows 10(64);Osbit=1;ReslutFlag=1;` | OS 报告，确定 |
| `0x8102c7` | Host→Guest | Vmbooster | 对上条的响应 | 同长度路由响应 | OS 报告确认，确定 |
| `0` | Host→Guest | Vmbooster | 启动期间多条 | 文本业务消息，如 `msgtype=4100` | 某些 Host→Guest 消息只在 payload 内分派，确定 |

`4002` 是当前最强在线判据：历史统计 24545 条，平均间隔约 30.41 秒，当前冷启动后也稳定每 30 秒发送。

## VmQoEAgent 消息

全部已观察消息均为 `dst_type=1, dst_mod=10`。

| int_msgid | 周期/时机 | payload 类别 | 是否暴露真实 Debian |
|---:|---|---|---|
| `9055` | 启动一次 | `VmStartTime` 日志 JSON | 低；时间应真实，OS 无关 |
| `9050` | 启动/Agent 重启 | environment JSON：计算机名、CPU、OS、bit、内存、MAC/IP、磁盘、vmtool 版本 | 高；必须由 Windows 兼容 profile 生成 |
| `9054` | 启动/Agent 重启，通常连续 3 批 | software/KB JSON | 极高；不能枚举 Debian 包，必须使用兼容 profile |
| `9051` | 约每 5 分钟，内含 5 个一分钟样本 | CPU、内存、网络、磁盘 performance JSON | 中；可采集通用运行指标，但字段形态需保持 Windows 基线 |
| `9052` | 约每 5 分钟 | 进程 CPU/内存/句柄/磁盘/网络 Top 列表 | 极高；当前 Windows 会上传真实进程名 |
| `9053` | 事件触发及约 5 分钟 | ICE/QoE/用户活动/前台进程日志 | 极高；当前 Windows 会上传登录、显示、输入和进程事件 |
| `9056` | 网络/QoE 事件 | `vm_ice` 网络连通性表 | 中；可提供平台所需连通状态，不应附带 Debian 软件信息 |

已有日志直接证明 `9052` 上报进程名、PID、CPU、内存、句柄、磁盘 I/O 和网络 I/O；`9053` 上报登录、认证、显示分辨率、输入活动与部分进程事件。这两个消息是“后台不知道 Debian 实际软件”的主要边界，Linux 实现不能照搬真实 `/proc` 枚举。

## 身份泄漏面

| 通道 | Windows 组件 | Debian 风险 | 设计处理 |
|---|---|---|---|
| vport0p3 管理链 | MswitchWin + Vmbooster/QoE/Monitor | OS、inventory、进程、heartbeat | `zte-mswitchd` + profile 驱动应答 |
| vport0p1 QGA | qemu-ga | `guest-get-osinfo`、文件/执行接口可暴露 Linux | RPC allowlist；必要时使用最小兼容代理 |
| vport0p2 桌面 Agent | Vdagent | 分辨率、会话、剪贴板及可能的 OS 能力 | 后续静态/被动验证后决定替换或禁用 |
| ICE/RAP/QoE | ICEDisplay/IceTunnel/VmQoEAgent | 会话、进程、活动和性能遥测 | 只提供平台必须字段；不扫描 Debian 软件/进程 |
| 网络层 | DHCP/MAC/hostname | hostname、DHCP vendor class、MAC 变化 | 保持授权实例 MAC/hostname 策略；不伪造不必要字段 |

## 最小“后台正常”集合（当前判断）

1. 模块 `0x80000001` 注册成功。
2. `8008 → 8009` VM identity 握手完成并取得 VM UUID。
3. 每 30 秒发送结构稳定的 `4002` heartbeat。
4. 完成 `0x8102bf/0x8102c1` 网络报告与确认。
5. 完成 `0x8102c5/0x8102c7` Windows OS 报告与确认。
6. 模块 `0x80000011` 提供 `9050/9054` 的固定 Windows environment/inventory。
7. 对 `9051/9052/9053/9056` 采取明确策略，不能让标准 Linux 采集路径直接暴露 Debian。
8. 验证 QGA vport0p1 没有第二条真实 OS 信息通道。

是否必须注册 Monitor 模块 `0x80000002`、以及后台是否要求 QoE 全量持续上报，仍需从断连/重启日志和后续隔离 canary 验证。

## QGA 当前调用证据

- qemu-ga 以 LocalSystem 自动启动，命令行仅为 `qemu-ga.exe -d`，没有配置 allowlist/denylist 参数。
- `time_sync_status` 于当前实例运行期间更新，内容只记录启用的 `host-get-time`，周期为 10 分钟。
- 当前 ZTE/QEMU 构建静态包含 `guest-get-osinfo`、`guest-info`、`guest-network-get-interfaces`、`guest-file-*`、`guest-exec*` 和 `guest-shutdown`。
- 因此已确认的必要 QGA 能力是时间同步；尚未看到 Host 调用 OS/文件/执行 RPC 的动态证据，但这些入口当前确实可用，Debian 版不能默认全开放。
