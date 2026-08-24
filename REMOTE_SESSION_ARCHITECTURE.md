# 官方客户端远程会话架构

更新时间：2026-08-24  
证据：当前实例进程树、TCP 连接、VirtIO Serial 句柄、冷启动/客户端连接日志。

## 1. 当前 Windows 会话路径

```text
移动云客户端 / 平台接入点
          │
          │ 多条外部 TCP，会话实例当前指向 100.121.21.22:19xxx
          ▼
      IceTunnel.exe  (Guest 172.20.176.122:5100)
          │ localhost
          ▼
      IceDisplay.exe (127.0.0.1:60063，本地会话枢纽)
       ├─ IceVGPUCapture.exe  画面捕获
       ├─ IceInput.exe        输入注入
       ├─ IceSound.exe        音频 + vport0p4
       └─ Vdagent.exe         会话/分辨率/剪贴板 + vport0p2

      IceTunnel ─ localhost:3246 ─ UsbIpc.exe ─ 20 个 VirtIO Serial 端口
      IceTunnel ─ ZDP-clipboard / RedirectAgent / RedirectProxy
```

`IceMainService.exe` 是主要父服务；`IceInputService.exe` 管理 IceInput；`Vdservice.exe` 管理用户会话中的 Vdagent。

## 2. 已确认职责

2026-08-24 只读连接快照再次确认 `IceDisplay:60063` 同时维持 5 条独立 localhost TCP 会话，客户端分别为 IceTunnel、IceVGPUCapture、IceInput、IceSound 和 Vdagent。这个进程/连接边界是真实可分离的；但仅凭 TCP 分离尚不能证明消息 schema 是公开、稳定或可替换的插件 ABI。

| 组件 | 当前证据 | 最可能的 Linux 对应边界 |
|---|---|---|
| IceTunnel | 唯一持有多条外部会话 TCP；本地连接 IceDisplay/USB/clipboard | 认证、加密、复用和外部会话 transport |
| IceDisplay | 本地 60063 汇聚 Tunnel、Capture、Input、Sound、Vdagent | 会话控制与显示通道枢纽 |
| IceVGPUCapture | IceMainService 子进程，与 IceDisplay 建立本地连接 | DRM/KMS/PipeWire/X11/Wayland 画面捕获与编码入口 |
| IceInput | 与 IceDisplay 建立本地连接，客户端活动时有持续 I/O | uinput/libinput 输入注入 |
| IceSound | 与 IceDisplay 建立本地连接并独占 vport0p4 | PipeWire/PulseAudio 音频桥 |
| Vdagent | vport0p2 + IceDisplay 本地连接；记录动态分辨率 | Linux session agent、分辨率和剪贴板 |
| UsbIpc | 20 个 vport + IceTunnel localhost | USB/打印/部分设备重定向，可分阶段 |
| ZDP-clipboard | Vdagent 与 IceTunnel 之间的 localhost 桥 | Wayland/X11 clipboard bridge |

## 3. 对 Debian 方案的直接影响

- 官方客户端的画面不是仅靠 Host 读取一个通用虚拟显卡就已完成；当前 Guest 内明确运行 `IceVGPUCapture` 并把数据送入 IceDisplay/IceTunnel。
- 只装 Debian VirtIO GPU、SPICE agent 或普通 qemu-ga，无法据现有证据保证官方客户端出现画面。
- vport0p3 身份兼容成功也不代表客户端功能成功；会话链与管理链是两个独立故障域。
- Linux session bridge 至少需要接入现有 ICE transport，或者获得官方 Linux ICE/RAP 构建。自己完整复制 Tunnel 的认证、加密和媒体协议将显著扩大工期与风险。

### `x11ice` 线索的准确含义

- 已证明：IceTunnel 的可达代码会从 socket 对象取得一个 PID 字符串，读取 `/proc/<pid>/cmdline` 并检查 `x11ice`；失败时把该 socket 标为异常。
- 当前最优假设：Linux/X11 会话端由独立 `x11ice` 进程承载，Tunnel 负责监视其生命周期。
- 不能推出：本机已有 Linux 包、当前资源池支持该包，或 `x11ice` 单独覆盖显示/输入/音频全部能力。
- 详细地址与证据见 `X11ICE_STATIC_ANALYSIS.md`。
- IceDisplay/Capture/Input/Sound 的字符串集有大量重叠，表明多个 EXE 静态链接了公共 `base_func.c`、`sock_func.c`、SPICE/捕获代码。组件中出现某个函数名只能证明代码被打包；在没有代码交叉引用或动态日志前，不能直接据此判定该组件实际执行该职责。

### localhost 连接头

- IceVGPUCapture、IceInput、IceSound 的可达连接初始化代码都会向本地 socket 写入 4 字节 `LinkHeader_t=0x0000009a`；它是公共握手常量，不是 Capture 角色编号。
- 后续通用消息解析另行检查单字节 magic `0xaa`；连接角色头和消息 framing 是两个阶段。
- 组件角色位于后续注册消息；当前不能用 `0x9a` 区分 Capture/Input/Sound/Vdagent，也不能据此宣称插件 ABI 已恢复。
- 已恢复后续注册类型：Input=`3/4`、Sound=`5/6`、Capture=`12`。前四个与 SPICE inputs/cursor/playback/record 编号一致，Capture `12` 是 ZTE 扩展。
- 详细证据见 `SESSION_LOCAL_IPC_FINDINGS.md`。

### Capture 数据面判断

- 已证明 IceVGPUCapture 包含驱动共享文件/surface/dirty-rect 路径，也包含 DDA/DXGI/D3D11 捕获和多种 GPU/软件编码路径。
- 因而“Linux 只把 framebuffer 地址交给 IceDisplay”不是当前证据支持的简单替代方式；更可能需要在 Linux 侧完成捕获和编码，再接入本地会话协议。
- 当前实例已通过间隔只读比较证明 `video_0.dat` 的控制区与像素区都持续变化；IceDisplay 同时包含明确的同名映射消费代码。因此共享映射是活动数据面，而非仅凭文件存在作出的推断。
- 映射布局总长 `35,426,672` 字节，等于 `0x9170` 控制区加 `4096×2160×4` 像素区。
- Input/Sound 的 SPICE 编号复用降低了语义分析难度，但并未消除 ZTE localhost framing、登录状态机和 Capture type `12` 的实现工作量。

## 4. 实现路线优先级

### 路线 A：官方 Linux ICE/RAP（首选）

取得与资源池兼容的 Linux IceTunnel/IceDisplay/session agent。`cmcc-guest-compat` 负责安装、配置、身份 profile 和 guardian，不重写媒体协议。

### 路线 B：保留官方 transport，替换 Guest 采集插件

若 Windows IceTunnel/IceDisplay 的协议与采集插件边界可分离，Linux 只实现 capture/input/sound/session adapter。需要确认是否存在插件 ABI、独立 IPC schema 或官方跨平台库。

### 路线 C：完整 Linux session bridge

只有 A/B 不成立时考虑。必须还原认证、加密、通道复用、显示编码、输入、音频与重连状态机；这不是简单驱动移植，不能与管理协议实现混为一项。

## 5. 下一步只读问题

1. 静态查找 IceDisplay 与 IceVGPUCapture 的 IPC 消息、端口发现和插件加载方式。
2. 检查 ICE 二进制是否包含 Linux/cross-platform 构建路径、Qt/GStreamer/FFmpeg 或动态采集 DLL 接口。
3. 分离客户端最小能力：先显示+输入，再音频/剪贴板，最后 USB/打印。
4. 从冷启动日志确定认证、Tunnel、Display、Capture、Vdagent 的严格启动顺序和超时。
5. 通过官方支持渠道确认 `x11ice` 的包名、版本矩阵和资源池许可；在取得包前不把该线索当作可部署方案。
# Debian/Wine 动态 PoC（2026-08-24）

- 在无虚拟网卡的 Debian 13 VM 中，以 Wine SCM 服务语义启动 `IceMainService` 后，已观察到它拉起 `IceTunnel`、`IceDisplay`、`IceDisplaySetting`。
- 成功窗口中 Wine 建立 `60063` 和 `5100` 监听，证明 Windows 核心会话服务可在 Wine 下到达真实监听阶段。
- `IceInputService`、`IceSound`、`IceInput`、`IceVGPUCapture` 也可形成进程，但五组件并发触发 Wine 10 wineserver fd 断言；尚未形成稳定完整会话。
- 此结果支持“保留官方 Windows transport/control，替换或桥接 Linux 采集/输入源”的路线，但当前不满足最终镜像稳定性门禁。
