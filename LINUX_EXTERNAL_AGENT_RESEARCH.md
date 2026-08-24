# Linux / External Guest Agent 调查

更新时间：2026-08-24（Asia/Shanghai）  
范围：只读角色识别；未连接 `65188/65519`，未发送私有协议，未构造代答或重放。

## 结论摘要

| 候选能力 | 当前结论 | 证据等级 |
|---|---|---|
| 官方 Linux Guest 集成路径 | **确定存在**。同代 ZTE 云电脑管家包含结构化 Linux 组件清单和可达的 Linux ISO 挂载/安装代码。实际 Linux payload 尚未取得。 | 安装日志+签名二进制/高 |
| 当前移动云公众版可用的 Debian 包 | **未确认**。本机安装树和更新缓存未找到 `.deb`、`.rpm`、离线 ISO 或下载地址。 | 否定性证据/中 |
| Host 侧多 VM 管理 | **存在**，但已确认能力是组件安装、升级、状态检测和虚拟化层管理；不是 Guest 深度遥测代采集的同义词。 | ZTE 专利/高 |
| Host 侧完整代采集 | **未发现证据**。尚无资料证明 Host 可替代 Guest 枚举软件/KB、Guest 进程、用户会话或输入活动。 | 当前未知 |
| External Guest Agent / Guest Proxy | **未发现当前产品实现的正面证据**。本机 `ExternalVdesktopAgent.exe` 是单机组件状态接口。 | 动静态交叉/高 |
| 一 Agent 管理多个 VM | ZTE 当前已确认架构是每 Host 一个 `hmbooster/HA/NA`，每 VM 一个 `vmbooster/vmmonitor/qga`。未发现一份 Guest Agent 代替多个 VM 上报 Guest 内状态。 | ZTE 专利/高 |
| `65188` | 本机组件到本机 `Vmbooster.exe` 的 IPC；不作为跨 VM 代理候选。 | 日志+当前端口/高 |

## 1. 官方 Linux Guest 组件

### 已证明

`C:\Program Files (x86)\SysGuard\config\install.bak` 是随 ZTE 签名 SysGuard 安装的数据库初始化模板。`osType=2` 的组件行明确包括：

```text
iceaudio             -> x11ice.service
spice-vdagent
spice-vdagentd       -> spice-vdagentd.service
usbipc               -> usbipc.service
vmoasagent           -> vmoasagent.service
vmbooster            -> vmbooster.service
vmmonitor            -> vmmonitor.service
x11ice.service
```

同一模板还为 Linux 定义端口保护：

```text
5100        tunnel,usbipc
3246        usbipc
10221-10231 vsftpd
60063       iceaudio,x11ice
```

这不是普通字符串表，而是云电脑管家运行数据库的结构化初始化数据；六项服务均被标为 `osType=2,isComponent=1`。不过该文件仍是 Windows 安装包内的跨 OS 模板，不是 Linux unit 文件或 Linux payload，因此它证明“官方产品预定义了这些 Linux Guest 组件”，不能单独证明本机已经找到可安装的正式 Linux 包。

### 六项服务逐项溯源

共同直接来源：

- 原始文件：`C:\Program Files (x86)\SysGuard\config\install.bak`
- SHA-256：`274D9D300AA5ABB5496DD92383C846C225C13DB9BA2543E3B5699B3AB3290CB6`
- 原始安装链：`Z:\vdesktop\VDesktop-setup-sysguardV7.25.21SP3.exe` → 解包 `guestos-sysguard.7z` → 写入当前 SysGuard 目录。
- 产品：中兴云电脑管家 / SysGuard；卸载项版本 `V7.25.21SP3`，`SysGuard.exe` 文件版本 `7.25.21.00`。
- 可信度边界：安装器日志和有效 ZTE 签名的 SysGuard/AssistantService 共同确认产品归属；`install.bak` 自身没有独立 Authenticode 签名。

| 服务 | 原始发现与交叉证据 | 产品归属判断 | 等级 |
|---|---|---|---|
| `vmbooster.service` | `install.bak` L191、L197；ZTE 专利 WO2021135995A1 明确把 `vmbooster` 定义为每 VM 的虚机代理模块。 | 高概率是 ZTE 官方 Linux Guest 管理 Agent；尚未找到 Linux 二进制、unit 或包。 | **[高概率]** |
| `vmmonitor.service` | `install.bak` L192、L198；同一 ZTE 专利明确列出每 VM 的 `vmmonitor`，并描述 Guest 组件升级/恢复检测。 | 高概率是 ZTE 官方 Linux Guest 监控/守护组件；尚未找到 Linux payload 和版本号。 | **[高概率]** |
| `vmoasagent.service` | 只在 `install.bak` L190、L196 的结构化组件行中发现；公开资料、本机其他二进制和日志未找到第二证据。 | 模板把它视为 Linux 云桌面组件，但职责、实际交付和产品边界尚未坐实。 | **[待验证]** |
| `x11ice.service` | `install.bak` L186、L193；L59 另列 `x11ice` 进程；Linux 端口规则 L230 指定 `60063=iceaudio,x11ice`；ZTE 签名 `IceTunnel.exe` 的可达代码读取 `/proc/<pid>/cmdline` 并验证 `x11ice`。 | 高概率属于 ZTE 官方 ICE/RAP Linux/X11 会话链；不是正式包已取得的证明。 | **[高概率]** |
| `usbipc.service` | `install.bak` L189、L195；Linux 端口规则 L227–L228 列出 `tunnel,usbipc`；Windows 同代产品有签名 `UsbIpc.exe` 和对应虚拟串口/USB 重定向链。 | 高概率是 ZTE Linux Guest USB/设备重定向组件；尚无 Linux unit、依赖和架构信息。 | **[高概率]** |
| `spice-vdagentd.service` | `install.bak` L187–L188、L194；上游 SPICE 文档确认 `spice-vdagentd` 是标准 Linux daemon、`spice-vdagent` 是每 X 会话进程。 | 确定是上游 SPICE 服务名；高概率被 ZTE Linux Guest 集成包作为依赖/组件纳管，不能称为 ZTE 自研 Agent。 | **[高概率]**（ZTE 集成） |

因此六项中没有任何一项可以标成“已找到正式 Linux Guest Agent 安装包”。`vmoasagent.service` 尤其只有单一模板证据，保持 **[待验证]**。

2026-08-24 对 ZTE 官网、ZTE 支持站索引及公开搜索引擎做了精确名称检索：`vmtool_linux`、`install_vmdesk.sh`、`vm_upgrade_mount_iso.sh`、`x11ice.service`、`vmoasagent.service`、`vmbooster.service`、`vmmonitor.service` 均没有出现可下载的 ZTE 安装介质或正式文档命中。搜索到的 Citrix、H3C、ZStack 等 Linux GuestTools 资料属于其他厂商，不能作为 ZTE 包证据。这个结果只说明公开索引中未找到，不能证明 ZTE/中国移动受限支持库中不存在。

### Linux 安装介质路径证据

有效 ZTE 签名的 `AssistantService.exe`（SHA-256 `E7864870F7C3EA93F07FDD8858CE26511E811900CCD249421B7B2FC9E70DCB29`）包含：

```text
bash /opt/zxve_vmmonitor/vm_upgrade_mount_iso.sh
echo 0|sh /media/vmtool_linux/install_vmdesk.sh
```

这两条不是孤立死字符串：静态交叉引用分别落在 `LocalSocketThreadService::MountISO` 和 `ProcessReInstallComponent` 的可达代码中；后者会构造并启动 `bash` 命令。PDB 路径还把该二进制关联到 `DEM-PlatformV7.25.21` 源码树。

由此可确定同一 V7.25.21 代产品定义了 Linux GuestTools ISO 安装流程，候选介质挂载点为 `/media/vmtool_linux`、入口为 `install_vmdesk.sh`。但当前磁盘没有该脚本或 ISO：现在的 `Z:` 是一个 358400 字节、无目录项的只读空 CDFS，不能当作 Linux GuestTools 介质。

### GuestTools 实际交付链追踪

#### Linux 侧静态状态机

`AssistantService.exe` 构造本地消息处理表时，把消息类型 `0x22` 映射到 `ProcessReInstallComponent`（注册代码 `0x0042007d–0x00420097`，处理函数 `0x00423470`）。处理函数读取消息体首个 32 位整数：

- 值 `0`：调用 `MountISO`（`0x00422390`），执行 `bash /opt/zxve_vmmonitor/vm_upgrade_mount_iso.sh`；Qt `QProcess` 分别以 30 秒等待启动和结束、读取标准输出、把成功布尔值发回本地 socket。
- 值 `1`：构造 `bash -c "echo 0|sh /media/vmtool_linux/install_vmdesk.sh"`，走 Qt `QProcess` detached 启动路径。

本地服务名是 `AssistSysGuard`。当前安装树中确认连接该 endpoint 的二进制只有 `SysGuard.exe` 和 `SysMonitorBall.exe`；但现有 Windows 日志没有出现 `0x22`、`MountISO` 或 `install_vmdesk.sh` 实际执行记录，尚不能确定消息最初由后台、SysGuard UI 还是 Linux 版其他组件触发。Windows PE 很可能保留了跨 OS 共用源代码路径，因此“代码可达”也不能等同于当前 Windows 实例可成功执行 `bash`。

#### Windows 同代真实升级链（用于识别 Linux counterpart）

`C:\Program Files (x86)\vmtool\vbmonitor.log` 保存了多次真实管理升级：

1. Host/后台向 `VmBoosterMonitor` 下发 `msgtype=7002`，字段包括 `msgid`、`vuuid`、`vtype=2`、`utype=0`。
2. Guest 立即回 `7005` 确认。
3. `VmBoosterMonitor` 枚举 CD-ROM，使用 `GetDriveTypeA`/`GetVolumeInformationA`；日志中的 `diskinfo[vt_V7.25.21SP3-9]` 因而是介质卷标，而不是 ISO 文件名。
4. 从挂载盘读取 `Z:\packageversion.ini`，字段为 `release_version`、`nanjing_package_version`、`pv_package_version`。
5. 执行 `Z:\vmtool-setup.exe /quiet /vmdesktop /nanjing`；必要时另走 `/chengdu_qga`。
6. 最终以 `7004` 回报 `result/restart/agent_version`。

2026-06-23 这次日志实际读到：

```text
volume label             = vt_V7.25.21SP3-9
release_version          = 1
nanjing_package_version  = V7.25.21SP3-9
pv_package_version       = 215a7089
```

历史日志还依次保存了 `vt_V7.24.10-57`、`vt_V7.24.10-SP5_`、`vt_V7.24.30SP1-2`、`vt_V7.24.42SP3-1`、`vt_V7.25.21SP3-9`，说明 Windows GuestTools 光盘卷标稳定采用 `vt_<交付版本>` 形式。该规律可用于向支持方定位同代介质，但不能据此自行构造 Linux 卷标或文件名。

这证明同代交付架构是“Host 管理请求 → 虚拟光驱介质 → Guest 内 vmmonitor 执行器 → 结果回报”。结合 Linux 路径 `/opt/zxve_vmmonitor/`，**[高概率]** Linux 的 `vmmonitor`/挂载脚本承担对应 Guest 执行职责；但 Linux 是否也使用 `7002/7005/7004`、其卷标和 manifest 字段是否相同，当前均未证明。

#### 下载、缓存和版本选择边界

- 通用 `uSmartviewUpdate.exe` 支持 `/version?objecttype=...&bit=...&os=...` 查询、`/download/`、`updateinfo.ini`、完整包/增量模块、`versionId` 和 SHA-256 校验。
- 可见元数据字段包括 `certifiedAddr/noncertifiedAddr`、`full_packname/full_packsha256`、`modules_name/modules_pack/modules_sha256/modules_versionIds`、`cachedVersion/cachedInstallType/cachedNetType`。
- Windows 下载缓存根为 `C:\Windows\Temp\update\`；AssistantService 还检查旧路径 `C:\Program Files (x86)\update\`，组件包目录是 `C:\Program Files (x86)\ComponentInfo\ComponentPkg`。
- 当前组件包目录仅有有效 ZTE 签名的 `VDesktop-setup-iceV7.25.21SP3.exe`；SHA-256 `75516253925D7D3AB3C0AC06C76EB2175F0C4B54D28CED10C6B13585AFE6FE0B`。这证明该目录是实际 Windows 组件缓存，不是 Linux ISO 缓存。
- 当前 updater `updateinfo.ini` 明确为 `OS_Type=Windows`、`OS_Version=all`、`Architecture=x86`，`zconfig.ini` server 为 `127.0.0.1`，日志持续显示 `no groups in regeditinfo.ini` / `regedit softs size=0`；当前没有发生版本查询或下载。
- AssistantService 自身注册 updater 的静态参数为 `objecttype=4`、`fullPkgGroup=Sysguard`，并传入 `currentVersion/ClientType/installInfoPath`；这证明通用版本选择模型，不证明 Linux GuestTools ISO 由该 Windows updater 下载。

#### ISO 名称与获取源的当前答案

- **Linux ISO 文件名：[待验证]**。所有本机文件、历史日志和静态字符串均没有出现 `.iso` 文件名。
- **Linux 卷标/挂载名候选：[高概率] `vmtool_linux`**。证据是固定挂载目录 `/media/vmtool_linux`；它也可能只是人工创建的 mount point，不能升级为确定卷标。
- **Windows 同代介质卷标：[确定] `vt_V7.25.21SP3-9`**；这是 `GetVolumeInformationA` 获得的历史卷标，不是 Linux 名称。
- **下载 URL/仓库地址：[未发现]**。只恢复到通用相对 API `/version`、`/download/`、`updateinfo.ini` 和动态地址字段，没有可直接访问的 ZTE Linux repo/URL。
- **发行版选择：[待验证]**。updater 有通用 `OSType/os/bit` 维度，但当前配置只注册 Windows，未找到 UOS/Kylin/Debian 的枚举值或包映射。

中兴官方材料同时明确列出 Windows VM、Linux VM、UOS/麒麟 VM；这与本机清单相互印证：[中兴云电脑产品资料](https://www.zte.com.cn/content/dam/zte-site/res-www-zte-com-cn/%E6%94%BF%E4%BC%81/ted/%E6%96%B0%E4%B8%9A%E5%8A%A1%E8%81%9A%E5%90%88%E9%A1%B5%E5%9B%BE%E7%89%87%E6%96%87%E4%BB%B6%E8%B5%84%E6%96%87%E4%BB%B6%E5%A4%B9/%E4%B8%AD%E5%85%B4%E9%80%9A%E8%AE%AF%E6%96%B0%E4%B8%9A%E5%8A%A1%E6%80%BB%E4%BD%93%E4%BB%8B%E7%BB%8D%E6%89%8B%E5%86%8C.pdf)。

另一份 ZTE 官方 uSmart 云电脑金融行业彩页明确写有 Windows/Linux 云桌面及 UOS、麒麟兼容，但仍未给出 GuestTools 文件名、组件版本或发行版支持矩阵：[官方彩页](https://www.zte.com.cn/content/dam/zte-site/res-www-zte-com-cn/mediares/zte/global/chanpinwenjian/%E4%B8%AD%E5%85%B4%E9%80%9A%E8%AE%AF%E4%BA%91%E7%94%B5%E8%84%91%E9%87%91%E8%9E%8D%E8%A1%8C%E4%B8%9A%E5%BD%A9%E9%A1%B5.pdf)。

ZTE 专利 [CN117290025A](https://patents.google.com/patent/CN117290025A/zh) 进一步给出 Linux 云桌面内核态/用户态组件的真实设计：在 Guest 内使用 FUSE 与用户态磁盘重定向模块，把云终端磁盘操作经网络转发。它证明 Linux 云桌面功能采用原生 Linux Guest 组件实现；但该专利讨论的是磁盘重定向，不是 Guest 管理遥测或跨 VM 代理。

### 版本对应关系

- [确定] 当前 Windows Guest 总包：`V7.25.21SP3-9`；Windows 安装介质中的组件安装器名为 `VDesktop-setup-{comm,ice,sysguard,usb}V7.25.21SP3.exe`。
- [确定] 当前 Windows vmtool 安装器：`Z:\vmtool-setup.exe`；`packageversion.ini` 记录 `nanjing_package_version=V7.25.21SP3-9`、`pv_package_version=215a7089`。
- [确定] Linux ISO 安装逻辑所在 `AssistantService.exe` 来自 `DEM-PlatformV7.25.21` 源码树，并随 `V7.25.21SP3` SysGuard 安装。
- [高概率] Linux GuestTools 流程与当前 Windows Guest 属于同一 V7.25.21 产品代。
- [待验证] Linux ISO 自身版本、各 Linux 组件版本，以及它是否精确匹配 `SP3-9`；当前不能仅凭公共源码树名称宣称二进制兼容。

### 发行版与桌面环境证据

`install.bak` 的 `osType=2,isComponent=3` OS 进程清单包含：

- RPM/RHEL 系线索：`dnf`、`rhsmcertd`、`platform-python`。
- X11/KDE 线索：`Xorg`、`sddm`、`kwin`、`pulseaudio`。
- DDE/Deepin/UOS 线索：`dde-desktop`、`startdde`。
- UKUI/麒麟线索：`peony-qt-desktop`、`ukui-panel`、`ukui-kwin_x11`。
- SysGuard 自身使用 `NSDL` 命名，并包含大量 `nde-*`/`startnde` 进程。

官方产品页明确列出 UOS、麒麟、Linux、NewStartOS；历史第三方报道还记录 uSmartView 与中标麒麟桌面 V7.0（兆芯版）完成互认证。上述资料仍然只到产品/生态级，未给出当前包的精确兼容矩阵。

目前没有在模板或安装逻辑中找到 `apt`、`dpkg`、Debian、Ubuntu、Wayland、glibc 最低版本或内核版本判断。因此：

- UOS/DDE、麒麟/UKUI、NewStart/NSDL、部分 RPM 系桌面：有直接或较强间接证据。
- Ubuntu/Debian：只有中兴产品宣传中的泛称 Linux，当前无包级证据。
- Debian 13：**[待验证]**，不得默认兼容。
- Wayland：**[待验证]**；当前 `x11ice`、`Xorg`、SDDM 证据反而指向 X11 路线。

### `x11ice` 当前角色判断

同一 Linux 组件清单同时列出 `IceTunnel`、`IceInput`、`IceVGPUCapture`、`IceSound`、`Vdagent` 和 `x11ice`，所以 `x11ice` 不是这些模块的笼统总称。端口规则进一步区分：

```text
5100  -> tunnel,usbipc
60063 -> iceaudio,x11ice
```

结合 Windows 同代架构中 `IceTunnel:5100` 与 `IceDisplay:60063` 的角色，当前最优推断是：`x11ice` 很可能是 Linux/X11 下对应 `IceDisplay` 的会话/显示枢纽，`iceaudio` 连接其 60063 通道；独立的 Input/Capture/Sound/Vdagent 负责各自子功能。该映射属于 **[高概率]**，没有 unit、启动参数或 Linux 日志前不能升级为确定。

上游 `spice-vdagentd` 通常负责 daemon 侧 Guest 集成，`spice-vdagent` 是 X 会话进程；ZTE 本地 ICE 组件复用 SPICE channel 语义，但存在自有 framing 和 Capture 扩展，因此不能假设发行版自带 `spice-vdagent` 就能替代 `x11ice/ICE`。

### 尚未证明

- 包格式是 `.deb`、`.rpm`、ISO 还是专用安装器。
- 支持 Debian/Ubuntu，还是只支持 UOS、麒麟、NSDL/CGSL 等认证发行版。
- x86_64、ARM64 或其他架构的支持矩阵。
- 与当前 `V7.25.21SP3-9` Host/Guest 组件的版本匹配关系。
- 中国移动公众版资源池是否已发布、授权并允许下发该包。

因此当前可说“V7.25.21 同代官方产品明确包含 Linux GuestTools 安装路径和组件清单”，不能说“已经拿到正式 Linux GuestTools 包”，也不能说“Debian 13 已受支持”。

## 2. Host 侧能力边界

ZTE 专利 [WO2021135995A1](https://patents.google.com/patent/WO2021135995A1) / [US20230032581A1](https://patents.google.com/patent/US20230032581A1/en) 明确描述：

```text
Host（每宿主机）: hmbooster + HA + NA
Guest（每虚机） : vmbooster + vmmonitor + qga + virtio
```

Host Agent 能按组件版本找到软件包，经 HA 建立的虚拟串口或虚拟光驱把文件投递到特定 VM，并在旧 Guest Agent 异常时继续完成安装/升级。专利还明确写到根据 GuestOS 类型判断重启，并检测 GuestOS 与虚机代理是否恢复正常。

这证明：

- 一个 Host Agent 管理同宿主机的多个 VM。
- Host 可执行组件生命周期管理和虚拟化层状态检测。
- Guest Agent 异常不一定阻断 Host 投递/修复组件。

它没有证明：

- Host 能在不运行 Guest 组件时读取 Guest 内安装软件/KB。
- Host 能替代 Guest 读取进程、登录用户、输入活动等语义。
- Host 会把一个 VM 的 Guest 状态作为另一个 VM 的状态上报。

中兴公开材料中的“无代理杀毒”只能证明特定安全能力可无代理化，不能外推为完整 Guest 管理链无代理化。

## 3. `ExternalVdesktopAgent.exe` 角色

### 已观察事实

- 路径：`C:\Program Files (x86)\SysGuard\ExternalVdesktopAgent.exe`
- Authenticode：ZTE CORPORATION，签名有效。
- Windows 服务：`ExternalVdesktopAgent`，自动启动，当前运行。
- 当前监听：`:::65519`。
- 配置：`port1=65188`、`port2=65520`、`ExternalPort=65519`。
- 二进制字符串指向：
  - `C:/Program Files (x86)/ComponentInfo`
  - `C:/Program Files/ComponentInfo`
  - `*.json`
  - `checkComponentProcess`
  - `GetServiceStatus`
- `ComponentInfo/*.json` 只有本机模块、服务名、进程路径、版本和守护标志，例如 `vmtool.json`、`ice.json`、`usb.json`。
- `ComponentInfo/ExternalVdesktopAgent.ini` 当前仅有 `vdesktopAgentUpdate=0`。

### 角色判断

当前最优判断是“SysGuard 对外提供的本机虚桌面组件健康/版本查询接口”。名称中的 `External` 指接口对 SysGuard 外部消费者开放，不等于它是 External Guest Gateway。

未在配置、组件 JSON 或二进制可见字符串中发现：

- 多个 VM UUID 的路由表。
- VM 到连接/通道的映射。
- 上游 Guest Proxy / Agent Gateway 地址。
- 代表其他 VM 采集或上报的字段。

负面证据不能证明所有版本绝对不存在该能力，但足以排除“当前这份 EXE 已经明显是一 Agent 多 VM Guest Proxy”的说法。

## 4. `65188` 角色识别

### 已证明

- 当前 `0.0.0.0:65188` 的监听进程是 `Vmbooster.exe`。
- `vmbooster.log` 与 `vmbooster_bak.log` 共找到 557 条 `Server start get connect from ...`。
- 557 条的源地址全部为 `7f000001`，即 `127.0.0.1`；没有第二个源地址。
- 已观察消息均围绕本机状态和本 VM 事件，例如 `8065/8066`、`8067/8068`、`8097/8098`、`8047`。

### 结论

`65188` 是本机组件访问本机 vmbooster 的 IPC 服务。绑定 `0.0.0.0` 只说明监听地址宽，并不能证明实际承担跨 VM 代理；现有调用历史反而强烈支持 loopback-only 使用形态。

本调查不连接该端口、不发包，也不从私有协议构造代答方案。

## 5. 一 Agent 多 VM 的证据分级

### 已证明存在的设计

- **Host Agent 管理多个 VM**：ZTE 专利明确证明，职责包括组件投递、安装、升级和运行状态检测。

### 其他厂商/通用概念存在，但不能归到 ZTE 当前产品

- “代理 VM 接收多个 VM 策略再内部分发”是虚拟化/安全产品中的已知架构，但检索到的典型专利 CN104484219B 属于奇安信体系，并非 ZTE，不能作为 ZXCLOUD-iVMC 的实现证据。

### 当前未证明

- ZTE 当前版本的 External Guest Agent。
- ZTE 当前版本的一份 Guest Agent 汇总多个 VM 的 Guest 深度遥测。
- 当前移动云公众版的 Guest Proxy 或 Agent Gateway。
- Host 侧对软件、KB、进程、用户活动的完整无代理代采集。

## 6. 下一步只读验证

1. 从 ZTE/中国移动支持渠道以精确锚点询证：`vmtool_linux` ISO、`install_vmdesk.sh`、`/opt/zxve_vmmonitor/vm_upgrade_mount_iso.sh`。
2. 取得 Linux ISO/包后只做离线验签、列文件、读取 unit/manifest/依赖和发行版判断，不安装、不运行。
3. 优先确认 Linux payload 是否存在 `V7.25.21SP3-9` 或明确兼容版本，以及六项服务各自版本。
4. 从脚本恢复 RPM/DEB 分支、架构、glibc/内核/X11/Wayland 要求；在此之前 Debian 13 保持待验证。
5. `65188`、External Agent 和多 VM Proxy 暂降优先级，除非出现新的直接证据。
