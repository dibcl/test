# 中国移动公众版云电脑原生 Debian 可行性阶段报告

报告时间：2026-08-24  
测试对象：中国移动公众版云电脑，官方 Windows 10 Enterprise LTSC 镜像  
调查方式：本机只读检查、冷启动日志交叉验证、PE/签名/哈希分析、公开资料与专利核验

## 一、执行摘要

### 阶段结论

1. **原生启动 Debian：硬件层面高可行。** 当前实例是 QEMU/KVM 类虚拟机，系统盘、数据盘、网卡和串口均为 Linux 内核原生支持良好的 VirtIO 设备；SeaBIOS/Legacy BIOS 也受 Debian 支持。
2. **保留控制台开关机/重启：高概率可行但尚未在 Debian 实测。** 此类能力通常位于 Hypervisor/Host 生命周期层；当前证据尚不能把它写成已验证。
3. **保留中国移动 Guest 正常状态与管理：当前不可判定为可行。** Windows 当前依赖 ZTE `MswitchWin + Vmbooster + VmBoosterMonitor + VmQoEAgent`，经 VirtIO Serial 与 Host 交互；标准 Linux `qemu-guest-agent` 不能覆盖这套管理链。
4. **保留官方客户端桌面：当前低可行。** 官方桌面依赖 ZTE ICE/RAP/Vdagent、显示/输入/音频和重定向组件，尚未获得可验证的 Linux 安装包。
5. **直接 DD Debian：不满足生产验证条件。** DD 工具会覆盖系统盘，且不会自动向 Linux 镜像注入 ZTE Guest 管理组件；公众版缺少已确认的应急控制台与无损回滚路径。

因此，本阶段的正式结论为：

> **Debian 裸机运行在计算与 VirtIO 硬件层面可行；在当前中国移动公众版 ZTE/vmtool 资源池内，保持 Guest 管理状态和官方桌面能力尚未证明可行。现阶段不具备在唯一生产实例上直接 DD 并验收的工程条件，需要可牺牲测试实例、官方恢复保障或兼容 Linux Guest 组件后再开展破坏性 PoC。**

## 二、实际完成的验证

### 1. 虚拟硬件

- 两块 Red Hat VirtIO SCSI 磁盘：80 GB 与 500 GB。
- VirtIO 网卡。
- 两个 VirtIO Serial 控制器，共 24 个 vport。
- SeaBIOS、Legacy BIOS/MBR。
- 未开放 nested virtualization，因此 WSL2/Hyper-V 不可用；QEMU TCG 已测试但性能仅约 Windows 原生的 17%–22%，不满足目标。

只读磁盘/启动检查补充：

- Disk 0：80 GB，MBR，包含约 50 MB 的活动“系统保留”分区和 Windows C:；Windows Boot Manager 位于 Disk 0。
- Disk 1：500 GB，MBR，当前整个磁盘为单一 D: NTFS 分区，约 473 GB 可用空间；没有现成的未分配空间。
- C:/D: 均未启用 BitLocker。
- BCD 仅有 Windows 10；`recoveryenabled=No`，WinRE 条目指向 `unknown`，不能把本机恢复环境视为可靠回滚手段。
- 因此，虽然 Disk 1 容量足够通过缩分区容纳 Debian，但缩分区、写 Disk 1 MBR、改变 BIOS/BCD/链式引导中的任一项都会改变系统状态；当前不存在无需修改启动链即可直接从 Disk 1 原生启动 Linux 的已证明路径。

### 2. 当前 Guest 管理链

已通过 2026-08-23 23:40 控制台冷启动日志确认：

```text
VmBoosterMonitor ─┐
Vmbooster ────────┼→ 127.0.0.1:10000 → MswitchWin
VmQoEAgent ───────┘                         │
                                            └→ vport0p3 / com.vmswitch.0 → Host
```

- Vmbooster：采集并上报 OS、IP、MAC、网络和运行时间，启动 heartbeat/report 线程并接收 Host 响应。
- VmQoEAgent：采集计算机名、CPU、真实 Windows 版本、位数、内存、磁盘、网络、软件和 KB，并汇集 ICE/会话 QoE 数据。
- VmBoosterMonitor：确定为 Mswitch 独立客户端；健康监控职责高概率成立，但具体业务边界仍待验证。
- qemu-ga：独占 vport0p1，是独立于上述 vmtool 链的基础 Guest Agent。

### 3. 官方桌面链

- vport0p2→Vdagent。
- vport0p4→IceSound。
- IceTunnel、IceDisplay、IceInput、Vdservice/Vdagent、RedirectAgent/Proxy、UsbIpc 组成独立 ICE/RAP 会话体系。
- 已观察到 vmtool 管理链正常而官方客户端会话失败的现场，因此管理状态与官方桌面连接不能视为同一能力。

### 4. 更新与组件体系

- 当前版本：ZTEGuestOS/vmtool `V7.25.21SP3-9`，vmbooster `V7.25.21SP3pv-9`，ICE `V7.25.21-13`，Vdagent `V7.25.21_20`。
- Windows 二进制含 `ZXCLOUD-iVMC-ComponentV7.25.21/.../windows/...` 构建路径。
- uSmartviewUpdate 静态支持按 OS type、bit、ClientType、objectType 查询，但当前日志组件注册数为零，没有当前实例查询/下载 Linux 包的证据。
- 本机未发现 `.deb`、`.rpm` 或 Linux/UOS/麒麟/Ubuntu/Debian 命名的 ZTE Guest 包。

## 三、尚未完成或不能宣称完成的验证

1. 没有在本测试对象上启动过原生 Debian，因此不能把 Debian 下的控制面状态写成“实测正常”。
2. 没有证据证明测试对象本人曾因 DD Debian 被平台禁用；相关失败现象目前来自公开社区案例，不能冒充本机测试结果。
3. 没有获得中国移动公众版 Guest 正常判据、超时阈值或 Linux 支持矩阵。
4. 没有找到可验证来源的 ZTE Linux vmbooster/vmmonitor/ICE/RAP 包。
5. 没有执行停止 Agent、篡改串口、协议重放或 OS 身份伪装测试。

## 四、可行性矩阵

| 目标 | 当前判断 | 证据状态 |
|---|---|---|
| Debian 使用 VirtIO 磁盘和网络启动 | 高可行 | 架构确认，尚未原生启动实测 |
| SSH/普通 Linux 网络服务 | 高可行 | 硬件支持推断，尚未实测 |
| 标准 qemu-guest-agent | 高可行 | Debian 原生包可用；Host 兼容性尚未实测 |
| 控制台开关机/重启 | 高概率可行 | Host 层推断，尚未 Debian 实测 |
| 平台 Guest 正常状态 | 未证明 | 缺少 ZTE Linux 管理 Agent/接口 |
| OS/网络/资产 inventory | 未证明 | Windows 由 Vmbooster/VmQoEAgent 完成 |
| 官方客户端图形桌面 | 低可行 | 缺少 Linux ICE/RAP/Vdagent 证据 |
| 在唯一实例上直接 DD 后稳定运行 | 不建议判定可行 | 无应急控制台、无已验证回滚、管理链会消失 |
| 保留 Windows、从 Disk 1 双启动 Debian | 技术上可设计，当前不宜直接执行 | D: 需缩分区且需修改/链式接管启动链；WinRE 不可靠 |

## 五、破坏性 PoC 的必要前置条件

满足以下任一组条件后，才适合开展实际 DD/原生启动测试：

### 方案 A：受支持测试

- 中国移动或 ZTE 确认该资源池支持 Linux Guest；
- 提供对应 Guest Agent/镜像或安装说明；
- 明确恢复、解锁和回退流程。

### 方案 B：隔离 PoC

- 提供可牺牲的同资源池测试实例；
- 测试失败不会影响生产账号/正式实例；
- 有控制台重装或人工恢复保障；
- 已获得测试授权并定义观察窗口、成功标准和停止条件。

### 方案 C：可回滚启动验证

- 保留原 Windows 系统盘或平台快照；
- Linux 从独立系统盘启动且无需改写 Windows；
- 能在 Guest Agent 失联时由控制面明确切回 Windows；
- 切换操作得到平台方确认。

## 六、建议测试用例

在满足前置条件后，按以下顺序测试，每一步失败立即停止：

1. Linux 启动、VirtIO SCSI、网卡和 DHCP。
2. Host 控制台显示的 VM power state。
3. 标准 qemu-guest-agent 的基础 ping、关机和重启能力。
4. Host 是否把 Guest 标记为异常、离线或维护状态。
5. 官方客户端是否能建立 ICE/RAP 会话。
6. 合法 Linux Guest Agent 的状态、inventory 与 power integration。
7. 连续运行、冷启动和多次控制台重启稳定性。

不得将伪造 Windows OS、伪造 heartbeat、复制实例身份或重放私有协议列为正式测试手段。

## 七、建议向开发/平台团队询证

1. 公众版当前 ZTE/vmtool 资源池是否支持 Linux Guest OS？
2. 对应 V7.25.21 的 Linux vmbooster/vmmonitor/GuestTools 包名与支持发行版是什么？
3. Host 判定 Guest 正常所需组件及超时阈值是什么？
4. 标准 qemu-guest-agent 能保留哪些控制能力？
5. Linux 是否有 RAP/ICE/Vdagent，是否支持 Debian/Ubuntu？
6. 能否提供同资源池测试实例、应急控制台、系统盘快照或人工恢复保障？
7. 自定义 OS 测试触发异常后是否自动禁用，如何恢复？

## 八、证据位置

- `RESEARCH_STATE.md`：确认事实与当前判断。
- `EVIDENCE_INDEX.md`：逐项证据文件、时间和关键行。
- `OPEN_QUESTIONS.md`：未解决问题。
- `NEXT_PLAN.md`：后续只读调查路线。
- `guest-coldboot-timeline.txt`：23:40 冷启动证据。
- `vmtool-client-role-timeline.txt`：Vmbooster/VmQoEAgent 当前职责证据。
- `mswitch-local-clients.txt`：Mswitch 三个客户端连接证据。
