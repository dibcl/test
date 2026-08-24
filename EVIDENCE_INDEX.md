# Evidence Index

更新时间：2026-08-24。行号以当前文件内容为准；动态行为优先采用 2026-08-23 23:40 冷启动段。

| 结论 | 证据文件 | 时间/关键行或关键词 | 等级 |
|---|---|---|---|
| 23:40 冷启动与核心进程启动 | `guest-coldboot-timeline.txt` | L2；L17–L27 | 当前实例/高 |
| MswitchWin 打开 `com.vmswitch.0` | `guest-coldboot-timeline.txt` | L38–L40 | 当前实例/高 |
| 三次本地客户端注册及串口写出 | `guest-coldboot-timeline.txt` | L41–L52 | 当前实例/高 |
| Mswitch 的三个已确认客户端 | `mswitch-local-clients.txt` | L4–L34；L40–L82 | 当前连接快照/高 |
| vport0p1→qemu-ga | `zte-full-vport-process-map.txt` | L22–L25 | 当前快照/高 |
| vport0p2→Vdagent | `zte-full-vport-process-map.txt` | L211–L214 | 当前快照/高 |
| vport0p3→MswitchWin | `zte-full-vport-process-map.txt` | L13–L16 | 当前快照/高 |
| vport0p4→IceSound | `zte-full-vport-process-map.txt` | L4–L7 | 当前快照/高 |
| UsbIpc 占其余串口 | `zte-full-vport-process-map.txt` | L31–L205 | 当前快照/高 |
| PDO 到 device object 对应 | `zte-vport-pdo-map.txt` | L340–L446（p1–p4） | 当前快照/高 |
| VmQoEAgent 动态生成环境清单 | `vmtool-client-role-timeline.txt` | L29–L39；`environment` | 23:40 当前实例/高 |
| VmQoEAgent 枚举软件与 KB | `vmtool-client-role-timeline.txt` | L41–L58 | 23:40 当前实例/高 |
| VmQoEAgent 汇集 ICE/会话/QoE | `vmtool-client-role-timeline.txt` | L59–L79 | 23:40 当前实例/高 |
| Vmbooster 注册、运行时间与 heartbeat | `vmtool-client-role-timeline.txt` | L94–L118；L135–L155 | 23:40 当前实例/高 |
| Vmbooster 上报网络与 OS | `vmtool-client-role-timeline.txt` | L121–L134；L152–L155 | 23:40 当前实例/高 |
| ZTEGuestOS 与 vmtool 版本 | `zte-component-metadata-map.txt` | L3–L12；L34–L41 | 本机元数据/高 |
| updater OS/client/object 元数据 | `zte-component-metadata-map.txt` | L16–L32 | 本机元数据/高 |
| updater 静态查询维度 | `zte-os-package-schema.txt` | `query from`、`clientType`、`ostype`、`objectType` | 静态能力/中高 |
| ZXCLOUD Windows 构建路径 | `zte-os-package-schema.txt` | L115、L157–L161（本轮 `rg -n`） | 静态字符串/高 |
| updater 当前无注册组件 | `usmart-updater-audit.txt` | 23:40:24/25，`no groups`、`regedit softs size=0` | 当前实例/高 |
| updater 当前仅监听本地端口 | `usmart-updater-audit.txt` | 23:40:24，`62226`、`62222` | 当前实例/高 |
| updater 当前无组件注册键 | `zte-update-client-registry.txt` | `MATCHING REGISTRY KEYS` 为空 | 当前快照/高 |
| 一个 ClientType 下有六个模块 ID | `C:\Program Files (x86)\ZTEGuestOS\installinfo.ini` | `01VDesktopComm/ICE/MEDIA/OTHER/sysguard/USB` | 本机元数据/高 |
| GuestTools/QGA 当前包版本 | `C:\Program Files (x86)\GuestTools\install.log` | L220、L268、L276、L296 | 本机安装日志/高 |
| 未找到 Linux 包 | 本轮只读目录扫描 | 三个安装树无 `.deb/.rpm` 或 Linux 命名包；`C:\Windows\Temp\update` 当前不存在 | 否定性证据/中 |
| 产品族支持 Linux 桌面 | ZTE 官方网页 | “Windows、Linux 桌面” | 官方公开/高（仅产品族） |
| RAP 产品支持多 OS 桌面 | ZTE D740 产品页 | “Windows、UOS、麒麟、Linux、NewStartOS”；RAP | 官方公开/高（仅产品族） |
| Guest 管理架构 | ZTE 专利 WO2021135995A1 | 段落 349–357：hmbooster、vmbooster、vmmonitor、qga、virtio、串口下发包 | 官方专利/高（架构） |
| IceTunnel 实际检查 `x11ice` PID | `X11ICE_STATIC_ANALYSIS.md` | `0x1400234c0`、`0x140023650`、对象偏移 `+0xb2b` | 静态代码/高 |
| 本机没有发现 `x11ice` 包 | `X11ICE_STATIC_ANALYSIS.md` | ZTEGuestOS/更新缓存定向扫描 | 否定性证据/中 |
| IceDisplay 五条独立本地会话 | `SESSION_LOCAL_IPC_FINDINGS.md` | 2026-08-24 `Get-NetTCPConnection` 快照 | 当前连接快照/高 |
| Capture/Input/Sound 公共连接头为 4 字节 `0x9a` | `SESSION_LOCAL_IPC_FINDINGS.md` | 三个 EXE 的 `sock_deal_connect_init` | 静态代码/高 |
| 通用本地消息检查 magic `0xaa` | `SESSION_LOCAL_IPC_FINDINGS.md` | IceVGPUCapture `0x14000c8c5–0x14000c91d` | 静态代码/高 |
| Input/Sound/Capture 注册类型 | `SESSION_LOCAL_IPC_FINDINGS.md` | Input `3/4`、Sound `5/6`、Capture `12` | 静态代码/高 |
| 活动显示共享映射 | `SESSION_LOCAL_IPC_FINDINGS.md` | `video_0.dat` 两次只读差异 + IceDisplay `display/pipe.c` | 动静态交叉/高 |
| surface-command ring 布局 | `SESSION_LOCAL_IPC_FINDINGS.md` | offset `0x906e`，type `0/1/2`，当前 `3072×1920×32` | 动静态交叉/高 |
| dirty-rect ring 布局 | `SESSION_LOCAL_IPC_FINDINGS.md` | offsets `0x5a/0x6a/0x6c/0x6e`，4096×9 bytes | 动静态交叉/高 |
| 官方 Linux Guest 组件清单 | `C:\Program Files (x86)\SysGuard\config\install.bak` | L186–L198；`osType=2`、`vmbooster.service`、`vmmonitor.service`、`vmoasagent.service`、`x11ice.service` | 厂商安装模板/高 |
| Linux ICE/USB 端口规则 | `C:\Program Files (x86)\SysGuard\config\install.bak` | L227–L230；`60063=iceaudio,x11ice` | 厂商安装模板/高 |
| SysGuard 模板的安装包来源 | `C:\Program Files (x86)\ZTEGuestOS\log\install.log` + `SysGuard\log\install_20260623224334.log` | L37：`VDesktop-setup-sysguardV7.25.21SP3.exe`；L21：`guestos-sysguard.7z` | 本机安装日志/高 |
| Linux GuestTools ISO 入口 | `C:\Program Files (x86)\SysGuard\AssistantService.exe` / `static-pe\sysguard\AssistantService.strings.txt` | L1188–L1193；`vm_upgrade_mount_iso.sh`、`/media/vmtool_linux/install_vmdesk.sh`，均有代码 xref | 签名二进制可达代码/高 |
| Linux ISO 本地请求状态机 | `AssistantService.exe` 静态反汇编 | AssistSysGuard 消息类型 `0x22`→`ProcessReInstallComponent`；body 0→`MountISO`，body 1→`bash -c install_vmdesk.sh` | 静态代码/高 |
| Windows 同代 Host 升级请求链 | `C:\Program Files (x86)\vmtool\vbmonitor.log` | L2288–L2292、L2333–L2342、L2352–L2363；7002→7005→ISO 执行→7004 | 历史真实日志/高 |
| 同代介质卷标与 manifest 字段 | 同上 + `VmBoosterMonitor.exe` | `diskinfo[vt_V7.25.21SP3-9]`；导入 `GetVolumeInformationA`；`release/nanjing/pv_package_version` | 日志+静态代码/高 |
| Windows GuestTools 卷标命名规律 | `vbmonitor.log` | `vt_V7.24.10-57`→`vt_V7.24.30SP1-2`→`vt_V7.24.42SP3-1`→`vt_V7.25.21SP3-9` | 多版本历史日志/高 |
| 通用 updater 下载/版本模型 | `static-pe/delivery-chain/uSmartviewUpdate.strings.txt` | `/version`、`/download/`、`updateinfo.ini`、`versionId`、完整包/增量模块及 SHA-256 字段 | 签名二进制静态能力/高 |
| Windows updater 缓存目录 | `AssistantService.log` + updater 字符串 | `C:/Windows/Temp/update/`、旧 `Program Files/update`、`ComponentInfo/ComponentPkg` | 历史日志+静态代码/高 |
| 当前组件缓存内容 | `C:\Program Files (x86)\ComponentInfo\ComponentPkg\VDesktop-setup-iceV7.25.21SP3.exe` | 有效 ZTE 签名；SHA-256 `755162...FE0B`；目录无 Linux 包 | 本机文件/高 |
| Linux ISO 文件名/仓库地址 | 本机全盘定向检索、日志和三组件静态字符串 | 无 `.iso` 文件名、Linux URL、repo 或对象 ID；`vmtool_linux` 仅为固定挂载目录候选 | 否定性证据/中 |
| Linux 安装逻辑所属产品代 | 同上 | PDB：`DEM-PlatformV7.25.21`；SysGuard 卸载项 `V7.25.21SP3` | 静态元数据+注册表/高 |
| 当前 Windows 同代介质结构 | `ZTEGuestOS\log\install.log`、`vmtool\vbmonitor.log` | `VDesktop-setup-*V7.25.21SP3.exe`、`vmtool-setup.exe`、`packageversion.ini` | 本机历史安装日志/高 |
| 发行版生态线索 | `SysGuard\config\install.bak` | `dnf/rhsmcertd/platform-python`、`Xorg/sddm`、`dde/startdde`、`UKUI/peony` | 厂商模板/中高 |
| Debian/Ubuntu 包级支持 | 本机定向扫描与模板检查 | 无 `apt/dpkg`、Debian/Ubuntu、`.deb` 或兼容矩阵 | 否定性证据/中 |
| ZTE 公开索引中的实际 Linux 包 | 2026-08-24 精确公开检索 | `vmtool_linux`、三个安装脚本锚点及六个 unit 名均无 ZTE 下载/文档命中；其他厂商结果排除 | 否定性证据/中 |
| 产品族支持 Linux/UOS/麒麟 | ZTE 官方 uSmart 云电脑金融行业彩页 | Windows/Linux 云桌面；兼容 UOS、麒麟；不含 GuestTools 包名和版本矩阵 | 官方公开/高（仅产品族） |
| ExternalVdesktopAgent 本机组件接口 | `C:\Program Files (x86)\SysGuard\ExternalVdesktopAgent.exe` + `C:\Program Files (x86)\ComponentInfo\*.json` | 有效 ZTE 签名；二进制路径/JSON/服务与进程检查字符串 | 静态+本机元数据/高 |
| ExternalVdesktopAgent 当前监听 | Windows 服务与 TCP 快照 | 自动启动、Running、`:::65519` | 当前快照/高 |
| 65188 是本机 vmbooster IPC | `C:\Program Files (x86)\vmtool\vm_booster\vmbooster*.log` | 557 次 `Server start get connect` 全为 `7f000001`；当前监听者 Vmbooster | 历史日志+当前快照/高 |
| Host/Guest 每层组件部署边界 | ZTE 专利 WO2021135995A1/US20230032581A1 | Host=`hmbooster/HA/NA`；每 VM=`vmbooster/vmmonitor/qga/virtio` | 官方专利/高 |
| Linux 云桌面原生 Guest 功能组件 | ZTE 专利 CN117290025A/EP4528528A1 | Guest 内核态 FUSE + 用户态磁盘重定向模块 | 官方专利/高（仅重定向功能） |

## 核心二进制检索锚点

| 文件 | 版本资源 | 签名 | SHA-256 |
|---|---|---|---|
| Vmbooster.exe | 无 | ZTE CORPORATION / Valid | `8F2C44900608AD6B907586887156C801236FC92C6FECBA6A1D7E259089066C3A` |
| VmBoosterMonitor.exe | 无 | ZTE CORPORATION / Valid | `FC8CE87A8EBA0DB3497E9605153D2CE9B171F68DD49EC07BD029935E3F064A7E` |
| VmQoEAgent.exe | 无 | ZTE CORPORATION / Valid | `DC547C4A93E5278D37A7C63DF75B88EC9C035065D1D0C4E4EA154D57EEEE7612` |
| MswitchWin.exe | 无 | ZTE CORPORATION / Valid | `585A3E2CEBE95F8B9A0658DD1BA31F97A1FA90A9ADEF4C14B502B23874D6B4B2` |
| qemu-ga.exe | QEMU 3.1.0 | 南京中兴新软件有限责任公司 / Valid | `35C1F2D503C03C4FC16440E29683326CFBD5A4C085DF6DE2777F5DB3FAB9D025` |
| Vdagent.exe | V7.25.21_20 | ZTE CORPORATION / Valid | `9E47974EAC77C34550A8DB923E565105BE3085BB616E654A3A8B316746ED339E` |
| IceTunnel.exe | V7.25.21-13 | ZTE CORPORATION / Valid | `B565310F75C867218B11C538ECA6D78A77750878AEBAD9B9D45A6E8D16A7EE0E` |
| IceDisplay.exe | V7.25.21-13 | ZTE CORPORATION / Valid | `610AFE0851789342BF35D4D5A12D893BE8A30A54A4EFAA5969F7F3AAF18B5B31` |

## 证据限制

- `zte-os-identity-audit.txt`、`usmart-updater-audit.txt` 等聚合文件包含 2024/2025 历史日志；引用时必须附时间，不可整体视为当前实例证据。
- 静态字符串证明代码路径/能力，不证明对应路径在当前实例被执行。
- 当前目录不存在包只能证明“本机未发现”，不能证明 ZTE/中国移动后台绝对不存在。
- 官网/专利证明 ZTE 产品族能力，不证明中国移动公众版当前资源池的许可、策略或包可用性。
