# GSHC 复验步骤

实际结果与精确数量只记录在根 [VALIDATION.md](../../VALIDATION.md)；能力说明见[当前进度](../../docs/CURRENT_PROGRESS.md)，操作语义见[共同契约](../../docs/AIR_CALIBRATION_CONTRACT.md)。

## 自动验证

从工作区根目录执行 PowerShell：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m compileall -q app.py main.py config.py protocol services transport ui processing tests
python -m pytest -q
python -m pytest tests/test_docs.py -q
python tests/validation/headless_gui_smoke.py
powershell -NoProfile -ExecutionPolicy Bypass -File tests/validation/packaging_smoke.ps1
```

`test_calibration_capability.py` 检查采样/事务列表、默认 NONE 的真实 AIR/GSP bytes、未知位、握手与多入口门禁、ACK 接受/错误/超时、NONE 快照、Reset/Alignment、GUI 重连及默认选项不会自动发送。既有 parser/command Golden 继续覆盖原协议。

`test_docs.py` 离线检查 Markdown 相对目标、参考镜像、最终校准语义和 AIR 常量；不把外部 URL 可用性作为测试依赖。镜像不是 GSHC 固件实现验收。共同契约还应在本机可访问 FCCG 时进行文件内容比对，不把兄弟仓库作为 CI 必需路径。

Headless smoke 的日志、截图与打包产物位于忽略的 `build/final_alignment_validation/`。测试进程独立加载 Windows 字体和 QSettings，隐藏 offscreen 不支持的 OpenGL 视口；真实 GPU/窗口需另验。

## 打包 smoke

[packaging_smoke.ps1](packaging_smoke.ps1) 使用原 spec 和只含 Python/Windows 的进程 PATH，避免工具运行环境中无关 Qt/ICU DLL 混入包；不改系统 PATH。它构建独立目录，以 offscreen 和独立数据目录启动 EXE，检查存活、异常标题和 stderr，最后只结束自己启动的测试进程。

输出包必须以完整文件夹使用，不能单独复制 EXE。此 smoke 不等于串口、GPU 或硬件验收，也不创建 Release、Tag 或推送。

## SS0.5 实机清单

1. 分别连接四种 Calibration build，完成最新 Capability ACK；断开重连后菜单必须替换，未知高位不产生新操作。
2. 无采样 build 由飞控自动 NONE/READY；GSHC 不发送重复 NONE，ready=0 保持真实阻塞。验证 CAL_RESET 后同样自动 NONE。
3. 含采样流程时显式点击“使用默认校正”，验证真实 CAL_START(NONE)、飞控 active IMU 锁定、旧 Alignment 失效、ACK accepted 和最终 NONE/READY/ready=1；选择菜单本身不发送。
4. OneFace、SixFace、六面重采与 Reset 后重新选择；检验 REJECTED、BAD_PARAM、BAD_STATE、BUSY 与丢 ACK 的有界恢复，不回退到其他模式。
5. 三条校准完成路径之后分别验证 Alignment、Sensor Snapshot、STALE 与显式重新对准；START 使用真实预飞门条件。
6. 暂停 AIR 下行，核对 PC request、serial write、GSP ACK、GS TX/RX/CRC、AIR ACK、快照年龄与 RSSI/SNR；结合 GS/FC 控制台判断空口或飞控运行问题。
7. 源码版和完整打包版分别验证真实串口、Qt/GPU 3D、双语/主题、JSONL 与后处理。
