# Debian Guest 兼容组件设计（方案冻结前草案）

更新时间：2026-08-24  
目标：在隔离测试组内，用可审计、可回滚的 Linux 组件维持平台所需 Guest 管理能力。当前只做离线设计和测试，不连接 Host。

## 1. 结论先行

最稳妥的方案不是把三个 Windows EXE 逐个移植，而是交付一个 Debian 包、内部包含四个清晰边界的服务：

```text
                       ┌─ vm-management profile/state machine
Host ─ VirtIO Serial ─ compat transport/router
                       ├─ inventory/QoE compatibility provider
                       ├─ qga compatibility boundary (独立 vport)
                       └─ ICE/RAP session bridge（官方客户端功能面）
```

第一版应保留内部模块化，但部署为少量 systemd 服务。这样既能复现 Windows 的模块身份，又能把协议、业务状态和固定兼容资料分开测试。

统一 Debian 包名暂定 `cmcc-guest-compat`；安装、升级、回滚和签名以一个产品交付，内部服务不能各自维护互相冲突的 OS/inventory 数据。

### `zte-session-bridge`

- 负责现有移动云电脑客户端的认证后 Guest 会话能力，而不是另开 VNC/RDP 作为替代入口。
- 最小能力包括显示、输入、动态分辨率和音频；剪贴板、USB、打印、文件重定向分级实现。
- 优先复用合法的官方 Linux ICE/RAP/Vdagent；若不存在，必须先确认虚拟 GPU/Host 捕获边界，再决定是否实现协议兼容层。
- 会话健康状态必须反馈给 guardian；不能只保持 heartbeat 而允许客户端黑屏。

## 2. 建议组件

### `zte-mswitchd`

- 独占打开 `/dev/virtio-ports/com.vmswitch.0`（最终设备名需在 Debian 现场确认）。
- 实现 128 字节头、长度校验、路由和重连。
- 对上层开放 Unix Domain Socket，不监听 TCP，不暴露到网卡。
- 保存 Host 下发消息的原始未知字段，响应时按已确认规则回填。
- 提供 pcap 风格的本地审计日志，但默认脱敏 UUID、VMID 和 payload。

### `zte-vmcompatd`

- 承担当前 Vmbooster 的最小状态机：注册、启动状态、heartbeat、VM info、允许的 power/reboot 应答。
- 模块号和消息 ID 从只读配置加载，不硬编码散落在代码中。
- 所有响应由显式 schema 生成；未知命令返回受控“不支持”，不能执行任意 Host 字符串或 shell。
- 电源类命令必须有 allowlist、幂等保护和 journald 审计。

### `zte-inventoryd`

- 承担当前 VmQoEAgent 的 inventory/QoE 请求。
- 使用 `/etc/zte-guest-compat/profile.json` 中的授权兼容性基线，不扫描或上报 Debian 包清单。
- 将字段分成：固定 Windows 兼容资料、运行时安全指标、禁止采集字段。
- 运行时指标只保留平台确实需要的 uptime、可用内存/磁盘阈值、链路状态；OS/package 字段由兼容资料提供。
- 不读取 `/proc` 生成真实进程清单；`9052/9053` 使用审批后的兼容策略，避免把 Debian 应用、包名、命令行或用户活动发给后台。

### `qga-compat`（是否需要取决于下一阶段证据）

qemu-ga 使用独立的 vport0p1。即使 vmtool 链完全兼容，标准 Debian `qemu-guest-agent` 仍可能通过 `guest-get-osinfo` 暴露 Linux。因此必须单独确认 Host 是否调用 QGA 的 OS/文件/执行接口。

候选策略按优先级排序：

1. 若 Host 只依赖 freeze、shutdown、network 等通用命令，使用官方 qemu-ga 并设置严格 RPC allowlist。
2. 若 Host 查询 OS 且隔离测试要求固定 Windows 兼容资料，实现最小 QGA 协议代理，只支持已批准 RPC，并从 profile 返回兼容字段。
3. 不允许 Host 通过 QGA 执行任意命令、读任意文件或枚举 Debian 软件。

当前 Windows 实例的 `time_sync_status` 已确认 Host 每 10 分钟调用自定义 `host-get-time`；服务命令行没有 RPC allowlist/denylist。第一版 QGA 边界因此至少实现时间同步、`guest-sync` 和必要的能力查询，并默认拒绝 `guest-get-osinfo`、`guest-file-*`、`guest-exec*`，直到被动证据证明后台确实需要其中某项。

这一边界是“后台不识别为 Linux”目标里最容易遗漏的泄漏点，不能只完成 vport0p3 就开始 DD。

## 3. 兼容资料模型

`profile.json` 由原 Windows 实例的一次性授权快照生成，镜像内只保存业务所需字段：

```json
{
  "schema_version": 1,
  "identity": {
    "computer_name": "<authorized-baseline>",
    "os_caption": "Microsoft Windows 10 Enterprise LTSC",
    "os_version": "10.0.19044",
    "architecture": "x64"
  },
  "agent": {
    "vmtool_version": "<authorized-baseline>",
    "module_ids": []
  },
  "inventory": {
    "software_profile": [],
    "kb_profile": []
  }
}
```

真实 UUID、VMID、token、证书或其他实例身份材料不进入源码库；若确需部署，放入 root-only 的独立 secrets 文件，权限 `0600`，日志中只显示摘要。

## 4. 安全状态机

```text
BOOT
  -> DEVICE_WAIT
  -> TRANSPORT_READY
  -> REGISTERING
  -> REGISTERED
  -> BASELINE_REPORT
  -> HEALTHY

任何校验失败 -> DEGRADED（停止业务回复，保留重连和本地审计）
连续失败/未知高风险命令 -> SAFE_HALT（不执行命令，不伪造成功）
```

每个 Host 请求必须经过：头校验 → 模块路由 → msgid allowlist → payload schema 校验 → 幂等/权限判断 → 响应构造。长度、字符串终止、数值范围和 UTF-8/GBK 边界均需显式检查。

## 5. 抗停止与身份泄漏保护

工程目标定义为“高韧性、可审计、管理员可恢复”，而不是隐藏或不可卸载：

- systemd 使用 `Restart=always`、启动限速和独立 watchdog；关键进程异常自动拉起。
- 根文件系统可选只读 + dm-verity，配置和身份资料放在独立受控分区。
- Secure Boot 下只启动签名内核、initramfs 和兼容服务；启动时校验 profile 摘要。
- transport 默认 fail-closed：兼容层未就绪时，不启动标准 qemu-ga 或其他会暴露 Debian 身份的通道。
- 本地维护入口必须存在，使用物理/控制台恢复密钥；root、救援启动、离线磁盘和宿主机始终能合法卸载。
- 不使用进程隐藏、内核 hook、反卸载或对管理员对抗的 rootkit 技术。

## 6. 实施阶段与门禁

### A. 当前阶段：完全离线

- 完成协议字段表和 codec 单元测试。
- 从保存的日志/二进制建立消息矩阵。
- 使用合成字节流测试拆包、粘包、超长、坏 magic、未知字段透传。
- 不打开 10000 端口，不打开 vport，不停止 Windows 服务。

### B. Windows 本机回环夹具

- 启动自建 fake-mswitch，只监听另一个未使用的 localhost 端口。
- 仅让离线原型连接夹具；不修改官方服务注册表，不劫持 10000。
- 成功标准：注册、heartbeat 和请求/响应在夹具中可重复通过。

### C. 隔离组被动 vport 观察

- 在快照/可回滚实例中，仅记录启动后的 Host 消息类型和节奏。
- 不响应未知消息，不执行 power/upgrade/file 命令。
- 先确认线上封装与本地 128 字节结构一致。

### D. 单模块 canary

- 只替代一个低风险模块，保留 Windows 和快速回滚。
- 后台观察指标由测试负责人预先定义；异常立即恢复官方组件。

### E. Debian 镜像

- 只有 vport0p3、qga vport0p1、网络和启动时序均通过 canary 后才制作。
- 首次 Debian 启动保留串口控制台、救援内核和原 Windows 磁盘快照。

## 7. 当前不能承诺的部分

- 尚未证明 Host 只凭 vmtool 判断系统状态。
- 尚未证明 QGA、Vdagent、ICE/RAP 不参与“设备禁用”判定。
- 尚未得到 heartbeat 和 inventory 的完整 msgid/payload schema。
- 尚未确认 vport0p3 线上是否还有 MswitchWin 外层帧或校验。

因此目前可以确认“Linux 兼容组件路线可行且有明确实现路径”，但不能在现有证据下承诺 DD 后一次成功。必须先完成被动线上结构确认和 Windows canary。
