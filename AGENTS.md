# SilverStar_GSHC 工作区规则

本仓库是 Python / PySide6 地面站上位机。修改范围限当前 GSHC，AIR 唯一依据为 `docs/AIR_PROTOCOL.md`；不修改 FCCG、飞控或 FLP，不引入 `.ssdecoder` 依赖。

## 代码风格

函数名参考项目，主要使用名词_动宾格式，中间用大驼峰，比如 Lora_TryStartNextTx。新增函数时，若返回值为成功或报错信息，且该函数会被上层调用，生成一个 enum 来包装，变量类型名大驼峰，最后一词为 Result，比如 LoraTxEnqueueResult。新增的头文件保护宏用文件名大写，“.”改为“_”，前面加入“__”，如 __DEBUG_LOG_H。尽量少用全局变量，若用全局变量更优，那也要用 static 关键字保护，避免外界直接调用；有必要调用时写接口函数。每次改动文件都要注意整体是否存在隐患。

已有 Python 模块遵循相邻代码的命名与封装约定；当前新增校准门禁结果类型为 `CalibrationStartResult`。

## 校准与协议不变量

- Profile 0、AIR 固定长度/布局/字节序、GSP CRC、命令 ID/Token 和握手延时保持不变。
- NONE bit 表示单位校正；用户采样模式仅 ONE_FACE/SIX_FACE。Capability mask 是 build 能力，不表示完成状态。
- CAL_START 的 Controller 公共入口、通用入口和重试发送均检查握手与对应 bit；NONE、未知或不支持的模式本地拒绝，不能 TX。
- mask 0x01 保留 CAL_RESET；NONE + ready=1 正常显示，ready=0 不自动推进。
- 新连接/成功握手更新 GUI 的模式列表，未知高位仅诊断。已握手后的迟到 Capability 不自动重置会话。
- CAL_START/ALIGN_START ACK OK 仅表示接受；BAD_PARAM 不自动换模式，BAD_STATE/BUSY 不判断为断链，普通 timeout 不清除 Capability。
- 保留分层链路诊断和有界状态缓存。串口、协议线程不操作 Widget。

## 验证

修改前阅读 README、TARGETS、CHANGELOG、AIR 协议、受影响模块及 tests。完成后运行 compileall、pytest（含 headless GUI 与 Golden）；环境允许时执行打包 smoke。命令及真实 SS0.5 待联调项目见 `tests/validation/README.md`。不要把模拟测试描述为硬件验证。
