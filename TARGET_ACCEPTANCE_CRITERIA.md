# Debian 最终目标与验收门禁

更新时间：2026-08-24  
适用范围：授权隔离测试组。

## 1. 最终交付形态

最终不是单一可执行文件，而是一个统一安装包 `cmcc-guest-compat`。用户只安装和管理一个 Debian 包，但包内按故障域拆分服务：

```text
cmcc-guest-compat
├─ session-bridge     官方客户端连接、显示、输入、分辨率、音频
├─ identity-bridge    vport0p3 管理协议、heartbeat、查询与上报
├─ qga-gate           vport0p1 时间同步及受控 QGA RPC
├─ profile-provider   固定 Windows OS/software/KB/agent 基线
└─ health-guardian    启动顺序、watchdog、自检、恢复与审计
```

Debian 内核、VirtIO block/network/GPU/input/audio 驱动保持原生；兼容包只接管平台交互边界，不伪装 Debian 用户空间内部环境。

## 2. 功能面验收

使用现有移动云电脑官方客户端完成以下测试：

| 项目 | 通过条件 |
|---|---|
| 客户端认证 | 使用原有实例入口成功认证，不新增手工端口或第二套远控 |
| 会话建立 | 后台出现 Authentication Success、client login、Display Channel Link Success 等等效状态 |
| 画面 | Debian 登录界面和桌面持续显示，无黑屏、冻结或仅控制台画面 |
| 输入 | 键盘、鼠标、组合键和中英文输入正常 |
| 分辨率 | 至少支持客户端窗口变化和当前基线分辨率切换 |
| 音频 | 播放音频正常；录音按现有资源池能力测试 |
| 剪贴板 | 文本双向；文件剪贴板是否要求按授权测试范围决定 |
| USB/打印/文件重定向 | 分项测试，不应因非核心重定向失败导致实例被判异常 |
| 网络 | Debian VirtIO 网卡获得预期地址，官方客户端会话不中断 |
| 重启恢复 | Debian 重启后服务自动启动，官方客户端在规定时间内重新连接 |

任何“后台显示在线但客户端黑屏”或“客户端可用但管理 Agent 异常”都算失败。

## 3. 管理身份面验收

后台主动查询与 Guest 主动上报必须使用同一个 Windows profile，不能出现跨通道矛盾。

| 类别 | 主动上报 | 被动查询应答 | 一致性要求 |
|---|---|---|---|
| OS | `9050` environment、OS report | QGA/管理命令中的 OS 查询 | Windows 版本、架构完全一致 |
| 软件/KB | `9054` 固定 inventory | 软件清单查询 | 同一快照、稳定排序和批次 |
| Agent | `4002`、版本报告 | Agent/version 查询 | vmtool/GuestTools 版本一致 |
| 机器身份 | computername、VM UUID/VMID | VM info 请求 | 只使用本实例授权身份材料 |
| 网络 | IP/MAC report | network 查询 | MAC 固定；IP 允许按实际网络受控更新 |
| 运行状态 | heartbeat、uptime | health/status 查询 | 时间单调、状态机一致，不产生不可能值 |
| 性能/QoE | `9051–9053/9056` | QoE 命令 | 不枚举或泄漏 Debian 软件、进程、命令行 |

失败示例：

- vport0p3 报 Windows，而 QGA 返回 Debian。
- inventory 报 Windows 软件，但 QoE 进程清单出现 `systemd`、`apt`、Linux 桌面或用户应用。
- 固定磁盘资料与真实容量矛盾到触发后台规则。
- heartbeat 继续发送，但客户端会话组件已失效。
- 组件启动前，标准 qemu-ga 抢先打开 vport0p1 并回答真实 OS。

## 4. profile 规则

- 基线来自覆盖 Windows 前的最后一次已批准快照。
- 固定字段：Windows OS、bitness、computername、vmtool/GuestTools 版本、软件和 KB 清单。
- 半动态字段：IP、可用磁盘、内存、uptime；按消息语义决定使用真实通用指标还是兼容值。
- 实例秘密：UUID、VMID、token、证书不得写进源码或普通日志。
- 所有应答由同一 provider 读取，禁止每个服务各维护一份身份数据。

## 5. 高韧性要求

- systemd 开机依赖顺序保证 identity/profile 就绪后才开放平台通道。
- `Restart=always`、软件 watchdog 和独立 guardian 负责崩溃恢复。
- 根文件系统可选只读 + dm-verity；包和配置使用签名清单校验。
- 配置采用原子更新，并保留 last-known-good profile。
- 连续失败进入 fail-closed：停止可能泄漏 Debian 的标准 Agent，保持本地控制台和恢复入口。
- 提供明确的管理员维护/卸载流程；不隐藏进程，不阻止 root，不使用 rootkit。

## 6. 当前最大技术风险

远程功能面比管理身份面更难。现有官方客户端会话明确依赖 Guest 内的 IceTunnel、IceDisplay、IceInput、IceSound、Vdagent 和 UsbIpc。尚未证明标准 Debian 图形栈能直接替代这些组件，也未取得可验证的官方 Linux 构建。

因此下一优先级是确定下列三条路线哪条可行：

1. 找到同版本/同资源池支持的官方 Linux ICE/RAP/Vdagent。
2. 证明 Host 能直接采集虚拟 GPU，Debian 只需标准 Linux VDAgent/输入/音频组件。
3. 若前两条均不成立，评估实现最小 Linux session-bridge 的协议规模；在完成协议边界前不承诺正式 DD。

