# 兼容目标

当前工程为 SilverStar_GSHC，Python / PySide6 地面站上位机；应用 metadata 保持 0.0.3。

- 目标平台 SilverStar Platform 0.0.10，AIR M0，PC ↔ GS 使用 GSP-MIN，实机联调目标为 SS0.5。
- 校准事务、默认校正、Reset 与 Alignment 依照[共同契约](docs/AIR_CALIBRATION_CONTRACT.md)；wire 以 [AIR_PROTOCOL](docs/AIR_PROTOCOL.md) 为准。
- 平台相关格式版本为 Serial Maintenance 0.0、SSLOG 0.0、`.ssdecoder` package/project-semantics 1.1；本工程仍只处理现有串口 AIR/GSP 和 JSONL，不因此新增维护串口或 `.ssdecoder` 输入。
- 保留采样校准、六面重采、初对准、START、分层诊断、双语/主题、日志及后处理。
- 不修改 FCCG、FLP、飞控或外部 reference firmware；GSHC 平台参考镜像的完整权威在 FCCG。

入口见[文档索引](docs/README.md)，能力状态见[当前进度](docs/CURRENT_PROGRESS.md)，实际验收结果与硬件限制见 [VALIDATION](VALIDATION.md)。
