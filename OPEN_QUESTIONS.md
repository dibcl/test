# Open Questions

仅记录尚未解决的问题，按阻塞 Linux 原生迁移的程度排序。

## P1：平台管理链

1. [已缩小] V7.25.21 同代产品的 Linux ISO 入口为 `/media/vmtool_linux/install_vmdesk.sh`；待取得实际 ISO/包及合法下载/支持入口。
2. [待验证] Linux 介质的正式文件名、哈希/签名、版本，以及其中是 RPM、DEB、tar 包还是直接文件树。
3. [待验证] `/media/vmtool_linux` 中的 `vmtool_linux` 是 ISO 卷标、固定 mount point 还是脚本创建目录；当前不能当作正式 ISO 文件名。
4. [待验证] Linux 侧是否复用 Windows 的 7002/7005/7004 管理升级消息，还是由另一条本地/Host 通道触发 `0x22`。
5. [待验证] `vmbooster/vmmonitor/x11ice/usbipc/vmoasagent` 的 Linux 二进制和 unit 内容、各自版本、CPU 架构与依赖。
6. [待验证] `vmoasagent.service` 是否真实随当前产品交付；目前只有 SysGuard 模板单一证据。
7. [待验证] Linux 构建的发行版矩阵；当前证据偏向 RPM/X11、UOS/DDE、麒麟/UKUI、NSDL/NewStart，Debian/Ubuntu 与 Wayland 未证明。
8. [待验证] glibc、内核、Xorg/Wayland、PulseAudio/PipeWire、systemd 的最低版本要求。
9. [待验证] 中国移动公众版 Host 是否启用/授权该 Linux ISO，还是仅 ZTE 政企产品族支持？
10. [待验证] qemu-ga 在本资源池承担哪些平台动作；当前只动态证明 10 分钟时间同步。
11. [待验证] 平台判定 Guest 正常所需的最小官方组件集合是什么。

## P2：RAP/ICE 会话链

1. [已缩小] `x11ice` 高概率是 Linux/X11 的 60063 会话/显示枢纽；待取得 unit、二进制和启动参数确认。
2. [待验证] Linux `tunnel/IceInput/IceVGPUCapture/IceSound/Vdagent/iceaudio` 是否同包交付，各自版本和进程拓扑是什么？
3. [待验证] 标准 `spice-vdagent(d)` 在 ZTE 套件中承担哪些上游职责，哪些仍由 ZTE ICE 扩展承担？
4. [待验证] 是否支持 Wayland/PipeWire；当前只发现 X11/Xorg/PulseAudio 线索。

## P3：更新与交付

1. [待验证] 从 ZTE/中国移动合法支持渠道取得 `vmtool_linux` ISO、manifest 和支持矩阵；公开支持站未登录状态无法加载产品文档索引，公开精确名称搜索也没有可下载命中。
2. [待验证] 是否能获得与 `V7.25.21SP3-9` 精确匹配的 Linux ISO，而不只是同一 `V7.25.21` 主线。
3. [待验证] 专利所述 Host 投递/挂载与本机 `MountISO`/`install_vmdesk.sh` 流程在当前公众版是否启用；不得主动触发。

## 已降优先级

- `65188`、External Guest Agent、多 VM Guest Proxy；除非出现非 loopback 对端、多 VM UUID 路由或官方文档新证据，不继续投入主线时间。

## 最终迁移门槛

1. [待验证] 中国移动明确允许自定义/切换 Guest OS，且不会触发失联、锁定或强制重装。
2. [待验证] 获得官方 Linux 集成包及安装/卸载/恢复文档，并验证包来源和签名/哈希。
3. [待验证] 有不覆盖 Windows 的可回滚验证路径（例如平台官方快照/镜像/备用系统盘能力）；任何实施前必须另行获得用户明确确认。
