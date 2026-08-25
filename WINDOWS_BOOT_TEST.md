# Windows 开机启动测试

启动任务运行 `windows_boot_agent.py`。每次运行都会创建独立目录：

```text
lab/mock-telemetry/out/boot-session/<session-id>/
  messages.jsonl
  mswitch.raw
  runtime-status.json
  start_time.txt
```

`latest.txt` 指向最近一次 session。身份始终读取现有 `local_env.json`；启动工具仅在内存配置中移除 `provider.state_path`，因此 PID、CPU、memory 和 IO 状态不会跨 Windows 重启继承。

## 安装启动任务

以管理员身份启动 PowerShell，并激活要使用的虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
powershell -ExecutionPolicy Bypass -File .\tools\windows_boot_task.ps1 -Action Install
```

安装脚本会记录当前虚拟环境的 `python.exe` 绝对路径。任务使用 LocalSystem 运行，由 Windows 启动触发，并延迟约 30 秒。

也可以显式指定 Python：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_boot_task.ps1 `
  -Action Install `
  -PythonPath C:\path\to\.venv\Scripts\python.exe
```

## 启动测试

不重启 Windows，手动触发已安装任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_boot_task.ps1 -Action Start
```

不安装任务，直接运行一次 310 秒模拟启动：

```powershell
python .\tools\windows_boot_agent.py `
  --config .\lab\mock-telemetry\config.windows-validation.json `
  --output-root .\lab\mock-telemetry\out\boot-session `
  --duration-seconds 310 `
  --simulated
```

查看状态：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_boot_task.ps1 -Action Status
```

## 停止任务

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_boot_task.ps1 -Action Stop
```

卸载开机任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_boot_task.ps1 -Action Uninstall
```

## 导出日志

导出最近一次 session：

```powershell
$latest = Get-Content .\lab\mock-telemetry\out\boot-session\latest.txt
Compress-Archive -LiteralPath $latest -DestinationPath .\boot-session.zip -Force
```

完整导出全部 boot session：

```powershell
Compress-Archive `
  -Path .\lab\mock-telemetry\out\boot-session\* `
  -DestinationPath .\boot-sessions-all.zip `
  -Force
```
