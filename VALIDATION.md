# GSHC 验收快照

## 2026-09-22 — 联合长时导出

起始与最终 HEAD `62656da1fa035bff344165c8857a932e644ddbae`，main，初始工作区干净。
没有 commit/push/tag/Release。当前完整文件清单和三仓库状态见 FCCG
`docs/JOINT_FIELD_REWORK.md`；历史段落仅适用于其日期。

### 改动

- `processing/time_ranges.py`：分页与恒定时间映射；PNG 默认 30 s，5/10/30/60/120/Custom/Full。
- `flight_plotter.py` / `flight_log_processor.py`：裁剪绘图输入、绝对时间命名、分类目录，
  保留完整解析数据与 TXT；GIF 源默认前 30 s，可 Full/自定义，30 fps，最多 30 s motion
  加 30 physical hold frames / 1 s。源终点单独渲染，没有事件慢放。
- GIF 编码逐幅处理，每帧进度/取消，不一次加载全部大图；厘秒量化采用 30/40 ms，
  每 30 帧严格 1 s。JSON 与 manifest 保存范围、速度、帧数；取消只删除本次独立新输出。
- `services/preferences.py` / `services/i18n.py` / `ui/main_window.py`：导出时长与双语说明；
  继续使用既有可触摸滚动对话框、主题和三页。`app.py` 仅把 worker GIF fps 改为 30。
- **AIR/GSP、telemetry、命令/ACK、serial、Controller 状态机未改**；没有新 frame/decoder 依赖。
  既有实时窗口 10 s 与 bounded point limits 不变，输出默认值不是任务最大时长。

### 实际检查

所有临时文件在 `D:/python_software/SilverStar_FCCG/tests/artifacts/joint`，下表使用该根的相对名。

| 检查 | 结果 | 证据 |
|---|---|---|
| 完整 pytest | **395 passed, 3 subtests passed in 375.65s**，无 skip | `gshc-full3.log` |
| 分页、物理 GIF、取消、双语 GUI 专项 | 16 passed，22.05 s | `gshc-focused2.log` |
| 原 packet/Golden/解析导出专项 | 12 passed，2.85 s | `gshc-packet3.log` |
| 200% DPI、中英/Light/Dark/1000×700、Custom 控件 | 4 passed，8 deselected，7.15 s | `gshc-dpi2.log` + screenshots |
| compileall | 39 个产品/测试 Python 文件通过 | `gshc-compile.log` |
| 原 headless smoke | 8 mask/language cases、dialog/event loop 通过 | `gshc-headless.log` + `gshc-headless/headless_gui.json` |
| 原 spec 打包/启动 | PyInstaller 构建通过，EXE 8 s 后存活，stderr 0 bytes | `gshc-package2/startup.json` |

完整 suite 包括既有 protocol/Golden、telemetry、command、ACK、serial、GUI、touch、
高 DPI、docs 链接。测试使用 FLP venv 的 pytest-qt，追加系统 site-packages 读取已有 pyserial；
不安装/修改全局依赖。先前失败为旧 `velocity_EN.png` 根目录断言，已改为实际分页类别路径。
新增 10/30/31/60/300 s 检查分别验证 300/900/900/900/900 motion + 30 hold physical frames，
速度 1/1/31÷30/2/10；逐帧解码证实末尾一致。61 s 真 PNG 得到 21 图，7 类输出项各 3 页，
分属 5 类目录；TXT 仍含末尾 61 s。取消编码保留预先存在的输出目录。

首次沙箱打包因子进程 PATH 被执行环境重写，误收 Poppler ICU / FreeCAD runtime，
导致 QtCore DLL load failed。进程 PATH probe 已复现；获批非沙箱进程保留 Python/Windows
临时 PATH 后，使用**未修改的原 spec** 完整重建通过。没有修改系统/user PATH 或生产 spec。
独立 smoke 只结束它自己启动的 EXE；这不是用户应用发布，也不是硬件验证。

EXE SHA256：`d7a4b71d0e3570eb1b8a5de06247d9423e617fdaa8d53df1f0470bc13a71c1c8`；完整包必须整目录使用。自动化没有实际串口/无线/GPU/CF-33 触屏验证。
原始含历史缓存目录的 compileall 会提示不可遍历旧产物，最终按 Git 产品/测试文件清单逐一
compileall，未删除或改写旧测试目录。新的输出行为见 [TIME_EXPORT](docs/TIME_EXPORT.md)。

## 2026-09-17 — FCCG/FLP 第二轮修改的地面站兼容验证

初始 HEAD：`92184a80acd48b5f56de6bb06857919d64de6aeb`；
初始 `git status --short` 为空。
本轮 **零产品源代码 diff**，只更新本验收报告；不为制造 diff 新增功能。
无 reset/checkout/clean、Release、Tag 或 push；报告独立中文本地提交。

### 兼容边界

AIR/GSP wire、能力握手、状态解析、Controller/serial worker、telemetry、触屏处理均未修改。
FCCG 新增参数只进入已有生成常量和 decoder actual values，不新增遥测字段；
GSHC 不依赖 FCCG/FLP 代码或 .ssdecoder。版本仍为 0.0.3。
COM10/COM100 宽度与高 DPI 连接状态修复保留，既有 touch support 未回退。
通过的是 Host/Qt 自动化验证，不是实际串口、CF-33、无线链路或飞行硬件验收。

### 命令和结果

- `python -m pytest tests -q --basetemp=tests/.pytest-full-0917 --ignore-glob='tests/.pytest-*' -o cache_dir=tests/.pytest-cache/0917`：
  **382 passed, 3 subtests passed, 242.32 s**。
  日志 `tests/.pytest-cache/0917-full.log`。包含 protocol/Golden、握手与状态模型、
  GUI、COM、高 DPI、touch 和 docs 链接。无跳过项。
- `python -m compileall -q app.py main.py config.py protocol services transport ui processing`：
  通过；进程 `PYTHONPYCACHEPREFIX=tests/.pytest-compile-0917`。
- 运行原 `tests/validation/headless_gui_smoke.py` 内容，仅在测试进程内将输出根替换为
  `tests/.pytest-gui-0917`，并把 TEMP/TMP 放在 `tests/.pytest-temp-0917`：
  **8 mask/language cases、dialog、event loop 通过**。没有修改原 smoke 源文件。
- 运行原 `tests/validation/packaging_smoke.ps1` 内容，固定当前 repository root，
  仅把输出根替换为 `tests/.pytest-packaging-0917`：
  **PyInstaller 构建与 EXE offscreen 启动通过**。
  八秒后进程存活，没有异常标题、Traceback/ImportError/DLL load failed；
  仅结束该 smoke 自己启动的子进程。
  报告 `tests/.pytest-packaging-0917/startup.json`，日志同目录 `build.log`。
  包必须整目录使用；本次不是新 Release。
- 上述运行仅调整测试进程环境，不修改系统 PATH、注册表或用户全局设置。
- `python -m pytest tests/test_docs.py -q --basetemp=tests/.pytest-doc-final-0917 -o cache_dir=tests/.pytest-cache/0917`：最终文档检查 **30 passed, 0.23 s**。
- `git diff --check`：通过。
  无真实硬件测试：没有连接目标硬件及指定实测日志，不声明硬件通过。

### Git 快照

产品源文件清单为空。唯一提交文件为 `VALIDATION.md`。
添加本报告前 `git diff --stat` 和 `git status --short` 均为空。
本仓库仅本地 `.git/info/exclude` 忽略 tests/.pytest-* 验证输出；未修改 .gitignore，
未删除验证产物。最终提交后的状态在交付消息核验。


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

## 2026-09-25 — Export theme and streaming GIF follow-up

The export dialog persists the requested theme mode (`follow_ui`, `light`, or `dark`);
the manifest records both `theme_mode` and `resolved_theme`. The GIF renderer reuses one
Matplotlib figure with two 3D axes, updates artists on each frame, converts the RGBA canvas
to one palette frame at a time, and writes GIF blocks directly. Normal export leaves no
intermediate PNG frame directory. The existing 30 fps, at most 30 s motion and 30-frame
final hold timing rules are unchanged. Static PNG generation was not parallelized because
profiling showed the GIF path was the dominant cost and the rewrite removed that bottleneck.

Synthetic 30 s / 930-physical-frame benchmark on this host (Python `tracemalloc` peak;
benchmark helper in `tests/benchmark_gif.py`):

| GIF path | Render | Encode | Total | Temporary PNG | Peak traced Python memory |
|---|---:|---:|---:|---:|---:|
| Previous per-frame figure + PNG | 469.373 s | 59.600 s | 529.328 s | 930 / 199,293,158 B | 166,855,087 B |
| Persistent figure + streaming GIF | 191.963 s | 4.003 s | 196.247 s | 0 / 0 B | 9,678,940 B |

Observed total time improved about 2.70× on this host; this is a benchmark, not a
portable time threshold. Verification: full pytest **399 passed, 3 subtests passed** in
258.73 s (including existing AIR/GSP Golden, packet, telemetry, command/ACK, serial,
GUI, touch, and offline processing regressions). Tracked Python source and test files
compiled; docs link tests **31 passed**; headless GUI smoke passed 8 mask/language
cases and dialog/event loop; PyInstaller package build and 8-second offscreen EXE
startup passed. No firmware or wire protocol source changed.

## 2026-09-25 -- GIF full-mission default

The GIF source range now defaults to the complete parsed mission in the plan, plotter,
resolved export options, GUI (Full), and CLI (0). A source longer than 30 s is sampled
uniformly into 30 s of motion; a source of at most 30 s plays at source speed. The
on-frame clock displays source mission time, including the actual endpoint. The 1 s
final hold, 30 fps encoding, explicit source-range choices, and PNG 30 s page default
remain as before.

Verification: focused export tests **21 passed** in 16.40 s, followed by four new/default
time and CLI tests **4 passed** in 1.73 s; complete pytest **402 passed, 3 subtests
passed** in 240.23 s. `compileall` passed. Docs-link and AIR/GSP fixed-vector Golden
tests **45 passed**. Headless GUI smoke passed **8 mask/language cases**. Existing
PyInstaller packaging and offscreen startup smoke passed: packaged EXE alive after
8 s, stderr **0 bytes**. `git diff --check` passed. No C, firmware, or wire protocol
source changed in this follow-up; GSHC has no firmware Power of Ten gate.
