# Debian/Wine ICE Session PoC

更新时间：2026-08-24（Asia/Shanghai）

## 目标与隔离条件

验证已签名 Windows ICE 会话组件能否在 Debian/Wine 中运行到服务和
localhost 会话边界。该 PoC 不连接移动云后台，不挂载实例身份材料，也不包含
任何管理协议发送代码。

- Debian：官方 `debian-13-genericcloud-amd64.qcow2`
- QEMU：11.1.0，TCG（宿主无嵌套虚拟化）
- Wine：Debian 10.0；另建子覆盖盘测试 WineHQ 11.0
- ICE 载荷：仅复制 `IceServer` 目录的 `.exe/.dll`
- 明确排除：`ice_session`、`ice_info_*.dat`、`ice-config.ini`、
  `tunnel.ini`、`mainservice.xml`
- 组件测试启动参数：`-nic none`；Guest 内 `ip -brief link` 只有 `lo`

## 已确认结果

### Wine 10 单组件/服务语义

- 命令行直接运行 `IceDisplay.exe` 时，PE 进程可持续运行至少 20 秒，但没有
  监听 60063。
- 命令行直接运行 `IceTunnel.exe` 会进入 Winsock 初始化，随后出现
  `RPC_S_SERVER_UNAVAILABLE (0x6ba)` 并以 255 退出。
- 把组件注册到 Wine SCM 后，`IceMainService` 曾稳定进入 `RUNNING`，并自动
  拉起 `IceTunnel.exe`、`IceDisplay.exe`、`IceDisplaySetting.exe`。
- 同一成功窗口内，Guest 出现 `0.0.0.0:60063` 和 `*:5100` 监听；这证明
  Wine 能承载当前构建的核心 ICE 服务入口，先前单独启动失败主要是 Windows
  服务语义不等价。
- 后续核心稳定性门禁（只启动 `IceMainService`，每 10 秒采样 6 次）结果为
  `CORE_GATE_RESULT=0`：服务首次报告 `RUNNING`，随后报告 `STOPPED`、退出码
  `1077 (0x435)`，整个采样窗口没有保持 60063/5100。早先的监听结果因此应标记
  为“瞬时可达”，不能视为稳定性通过。

### 输入、声音与捕获进程

- `IceInputService.exe`、`IceSound.exe` 可由 Wine SCM 启动。
- `IceInputService.exe` 能拉起 `IceInput.exe`。
- 在 Xvfb 下手工启动 `IceVGPUCapture.exe` 后，进程至少存活 10 秒。
- 本轮核心 Display/Tunnel 未稳定留存，因此尚未观察到这些子组件到 60063 的
  ESTABLISHED 会话，不能把“进程能启动”升级为“功能已工作”。

## 当前阻塞

- Wine 10 在五组件并发窗口触发内部断言：
  `server/fd.c:1629: set_fd_events: Assertion 'poll_users[user] == fd' failed`。
  wineserver 崩溃后所有 Wine 服务停止。
- 核心三服务曾成功出现 `RUNNING` 和监听，但核心稳定性门禁复跑在 10 秒内停止；
  因此当前 PoC 证明“瞬时可达”，未证明“可重复稳定”。
- WineHQ 11.0 已安装到独立子覆盖盘并验证 `wine-11.0`，但旧前缀迁移和全新
  前缀的 `wineboot` 均在 180 秒门限内未完成，后续 SCM 命令阻塞。当前不能
  判断 Wine 11 是否修复 Wine 10 的 fd 断言。

## 结论

Wine 路线不再是纯假设：核心服务、60063/5100 监听以及输入/声音/捕获进程均
已在无网卡 Debian VM 中到达。但它还不能用于最终镜像，下一门禁是获得可重复
的完整启动、稳定 wineserver，并观察 Input/Sound/Capture 到 60063 的独立连接。

## 可重复材料

- `tools/create_nocloud_seed.py`
- `lab/cloud-init/user-data`
- `lab/cloud-init/meta-data`
- `lab/run_ice_wine_probe.sh`
- `lab/run_ice_service_probe.sh`
- `lab/install_winehq11.sh`

实验盘在 `D:\zte-lab\vms\ice-wine-lab`；Debian 原始镜像保持不变，Wine 10
和 WineHQ 11 使用独立 qcow2 覆盖层。
