# 当前进度

- 已对齐 Platform 0.0.10 / AIR M0 [Calibration 契约](AIR_CALIBRATION_CONTRACT.md)：按 build 选择自动 NONE 或显式默认/采样事务。
- Controller 门禁覆盖直接调用与重试；GUI 使用当前握手能力，未知位只诊断，真实快照决定 NONE 就绪。
- 保留 Reset、Alignment、START、有界 ACK 重试、链路分层诊断、日志、双语与主题。
- 新文档树作为基线；平台内容仅为必要参考，完整权威位于 FCCG。
- 自动化验证、共同契约比对、打包及硬件限制见根 [VALIDATION](../VALIDATION.md)。这里不重复测试数量、hash 或构建资源数据。
- SS0.5 真机的采样、默认 NONE、Reset、Alignment、串口空口与 GPU 仍需联调，按[验证步骤](../tests/validation/README.md)执行。
