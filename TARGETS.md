# 兼容目标

当前软件：SilverStar_GSHC 地面站上位机，版本 metadata 保持 0.0.3。

- 适配 FCCG 0.0.10 生成工程的动态校准能力，通过现有 SilverStar AIR V0 / `AIR_PROFILE_COMPACT_V0=0` 通信。
- PC ↔ GS 使用 GSP-MIN 串口封装；GS ↔ FC 保持现有 AIR wire format。真实联调目标为 SS0.5。
- 接受 mask 0x01/03/05/07 及附带未知高位；分别提供 0/1/1/2 个用户采样模式。
- NONE 表示单位校正；只根据状态字段显示 ready，不要求用户启动 NONE。
- 保留校准 reset、单面/六面采样、六面重采、初对准、START、分层链路诊断、双语/主题、日志与后处理。
- 不修改 FCCG 生成器、飞控固件或 FLP；不新增 AIR command、profile、字段或 `.ssdecoder` 输入。

硬件进度：本轮自动化验证仅覆盖 PC 协议/Controller/GUI/打包；SS0.5 的空口时序、飞控采样与初对准运行仍需真实设备验证，见 `tests/validation/README.md`。
