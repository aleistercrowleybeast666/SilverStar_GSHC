# 校准能力兼容验证

本记录针对 2026-09-05 的 FCCG 0.0.10 Calibration Capability 兼容改动，仅验证当前 GSHC。协议仍为 AIR V0 / Profile 0。

## 自动验证结果

| 项目 | 结果 |
|---|---|
| compileall | 通过，覆盖入口、protocol、services、transport、ui、processing、tests |
| 全量 pytest | 296 passed，2 subtests passed |
| 最终 NONE 按钮与英文宽度调整后复验 | 校准兼容、GUI workflow、i18n 共 205 passed |
| parser / command Golden | 全量回归覆盖 AIR 全部现有 parser、Capability ACK、CAL_START、CAL_RESET、ALIGN_START、GSP framing |
| Git 基线字节核对 | 256 个 seq × 5 组命令 = 1280 组 AIR/GSP bytes 与 HEAD 一致 |
| 未变更源码 | protocol/air.py、common.py、gsp_min.py、receive_pipeline.py、transport/serial_backend.py、protocol_worker.py 与 HEAD 一致 |
| headless GUI | 四种 mask × 中英文，共 8 组；菜单、NONE 文案、reset、对话框与 Qt 事件循环通过，已检查截图 |
| Packaging | 原 spec 在清洁 PATH 下构建成功；最终 EXE offscreen 启动存活 8 秒、无异常日志，随后结束测试进程 |
| 实机 SS0.5 | 未连接真实设备，待联调 |

测试环境：Windows、Python 3.14.0、PySide6/Qt 6.10.1、PyInstaller 6.16.0。系统 Python 提供 pytest/PyInstaller，项目 .venv 缺少这两个工具。测试未新增运行期依赖。

结果与截图位于忽略目录 `build/calibration_validation/`：`pytest.log`、`gui-regression.log`、`headless_gui.json`、`wire_compatibility.json`、`calibration_*.png`、`packaging-smoke/`。

## 可重复命令

从工作区根目录执行（PowerShell）：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m compileall -q app.py main.py config.py protocol services transport ui processing tests
python -m pytest -q
python tests/validation/headless_gui_smoke.py
```

新增 `test_calibration_capability.py` 覆盖：

- 0x01/03/05/07、各自附带 bit7 或 bit3..7、0x00 与仅未知位；parser 保留原 mask，握手不因未知位拒绝。
- 对应 0/1/1/2 个可启动采样模式；NONE 从菜单排除，强行注入 NONE 菜单项也不发用户命令。
- 未握手、unsupported、NONE、负数、未知及超出一字节的 mode 本地拒绝；公共入口、通用入口、retry 都校验。
- NONE 的 ready=0/1 独立显示；CAL_START ACK accepted → COLLECTING → READY；BAD_PARAM/BAD_STATE/BUSY/timeout 不自动换模式、不清除 Capability。
- Capability 新 seq 替换 pending ACK，重连刷新隐藏控件，迟到 Capability 仍仅诊断。
- 0x01 下 CAL_RESET 与 ALIGN_START 保持 Golden；下行暂停时 PC 请求、串口写入、GSP ACK、GS TX/RX/CRC、AIR ACK、快照和 RSSI/SNR 分别可查。

Headless smoke 仅在测试进程加载本机字体，隐藏无法创建 OpenGL context 的 offscreen 视口。原有 3D 几何/相机回归通过；真实 GPU 与窗口渲染仍需交互检查。

## 打包环境说明

使用原 `SilverStar_GSHC.spec`，构建输出位于独立 smoke 目录。首次在工具环境的默认 PATH 构建成功，但启动报 QtCore DLL 导入错误。依赖表显示打包混入 Codex runtime 的 ICU/UCRT，其中 ICU 导出符号与 Qt 所需不匹配。后续在仅含 Python 与 Windows 的进程 PATH 下重新构建；不修改系统 PATH，也不修改项目 spec。

```powershell
$pythonExe = (Get-Command python).Source
$pythonDir = Split-Path -Parent $pythonExe
$env:PATH = "$pythonDir;$pythonDir\Scripts;C:\Windows\System32;C:\Windows"
python -m PyInstaller --noconfirm --distpath build/calibration_validation/packaging-smoke/dist-sanitized --workpath build/calibration_validation/packaging-smoke/work-sanitized SilverStar_GSHC.spec
```

首次 smoke 失败包位于 `dist/`；不要将其作为可用发布包。最终通过的测试包位于 `dist-sanitized/SilverStar_GSHC/`，启动结果记录于 `packaging-smoke/startup-sanitized.json`。启动期间未连接串口；实际设备与 GPU 尚需联调。日志中仍有未使用 OpenGL 附带 DLL 的 MSVCR90 依赖警告，不影响本次 headless 启动；真实图形驱动应按下方清单验证。

## 真实 SS0.5 待联调

1. 分别使用实际 mask 0x01/03/05/07 的 build，PC 明确断开/重连后确认最新 Capability seq/profile ACK 成功和模式列表替换。未知高位可用注入数据验证。
2. 0x01 上报 NONE + ready=1 时应正常显示单位校正并允许按现有条件开始初对准；ready=0 时保持真实阻塞且无 CAL_START(NONE)。实测原 CAL_RESET 的 ACK 与后续 mode/state/ready。
3. 单面、六面及六面重采：ACK OK 后等待状态完成，不把接受当成 READY；实测 BAD_PARAM/BAD_STATE/BUSY 和 ACK 丢失后的有界重试/状态恢复。
4. ALIGN_START 使用原 wire bytes；实测 COLLECTING/CHECKING/READY、Sensor Snapshot 和 STALE 后的显式重新对准。
5. 在校准/初对准期间暂停 AIR 下行，核对 PC request、serial write、GSP ACK、GS TX/RX/CRC、AIR ACK、PREFLIGHT_STATUS、RSSI/SNR 和距今时间。PC 计数只能定位层级，区分空口故障与飞控任务停滞还需结合 GS/FC 本地控制台和运行状态。
6. 源码版和最终打包版分别检查真实串口、Qt/GPU 3D、双语/主题、日志记录与预飞 START 条件。本轮没有修改飞控运行逻辑，也没有完成硬件验收。
