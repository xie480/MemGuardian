# MemGuardian

Windows 内存阈值守护工具。后台脚本监控系统内存占用，并通过 Windows 任务计划程序请求启动 `memory_boost.exe`。

---

## 清理效果说明

守护脚本负责检测阈值并启动清理任务。实际释放量、执行耗时和对前台程序的影响由 `memory_boost.exe` 的实现及当前系统状态决定；仓库不包含该程序的源码，因此不承诺固定的内存下降幅度或无感运行。

---

# 使用教程

## 1. 配置两个开机任务

本工具需要两个独立的 Windows 计划任务：

| 任务名 | 作用 | 启动时行为 |
| --- | --- | --- |
| `MemoryBoost` | 运行 `memory_boost.exe` | 开机时运行一次；之后也由守护任务按阈值触发 |
| `MemGuardian` | 后台监控内存阈值 | 开机时启动并持续监控 |

打开任务计划程序：按 `Win + R`，输入 `taskschd.msc`，然后选择“创建任务”。两个任务都需要分别配置，不能把守护脚本和清理程序填在同一个任务中。

### 1.1 清理任务：`MemoryBoost`

**常规**

- 名称：`MemoryBoost`
- 选择“无论用户是否登录都要运行”。保存时按提示输入该账户密码。
- 勾选“使用最高权限运行”。
- 配置为 Windows 10 / Windows 11。

**触发器**

- 新建触发器，选择“启动时”。

> 这会让清理程序每次开机时运行一次，即使当时内存没有达到阈值。之后 `MemGuardian` 仍会在达到阈值时再次调用该任务。

**操作**

- 程序：填写 `memory_boost.exe` 的完整路径，例如 `E:\YilenaCode\MemGuardian\memory_boost.exe`；请按实际存放位置修改。
- 起始于：填写该 EXE 所在目录，例如 `E:\YilenaCode\MemGuardian`。

**条件**

- 取消“只有在计算机使用交流电源时才启动此任务”。
- 取消“如果计算机切换为电池电源，则停止”。

**设置**

- 取消“如果任务运行时间超过以下时间，则停止任务”。
- “如果任务已在运行，则以下规则适用”选择“不启动新实例”。

### 1.2 守护任务：`MemGuardian`

**常规**

- 名称：`MemGuardian`
- 选择“无论用户是否登录都要运行”。保存时按提示输入该账户密码。
- 勾选“使用最高权限运行”。
- 配置为 Windows 10 / Windows 11。

**触发器**

- 新建触发器，选择“启动时”。这样会在开机时启动监控，不需要等到用户登录。

**操作**

- 程序：填写 Python 安装目录中的 `pythonw.exe` 完整路径，例如 `F:\py\pythonw.exe`。
- 添加参数：

```text
"E:\YilenaCode\MemGuardian\memory_guardian.py" --task "\MemoryBoost" --threshold 90 --release 75 --interval 0.25 --cooldown 120
```

- 起始于：填写 `memory_guardian.py` 所在目录，例如 `E:\YilenaCode\MemGuardian`。
- 上述路径是本机示例；请按 Python 和仓库实际位置修改。命令行参数会覆盖脚本中的默认阈值。

本机示例参数为触发 90%、恢复 75%；脚本默认恢复值为 80%。如需使用默认值，将 `--release 75` 改为 `--release 80`。

**条件**

- 取消“只有在计算机使用交流电源时才启动此任务”。
- 取消“如果计算机切换为电池电源，则停止”。

**设置**

- 取消“如果任务运行时间超过以下时间，则停止任务”，否则守护进程到时会被计划程序结束。
- “如果任务已在运行，则以下规则适用”选择“不启动新实例”。

`MemGuardian` 启动后会立即采样一次，之后默认每 0.25 秒检查。达到阈值时请求 `MemoryBoost`；持续高于阈值时默认每 120 秒重试。内存降至恢复阈值后重新布防，任务请求失败时按 5、10、20、40、最高 60 秒退避重试。

## 2. 验证任务

保存后可以分别查询两个任务：

```powershell
schtasks /Query /TN "\MemoryBoost" /V /FO LIST
schtasks /Query /TN "\MemGuardian" /V /FO LIST
```

如需单独测试清理任务，可手动运行它。此命令会立即启动清理器，不受内存阈值控制：

```powershell
schtasks /Run /TN "\MemoryBoost"
```

手动启动守护任务进行验证：

```powershell
schtasks /Run /TN "\MemGuardian"
```

查看状态和日志：

```powershell
Start-Sleep -Seconds 2
Get-Content .\memory_guardian.log -Tail 20
```

日志出现 `Started memory guardian` 表示守护进程启动；出现 `Threshold reached` 和 `Task start request accepted` 表示已检测到阈值并请求运行清理任务。任务计划程序接受请求不代表清理动作已经成功完成，仍需检查 `MemoryBoost` 的“上次运行结果”和“历史记录”。

---

## 工作流程

```text
系统启动
   ├─ MemoryBoost：开机运行一次清理
   └─ MemGuardian：启动后台监控
        ↓
MEM 达到或超过阈值
        ↓
MemGuardian 请求运行 MemoryBoost
        ↓
冷却期后复查；若仍高则再次请求
        ↓
MEM 降至恢复阈值后重新布防
```

---

## 日志

后台日志默认保存为：

```text
memory_guardian.log
```

可用于查看：

- 触发记录
- 错误信息
- 运行状态

---

## License

MIT License
