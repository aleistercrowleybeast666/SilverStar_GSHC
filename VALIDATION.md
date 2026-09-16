# GSHC 验收快照

## 2026-09-16 — CF-33 port/connection clipping closeout

Baseline: clean source worktree at `3159680`. The new attachment explicitly authorized this
independent repository. No reset, checkout, commit or push. GSHC version 0.0.3, AIR/GSP wire,
Controller, serial worker, state machine, telemetry and previous touch-scroll implementation are unchanged.

### Root cause and measured evidence

The old combo used only AdjustToContents/minimumSizeHint, with no guaranteed edit-field width.
The connected status additionally inherited `QSizePolicy.Ignored`, minimum width 110 and
wordWrap=False, and shared the crowded first row with connection controls.

The committed HEAD was loaded into a separate Qt process (no checkout or source replacement).
At 1280×800, QT_SCALE_FACTOR=1.5 and Segoe UI 16 pt, the old connected label had 110 logical
pixels available, while `COM10@230400` required 155 and `COM100@230400` required 166.
This reproduces **connection-status clipping**. In that same run the combo edit field was 98
pixels and popup viewport 126 pixels, both sufficient for COM100 (80 pixels). Combo/popup
clipping on the actual CF-33 has therefore **not** been independently reproduced here.
The fix protects all three surfaces. This is not a physical-device acceptance claim.

### Changes and new checks

- `ui/port_combo.py`: use QStyleOptionComboBox, font advance/ink bounds, CT_ComboBox and
  SC_ComboBoxEditField to set minimumWidth explicitly. Recompute on model, font, style,
  polish, show and DPI changes; popup reserves text, frame and scrollbar space.
- `ui/main_window.py`: move connection label/status to the second row; use word wrapping and
  MinimumExpanding sizing. Preserve full status/tooltip. Remove the baud spinbox's fixed
  88-pixel width after screenshot review exposed a clipped baud digit with large fonts.
- `tests/test_port_gui.py`: existing end-to-end five-port checks plus six subprocess geometry cases.
  Enumeration → set_ports → current_port → Controller.connect → SerialConfig → serial.Serial
  retains COM1/COM9/COM10/COM99/COM100 byte-for-byte; only I/O/threads are mocked.
- `tests/validation/port_geometry_probe.py`: active style edit rect, popup, baud edit field,
  status text rectangle/height and screenshots, with an optional committed-HEAD comparison.
- `docs/GUI_STYLE_GUIDE.md`, this report: persistent header requirements and evidence.

All six combinations of 1280×800 / 1920×1080 logical window size and 1.0 / 1.5 / 2.0 scale,
with Microsoft YaHei / Segoe UI 16 pt, passed actual text-vs-geometry checks. The 1.5× repaired
status has 179 pixels available, exceeding the 166-pixel COM100@230400 text. COM selectors,
popup and connected statuses are complete in all combinations. Viewports are measured;
this does not just compare sizeHint. Qt offscreen rendering is used; physical CF-33 driver,
Windows font substitution, monitor transitions, serial hardware and touch remain field checks.

### Commands, results and artifacts

Process-local QT_QPA_PLATFORM=offscreen and PYTHONDONTWRITEBYTECODE=1; TEMP/TMP,
MPLCONFIGDIR, PyInstaller cache and test outputs are below `tests/.pytest_cache/`.

```powershell
python -m pytest tests/test_port_gui.py -q --basetemp=tests/.pytest_cache/port_revision_matrix_final0916 -o cache_dir=tests/.pytest_cache/portcache0916
python -m pytest -q --basetemp=tests/.pytest_cache/full0916-final -o cache_dir=tests/.pytest_cache/full0916-cache
python tests/validation/port_geometry_probe.py tests/.pytest_cache/port_baseline_0916 1280 800 --baseline
```

Focused: **16 passed**. Full final result: **382 passed, 3 subtests passed in 323.28 s**.
The earlier full run before the final baud sizing adjustment passed 382 tests and 3 subtests.
`tests/validation/packaging_smoke.ps1` is executed in memory with only projectRoot fixed to
this repository and packageRoot redirected to `tests/.pytest_cache/package0916`; it changes
PATH only for its child process environment. Final package result: **passed; packaged process remained alive for 8 s, stderr 0 bytes; 2026-09-16T08:44:48.4172889+08:00**.

- Full log: `tests/.pytest_cache/full0916-final.log`.
- Packaged executable: `tests/.pytest_cache/package0916/dist/SilverStar_GSHC/SilverStar_GSHC.exe`.
- Packaged startup evidence: `tests/.pytest_cache/package0916/startup.json`.
- Six sets of geometry.json, window.png and popup.png:
  `tests/.pytest_cache/port_revision_matrix_final0916/test_port_and_connected_status0` through `5`.
- Old layout geometry/screenshot: `tests/.pytest_cache/port_baseline_0916/`.

No protocol/schema/version changes. No source edits beyond the listed GUI/test/doc files.
`git diff --check` passes. Generated artifacts remain ignored and are not product source.

<!-- closeout-git-begin -->
### Final Git snapshot

Tracked diff (new files are listed separately by status):

```text
 VALIDATION.md           | 94 +++++++++++++++++++++++++++++++++++++++++++++++++
 docs/GUI_STYLE_GUIDE.md | 14 ++++++++
 tests/test_port_gui.py  | 16 +++++++++
 ui/main_window.py       |  8 +++--
 ui/port_combo.py        | 64 +++++++++++++++++++++++++++++----
 5 files changed, 186 insertions(+), 10 deletions(-)
```

```text
 M VALIDATION.md
 M docs/GUI_STYLE_GUIDE.md
 M tests/test_port_gui.py
 M ui/main_window.py
 M ui/port_combo.py
?? tests/validation/port_geometry_probe.py
```
<!-- closeout-git-end -->


## 2026-09-14 — GUI field-usability closeout

Scope: GUI/helper, corresponding tests and documentation only. Initial working tree was clean.
No protocol, transport, Controller/state-machine, telemetry, export numerical logic, version,
commit or push changed. The three user-authorized repositories remain independent.

<!-- touch-git-snapshot -->
### Modified files and Git snapshot

- `VALIDATION.md`
- `docs/GUI_STYLE_GUIDE.md`
- `tests/test_port_gui.py`
- `tests/test_touch_scroll.py`
- `ui/main_window.py`
- `ui/port_combo.py`
- `ui/theme.py`
- `ui/touch_scroll.py`

`git diff --stat` (tracked files only; new files are listed above):

```text
 VALIDATION.md           | 86 +++++++++++++++++++++++++++++++++++++++++++++++++
 docs/GUI_STYLE_GUIDE.md | 16 +++++++++
 ui/main_window.py       | 27 ++++++++++++----
 ui/theme.py             |  4 ++-
 4 files changed, 126 insertions(+), 7 deletions(-)
```

`git status --short`:

```text
 M VALIDATION.md
 M docs/GUI_STYLE_GUIDE.md
 M ui/main_window.py
 M ui/theme.py
?? tests/test_port_gui.py
?? tests/test_touch_scroll.py
?? ui/port_combo.py
?? ui/touch_scroll.py
```
<!-- /touch-git-snapshot -->

### Cause and repair

The complete serial string chain was audited and tested: list_serial_port_names -> set_ports ->
port_combo -> current_port -> actual Controller.connect -> SerialConfig -> SerialWorker._open_serial
-> mocked serial.Serial. COM1, COM9, COM10, COM99 and COM100 remain byte-for-byte complete. No actual
string truncation was found. The old combo relied on its default first-show sizing and compressible
layout policy. PortComboBox now uses AdjustToContents, a font/style-derived minimumSizeHint, Minimum
horizontal size policy and a contents-sized popup, with the full name also in a tooltip.
The original COM10-to-COM1 visual symptom was not reproduced on this machine; this is a GUI sizing
repair, not evidence that the transport string was ever truncated.

Native TouchGesture coverage: preflight and post-processing forms; calibration and export option
forms; event history list; link/sensor detail text; calibration, directory policy, export language,
header language/theme and serial popup lists. Flight plots and AttitudeGLViewWidget remain outside
page scroll areas; no plot, GL, slider, button, combo body, spinbox or header is registered.
QSS effective header heights with Windows fonts at 1280x800: refresh 36 px, port 34 px, baud 37 px.
Existing larger action controls remain available. No fake mouse drags or inertia tuning added.

### Executed checks

- `python -B -m pytest tests/test_ui_workflow.py -q --basetemp=tests/.pytest-work-touch-focused -o cache_dir=tests/.pytest-cache-touch`: **26 passed**.
- `python -B -m pytest tests/test_port_gui.py tests/test_touch_scroll.py -q --basetemp=tests/.pytest-work-touch-new2 -o cache_dir=tests/.pytest-cache-touch`: **28 passed**.
- Full `pytest.main(['-q', '--basetemp=tests/.pytest_cache/touch-validation/full', '-o', 'cache_dir=tests/.pytest_cache/touch-validation/cache'])`: **376 passed, 3 subtests passed**, 242.47 s; includes AIR/GSP Golden, protocol, state-machine, docs and UI tests.
- Final GUI after conservative height adjustment: `python -B -m pytest tests/test_ui_workflow.py tests/test_port_gui.py tests/test_touch_scroll.py -q --basetemp=tests/.pytest_cache/touch-validation/final-gui -o cache_dir=tests/.pytest_cache/touch-validation/cache`: **54 passed**, 134.22 s.
- Windows fonts and `QT_SCALE_FACTOR=2`: `python -B -m pytest tests/test_port_gui.py -q --basetemp=tests/.pytest_cache/touch-validation/dpi2 -o cache_dir=tests/.pytest_cache/touch-validation/cache`: **10 passed**. Includes ports arriving after first show, larger fonts, popup/edit-field geometry and refresh selection retention.
- `python -m compileall -q main.py app.py config.py protocol services transport ui processing tests/test_port_gui.py tests/test_touch_scroll.py`: **passed**, with PYTHONPYCACHEPREFIX below the test output root.
- `python -B -m pytest tests/test_docs.py -q --basetemp=tests/.pytest_cache/touch-validation/docs-final -o cache_dir=tests/.pytest_cache/touch-validation/cache`: **30 passed**.
- Existing `tests/validation/headless_gui_smoke.py`, executed in memory with only its output root redirected to this task's tests directory: **8 mask/language cases, dialog and event loop passed**.
- Existing `tests/validation/packaging_smoke.ps1`, executed in memory with project/output roots fixed to this repository/tests directory: **final PyInstaller build and 8-second EXE startup passed**, stderr 0 bytes; only the spawned smoke process was stopped.
- `git diff --check`, static scrolling inventory and unchanged protocol/transport source audit: **passed**.

The full-run wrapper used `QT_QPA_PLATFORM=offscreen`, process-local TEMP/TMP/MPLCONFIGDIR and
QSettings IniFormat paths below `tests/.pytest_cache/touch-validation`. Build/cache/runtime-data
outputs also stay there. Evidence: `full.log`, `final-gui.log`, `headless.log`, `headless/`,
`header-final.png`, `packaging-smoke/build.log` and `packaging-smoke/startup.json` under that root.

### Remaining validation limits

No connected serial hardware or CF-33 physical touchscreen/stylus was available. Synthetic Qt
finger swipes actually moved page/list/table/text scrollbars; mouse selection and graphics
exclusion tests passed. Physical driver behavior and the original field-only visual symptom
still require a field check. No failing executed regression remains.


日期：2026-09-05。本文件是精确测试数量、hash 和构建验收数据的唯一快照权威；能力状态见 [CURRENT_PROGRESS](docs/CURRENT_PROGRESS.md)，复验命令和实机步骤见[验证步骤](tests/validation/README.md)。

## 范围与基线

- 当前仓库：SilverStar_GSHC，Python / PySide6 地面站上位机。
- 按新 docs 基线对齐 Platform 0.0.10 / AIR M0 最终 Calibration 契约，没有从 Git 历史恢复旧 docs。
- 修改前代码基线：`aaf0263fcdbf6ae9fa3474969eaf7d0f84d37657`。当时新复制的 docs 已有未提交变更，本轮保留该基线并校对。
- 本轮只修改 GSHC；本机 FCCG 共同契约仅做只读比对。未改 FCCG、FLP 或外部 reference firmware，未改 AIR/GSP wire、SSLOG layout、`.ssdecoder` schema 或应用 metadata。

## 实际自动验证

| 检查 | 实际结果 |
|---|---|
| compileall | 通过；入口、protocol、services、transport、ui、processing、tests |
| 核心校准与命令回归 | 211 passed，19 deselected，3 subtests passed；2.42 s |
| 全量 pytest | **348 passed，3 subtests passed**；277.64 s |
| 独立 docs test | **30 passed**；覆盖 docs 与根文档的相对目标、镜像、Calibration 语义和 AIR 常量 |
| headless GUI | 4 masks × 2 languages = **8 cases**；对话框、Qt 事件循环与截图通过 |
| GUI 可视检查 | 中文/英文默认校正完整显示；0x01 主面板开始按钮禁用、Reset 保留、单位校正正常显示 |
| AIR/GSP 基线比对 | **1536 组**：256 seq × Capability ACK / NONE / OneFace / SixFace / Reset / Align Start；与修改前 HEAD 逐字节一致 |
| 协议/传输源码 | protocol/air.py、common.py、gsp_min.py、receive_pipeline.py、transport/serial_backend.py、protocol_worker.py 与 HEAD 一致（按文本统一换行后比较） |
| PyInstaller | 原 spec、独立输出与清洁进程 PATH 构建通过 |
| 打包启动 | EXE offscreen 存活 **8 s**，stderr **0 bytes**，无异常标题；仅停止自行启动的测试进程 |
| 中英文翻译 | translation key 集合一致 |
| Git diff --check | 通过 |
| 真实设备 | 未连接 SS0.5；没有实机验证结论 |

环境：Windows；Python 3.14.0；PySide6/Qt 6.10.1；PyInstaller 6.16.0。使用系统 Python，未新增运行期依赖。

新增 NONE 回归包含公共/通用/重试门禁、0x01 自动 NONE 的明确本地结果、显式 NONE 的真实 Golden bytes、ACK OK 不伪造 ready、真实 NONE/READY/ready=1 快照恢复、错误不换模式/不清握手、Reset 的 build 分支，以及 GUI 高亮/重连/语言切换不自动发送。原有采样、Alignment、START、STALE、日志及 3D 场景回归同时通过。

## 共同契约一致性

本机 `D:/python_software/SilverStar_FCCG/docs/AIR_CALIBRATION_CONTRACT.md` 与本仓库 [AIR_CALIBRATION_CONTRACT](docs/AIR_CALIBRATION_CONTRACT.md) **原始文件 bytes 完全相同**。本轮未编辑共同契约。

SHA-256：`ffb8013cb9e1f254872255f8e4dc86abd374af5199ece39963fc90a0301f1f40`。

此结果只证明文件一致，不代表 FCCG 固件代码或硬件行为已在本任务中验收。

## 证据与打包路径

产物均在 Git 忽略目录 `build/final_alignment_validation/`：

- `pytest.log`、`docs.log`、`headless.log`、`headless_gui.json`、`calibration_*.png`；
- `wire_compatibility.json`（基线、协议源码 hash、AIR/GSP 向量数、共同契约比对）；
- `packaging-smoke/build.log`、`startup.json`、`stdout.log`、`stderr.log`；
- 通过 smoke 的完整包：`packaging-smoke/dist/SilverStar_GSHC/`，入口 `SilverStar_GSHC.exe`。

打包脚本只为当前进程设置 Python/Windows PATH，防止外部工具的 Qt/ICU DLL 混入，随后恢复环境。首次脚本执行遇到 Windows PowerShell 把 PyInstaller INFO stderr 当异常的问题，已改为以原生退出码判定并成功重跑；不是应用启动故障。

Headless 渲染在测试进程加载字体并隐藏不支持的 OpenGL 视口，不覆盖真实 GPU/窗口或串口交互。测试与构建目录保持忽略，未添加到 Git。

## 待实机验证

按[SS0.5 清单](tests/validation/README.md)验证四种 build 的最新握手与菜单、自动/显式 NONE、active IMU 锁定、旧 Alignment 失效、采样/重采与 Reset、错误/超时、Alignment READY/STALE、START、空口停滞诊断，以及源码/打包版的串口和 GPU。

本轮未创建提交、Release、Tag，未推送远端。工作区保留代码、文档与测试变更，供审阅。
