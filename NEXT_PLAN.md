# Next Plan

所有步骤默认只读。凡涉及连接私有服务、运行未知二进制、安装软件、改变服务/驱动/注册表/网络/启动盘，必须暂停并取得用户明确确认。

## 当前主线 P1：取得 Linux GuestTools 实际介质

1. 精确询证 `vmtool_linux`、`install_vmdesk.sh`、`vm_upgrade_mount_iso.sh` 和 `V7.25.21SP3-9 Linux GuestTools`。
2. 优先来源：ZTE/中国移动官方支持介质、正式 manifest、安装手册或有校验信息的离线包。
3. 询证时同时询问 Linux 介质的 ISO **文件名与卷标**，避免把 `/media/vmtool_linux` 的 mount point 误当成文件名；提供 Windows 同代卷标 `vt_V7.25.21SP3-9` 作为定位锚点。
4. 得到介质后只做离线验签、哈希、列目录和解包检查；不得安装、执行或触发 Host 挂载。
5. 成功标准：实际 ISO/RPM/DEB/tar/脚本及合法来源；只有组件名或产品宣传不算完成。

## P2：版本对应关系

1. 对照当前 Windows `V7.25.21SP3-9`、ICE `V7.25.21`、QGA build `215a7089`。
2. 恢复 Linux ISO/packageversion/manifest 中的产品版本和各组件版本。
3. 区分“同一 V7.25.21 主线”与“精确 SP3-9 兼容”；无官方矩阵时不自行等同。

## P3：发行版支持矩阵

1. 从 `install_vmdesk.sh` 提取发行版检测、RPM/DEB 分支、架构和依赖。
2. 分别列 Ubuntu、Debian、Kylin、UOS、CentOS/RHEL、openEuler/NewStart 支持状态。
3. 提取 glibc、内核、systemd、Xorg/Wayland、PulseAudio/PipeWire 要求。
4. 当前 Debian 13 保持 [待验证]，不得由泛称 Linux 推导兼容。

## P4：`x11ice` 官方实现

1. 取得 `x11ice.service` unit、二进制、版本和启动参数。
2. 验证 `x11ice:60063` 是否对应 Windows `IceDisplay`，以及与 `tunnel:5100`、`iceaudio`、Input/Capture/Sound/Vdagent 的拓扑。
3. 确认 X11 与 Wayland 支持边界；优先官方现成实现，不预设重写 Windows ICE 组件。

## 暂缓方向

- `65188`、External Guest Agent、多 VM Guest Proxy、一台 Windows 代理多 VM。
- 协议模拟、Host 发包、服务替换、Wine 稳定性和自研媒体桥；除非 P1–P4 证明官方组件不可获得才重新评估。

## 下一轮具体动作

1. 通过官方支持渠道索取或定位 `vmtool_linux` ISO；询证时使用精确锚点 `V7.25.21SP3-9 Linux GuestTools`、`install_vmdesk.sh`、`vm_upgrade_mount_iso.sh`，但不提交本机日志、UUID、IP 或其他敏感数据。
2. 若获得介质，建立只读清单：卷标、SHA-256、签名、目录树、`install_vmdesk.sh`、package manifest 和全部 systemd unit。
3. 从脚本生成严格的“发行版 × 架构 × 依赖 × 组件版本”矩阵。
4. 单独核验 `x11ice.service` 的 `ExecStart`、依赖、监听端口和进程拓扑。
5. 在 P1–P4 完成前，不恢复协议模拟、Wine 或自研 ICE 桥工作。
