# System Profile 参考

> 本文件是 GSHC 仓库中的只读平台参考镜像；完整权威版本由 SilverStar_FCCG `docs/platform/` 维护。基线：Platform 0.0.10。

## 能力与运行状态

必须区分构建选择、profile 启用、required/optional 策略和 runtime usable。编入能力不能证明设备当前 online、healthy、valid 或 fresh；运行时健康也不能赋予未编入的算法能力。

Calibration build 支持哪些采样流程通过 Capability 声明；本次选择及是否就绪通过实际状态回传。GSHC 按[共同契约](../AIR_CALIBRATION_CONTRACT.md)生成操作菜单，不能根据设备型号或旧连接猜测支持模式。

AIR profile 表示 wire 编码，与平台版本、设备型号、System Profile 配置和 command policy 相互独立。具体定义见 [AIR_PROTOCOL](../AIR_PROTOCOL.md)。

## 预飞与冻结

Alignment 的 required sources 必须满足才能 READY；某个传感器 optional 不代表当前算法所需的数据也 optional。GSHC 使用总体 alignment ready 和 start block reason，不根据固定设备列表自行判断。

START 事务由飞控检查并冻结本次 Calibration correction、Alignment 结果和任务配置。任务期间的命令许可遵循 AIR command policy；GSHC 不绕过飞控生命周期。

## 所有权

设备组合、Target、Board、MCU、构建资格、默认算法与日志配置均在 FCCG 中维护。GSHC 只显示 AIR 可观察的能力与状态，不维护第二套具体固件 build 配置。
