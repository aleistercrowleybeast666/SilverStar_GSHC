# SystemAlignment 平台接口与初始状态规范

> 本文件是GSHC仓库中的只读平台参考镜像；完整权威版本由SilverStar_FCCG `docs/platform/`维护。
> **0.0.10增量**：`ALIGN_START`只完成即时合法性/初始化，完整`SystemAlignment_Process()`由FlightTask周期推进，避免把重处理压在Telemetry/Serial任务栈。Calibration必须READY；NONE/Identity同样满足该前置条件。

> 文档版本：0.0.10
> 适用范围：SilverStar 0.0.10

## 1. 职责与依赖

SystemAlignment 管理任务开始前的逻辑对准来源，不拥有具体 Device，也不执行飞行中估计。正式依赖固定为：

```text
Calibration READY -> ALIGN START -> Alignment READY -> START READY
```

任何新 Calibration 事务或 `CAL RESET` 都立即使 Alignment 全部结果失效；`ALIGN START/STOP/RESET` 不得反向清除 Calibration。

## 2. 稳定 Source 模型

Source ID 是与设备型号无关的稳定逻辑编号：

| ID | key | 当前用途 |
|---:|---|---|
| 0 | `attitude` | 初始姿态 |
| 1 | `gnss` | GNSS 原点 |
| 2 | `baro` | 气压原点 |
| 3 | `mag` | 预留磁对准 |
| 4 | `dual_gnss_heading` | 预留双 GNSS 航向 |
| 5 | `external_attitude` | 预留外部姿态 |

Source descriptor 至少包含 `source_id`、`key`、`bit`。`key` 由 SystemAlignment 定义；JY901B、NEO-M9N 等物理名称只能来自各直接Interface的`NameGet/InfoGet`。

每个 source 使用固定数组项 `component[SYSTEM_ALIGNMENT_SOURCE_COUNT]` 保存：`state`、`supported`、`selected`、`required`、`ready`。姿态、GNSS 和气压的专用字段保存在类型安全 union 中；禁止 `void *`、动态 key-value 和动态内存。

## 3. 四个 32-bit mask

```c
typedef uint32_t SystemAlignmentSourceMask;

typedef struct
{
    SystemAlignmentSourceStatus component[SYSTEM_ALIGNMENT_SOURCE_COUNT];
    uint32_t start_sequence;
    SystemAlignmentSourceMask capability_mask;
    SystemAlignmentSourceMask selected_mask;
    SystemAlignmentSourceMask required_mask;
    SystemAlignmentSourceMask ready_mask;
    SystemAlignmentSourceMask unavailable_mask;
    SystemAlignmentSourceMask missing_adapter_mask;
    SystemAlignmentState state;
    SystemAlignmentStaleReason stale_reason;
    SystemAlignmentConfigResult config_result;
    uint8_t ready;
} SystemAlignmentStatus;
```

- `capability_mask`：根据Target build capability、直接Interface能力、启动结果及已链接backend自动生成，表示“能否执行”，不表示当前是否已有样本。GNSS 没有 fix 不会清除 capability。
- `selected_mask`：当前任务选用的来源，由 `System/User/system_user_alignment_config.h` 定义。
- `required_mask`：selected 中阻止 Alignment READY 的来源，同样由 System/User 定义。
- `ready_mask`：运行时真正完成的 selected sources。

必须满足：

```text
required_mask & ~selected_mask == 0
source_ready = ((ready_mask & required_mask) == required_mask)
Alignment READY <=> source_ready && stale_latched == 0
```

当前默认：

```text
selected_mask = ATTITUDE | GNSS_ORIGIN | BARO_ORIGIN = 0x00000007
required_mask = ATTITUDE | BARO_ORIGIN               = 0x00000005
```

因此 `ATTITUDE=READY, BARO=COLLECTING` 时总体必须保持 `COLLECTING`。只有 ATTITUDE 和 BARO 都 READY 后总体才 READY；GNSS 可以保持 NOT_READY，且本次任务按既有 `NO_PREFLIGHT_ORIGIN` 策略禁用 GNSS 融合。

若 `required` 不是 `selected` 子集，配置为 `REQUIRED_NOT_SELECTED`。若 selected 但 optional 的 source 本次启动没有 capability，则保留在 `unavailable_mask`、组件显示为 `DISABLED`，但配置仍为 `OK` 并允许降级；若缺失的是 required source，则配置为 `REQUIRED_UNAVAILABLE`，保持在 PREFLIGHT 且不得 READY/START，不应把整个 Lifecycle 误送入 FAULT。若某 source 已声明 capability 且为 required，却没有可用的静态backend实现，则属于代码集成错误，配置为 `ADAPTER_UNAVAILABLE`（枚举名为0.0.8兼容保留）。

## 4. Backend与所有权

`System/Alignment/Inc/system_alignment_backend.h`声明由当前Target链接图唯一提供的直接组合符号：

```c
SystemDeviceResult SystemAlignmentBackend_Reset(void);
SystemDeviceResult SystemAlignmentBackend_PrepareMission(void);
SystemDeviceResult SystemAlignmentBackend_FreezeSources(void);
SystemDeviceResult SystemAlignmentBackend_GuardSampleGet(
    SystemAlignmentGuardSample *sample);
void SystemAlignmentBackend_MissionPreparationAbort(void);
SystemDeviceResult SystemAlignmentBackend_SourceStatusGet(
    SystemAlignmentSourceId source_id,
    SystemAlignmentSourceStatus *status);
```

这些符号由APP组合层实现并直接调用Calibration、Estimator等拥有者，不含回调表、adapter注册或动态内存。source ID由SystemAlignment固定定义且不得重复。NULL输出参数返回`SYSTEM_DEVICE_INVALID_ARGUMENT`，未初始化返回`SYSTEM_DEVICE_NOT_READY`，并发事务返回`SYSTEM_DEVICE_BUSY`，START后修改返回`SYSTEM_DEVICE_BAD_STATE`。

## 5. 生命周期与 START 原子事务

- `SystemAlignment_Start()`：仅 Calibration READY 且配置有效时允许；清除旧姿态/原点并进入 COLLECTING。
- `SystemAlignment_Process()`：遍历 selected sources，从 adapter 更新 component 和 ready mask，再仅按 required mask 计算总体状态。
- `SystemAlignment_PrepareMission()`：再次验证总体READY，并取得姿态Source已经由完整窗口冻结的`final_alignment_q_nb`；START不在此临时构建或替换姿态。
- `SystemAlignment_OriginsFreeze()`：按现有策略冻结 GNSS/气压原点。
- `SystemAlignment_MissionPreparationAbort()`：START 后续步骤失败时恢复预飞采集，不留下部分任务状态。
- `SystemAlignment_Reset()`：清除 Alignment，不清除 Calibration。
- `SystemAlignment_CalibrationInvalidate()`：由 Calibration 单向使全部 Alignment 结果失效。

四元数固定定义为`q_nb: body -> ENU`；默认姿态Source使用corrected accel/gyro窗口和known ENU yaw直接构造，不依赖hardware quaternion。完整算法、yaw和Euler policy见[CALIBRATION_AND_ALIGNMENT.md](CALIBRATION_AND_ALIGNMENT.md)，GNSS融合许可策略保持不变。

## 6. Console

`ALIGN STATUS` 保持单行，首先输出总体状态、四个 mask 和配置诊断，然后遍历 `selected_mask` 动态追加 `<key>=<state>`。未选择 GNSS 时不得出现 `gnss=...`；以后选择 MAG 后，无需修改 Console 主循环即可出现 `mag=...`。

`ALIGN DETAIL` 首行输出总体状态和mask，并附加algorithm、attitude source、window统计、均值、final quaternion、unified yaw、known yaw/declination和frozen标志；随后每个selected source一行：

```text
ALIGN SOURCE name=attitude state=READY required=1 ...
ALIGN SOURCE name=gnss state=NOT_READY required=0 ...
ALIGN SOURCE name=baro state=READY required=1 ...
```

`EVENT ALIGN COMPLETE result=PASSED` 只能在 required mask 全部 READY 后产生，并同样只遍历 selected sources。不得再出现 `result=PASSED baro=COLLECTING`（当 BARO required 时）。

## 7. AIR遥测协议M0边界

SystemAlignment内部source mask为32-bit，但AIR M0不把该mask或固定的ATTITUDE/GNSS_ORIGIN/BARO_ORIGIN ready bits写入`PREFLIGHT_STATUS`。该帧的byte6低四位只编码总体`alignment_state`，高四位保留；具体设备状态由通用9-byte `SENSOR_STATUS` snapshot表示。

AIR M0可以通过`SENSOR_STATUS.sensor_id/instance_id`表示Target静态descriptor中当前启用的任意sensor。新增Magnetometer等通用sensor不要求升级AIR协议，也不改变任何既有固定帧长度、字段偏移或packet type；接收端不认识的`sensor_id`必须保留原值并安全显示或忽略，不能拒绝整个snapshot。当前JY901B Magnetometer接口默认关闭，因此默认snapshot不包含`MAGNETOMETER`，但`AIR_SENSOR_ID_MAGNETOMETER=0x04`继续保留在稳定Sensor ID表中。

Alignment进入READY或FAILED后，snapshot按IMU、GNSS、其余sensor ID/instance升序发送，最后由`STATUS ALIGNMENT`标记事务完成。STALE只发送`STATUS ALIGNMENT`且`arg1=0xFF`，不重新采集或发送snapshot。AIR M0不定义Sensor Query命令；Ground Station的详情来自已经接收并缓存的`SENSOR_STATUS`。

## 8. 公共接口

接口以 FCCG 生成工程 `System/Alignment/Inc/system_alignment.h` 为准，包括 Init/Start/Stop/Process/StatusGet/DetailGet/Reset、CalibrationInvalidate、PrepareMission、OriginsFreeze、CapabilityMaskGet、MasksValidate、descriptor/status查询和detail formatter。当前接口不存在`OpsSet`或运行期注入入口。StatusGet返回完整值拷贝，调用者不持有内部地址，只允许用于低频详细诊断。

周期路径使用三类轻量接口：`SystemAlignment_SummaryGet()`只复制总体state、stale reason、selected/ready mask、start sequence和预飞姿态source；`SystemAlignment_IsReady()`只读取ready；`SystemAlignment_PreflightQuaternionGet()`只返回四个float及`HARDWARE/ALIGNMENT`source。最后一个接口仅允许PREFLIGHT/READY生命周期：final attitude尚未有效时读取hardware quaternion，有效冻结且未失效时读取final q，STALE/失效时回退hardware quaternion。Telemetry、Lifecycle、Health和Indicator不得用完整Status代替这些轻量接口。

## 9. READY validity guard 与 STALE

Alignment 总体状态固定为 `IDLE/COLLECTING/CHECKING/READY/FAILED/STALE`。`STALE` 只表示本次 Alignment 曾经 READY，但在 START 前检测到足以使该初始状态失效的运动；它不表示任何Device或source本身失败。

首次进入READY时保存归一化reference attitude和最终窗口结果。此后到START成功前，由现有`SystemAlignment_Process()`非阻塞检查：

- 当前 Mission Calibration correction 只应用一次后的 corrected gyro 模长；
- corrected accel 模长相对 `SYSTEM_LOCAL_GRAVITY_MPS2` 的偏差；
- hardware quaternion模式下，最新有效姿态相对reference quaternion的最小夹角；软件`GRAVITY_KNOWN_YAW/GRAVITY_MAG_TRIAD`模式不使用hardware attitude delta，避免硬件AHRS变化错误使软件对准STALE；
- inertial/attitude sample 的时间戳、sequence 和 freshness。

任一运动条件连续达到 confirm duration 后锁存 STALE。单个噪声 sample、重复 sequence、乱序或陈旧 sample 不得触发；条件恢复也不得自动返回 READY。进入 STALE 后：

```text
state = STALE
stale_reason = MOTION
ready = 0
ready_mask = 各 source 当前局部 ready mask（保持诊断语义）
Lifecycle READY -> PREFLIGHT
START -> ALIGNMENT_REQUIRED
```

只有显式 `ALIGN START`（或 `ALIGN RESET` 后再 `ALIGN START`）会清除 stale latch，并重新执行完整 Alignment，包括 required Barometer。Calibration 重新开始/改变时原有单向失效逻辑不变。`SystemAlignment_OriginsFreeze()` 成功后 guard 停止；START 后正常飞行动作不得触发 STALE。

Guard 独立配置位于 `System/User/system_user_alignment_config.h`：

```text
SYSTEM_USER_ALIGNMENT_GUARD_ENABLE
SYSTEM_USER_ALIGNMENT_GUARD_GYRO_THRESHOLD_RADPS
SYSTEM_USER_ALIGNMENT_GUARD_ACCEL_TOLERANCE_MPS2
SYSTEM_USER_ALIGNMENT_GUARD_ATTITUDE_DELTA_RAD
SYSTEM_USER_ALIGNMENT_GUARD_CONFIRM_DURATION_US
SYSTEM_USER_ALIGNMENT_GUARD_SAMPLE_FRESHNESS_US
```

`SystemAlignmentBackend_GuardSampleGet()`返回最新raw Virtual IMU经当前Mission Calibration correction修正一次后的机体系加速度/角速度，以及observation/sample/receive timestamp、sequence和valid mask；SystemAlignment复制值，不持有调用方内存。NULL输出返回`SYSTEM_DEVICE_INVALID_ARGUMENT`，暂无样本返回对应`SystemDeviceResult`，不得阻塞。

维护串口 `ALIGN STATUS/DETAIL` 显示 `state=STALE` 与 `stale_reason=MOTION`；进入边沿异步输出：

```text
EVENT ALIGN STALE reason=MOTION ready_mask=0x00000005
```

AIR 使用既有9-byte`STATUS ALIGNMENT`，`arg0=STALE(5)`、`arg1=0xFF`表示不关联新snapshot；`PREFLIGHT_STATUS.flags.alignment_ready=0`且`start_block_reason=ALIGNMENT_REQUIRED`（除非有更高优先级原因）。固定长度、偏移和profile均不改变。

## 10. LOG记录

Alignment的事件时间线继续使用既有`ALIGNMENT_START/READY/FAILED` EVENT。READY事件的`arg0=ready_mask`、`arg1=required_mask`；FAILED事件使用`arg0=unavailable_mask`、`arg1=missing_adapter_mask`，方便旧事件解析链快速诊断。

Alignment进入READY、FAILED或STALE时另写固定96-byte的`ALIGNMENT_RESULT(0x18)`，一次性保存通用mask以及AIR M0对应的姿态/GNSS/Baro候选结果。STALE同时复用既有`ALIGNMENT_REJECTED` Event Record，`arg0=STALE`、`arg1=MOTION`；不改变Event Record或0x18的二进制布局：

```text
capability_mask
selected_mask
required_mask
ready_mask
unavailable_mask
missing_adapter_mask
start_sequence
state
config_result
ready
source_count
```

0x18除通用mask外还保存当前attitude timestamp/q_nb、GNSS origin候选与精度、Baro origin候选以及三项component state。START真正提交时仍由`INITIAL_STATE(0x0D)`保存最终冻结/采用的姿态四元数、GNSS origin、Baro origin和P0。这样未来增加Mag/dual-GNSS等Source时，mask层无需改变；需要持久化新的Source专用数值时追加新的独立Record，而不修改既有0x18布局。
