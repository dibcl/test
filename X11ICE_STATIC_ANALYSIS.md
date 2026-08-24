# `x11ice` 静态分析结论

更新时间：2026-08-24  
样本：`C:\Program Files (x86)\ZTEGuestOS\IceServer\IceTunnel.exe`  
SHA-256：`B565310F75C867218B11C538ECA6D78A77750878AEBAD9B9D45A6E8D16A7EE0E`

## 已证明事实

- `x11ice` 只在本机的 `IceTunnel.exe` 中命中；ZTEGuestOS 安装树、`C:\Windows\Temp\update` 和定向包名扫描均未发现名为 `x11ice` 的可执行文件、`.deb` 或 `.rpm`。
- `IceTunnel.exe` 包含并实际引用这些字符串：`/proc/%s/cmdline`、`tn_check_x11ice_pid`、`x11ice`、`pSocket->x11ice_pid:%s`。
- 函数 `0x1400234c0` 使用 socket 对象偏移 `+0xb2b` 的字符串构造 `/proc/<value>/cmdline`，以 `rb` 模式打开并读取该文件，然后检查内容是否包含 `x11ice`。
- socket 读处理函数 `0x140023650` 在 `+0xb2b` 非空时调用上述检查；检查失败会把 socket 状态字段 `+0x318` 设为 `-1`，随后继续进入统一读结果/状态处理。
- 这段代码位于当前签名有效的 Windows PE 中，不是仅存在于字符串表而没有代码引用的死字符串。
- 本轮只做静态读取；没有执行未知二进制、连接私有服务、修改服务或向 Host 发送数据。

## 当前最优解释

- [高概率] ICE Tunnel 源码树同时服务 Windows 和 Linux，Windows 构建保留了 Linux `/proc` 进程存活检查路径。
- [高概率] 某类 Tunnel socket 会绑定一个 `x11ice` 进程 PID；当对应 X11 会话进程退出时，Tunnel 将该 socket 标记异常并进入清理/断线流程。
- [中概率] `x11ice` 是 Linux/X11 会话端或采集端，而不是完整 Tunnel 的别名。当前函数只证明 Tunnel 会监控它，尚未证明它负责捕获、编码、输入还是会话生命周期。

## 尚未证明

- 未证明中国移动公众版当前资源池提供或允许 Linux `x11ice` 包。
- 未证明 `x11ice` 与当前 Windows `IceVGPUCapture + IceInput + IceSound + Vdagent` 的能力一一对应。
- 未证明标准 SPICE `spice-vdagent` 可直接替代 ZTE 的 Vdagent/ICE 扩展。
- 未证明 Windows `IceTunnel.exe` 能在 Debian 上复用，也未发现官方 Linux 构建的文件名、版本或下载入口。

## 对路线选择的影响

`x11ice` 将“官方存在 Linux/X11 会话实现”的概率提高，但不足以进入安装或 DD 阶段。当前首选动作仍是向 ZTE/中国移动取得同资源池、同协议版本的 Linux ICE/RAP/X11 Guest 包或支持矩阵。若官方确认不存在，再评估公开、可审计的 Linux session adapter；不应把单个字符串线索扩大成完整兼容性结论。

## 工具修正

`tools/pe_string_xrefs.py` 已补充 x64 RIP-relative 寻址、Capstone `skipdata` 和内存位移检索。修正前的 `xrefs=0` 是分析器在 `.text` 中遇到不可解码字节后提前停止造成的工具假阴性。
