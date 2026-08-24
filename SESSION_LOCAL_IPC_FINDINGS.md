# ICE 本地会话 IPC 证据

更新时间：2026-08-24  
范围：IceDisplay 与 IceTunnel/IceVGPUCapture/IceInput/IceSound/Vdagent 的 localhost 会话；只读动态状态与静态代码分析。

## 已证明事实

- 当前 `IceDisplay.exe` 监听 TCP `60063`，并分别与 IceTunnel、IceVGPUCapture、IceInput、IceSound、Vdagent 保持 5 条独立 localhost TCP 连接。
- Capture/Input/Sound 都包含同源的 `sock_func.c` framing 代码，日志锚点包括 `LinkHeader_t`、magic、`ctrl_msg`、`msg_type`、`msg_size` 和 connect acknowledgement。
- `IceDispalyPort_%u`（原程序拼写）由 session id 参数化；Capture/Input/Sound/Vdagent 均包含该端口发现键。
- 当前稳定运行状态下没有在注册表查到 `IceDispalyPort_<session>` 值；IceDisplay 同时包含 `ice_del_app_server_port`。
- IceVGPUCapture、IceInput、IceSound 的连接初始化代码都向本地 socket 写入同一个 4 字节 `LinkHeader_t`，其值为 `0x0000009a`；日志调用同时明确输出 `LinkHeader_t:4`。
- 通用消息解析代码检查单字节 magic `0xaa`，随后按控制标志、消息类型和消息长度处理；这与前述 4 字节连接头是两个阶段，不能合并为同一个结构。
- 握手成功后，各子组件发送 6 字节 channel registration payload，前 4 字节共同为 `0x00060443`，最后一个字节是 channel type：
  - IceInput 注册 `3` 和 `4`；
  - IceSound 注册 `5` 和 `6`；
  - IceVGPUCapture 注册 `12`。
- `3/4/5/6` 与上游 SPICE 的 inputs/cursor/playback/record channel 编号一致；`12` 是当前上游常用 channel 集合之外的 ZTE Capture 扩展。
- Capture type `12` 的内部调度已确认：message type `1` 进入控制消息处理，type `2` 进入连接状态处理；连接事件 `0x65` 触发注册，`0x66` 表示断开。
- Capture 控制事件至少包括 `0x44e`（配置消息，后随配置文本）和 `0x472`（GPU/capture 参数变化）。
- IceVGPUCapture 同时打包两类明确的数据源路径：
  - 驱动/共享文件路径：`C:\Windows\System32\video_%d.dat`、surface command、dirty rectangles 和共享内存复制；
  - DDA/DXGI/D3D11 路径：Desktop Duplication 捕获、GPU texture、AMD/NVIDIA/MT 硬件编码及 CPU 映射回退。
- 当前系统确实存在 4 个约 35 MB 的 `video_0.dat`–`video_3.dat` 和 4 个 `cursor_*.dat` 文件；仅凭文件存在及时间戳不能判断当前会话正在使用哪条捕获路径。
- `video_*.dat` 的精确总大小为 `35,426,672`：`0x9170` 字节控制区加 `4096×2160×4=35,389,440` 字节最大像素区。该计算与 IceVGPUCapture 中的 `0x9170` 常量及文件实际长度完全一致。
- 对活动 `video_0.dat` 做两次间隔 2 秒的只读比较，控制区变化 42 字节、像素区变化 36,642 字节；因此它不是静态遗留文件，而是当前活动数据面。
- IceDisplay 的 `display/pipe.c` 明确打开同一 `video_%d%s.dat`，计算 command/surface offsets，并消费 surface messages、dirty rectangles；Capture 侧 `memOp.c` 负责向共享区添加 surface command 和 dirty rect。
- IceDisplay 计算出的精确偏移为：surface-command ring=`0x906e`，pixel frame=`0x9170`；ring 长度正好是 `0x102` 字节。
- surface-command ring 布局已恢复为：`u16 next_index`、16 字节时间戳、16 个 15 字节记录。记录结构为 `u8 type, i32 surface_id, u16 width, u16 height, u16 stride, u16 bpp, u16 flags`。
- type `0/1/2` 分别由 IceDisplay 处理为 destroy all、destroy primary、create primary。当前活动记录为 type `2`、surface id `0`、`3072×1920`、stride `12288`、32 bpp、flags `1`，与当前视频控制器分辨率完全一致。
- dirty-rect ring 已恢复：`0x5a` 为 16 字节时间戳，`0x6a/0x6c` 为 `u16` read/write index，`0x6e` 起有 4096 个 9 字节记录，恰好延伸到 `0x906e`。
- dirty 记录布局为 `u8 state, u16 top, u16 left, u16 bottom, u16 right`。活动历史槽解析出的坐标均落在当前 `3072×1920` surface 内；producer/consumer 索引在运行中持续前进。

## 当前最优假设

- [高概率] IceDisplay 在启动阶段按 Windows session id 临时发布端口，子组件取得端口并连接后删除该发现值；要证明需要捕获下一次正常冷启动期间的注册表时间线。
- [确定] 4 字节 `0x9a` 是共享连接握手常量，不是 Capture 专属角色值；组件角色在后续注册消息中表达。
- [中概率] `0xaa` 是后续通用 socket 消息头 magic；字段的精确字节布局仍需逐个调用点或受控仿真确定。
- [高概率] ZTE 本地会话层保留了 SPICE channel type 语义，但包裹在自有 localhost transport/framing 和扩展 channel 中。
- [确定] 当前活动路径至少包含 Capture 写 `video_0.dat`、IceDisplay 读取该共享映射的原始像素/surface command 数据面；localhost TCP 承担注册和控制。
- [高概率] IceDisplay 在当前活动路径中完成主要编码和客户端通道输出；Capture 自带的 GPU 编码路径属于可配置的替代路径，不能当作当前必经路径。

## 尚未证明

- 未证明这些本地消息结构是版本稳定的公开插件 ABI。
- 未证明 IceVGPUCapture 可被单独替换而不依赖 Windows DDA/D3D/驱动共享内存。
- 未确定 Vdagent 后续注册消息中的准确 channel type/id；初始 4 字节握手不能用于区分组件。
- 已恢复 dirty-rect ring 和末尾 surface-command ring；`0x0000–0x0059` 的全局元数据/同步字段仍未完整恢复。
- 未恢复 type `12` 编码视频块的 framing、时间戳、显示编号、关键帧标志和重配置确认；当前新增常量只覆盖控制面。
- 未确定当前实例实际选择共享文件、DDA 软编码、DDA 硬编码或其他 vGPU 路径；对应运行日志正被进程独占，静态配置和现有注册表没有给出可证明的选择值。

## 路线含义

进程与 TCP 边界清晰，且 Input/Sound channel type 与 SPICE 一致。活动画面数据面现已定位到共享映射，这降低了“未知 TCP 媒体协议”的不确定性，但没有解决 Debian 上缺少原生 IceDisplay/IceTunnel 的问题。若取得官方 Linux IceDisplay/`x11ice`，Linux Capture adapter 很可能可围绕共享 surface/command ABI 实现；若只能使用 Windows PE，则还需证明 Wine 或其他受支持运行方式能承载 Display/Tunnel，当前没有该证据。

本文件不包含连接、发送、重放或 Host 通信代码。
# 离线 writer（2026-08-24）

- `prototype/ice_local_protocol.py` 已增加纯内存 video map writer：surface-command ring、dirty-rect ring 和 packed primary frame 写入。
- writer 保留所有未知/保留字节，dirty ring 满时拒绝覆盖未读记录；不会打开或修改活动 `C:\Windows\System32\video_0.dat`。
- `state=6` 的语义仍未确认，所以 API 要求调用者显式提供 state，不把 6 固化成通用默认值。
