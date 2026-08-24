# ZTE Guest 管理协议架构（静态分析阶段）

更新时间：2026-08-24  
证据范围：本机已安装 PE、已有日志与进程/端口快照；本阶段未连接 Host、未发送任何测试消息。

## 1. 已确认的数据路径

```text
Vmbooster (module 0x80000001) ─┐
VmQoEAgent                    ├─ libmswitch.dll ─ TCP 127.0.0.1:10000 ─ MswitchWin
VmBoosterMonitor              ┘                                      │
                                                                        └─ com.vmswitch.0 (vport0p3) ─ Host
```

`libmswitch.dll` 只负责本地 Agent 到 MswitchWin 的注册、消息构造和 socket 收发。MswitchWin 维护 `module -> local socket` 路由表，并把需要上行的消息做字节转义后写入 VirtIO Serial。

## 2. libmswitch 导出 ABI

以下调用约定来自导出函数和 Vmbooster 调用点的 x86 静态反汇编。

```c
int Register(uint32_t local_mod, uint8_t uuid_out[16]);

int BuildMsg(
    uint32_t dst_mod,
    const uint8_t uuid[16],
    int16_t dst_type,
    uint32_t int_msgid,
    const void *payload,
    int32_t payload_len,
    void *out_message);

int SendMsg(uint32_t local_mod, const void *message, int32_t message_len);
int RecvMsg(uint32_t local_mod, void *buffer, int32_t buffer_len);
int RecvMsgTimeout(uint32_t local_mod, void *buffer, int32_t buffer_len,
                   int32_t timeout_ms);
int FreeMsg(uint32_t local_mod);
```

已确认行为：

- `BuildMsg` 输出 `0x80 + payload_len` 字节；无 payload 时返回 `0x80`。
- `SendMsg/RecvMsg` 的最大缓冲区检查值是 `0xc800`（51200）。
- `Register` 连接 `127.0.0.1`；基础端口为 10000，可从 MswitchWin 服务注册表读取 `OpenPort`。
- Vmbooster 以模块号 `0x80000001` 注册。
- Vmbooster 的已观察目的模块至少包括 `0x80000000`、`10` 和 `6`。

## 3. 128 字节消息头

所有整数在当前 x86 构建中按 little-endian 写入。字段名分为“已确认语义”和“暂定名”；未知字节必须原样保留或置零，不能凭猜测复用。

| 偏移 | 大小 | 名称 | 置信度 | 证据 |
|---:|---:|---|---|---|
| `0x00` | 4 | `magic` = `0x5b5b5b5b` | 确定 | 构造、接收校验、MswitchWin 校验 |
| `0x04` | 4 | `version` = `1` | 高概率 | 所有构造路径固定写 1 |
| `0x08` | 4 | 未知 | 未知 | 当前构造路径不显式赋值 |
| `0x0c` | 1 | `msgtype` | 确定 | 注册请求为 0、响应改为 1；Vmbooster 收发逻辑也显式修改 |
| `0x0d` | 3 | 保留 | 确定 | `BuildMsg` 清零 |
| `0x10` | 2 | `client_type_or_port`（暂定） | 待验证 | MswitchWin 注册表保存该值，普通构造不赋值 |
| `0x12` | 16 | 未知/保留 | 未知 | 尚无可靠读写证据 |
| `0x22` | 2 | `dst_type` | 确定 | MswitchWin 日志参数直接从此处读取 |
| `0x24` | 16 | `uuid` | 确定 | `BuildMsg` 复制 16 字节；MswitchWin 注册路由表也保存该值 |
| `0x34` | 4 | `src_mod` | 确定 | 注册请求写本地模块号；注册响应日志标为 `src_mod` |
| `0x38` | 4 | `dst_mod` | 确定 | `BuildMsg` 写入；MswitchWin 用它查找本地目标模块 |
| `0x3c` | 4 | `route_a`（暂定） | 待验证 | 响应构造会复制到对端头的 `0x44` |
| `0x40` | 4 | `route_b`（暂定） | 待验证 | 响应构造会复制到对端头的 `0x48` |
| `0x44` | 4 | `reply_route_a`（暂定） | 待验证 | Vmbooster 高级发送包装器显式赋值 |
| `0x48` | 4 | `reply_route_b`（暂定） | 待验证 | Vmbooster 高级发送包装器显式赋值 |
| `0x4c` | 4 | 保留/状态 | 待验证 | `BuildMsg` 固定清零 |
| `0x50` | 4 | `int_msgid` | 确定 | MswitchWin 日志命名；Vmbooster 用常量分派 |
| `0x54` | 4 | 保留/状态 | 待验证 | `BuildMsg` 固定清零 |
| `0x58` | 4 | 未知 | 未知 | 尚无可靠读写证据 |
| `0x5c` | 4 | `data_len` | 确定 | 接收长度严格按 `0x80 + data_len` 校验 |
| `0x60` | 32 | 保留 | 未知 | 当前构造路径保持零值 |
| `0x80` | 可变 | payload | 确定 | `BuildMsg` 复制 payload |

## 4. 本地注册状态机

```text
Agent                              MswitchWin
  | TCP connect 127.0.0.1:10000       |
  |----------------------------------->|
  | register request (0x84 bytes)      |
  | msgtype=0                          |
  | src_mod=local_mod                  |
  | int_msgid=0x20130223               |
  | data_len=4, payload=u32 local_mod  |
  |----------------------------------->|
  | register response (0x90 bytes)     |
  | msgtype=1                          |
  | int_msgid=0x20130223               |
  | data_len=16, payload=uuid16        |
  |<-----------------------------------|
  | Register() returns socket + uuid   |
```

MswitchWin 接受注册时，以 `src_mod` 为模块键，保存 socket、头部 `0x10` 的 16 位值和 `uuid`。重复模块号会替换旧 socket。libmswitch 则在进程内保存 `module -> {uuid, socket}` 表，后续 `SendMsg/RecvMsg` 先按模块查表。

## 5. 已确认但尚未命名的行为

- MswitchWin 的串口外层帧已确认：原消息中的 `0x3b`（`;`）和 `0x5c`（`\\`）前加一个 `0x5c`，最后追加未转义的 `0x3b` 作为帧终止符。接收端支持跨 read 缓存、去转义和连续多帧。
- 去除串口外层帧后，内部内容就是同一个 0x80 头消息；MswitchWin 校验 `magic` 和 `0x80 + data_len`，再按 `dst_mod` 路由给本地 Agent。
- 本地 Agent 发给 MswitchWin 的普通消息，MswitchWin 按注册模块补充/核对源路由信息后写串口；具体补写字段仍需逐条确认。
- `int_msgid` 是业务分派主键。Vmbooster 已观察常量包括 `0x8102be`、`0x8102c0`、`0x8102c1`、`0x8102c2`、`0x8102c7`。
- `msgtype` 至少区分请求/响应，但是否还包含事件或错误类型，尚无足够证据。

## 6. 下一项协议工作

1. 对三个 Agent 建立 `module ID / int_msgid / direction / payload schema / cadence` 矩阵。
2. 从已有日志对齐 heartbeat、VM info、inventory 的具体 `int_msgid`，不进行主动探测。
3. 继续确认 MswitchWin 上行前如何补写 `src_mod`、路由字段和 UUID。
4. 离线 codec 只实现已确认字段；未知区保持可透传，防止错误归零破坏兼容性。
