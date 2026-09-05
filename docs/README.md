# SilverStar_GSHC 文档

本目录是当前 GSHC 文档入口，适配 SilverStar Platform 0.0.10 / AIR M0。

## 协议与应用

- [AIR_PROTOCOL](AIR_PROTOCOL.md)：GSHC 唯一 AIR wire authority。
- [GSP_MIN_PROTOCOL](GSP_MIN_PROTOCOL.md)：PC ↔ 地面站串口封装。
- [AIR_CALIBRATION_CONTRACT](AIR_CALIBRATION_CONTRACT.md)：FCCG / 飞控 / GSHC 共同契约，保持逐字一致。
- [CALIBRATION_USER_GUIDE](CALIBRATION_USER_GUIDE.md)：默认校正、采样、重置与初对准操作。
- [UPPER_COMPUTER_ARCHITECTURE](UPPER_COMPUTER_ARCHITECTURE.md)：线程、状态、日志与 GUI 边界。
- [GUI_STYLE_GUIDE](GUI_STYLE_GUIDE.md)：GUI 规范；[CXYL 兼容入口](CXYL_Python_GUI_STYLE_GUIDE.md)。
- [CURRENT_PROGRESS](CURRENT_PROGRESS.md)：当前能力与待验证项；精确验收数据仅由根 [VALIDATION](../VALIDATION.md) 维护。

## 平台只读参考

[platform/README](platform/README.md)只保存 GSHC 所需的坐标、生命周期、Calibration / Alignment、Inertial、Telemetry 与 Profile 语义。
完整平台规范由 SilverStar_FCCG `docs/platform/` 维护；这里不建立 MCU/Board/Build/Storage 的第二套规范。文中的固件源码路径是 FCCG 生成工程路径文字，不代表 GSHC 本地文件。

相关格式版本为 Serial Maintenance 0.0、SSLOG 0.0、`.ssdecoder` package/project-semantics 1.1；本轮不新增这些格式的 GSHC 解析器。
