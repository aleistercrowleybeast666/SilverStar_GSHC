# SilverStar Platform References for GSHC

本目录为 GSHC 必要的平台只读参考镜像。完整 SilverStar Platform 0.0.10 authority 位于 SilverStar_FCCG `docs/platform/`；固件源路径均是外部工程说明，不是本地链接。

- [COORDINATE_FRAMES](COORDINATE_FRAMES.md)：ENU、Body、WXYZ 四元数。
- [SYSTEM_LIFECYCLE](SYSTEM_LIFECYCLE.md)：预飞依赖、START 与任务事件。
- [SYSTEM_CALIBRATION](SYSTEM_CALIBRATION.md)：Calibration 状态机与 correction。
- [SYSTEM_ALIGNMENT](SYSTEM_ALIGNMENT.md)：初对准、快照与 STALE。
- [SYSTEM_INERTIAL](SYSTEM_INERTIAL.md)：Virtual IMU 与校准层次。
- [CALIBRATION_AND_ALIGNMENT](CALIBRATION_AND_ALIGNMENT.md)：测量校正与初始姿态的区别。
- [TELEMETRY_INTERFACE](TELEMETRY_INTERFACE.md)：AIR / Transport 与下行调度。
- [SYSTEM_PROFILE](SYSTEM_PROFILE.md)：构建能力与运行状态的区别。

GSHC AIR wire 只由 [AIR_PROTOCOL](../AIR_PROTOCOL.md) 定义，跨组件 Calibration 行为只由[共同契约](../AIR_CALIBRATION_CONTRACT.md)定义。本目录不维护 MCU/Board/Build/Storage 底层规范；算法窗口等配置示例需以具体 FCCG 生成工程为准，不能成为 GSHC 本地就绪门禁。
