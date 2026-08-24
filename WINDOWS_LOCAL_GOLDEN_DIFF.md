# Windows 本地 Golden Diff 结果

## 可用证据边界

当前工作区没有真实 Windows `.bin`、`.pcap` 或 `.pcapng`。现有 `.bin` 均位于
`tmp/golden-fixtures.synthetic`，由 Mock 生成。真机可用证据是：

- `C:\Program Files (x86)\vmtool\vm_booster\vmswitch.log` 中的
  `data_len/ret/msg=`；
- `Vmbooster.strings.txt`、`VmQoEAgent.strings.txt`、`IceDisplay.strings.txt`
  中的 PE 格式串；
- 已确认的 0x80 Header 偏移和串口转义规则。

因此目前可以确认 Schema、字段顺序、可见文本尾字节、固定 payload 长度及
`ret = 0x80 + data_len + escape_expansion + 1`；不能对未知 Header 字节、真实
UUID16 和完整转义流宣称 0 diff。

## 对齐结果

| ID | 真机证据 | 修正后 Mock | 结果 |
|---:|---|---|---|
| 4002 | 顶层顺序为 `msgtype,agentversion,vmid,agentstatus,computername,issysprep`；可见尾为 `}`；`data_len=512`，常见 `ret=641` | 相同字段顺序；JSON 后以 NUL 填充到512；raw=640，零转义时serial=641 | 已对齐已证明部分 |
| 4004 | 顺序为 `msgtype,vmid,vmbooster,vmagent,PVDriver,vdagent,usbipc,media_redirect`；允许后三个版本为空；`data_len=512`、`ret=641` | 相同单引号Plaintext顺序；允许可选空版本；NUL填充到512 | 已对齐已证明部分 |
| 8047 | Host线payload为内层 `{msgtype:'8047',msgid:'...',vmuuid:'...'}`，不是IceDisplay本地外层包装；`data_len=80/81` | 改为内层对象；Golden使用10位synthetic msgid，`data_len=81` | 已修正结构和长度类别 |
| 9050 | 顶层顺序为 `source,uuid,hostid,time,groupid,createtime,environment`；environment顺序为 `computername,cpu,os,bit,mem,mac,ip,disk,diskused,version,targetversion`；可见尾为 `}`；长度随值变化 | 两层顺序一致；无NUL填充；当前synthetic `data_len=480` | Schema/尾字节对齐，值长度不同属预期 |

## Tail Byte

- 4002：可见 JSON 以 `0x7d` (`}`) 结束，其后 NUL 填充至512字节。
- 4004：可见 Plaintext 以 `0x7d` (`}`) 结束，其后 NUL 填充至512字节。
- 8047：Host payload 以 `0x7d` (`}`) 结束，无固定512填充证据。
- 9050：以 `0x7d` (`}`) 结束，无 `LF/CR/NUL` 尾部证据。

## Serial Escaping

现有静态分析确认的转义不是 `0x7d`：

- `0x3b` (`;`) → `0x5c 0x3b`
- `0x5c` (`\`) → `0x5c 0x5c`
- 帧尾追加未转义 `0x3b`

转义作用于完整 `0x80 Header + payload`。真机日志中
`ret - 128 - data_len - 1` 的观察扩展量包括：4002=`0..5`、4004=`0..3`、
8047=`0..2`、9050=`0`，与 Header/正文中 `0x3b/0x5c` 出现次数变化相容。

当前没有证据表明协议使用 `0x7d` 作为 escape byte，也没有发现独立 checksum
字段或可验证的 checksum 计算。`CRC_Send` 只是当前日志函数名证据，不能单独证明
线协议存在 CRC 字段，因此没有添加猜测性校验和。

## 仍需真实帧才能完成的门禁

获得脱敏 raw/serial `.bin` 后，以下命令才能给出真正的完整帧 0 diff：

```powershell
python tools\mswitch_golden_diff.py real.serial.bin mock.serial.bin --serial-framed
```

在此之前，完整 Header 与真实转义字节流状态保持“待验证”，不标记为 0 diff。
