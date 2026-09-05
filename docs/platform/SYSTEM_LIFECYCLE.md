# SilverStar 系统生命周期与START事务

> 本文件是GSHC仓库中的只读平台参考镜像；完整权威版本由SilverStar_FCCG `docs/platform/`维护。

> 文档版本：0.0.10
> 适用范围：SilverStar 0.0.10

## 1. 状态

```text
BOOT -> SELF_TEST -> PREFLIGHT <-> READY -> FLIGHT -> RECOVERY
                                         -> LANDED -> POSTFLIGHT
任意严重故障 -> FAULT
```

READY不是独立飞行算法状态，只表示当前Required条件满足。Calibration或Alignment失效后，系统必须从READY恢复PREFLIGHT，且不能START。

## 2. 预飞依赖

正式单向依赖固定为：

```text
Calibration READY
    -> Alignment READY
        -> START READY
```

- Calibration NONE始终合法：无采样procedure时自动NONE/READY；有采样procedure时可显式选择NONE；状态机仍必须READY。
- Alignment不可跳过；Calibration未READY时不能READY。
- 新Calibration事务或CAL RESET立即使Alignment和START READY失效。
- ALIGN RESET/重新Alignment不清除Calibration。
- GNSS是Optional；无预飞GNSS origin不阻止READY，但任务内GNSS融合保持disabled。

SystemHealth的blocking mask分别使用`CALIBRATION_NOT_READY`和`ALIGNMENT_NOT_READY`，并继续检查Profile、Required Interface、Startup、IMU、姿态和输出safe。

## 3. START来源

`SystemLifecycle_SubmitStart()`支持AIR、Console和Local来源，均进入同一有界请求队列和原子事务。相同时间只允许一个START请求/事务；真正已有请求时才返回`BUSY/REQUEST_PENDING`。

`SystemLifecycle_StartReadinessGet(reason)`执行与真实START相同的无副作用前置检查，用于`PREFLIGHT_STATUS.start_block_reason`。真实START在同一前置检查通过后才执行prepare/freeze/navigation/queue等有副作用步骤；二者共用同一result/reason语义，Telemetry不得复制第二套判断链。

本机`SYSTEM START`不依赖AIR握手或AIR lock。AIR `START_MISSION`在提交Lifecycle前额外检查：

```text
Capability ACKED
AIR interlock UNLOCKED
```

失败分别返回`CAPABILITY_REQUIRED`和`LOCKED_REQUIRED`，不得提交Lifecycle。

## 4. START检查与执行顺序

Lifecycle执行顺序：

1. 当前状态允许START；
2. `SystemCalibration_IsReady()!=0`，否则`CALIBRATION_REQUIRED`；
3. `SystemAlignment_IsReady()!=0`，否则`ALIGNMENT_REQUIRED`；Lifecycle周期预检查不得复制完整Alignment detail；
4. Lifecycle为READY且SystemHealth ready；
5. `prepare_start()`冻结/检查最新有效姿态；
6. `freeze_origins()`按当前策略冻结GNSS/Barometer原点；
7. 冻结Profile、Log Policy、Navigation Profile、Estimator Profile并启动mission time；
8. 初始化Pure INS和KF6；
9. 复位飞行队列；
10. 一次性提交FLIGHT。

任何中途失败必须调用abort并回滚mission time和所有Profile冻结，不允许部分进入FLIGHT。日志失败是降级项，不回滚已经成功的START。

## 5. 诊断原因

`SystemLifecycleStartResult`描述结果类别；`SystemLifecycleStartReason`描述具体原因。当前正式reason：

```text
NONE
REQUEST_PENDING
CALIBRATION_REQUIRED
ALIGNMENT_REQUIRED
ATTITUDE_NOT_READY
ATTITUDE_INVALID
ATTITUDE_STALE
SYSTEM_NOT_READY
LOCKED
HOOKS_UNAVAILABLE
PREPARE_FAILED
ORIGIN_FAILED
NAVIGATION_FAILED
QUEUE_FAILED
```

AIR映射必须同时读取result与reason。`BUSY`只表示已有请求/事务，不得代替Calibration、Alignment、姿态、系统、原点、导航或队列错误。`HOOKS_UNAVAILABLE`和`PREPARE_FAILED`具有独立AIR结果，不得无条件映射为`REJECTED`。同一映射同时服务真实AIR START ACK和预飞快照。本机`SYSTEM START RESULT`输出完整result、reason、request_id和timestamp。

## 6. START后锁定

FLIGHT及之后配置被锁定。AIR Capability状态进入`DISABLED_FOR_FLIGHT`，停止Capability、PREFLIGHT_STATUS和PREFLIGHT_STATE；TelemetryService根据独立`command_policy`决定RX行为。当前0.0.10为`PREFLIGHT_ONLY`，因此仍排空Transport RX但不解析、不执行任何新CMD，也不为新入站命令回ACK。若AIR START已在PREFLIGHT合法提交，其Lifecycle最终响应即使在状态切换后才被TelemetryTask消费，也必须排入ACK队列并发送一次；关闭预飞RX不得清除此pending事务或ACK队列。任务期间不能重新握手或恢复预飞命令。

## 7. 所有权

Lifecycle只编排事务，不直接访问HAL、UART、FatFs、SDIO或具体设备。Start Hooks必须全部非NULL并在任务启动时注册；所有请求、响应和诊断使用静态有界存储，不使用动态内存。

## 8. Alignment STALE 联动

Alignment 首次 READY 后、START 成功前的 validity guard 若锁存 `STALE`，必须立即令 `alignment.ready=0`。若 Lifecycle 当时为 READY，SystemAlignment 将其恢复到 PREFLIGHT；下一次 SystemHealth 周期重新置位 `ALIGNMENT_NOT_READY`，本机 `SYSTEM READY` 同步变为未就绪。

Lifecycle 的前置检查顺序不变：Calibration 仍高于 Alignment。Calibration READY 且 Alignment STALE 时，`SystemLifecycle_StartReadinessGet()` 与实际 START 都返回 `SYSTEM_LIFECYCLE_START_NOT_READY / SYSTEM_START_REASON_ALIGNMENT_REQUIRED`；AIR 映射为 `ALIGNMENT_REQUIRED`，不得返回 BUSY。重新静止不能自动清除该原因，必须完成新的 `ALIGN START`。

成功执行 `freeze_origins()` 后 guard 停止。START 原子事务提交 FLIGHT 后，正常运动不得反向改变 Alignment 为 STALE；若 START 后续步骤失败并 abort 回到预飞，则继续使用本次仍有效的 reference，除非显式重新 Alignment 或 Calibration 将其失效。

## 9. GSHC 任务事件

START 请求或 ACK accepted 不等于所有飞控动作已完成。GSHC 保留既有 START ACK、MISSION_START 或首个 FLIGHT_STATE 的任务确认路径。

飞控的 MISSION_START、PARACHUTE_DEPLOY、LANDING 等事件表示真实任务进展；GSHC 不凭高度、速度或时间自行补造动作。执行器、deploy/landing 算法与具体构建资格由 FCCG 完整平台规范维护。
