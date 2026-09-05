# Changelog

## Unreleased — 2026-09-05

- 兼容 FCCG 0.0.10 动态 Calibration Capability；0x01/03/05/07 对应无采样/单面/六面/单面加六面。
- NONE 从用户开始校准操作中移除，明确显示单位校正；ready 仅来自飞控状态。
- CAL_START 公共入口、通用命令入口和重试发送增加握手、模式及 Capability bit 门禁，本地拒绝保留日志。
- 切换连接/握手后更新隐藏和可见的校准控件；预飞面板增加独立重置按钮，0x01 仍可发送原 CAL_RESET。
- BAD_PARAM 显示 build 不支持或参数错误；保留 ACK accepted、状态完成、BAD_STATE/BUSY 与有界 timeout 语义。
- 链路详情增加未知校准位、Capability/预飞快照计数、最近 AIR 命令结果和下行/快照距今时间。
- 补充 mask、门禁、NONE ready、ACK、timeout、GUI 重连及 AIR/GSP Golden 测试和校准说明。
- AIR/GSP parser、编码、ID、布局、CRC 和握手延时保持原样；无新依赖。
