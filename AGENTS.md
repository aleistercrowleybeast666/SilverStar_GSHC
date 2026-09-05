# SilverStar_GSHC 工作区规则

本仓库是 Python / PySide6 地面站上位机。修改范围限当前 GSHC，AIR 唯一依据为 `docs/AIR_PROTOCOL.md`；不修改 FCCG、飞控或 FLP，不引入 `.ssdecoder` 依赖。

## 代码风格

函数名参考项目，主要使用名词_动宾格式，中间用大驼峰，比如 Lora_TryStartNextTx。新增函数时，若返回值为成功或报错信息，且该函数会被上层调用，生成一个 enum 来包装，变量类型名大驼峰，最后一词为 Result，比如 LoraTxEnqueueResult。新增的头文件保护宏用文件名大写，“.”改为“_”，前面加入“__”，如 __DEBUG_LOG_H。尽量少用全局变量，若用全局变量更优，那也要用 static 关键字保护，避免外界直接调用；有必要调用时写接口函数。每次改动文件都要注意整体是否存在隐患。

已有 Python 模块遵循相邻代码的命名与封装约定；当前新增校准门禁结果类型为 `CalibrationStartResult`。

## 文档与行为约束

- 先阅读[文档索引](docs/README.md)、[AIR wire 规范](docs/AIR_PROTOCOL.md)和[共同 Calibration 契约](docs/AIR_CALIBRATION_CONTRACT.md)。新 docs 是基线，不从历史恢复旧树。
- AIR M0、GSP、命令与握手时序不因文档对齐发生变更。协议字段由 docs 维护，根文件不复制字段表。
- `sampling_calibration_modes()` 只描述采样流程；`calibration_start_modes()` 描述可显式发送的事务。含采样流程时允许默认 NONE，无采样流程时由飞控自动 NONE。
- 公共入口、通用命令入口与重试都执行 Controller 门禁，未知位仅诊断。就绪状态来自飞控，不能由 Capability、选项或 ACK OK 伪造。
- 新连接/成功握手更新 GUI，包括隐藏对话框；已握手后的迟到 Capability 只诊断，不重置会话。
- REJECTED/BAD_PARAM/BAD_STATE/BUSY 与普通超时不自动换模式、不清除握手、不判为断链。
- GSHC 平台文档是参考镜像；完整平台权威在 FCCG。保持共同契约逐字一致，不维护第二套 MCU/Board/Build/Storage 规范。
- 保留分层诊断和有界缓存；串口、协议线程不操作 Widget。

## 验证

修改前阅读根 README/TARGETS/CHANGELOG/VALIDATION 及受影响模块与测试。完成后执行 compileall、pytest、docs link test、headless GUI 和 Golden，环境允许时执行打包 smoke。
精确结果只写入根 [VALIDATION.md](VALIDATION.md)，能力状态见 [CURRENT_PROGRESS](docs/CURRENT_PROGRESS.md)，复验与硬件清单见 [验证步骤](tests/validation/README.md)。不把模拟测试描述为硬件验证；不创建 Release/Tag，不推送远端。
