# GSHC 验收快照

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
