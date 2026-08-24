# 8047 静态定位与只读观测清单

## 已确认静态证据

- 文件：`C:\Program Files (x86)\ZTEGuestOS\IceServer\IceDisplay.exe`
- SHA-256：`610afe0851789342bf35d4d5a12d893be8a30a54a4efaa5969f7f3aaf18b5b31`
- 架构：PE32+ x86-64，ImageBase `0x140000000`
- 原始格式串 RVA `0xFC760`：

```text
msgtype=8047;msgdata={msgtype:'8047',msgid:'%u',vmuuid:'%s'};
```

- 格式串只有一个代码交叉引用：RVA `0x80B01`。
- 所在函数入口约为 RVA `0x80A50`，相邻符号字符串为
  `ice_send_client_quit_msg`，源码路径字符串为
  `main_channel\main_channel.c:1492`。
- 该函数把格式化结果交给 RVA `0x80930`；调用点 RVA `0x80B7E`。
- 函数本身只有一个直接调用点：RVA `0x9D6C9`。

因此，8047 目前应从“完全 UNKNOWN”调整为“发送方向和核心字段高概率已知”：
它由 IceDisplay 的 client-quit 路径生成，经内部消息通道发送，正文至少包含动态
`msgid` 和当前 `vmuuid`。是否还会被 Vmbooster 二次封装、Host 是否回应以及异常
重试规则仍未确认。

## Ghidra/IDA 静态分析顺序

1. 以 SHA-256 校验目标文件，加载为 x86-64 PE，保持默认 ImageBase。
2. 定位 RVA `0xFC760` 格式串并查看唯一 XREF `0x80B01`。
3. 将 RVA `0x80A50` 标记为 `ice_send_client_quit_msg_candidate`。
4. 将 RVA `0x80930` 标记为 `ice_send_inside_msg_candidate`。
5. 从 `0x80B7E` 反推传入结构，重点确认 type/channel、buffer 指针及长度字段。
6. 转到唯一上游调用 `0x9D6C9`，恢复触发条件及调用前后的会话清理顺序。
7. 继续追踪 `0x80930` 的下游，不以函数名猜测最终线路；确认它进入的是本地
   IPC、Mswitch API，还是另一个队列线程。
8. 搜索同格式的 `8067`、退出/断线字符串，比较公用封装函数和序列号生成器。

## 只读运行观测点

只设置日志断点，不改寄存器、参数、返回值或控制流：

- `module_base + 0x9D6C9`：确认什么事件触发 client-quit 上报。
- `module_base + 0x80A50`：记录进入次数和调用栈。
- `module_base + 0x80B7E`：读取即将发送的本地消息结构和格式化文本。
- `module_base + 0x80930`：确认内部发送函数的参数 ABI、返回值及失败路径。

需要记录线程 ID、调用栈、动态 `msgid`、`vmuuid`、返回值、相邻日志时间戳，
以及同一时刻 PCAP 中是否出现 int_msgid 8047。不要在生产后台连接期间修改函数
行为；证据不足时继续保持 `unsupported_fixture`，不生成推测性 ACK。

## 当前推定伪代码

```c
int ice_send_client_quit_msg_candidate(void) {
    inside_message msg = {0};
    msg.channel_or_type = 1;
    format_with_context(
        msg.text,
        0x6e,
        "ice_send_client_quit_msg",
        1493,
        "msgtype=8047;msgdata={msgtype:'8047',msgid:'%u',vmuuid:'%s'};",
        next_or_current_msgid,
        current_vmuuid
    );
    return ice_send_inside_msg_candidate(1, &msg);
}
```

`channel_or_type=1`、结构偏移和 `next_or_current_msgid` 的具体来源仍需反编译器类型
恢复或只读断点确认，不能视为最终协议定义。
