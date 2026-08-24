# Windows Guest 与 Mock Telemetry Agent 协议差异矩阵

更新时间：2026-08-24  
用途：授权测试环境中的协议覆盖、解析兼容性、异常注入和服务端检测验收。  
安全边界：Mock 仅支持 Memory 和 Loopback TCP；本报告将拟真差异表述为服务端检测信号，不给出规避检测的改造步骤。

## 1. 证据范围与结论

真机侧证据来自当前安装后 `2026-08-24 00:24` 启动窗口的：

- `vmswitch.log` Guest→Host/Host→Guest消息；
- `vmbooster.log`业务分派和本地IPC；
- `QoEAgent.log`环境、软件、性能、进程、活动和ICE事件；
- Windows系统事件中的QGA十分钟校时；
- ICE/Vdagent日志中的会话事件。

镜像中早于 `2026-08-23 02:44` 的 `7002` 管理升级记录属于模板历史，不作为当前实例本次运行的Host命令，但证明Agent代码具备该分支。

当前总判断（Schema对齐补全后）：

- Mock已覆盖核心启动遥测、VM身份、网络/OS确认、30秒心跳和9050–9056/9060测试载荷。
- Mock已新增1300/1400、8007、8059、8063/8064、9011/9012；仍未达到Windows Guest全生命周期协议等价。
- 最大差异不是JSON字段，而是实际Mswitch线协议、缺失业务消息、异常恢复状态机及ICE/QGA旁路。

## 2. 消息全集覆盖

### 2.1 Guest→Host

| ID | 真机作用与时机 | Mock状态 | 主要差异 |
|---:|---|---|---|
| `1300` | 启动时MAC报告 | 已实现/简化 | 使用测试对象payload及测试MAC |
| `4002` | 约30秒心跳 | 已实现/Schema对齐 | 已补`issysprep`；sequence/uptime由Profile开关控制，测试标记保留 |
| `4004` | 组件版本；当前约2小时一次 | 已实现/简化 | Mock只在启动时发送，版本字段集合少于真机 |
| `8007` | 约5分钟RDP状态 | 已实现/简化 | 随QoE抖动窗口发送测试RDP状态 |
| `8008` | 启动/重连请求VM资料 | 已实现 | Memory/Loopback测试状态机 |
| `8047` | 本地事件携带动态msgid后转发Host | Synthetic hook | 已有动态关联ID测试钩子；业务来源和响应语义仍未确定 |
| `8059` | 网关/IP/主机名告警，约5分钟 | 已实现/简化 | 随QoE抖动窗口发送Profile状态 |
| `8060` | 锁屏状态 | 已实现/测试状态机 | 锁屏/解锁切换与9053合成活动事件使用同一状态 |
| `8063` | CSAP地址请求 | 已实现/测试状态机 | 动态测试msgid与8064关联 |
| `9011` | IP信息请求，动态`getipinfo<id>` | 已实现/测试状态机 | 动态测试msgid与9012关联 |
| `9050` | 启动环境快照 | 已实现/Schema对齐 | 已剥离网络子字段并支持computername/OS URL编码 |
| `9051` | 每5分钟、含5个一分钟性能样本 | 已实现/简化 | Seed动态值；字段丰富度低于WMI/PDH真机数据 |
| `9052` | 每5分钟进程Top | 已实现/简化 | 合成进程池，行格式和指标维度简化 |
| `9053` | 事件触发及周期活动/会话 | 已实现/简化 | 仅合成输入事件，无完整登录/认证/显示事件族 |
| `9054` | 启动时3批软件/KB | 已实现/字段对齐 | JSON路径/类型匹配，名称支持URL编码；值为测试Profile |
| `9055` | 启动时间 | 已实现/简化 | 使用模拟时钟 |
| `9056` | ICE网络连通性 | 已实现/简化 | 固定测试行，未执行真实连通探测 |
| `9060` | ICE认证、Set-Key、Display等追踪 | 已实现/高度简化 | Mock只有最小`resourceSpans`，无完整span链 |
| `0x8102bf` | 网络资料报告 | 已实现/简化 | 测试字段为对象；真机线payload为分隔文本 |
| `0x8102c4` | 当前窗口约28分钟或会话事件发送1字节状态 | Binary shape only | 已按单字节`0/1`编码；业务语义仍不能可靠命名 |
| `0x8102c5` | Windows OS报告 | 已实现/简化 | Mock对象结构；真机为`OsName=...;Osbit=...`文本 |

### 2.2 Host→Guest

| ID | 真机证据 | Mock状态 | 主要差异 |
|---:|---|---|---|
| `1400` | 回送MAC→MAC UUID映射 | 已实现/测试状态机 | 返回测试MAC UUID |
| `4100` | 每次`4002`后返回VM UUID | 已实现/测试扩展 | Mock回显测试sequence；真机payload没有该字段 |
| `8009` | 回答`8008`，包含VM UUID/BusServer | 已实现/简化 | Mock只返回测试Session UUID |
| `8064` | 回答`8063`，包含CSAP IP/port和动态msgid | 已实现/测试状态机 | 使用TEST-NET端点 |
| `9012` | 回答`9011`，包含VM/Host UUID和动态msgid | 已实现/测试状态机 | 使用测试Host UUID |
| `8052` | 每约2小时向Vmbooster下发OAS/report策略：`oas_flag/oas_interval/report_flag` | 缺失/语义待定 | 当前实例已确认实际下发；未观察到专用ACK |
| `9502` | 与8052同msgid向VmQoEAgent下发`oas_interval` | 缺失/语义待定 | 当前实例已确认实际下发；未观察到专用ACK |
| `0x8102c1` | 网络报告确认/回显 | 已实现/简化 | Mock只返回`result=0` |
| `0x8102c7` | OS报告确认/回显 | 已实现/简化 | Mock只返回`result=0` |
| `7002` | 模板历史中的管理员升级命令 | 缺失且不应在安全Mock执行 | 当前安装后未观察到；Mock不执行升级/命令 |

### 2.3 Guest本地IPC但非Host消息

下列消息在 `127.0.0.1` 本地组件之间出现，不能误标为Host下发：

| 本地交互 | 当前判断 | Mock状态 |
|---|---|---|
| `8065→8066` | 触发CSAP地址刷新 | 缺失 |
| `8067(connect=0/1)→8068` | 本机会话连接状态 | 缺失 |
| `8097(computername)→8098` | 主机名状态 | 缺失 |

### 2.4 独立旁路

- QGA/vport0p1：已确认Host每10分钟调用`host-get-time`；Mock已提供确定性600秒Fake Host调度与JSON-RPC应答，不连接真实vport。
- ICE/Vdagent：认证、显示、输入、音频、分辨率和剪贴板属于独立会话链；Mock的`9060`不能替代这些组件。
- USB/打印：UsbIpc及多个VirtIO Serial端口未覆盖。

## 3. 字段级Schema差异

### 3.1 Mswitch头和传输封装

| 层 | 真机 | Mock |
|---|---|---|
| 消息头 | `0x80`字节，magic=`0x5b5b5b5b`、version、msgtype、dst_type、UUID16、src/dst module、int_msgid、data_len | JSON `Envelope`字段 |
| 本地注册 | `0x20130223`请求/响应，分配UUID16 | Mock对象状态机，没有真实模块注册 |
| vport framing | `;`、`\`转义，未转义`;`结帧 | Loopback为4字节大端长度+JSON |
| 路由 | MswitchWin按module/UUID路由 | TestHostResponder按`int_msgid`分派 |
| 未知头字段 | 真机保留/回填 | Mock不存在 |

因此当前Mock的Payload语义测试通过，不代表真实线协议兼容。

### 3.2 `9050`环境

共同顶层字段：

```text
source:int
uuid:string
hostid:string
time:string
groupid:string
createtime:string
environment:object
```

真机和Mock均含：`computername/cpu/os/bit/mem/mac/ip/disk/diskused/version/targetversion`，类型均为字符串。

差异：

- 真机的computername、OS等字段按URL编码后写入JSON；Mock现已按Profile开关支持同类URL编码。
- Mock已从9050剥离`gateway/netmask/dns/dhcp`，这些字段只在独立网络/告警消息中使用。
- Mock使用测试UUID/hostid；真机使用Mswitch/VM状态取得的实例标识。
- 真机磁盘值来自启动时实际扫描；Mock来自冻结Profile。

### 3.3 `9054`软件/KB

当前字段路径和类型基本一致：

```text
source:int
uuid:string
hostid:string
createtime:string
mothod:string       # 保留原程序拼写
softwares:array
  name:string
  type:string       # 1=软件，2=KB
  publisher:string
  installtime:string
  size:string
  version:string
  operate:string
```

差异：

- 真机启动时实际扫描64位卸载键、Wow6432Node和WMI KB；Mock读取冻结JSON。
- Mock现已对软件/KB名称执行可配置URL编码。
- 三批在同一秒发送，两侧时序一致；每批数量和排序没有设置为真机实例的镜像。

### 3.4 `4002`心跳

真机字段：

```text
msgtype:string
agentversion:string
vmid:string
agentstatus:string
computername:string
issysprep:string
```

Mock默认对齐字段：

```text
msgtype:string
agentversion:string
vmid:string
agentstatus:string
computername:string
issysprep:string
```

`sequence/uptime_seconds`可由Profile中的`heartbeat_extensions`开启；当前模板关闭。测试来源标记仍保留在测试API对象中。

## 4. 状态机与时序

### 4.1 正常启动

真机：

```text
服务启动
→ 三个Agent向Mswitch注册
→ 8008→8009
→ 1300→1400
→ 网络报告→0x8102c1
→ OS报告→0x8102c7
→ 9055/9050/3×9054
→ ICE Set-Key/Authentication/Display
→ 30秒4002/4100
→ 5分钟8007/8059/9051/9052/9053/9056
→ 2小时4004/CSAP刷新
→ 10分钟QGA校时（独立）
```

Mock：

```text
NEW
→ 8008
→ 收到8009：VM_IDENTIFIED
→ 网络/OS报告
→ 收到c1/c7
→ 4004
→ HEALTHY
→ 启动9055/9050/3×9054/9060
→ 精确30秒心跳
→ 精确5分钟QoE
```

### 4.2 异常路径

| 场景 | 真机证据 | Mock当前行为 | 等价性 |
|---|---|---|---|
| `8009`超时 | 未捕获实际超时窗口 | 立即抛错并进入`DEGRADED` | 不等价；无重试/退避 |
| 网络/OS ACK缺失 | 未捕获 | 立即`DEGRADED` | 未证明真机同样处理 |
| 单次`4100`丢失 | 当前日志中未出现可确认丢包 | 连续1–2次丢失仍保持`HEALTHY`；第3次进入`DEGRADED` | 已具备测试容错，真机阈值仍未知 |
| TCP/vport断线 | 重启后Agent会重新注册；运行时完整退避规律未恢复 | 无自动重连；Loopback socket错误直接异常 | 缺失 |
| Host重新协商 | 未观察到明确消息 | 仅测试代码显式调用`renegotiate()` | 缺少真实触发器 |
| 重复/乱序ACK | 未观察到 | 非当前阶段响应会被忽略；无窗口/去重表 | 简化 |
| Agent重启 | 真机会重新取UUID、重发基线 | Mock重新实例化可重跑，状态不持久化 | 部分覆盖 |
| 系统关机 | 看到Windows服务收到`SERVICE_CONTROL_SHUTDOWN`，未确认专用最终上报 | 无关机状态/最终消息 | 缺失 |
| 后台升级 | 模板历史有`7002/7005/7004` | 明确不执行 | 安全Mock预期差异 |

### 4.3 时序差异

- 真机`4002`：中位30秒，当前窗口约28–31秒；Mock使用Seed驱动的28–31秒均匀/高斯可配置抖动。
- 真机`9051/9052`：约299–301秒；Mock使用Seed驱动的298–303秒可配置抖动。
- 真机`9053`：同时具有事件触发和周期路径；Mock只有5分钟周期。
- 真机`9056`：约299–305秒；Mock严格300秒。
- 真机`4004`：约7200秒；Mock只在启动发送一次。
- 真机`9060`：由认证、登录、显示和输入事件触发，间隔高度不规则；Mock只在启动发送一个最小trace。

## 5. 服务端可用于识别Mock的检测信号

以下是防御检测差异，不是绕过建议：

1. 明确测试标记：`test_mode=true`、测试UUID命名空间、`TEST-`名称、TEST-NET地址和本地管理MAC。
2. Transport指纹：新增Loopback-only Mswitch二进制Provider，覆盖0x80头、模块路由字段及vport转义；实际VirtIO设备接入未实现。
3. 调度模型：虽已加入Seed抖动，但没有真实系统暂停、线程竞争或负载反压。
4. Schema指纹：测试API仍保留测试来源标记；开启heartbeat扩展时会出现sequence/uptime。
5. 编码模型：已支持指定字段URL编码，但没有覆盖所有真机字符串边界和异常字符。
6. 消息缺口：8047仅有Synthetic hook，0x8102c4仅有二进制shape；Host策略8052/9502及本地8065/8067/8097仍未实现。
7. 事件熵不足：9053、9056、9060事件族和顺序远少于真机会话链。
8. 进程模型简化：9052各Top维度使用相同行集合，真机五个Top列表通常不同。
9. ACK延迟：Memory/Loopback响应接近零延迟，真机Host往返和队列存在波动。
10. 跨通道缺失：QGA、ICE、Vdagent、USB/打印没有与管理遥测形成一致状态。
11. 生命周期断层：无真正的服务重启、网络切换、锁屏、关机和升级状态。
12. Seed可重复：相同Seed产生完全相同的长序列，服务端可在测试数据中验证可重复性。

## 6. 自动诊断脚本

脚本：[tools/protocol_diff.py](C:/Users/Administrator/Desktop/zte-research/tools/protocol_diff.py)

运行当前安装后的窗口：

```powershell
cd C:\Users\Administrator\Desktop\zte-research

python .\tools\protocol_diff.py `
  --since 2026-08-24T00:24:00 `
  --mock-duration 600
```

保存Markdown结果：

```powershell
python .\tools\protocol_diff.py `
  --since 2026-08-24T00:24:00 `
  --mock-duration 600 `
  --output .\protocol-diff.generated.md
```

脚本只输出：

- Protocol ID及方向；
- 真实/Mock出现次数；
- 字段JSONPath及类型；
- 中位/最小/最大发送间隔；
- 覆盖/缺失状态。

它不输出UUID、MAC、IP、软件名、进程名或payload值。

## 7. 当前验收结论

| 门禁 | 状态 |
|---|---|
| 9050/9054 JSON字段类型 | 通过当前自动字段门禁；值域仍为测试Profile |
| 4002 JSON字段类型 | `issysprep`已对齐；扩展字段当前关闭 |
| 核心Memory双向握手 | 通过 |
| Loopback双向握手 | 通过 |
| 全消息ID覆盖 | 未通过 |
| Mswitch真实线协议 | 未接入Mock |
| 运行时重连/退避 | 未实现 |
| QGA/ICE/Vdagent跨通道 | 未覆盖 |
| 关机完整生命周期 | 未覆盖 |
| 当前单元测试 | 40项通过 |

因此当前Mock适用于解析、阈值、字段、异常注入和Loopback状态机测试，不应被标记为Windows Guest协议等价实现。
