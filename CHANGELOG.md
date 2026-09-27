# Changelog

## Unreleased — Navigation preparation and health

- 保持 AIR M0/GSP 的 9-byte 外封装，增加独立协商的导航准备和健康消息；旧固件显示 UNSUPPORTED。
- 导航准备分步骤显示，READY 来自当前会话、准备代次及新鲜机载快照；重启、断线、重准备及过期清除就绪。
- START 公共入口、通用入口和重试均检查导航准备；ACK 不生成 READY。
- 飞行页支持五组融合质量、成功/接收/有效/尝试/恢复年龄、R/NIS，以及真实 GNSS 精度、IMU 质量和 logger 队列详情，保留双语与主题。

## Unreleased — 2026-09-05

- 对齐 SilverStar Platform 0.0.10 / AIR M0 [共同 Calibration 契约](docs/AIR_CALIBRATION_CONTRACT.md)。
- 含采样流程的 build 可显式选择“使用默认校正（不进行采样）”并发送现有 CAL_START(NONE)；无采样流程的 build 由飞控自动 NONE。
- 分开采样能力与可启动事务，公共入口、通用入口和重试使用统一门禁。未知能力位只诊断；刷新/握手/语言切换不自动发校准命令。
- NONE 与无采样 build Reset 按真实预飞快照恢复事务；ACK OK 仍仅表示接受。
- BAD_PARAM 表示 mode 编码非法，REJECTED 表示合法采样模式未编入 build；保留 BAD_STATE/BUSY 和有界超时语义。
- 保留独立校准重置、重连控件更新、下行年龄与分层链路诊断。
- 以新 docs 树为基线校对索引、平台参考镜像和应用文档，补充链接与语义回归；精确验收数据集中到根 [VALIDATION](VALIDATION.md)。
- AIR/GSP 编码、解析、ID、长度、offset、CRC 和握手延时不变，无新增运行期依赖。
