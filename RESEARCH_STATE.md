# Research State

更新时间：2026-08-24（Asia/Shanghai）  
研究边界：默认只读；禁止伪造/重放私有协议，禁止改变 Guest Agent、服务、驱动、注册表、网络、启动盘或操作系统。

## 时间证据规则

- [确定] 2026-08-23 03:00 前仅作历史镜像/模板参考。
- [确定] 03:00–04:06 为灰区，不单独用于证明当前实例行为。
- [确定] 04:06 后可作当前实例证据；23:40 控制台整机重启后的冷启动日志可信度最高。

## 当前实例已确认

- [确定] Guest 是 Windows 10 Enterprise LTSC 10.0.19044 x64；冷启动动态采集结果明确为 64 位 Windows 10。
- [确定] VM 使用 VirtIO SCSI 磁盘、VirtIO 网络和两个 VirtIO Serial 控制器；当前映射为：vport0p1→qemu-ga、vport0p2→Vdagent、vport0p3→MswitchWin、vport0p4→IceSound；UsbIpc 占用其余 20 个端口。
- [确定] MswitchWin 在 `127.0.0.1:10000` 监听，三个已确认本地客户端是 VmBoosterMonitor、Vmbooster、VmQoEAgent；23:40 冷启动日志记录三次注册，连接快照确认三个 PID。
- [确定] MswitchWin 通过 `\\.\Global\com.vmswitch.0` 打开 vport0p3，并向 Host 写入数据。
- [确定] VmQoEAgent 在 23:40 冷启动时动态采集 computer name、CPU、真实 OS、bitness、memory、MAC、IP、disk、vmtool version、已装软件和 KB，并经 MswitchWin 写出；它还汇集 ICE authentication、client login、display/device/session 等 QoE 事件。
- [确定] Vmbooster 在当前实例中注册管理链，采集 OS、IP/MAC，向 Host 报告 OS/网络，启动运行时间、告警、computer-info/heartbeat 线程，并接收 Host 响应。
- [确定] qemu-ga 独占 vport0p1；ZTE GuestTools 安装日志显示当前 QGA/GuestTools 包版本 `3.18.56.215a7089`。现有证据尚未识别它在中国移动平台内的具体必需职责。
- [确定] ICE/RAP/Vdagent/UsbIpc 是远程显示、输入、音频、重定向与会话体系；它与 vmtool 管理链至少在进程、端口和串口层面分离。
- [确定] 当前 IceTunnel 是 Guest 内官方会话的外部 TCP 端点；IceDisplay 在 localhost:60063 汇聚 IceTunnel、IceVGPUCapture、IceInput、IceSound 和 Vdagent。IceVGPUCapture 的存在说明官方画面链包含 Guest 侧捕获组件。
- [确定] 2026-08-24 只读连接复查确认上述五个进程分别以独立 TCP 会话连接 `IceDisplay:60063`；这是清晰的进程/传输边界，但尚未证明存在稳定插件 ABI。
- [确定] 仅完成 vport0p3 身份兼容或安装标准 VirtIO GPU，不能据现有证据保证移动云官方客户端正常显示 Debian 桌面。
- [确定] `IceTunnel.exe` 中的 `x11ice` 不是孤立字符串：实际代码会读取 `/proc/<pid>/cmdline`、检查进程名是否包含 `x11ice`，并在检查失败时把关联 socket 状态设为异常。
- [确定] 本机定向扫描只在 `IceTunnel.exe` 中发现 `x11ice`；安装树和更新缓存没有发现同名程序或 Linux 安装包。
- [高概率] 当前 ICE Tunnel 源码树包含 Linux/X11 会话路径；但这不等于当前资源池或安装介质已经提供可用 Linux Guest 组件。
- [确定] ICE 子组件静态字符串大量重叠，多个 EXE 打包了公共 socket/SPICE/捕获代码；今后单纯字符串命中只能作为线索，必须用交叉引用或动态行为升级证据等级。
- [确定] IceVGPUCapture 自身包含驱动共享文件/surface 路径和 DDA/DXGI/D3D11 捕获、多种编码路径；它不是只负责通知 IceDisplay 读取通用虚拟显卡的轻量进程。
- [确定] IceInput 向 IceDisplay 注册 channel type `3/4`，IceSound 注册 `5/6`，与 SPICE inputs/cursor/playback/record 编号一致；IceVGPUCapture 注册 ZTE 扩展 type `12`。
- [高概率] ZTE 本地会话层保留 SPICE channel 语义，但增加了自有 TCP framing、会话状态和 Capture 扩展。
- [待验证] 共享 mapping 上游的实际捕获源是 DDA、ICE WDDM/IDD 还是其他 vGPU 路径，以及当前编码发生在 Capture 还是 IceDisplay 的哪一分支。
- [确定] 当前 `video_0.dat` 控制区和像素区在 2 秒只读窗口内均发生变化，且 IceDisplay 明确消费同名 mapping；当前会话活动数据面包含共享 surface/command 映射。
- [确定] mapping 总长 `35,426,672`=`0x9170 + 4096×2160×4`；`0x9170` 控制区字段布局仍待恢复。

## 组件与更新器

- [确定] ZTEGuestOS/vmtool 当前版本体系包括 `V7.25.21SP3-9`、`V7.25.21SP3pv-9`；uSmartviewUpdate 为 `7.24.10.01`。
- [确定] VmBoosterMonitor/Vmbooster 静态字符串含 `ZXCLOUD-iVMC-ComponentV7.25.21` 的 `windows` 构建路径，以及 `vmbooster-window/vm_win_2008/vmbooster` 路径。
- [确定] 这些字符串只证明当前二进制来自 Windows 分支；不能据此证明存在同级 Linux 分支。
- [确定] uSmartviewUpdate 的安装元数据包含 `ClientType=1`、`OS_Type=Windows`、`OS_Bit=0`、`Architecture=x86`、`objecttype=2`；二进制静态字符串证明查询框架使用 OS type、bit、clientType、objectType 等维度。
- [确定] 当前 `zconfig.ini` 的 updater server 是 `127.0.0.1`；当前日志反复显示 `no groups in regeditinfo.ini`、`regedit softs size=0`，没有 04:06 后的版本查询、远端版本、下载或安装证据。
- [确定] `C:\Windows\Temp\update` 当前目录不存在（即无现存缓存）；ZTEGuestOS、vmtool、uSmartviewUpdate 目录内未发现 `.deb`、`.rpm`、Linux/UOS/麒麟/Ubuntu/Debian 命名包。
- [确定] 当前注册表审计未找到 uSmartviewUpdate 的组件注册键；这与当前日志 `regedit softs size=0` 一致。Vmbooster 静态字符串虽含完整 `ZTE VMTOOL` 注册参数格式，但没有证据表明当前实例已执行注册。
- [确定] `ZTEGuestOS\installinfo.ini` 将同一 VdesktopVersion 下的组件分为 `01VDesktopComm`、`ICE`、`MEDIA`、`OTHER`、`sysguard`、`USB` 六个独立 versionId；所以一个 ClientType 下可包含多个模块，ClientType 不能当作唯一组件标识。
- [确定] 核心 vmtool EXE 均具有有效 ZTE CORPORATION Authenticode 签名，但没有 FileVersion/ProductVersion 资源；Vdagent 与 ICE EXE 则携带 V7.25.21 系列版本资源。QGA 的 PE 产品为 QEMU 3.1.0，并由南京中兴新软件有限责任公司签名。
- [高概率] uSmartviewUpdate 是通用升级执行框架，但实际产品组件必须先由其他本地应用注册/提供查询上下文；当前它不能直接证明服务器端存在 Linux Guest 包。

## 公开资料

- [确定] ZTE 官方资料证明 uSmartView 产品族支持交付 Windows、Linux、UOS、麒麟等桌面，并存在 RAP 支持多桌面系统的产品形态。
- [确定] ZTE 专利 WO2021135995A1 明确描述 Host 侧 hmbooster/HA/NA 与 Guest 侧 vmbooster/vmmonitor/qga/virtio 的管理架构，并描述 Host 通过虚拟机串口下发组件包。
- [确定] 该专利正文没有 Linux、OS 类型或 Linux 包名；公开官网本轮也未找到可下载的 Linux Guest Agent。
- [待验证] ZTE 产品族内部是否有合法 Linux 版 vmbooster/vmmonitor/RAP/ICE。
- [待验证] 中国移动公众版当前资源池是否授权/发布这类 Linux Guest 集成。
- [待验证] `x11ice` 的正式包名、支持矩阵、启动参数，以及它与 Capture/Input/Sound/Vdagent 的能力边界。

## 当前判断

- [确定] “只安装标准 qemu-guest-agent 就能完整保留平台管理”没有证据支持；当前平台的 OS/网络/资产/heartbeat 管理明显还依赖 vmtool 链。
- [高概率] Linux 最低集成集合为 VirtIO block/network/serial + qemu-guest-agent + ZTE vmbooster/vmmonitor 等效官方组件；若保留官方图形会话，还可能需要 Linux RAP/ICE/Vdagent 体系。
- [确定] 在找到合法兼容组件并确认中国移动侧支持前，不应覆盖 Windows、改启动盘或实施 Linux 安装。
- [确定] 已在 QEMU `-nic none` 的 Debian 13/Wine 10 PoC 中以 Wine SCM 服务语义瞬时启动 `IceMainService`；至少一次窗口内它自动拉起 `IceTunnel`、`IceDisplay`、`IceDisplaySetting`，并建立 60063/5100 监听。
- [确定] `IceInputService`、`IceSound`、`IceInput` 和 `IceVGPUCapture` 均能在同一无网卡 PoC 中形成实际 PE 进程；但尚未证明它们成功连接 60063 或完成输入、声音、画面功能。
- [确定] Wine 10 五组件并发触发 `wineserver64` 的 `set_fd_events` 内部断言并导致服务停止；当前 Wine 路线已证明可达，但未通过稳定性门禁。
- [确定] 后续核心稳定性门禁只启动 `IceMainService` 并采样 6×10 秒，结果为服务在首次 `RUNNING` 后停止，退出码 1077；60063/5100 未保持。因此不能把此前瞬时监听结果当作稳定成功。
- [确定] WineHQ 11.0 独立覆盖盘已建立，但旧/新前缀的 `wineboot` 在 180 秒门限内均未完成；尚无证据表明 Wine 11 已修复上述断言。

## libmswitch 协议静态还原（2026-08-24 02:26）

- [确定] 三个管理 Agent 动态加载同一个 32 位 `libmswitch.dll`，统一调用 `Register/BuildMsg/SendMsg/RecvMsg/RecvMsgTimeout/FreeMsg`。
- [确定] `Register(local_mod, uuid_out16)` 连接 `127.0.0.1:10000`；Vmbooster 的本地模块号是 `0x80000001`。
- [确定] 普通消息由 0x80 字节头和可变 payload 组成，magic 为 `0x5b5b5b5b`，最大消息长度为 `0xc800`。
- [确定] `BuildMsg` 的 7 个参数已恢复为 `dst_mod, uuid16, dst_type, int_msgid, payload, payload_len, out`。
- [确定] 头字段已确认：`0x22=dst_type`、`0x24=uuid16`、`0x34=src_mod`、`0x38=dst_mod`、`0x50=int_msgid`、`0x5c=data_len`、`0x80=payload`。
- [确定] 本地注册请求为 0x84 字节：`msgtype=0`、`src_mod=local_mod`、`int_msgid=0x20130223`、payload 为 4 字节模块号；响应为 0x90 字节：`msgtype=1`、payload 为 16 字节 UUID。
- [确定] vport0p3 存在一层简单字节帧：消息内 `;` 和 `\\` 均以前置 `\\` 转义，帧尾追加未转义 `;`；去帧后仍是相同的 0x80 头消息。
- [确定] 已建立完全离线 codec 和 6 项单元测试；未连接 10000、未打开 vport、未发送消息。
- [高概率] 最终 Debian 方案除 vport0p3 管理链外，还必须单独处理 vport0p1 的 QGA OS 信息边界，否则标准 Debian qemu-ga 可能暴露真实 Linux 身份。
- [确定] 当前 qemu-ga 通过 `time_sync_status` 记录 Host 每 10 分钟调用自定义 `host-get-time`；服务未配置 RPC allowlist/denylist，二进制同时提供 OS、网络、文件、执行和关机接口。
- [高概率] QGA 在本资源池至少承担时间同步；现有动态证据尚未显示 Host 调用 `guest-get-osinfo`、文件或执行 RPC。

## Linux GuestTools 介质调查（2026-08-24）

- [确定] ZTE 云电脑管家 `V7.25.21SP3` 的 `config/install.bak` 明确列出 `osType=2,isComponent=1` 的 Linux Guest 组件引用：`vmbooster.service`、`vmmonitor.service`、`vmoasagent.service`、`x11ice.service`、`usbipc.service`、`spice-vdagentd.service`。
- [确定] 该模板来自 `Z:\vdesktop\VDesktop-setup-sysguardV7.25.21SP3.exe` 解出的 `guestos-sysguard.7z`；它是正式产品安装数据，但不是 Linux payload 或 unit 文件。
- [确定] ZTE 签名 `AssistantService.exe` 的可达代码执行 `/opt/zxve_vmmonitor/vm_upgrade_mount_iso.sh` 并从 `/media/vmtool_linux/install_vmdesk.sh` 启动 Linux Guest 组件安装；PDB 属于 `DEM-PlatformV7.25.21`。
- [高概率] V7.25.21 同代产品存在独立 Linux GuestTools ISO/介质；当前本机没有该 ISO、脚本、RPM、DEB 或 tar 包，不能标为正式安装包已取得。
- [高概率] `vmbooster.service`、`vmmonitor.service`、`x11ice.service`、`usbipc.service` 是 ZTE Linux Guest 组件；`vmoasagent.service` 只有模板单证据，保持 [待验证]；`spice-vdagentd.service` 是上游 SPICE 服务，高概率由 ZTE 套件集成而非自研。
- [确定] 同一模板为 Linux 定义 `60063=iceaudio,x11ice`、`5100=tunnel,usbipc` 等端口保护规则，说明 Linux 会话链不仅是产品宣传中的抽象支持。
- [确定] 模板包含 RPM/X11、DDE/UOS、UKUI/麒麟和 NSDL/NewStart 生态进程；未找到 `apt/dpkg`、Debian、Ubuntu、Wayland、glibc 或内核版本判断。
- [待验证] 当前移动云公众版是否提供与 `V7.25.21SP3-9` 兼容的 Linux 包；尤其不能据现有证据宣称 Debian 13 受支持。
- [未发现] 2026-08-24 对 ZTE 官网/支持站公开索引和公开搜索引擎精确检索 `vmtool_linux`、安装脚本及六个 unit 名，没有找到 ZTE 可下载介质、manifest 或正式支持矩阵；其他厂商的 GuestTools 资料已排除。
- [高概率] `x11ice` 是 Linux/X11 会话链中对应 Windows `IceDisplay:60063` 的枢纽，而 `tunnel:5100`、Input/Capture/Sound/Vdagent 独立存在；尚缺 unit、启动参数和 Linux 运行日志。
- [确定] `AssistantService` 的 AssistSysGuard 本地协议消息类型 `0x22` 映射 `ProcessReInstallComponent`：body=0 调用 `/opt/zxve_vmmonitor/vm_upgrade_mount_iso.sh`，body=1 通过 `bash -c` 启动 `/media/vmtool_linux/install_vmdesk.sh`；`MountISO` 等待启动/结束各 30 秒并回传布尔结果。这里的消息类型与 Mswitch 帧头偏移 `0x22` 无关。
- [确定] 历史 Windows 同代真实升级由 Host `7002` 请求、Guest `7005` 确认、`VmBoosterMonitor` 从虚拟光驱读取 `packageversion.ini` 并运行 `vmtool-setup.exe`、最后 `7004` 回报；这是“Host 管理请求 + ISO + Guest vmmonitor 执行器”的直接证据。
- [确定] 2026-06-23 历史介质卷标为 `vt_V7.25.21SP3-9`，元数据为 `release_version=1`、`nanjing_package_version=V7.25.21SP3-9`、`pv_package_version=215a7089`；该卷标由 `GetVolumeInformationA` 取得，不是 ISO 文件名。
- [确定] Windows 历史卷标从 `vt_V7.24.10-57`、`vt_V7.24.30SP1-2`、`vt_V7.24.42SP3-1` 演进到 `vt_V7.25.21SP3-9`，稳定遵循 `vt_<版本>`；该规律不能外推 Linux 文件名。
- [确定] 通用 updater 的缓存/元数据模型包含 `C:\Windows\Temp\update\`、`updateinfo.ini`、完整包/增量模块、`versionId`、包 SHA-256、`certifiedAddr/noncertifiedAddr` 及 `/version`、`/download/` 相对 API。
- [确定] 当前实际组件缓存 `C:\Program Files (x86)\ComponentInfo\ComponentPkg` 仅有签名 Windows ICE 包 `VDesktop-setup-iceV7.25.21SP3.exe`；没有 Linux ISO 或包。
- [待验证] Linux ISO 的真实文件名、卷标、对象 ID、Linux manifest 字段及上游请求者；`vmtool_linux` 当前只能作为挂载名/卷标候选，不能标成正式 ISO 名。
- [未发现] 当前 updater 中没有 UOS/Kylin/Debian 枚举映射或 Linux 仓库地址；只有通用 `OSType/os/bit` 选择维度，当前注册值明确为 Windows。
- [确定] ZTE 专利架构是每 Host 部署 `hmbooster/HA/NA`，每 VM 部署 `vmbooster/vmmonitor/qga/virtio`；Host 可为多个 VM 投递和升级组件，但这不证明 Host 替代 Guest 深度遥测。
- [确定] 当前 `ExternalVdesktopAgent.exe` 读取 `C:\Program Files (x86)\ComponentInfo\*.json`，查询本机服务/进程/版本并监听 65519；未发现多 VM UUID 路由、Guest Proxy 或 Agent Gateway 数据结构。
- [确定] `65188` 当前由本机 `Vmbooster.exe` 监听；两份日志中 557 次历史连接的源地址全部为 `7f000001`（127.0.0.1）。它归类为本机 IPC，不再作为跨 VM Guest 代理候选。
- [未发现] 当前版本的官方 External Guest Agent、Guest Proxy、Agent Gateway，或一份 Guest Agent 代表多个 VM 上报 Guest 内软件/KB/进程/用户活动的正面证据。
- [说明] 完整证据与边界见 `LINUX_EXTERNAL_AGENT_RESEARCH.md`。

## 公开来源

- ZTE 专利：[WO2021135995A1](https://patents.google.com/patent/WO2021135995A1)
- ZTE 官方 Linux 桌面说明：[云桌面助力国家教育“改薄”大业](https://www.zte.com.cn/china/about/magazine/zte-technologies/2017/12/cn_1291/466504.html)
- ZTE 官方多 OS/RAP 产品页：[XC 型云电脑终端 D740](https://www.zte.com.cn/china/product_index/secure_office_cloudcomputers/usmart/D720/usmart_d720.html)
