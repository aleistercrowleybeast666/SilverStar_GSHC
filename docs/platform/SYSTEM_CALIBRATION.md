# SystemCalibration 接口与预飞校准规范

> 本文件是GSHC仓库中的只读平台参考镜像；完整权威版本由SilverStar_FCCG `docs/platform/`维护。
> **0.0.10增量**：FCCG GUI只配置OneFace/SixFace采样procedure，NONE始终属于固件能力。无采样procedure的build启动/Reset自动NONE/Identity/READY；build含采样procedure时，用户仍可通过现有`CAL START NONE`选择默认校正。Capability mask为0x01/0x03/0x05/0x07。`CALIBRATION_RESULT`始终表示本次实际生效correction。

> 文档版本：0.0.10
> 适用范围：SilverStar 0.0.10

## 1. 职责与边界

SystemCalibration 负责传感器校准结果的选择、采集、计算和发布；SystemAlignment 负责当前任务初始姿态与Optional原点建立。二者必须分离：

```text
System Startup -> Calibration READY -> Alignment READY -> START READY
```

编入采样流程时，上电/Reset 后为 `mode=NOT_SELECTED`、`state=IDLE`、`ready=0`，等待用户选择；未编入采样流程时自动 NONE/Identity/READY。IMU 预飞数据仍可正常回传，不自动启动采样。共同事务语义以 [AIR_CALIBRATION_CONTRACT](../AIR_CALIBRATION_CONTRACT.md) 为准。

Calibration的`NONE`始终合法：无采样procedure的build启动时自动进入NONE/READY；编入采样procedure时用户仍可通过现有`CAL START NONE`显式选择默认校正。Alignment不能跳过。`CAL START NONE`是一次完整、可记录的Calibration事务，不是绕过状态机。

当前NONE/ONE_FACE/SIX_FACE数学、窗口阈值、corrected measurement边界以及与Initial Alignment的区别以[CALIBRATION_AND_ALIGNMENT.md](CALIBRATION_AND_ALIGNMENT.md)为详细权威说明；本文继续定义SystemCalibration公共接口与事务语义。

## 2. 公共类型

```c
typedef enum
{
    SYSTEM_CALIBRATION_MODE_NONE = 0,
    SYSTEM_CALIBRATION_MODE_ONE_FACE = 1,
    SYSTEM_CALIBRATION_MODE_SIX_FACE = 2,
    SYSTEM_CALIBRATION_MODE_NOT_SELECTED = 0xFF
} SystemCalibrationMode;

typedef enum
{
    SYSTEM_CALIBRATION_STATE_IDLE = 0,
    SYSTEM_CALIBRATION_STATE_WAIT_FACE,
    SYSTEM_CALIBRATION_STATE_COLLECTING,
    SYSTEM_CALIBRATION_STATE_CHECKING,
    SYSTEM_CALIBRATION_STATE_READY,
    SYSTEM_CALIBRATION_STATE_FAILED
} SystemCalibrationState;

typedef struct
{
    SystemCalibrationMode mode;
    float accel_bias_mps2[3];
    float accel_scale[3];
    float gyro_bias_radps[3];
    float gyro_scale[3];
    uint8_t ready;
} SystemCalibrationImuCorrection;
```

face枚举顺序固定为`X+、X-、Y+、Y-、Z+、Z-`，对应值0..5和`completed_face_mask`的bit0..bit5。

## 3. 公共接口

```c
void SystemCalibration_Init(void);
void SystemCalibration_Process(void);
void SystemCalibration_ImuSampleProcess(const SystemInertialSample *sample);
SystemDeviceResult SystemCalibration_Start(SystemCalibrationMode mode);
SystemDeviceResult SystemCalibration_FaceCollect(SystemCalibrationFace face);
SystemDeviceResult SystemCalibration_Stop(void);
SystemDeviceResult SystemCalibration_Reset(void);
SystemDeviceResult SystemCalibration_StatusGet(SystemCalibrationStatus *status);
SystemDeviceResult SystemCalibration_ImuCorrectionGet(
    SystemCalibrationImuCorrection *correction);
SystemDeviceResult SystemCalibration_ImuCorrectionApply(
    const float raw_accel_b_mps2[3],
    const float raw_gyro_b_radps[3],
    const SystemCalibrationImuCorrection *correction,
    float corrected_accel_b_mps2[3],
    float corrected_gyro_b_radps[3]);
uint8_t SystemCalibration_CapabilityMaskGet(void);
uint8_t SystemCalibration_IsReady(void);
```

- `Start/FaceCollect/Stop/Reset`只允许BOOT、SELF_TEST、PREFLIGHT、READY；任务开始后返回`SYSTEM_DEVICE_BAD_STATE`。
- NULL输入或输出参数返回`SYSTEM_DEVICE_INVALID_ARGUMENT`；`Process`和`ImuSampleProcess(NULL)`无副作用。
- 正在执行互斥事务时返回`SYSTEM_DEVICE_BUSY`。
- `StatusGet`返回当前快照；模块未初始化时返回`SYSTEM_DEVICE_NOT_READY`。
- `ImuCorrectionGet`只在correction READY时成功；调用者获得值拷贝，不拥有模块内部存储。
- `ImuCorrectionApply`是INS和Telemetry唯一共用的校正公式入口；任一NULL参数返回`SYSTEM_DEVICE_INVALID_ARGUMENT`，任一输入、correction或输出中间值非finite时返回`SYSTEM_DEVICE_VERIFY_FAILED`，失败时不提交部分输出。
- `CapabilityMaskGet`返回固件实际支持的Calibration模式bit mask：bit0=`NONE`、bit1=`ONE_FACE`、bit2=`SIX_FACE`；始终包含 NONE，再按 build 加入采样位，可能为 `0x01/0x03/0x05/0x07`。它不表示当前已选择或 READY 的模式。
- SIX_FACE的`FaceCollect`在`WAIT_FACE`和`READY`均可请求任一合法face；只有最新样本通过数据流、静止、比力模长和重力方向检查后，请求才算接受并开始修改旧结果。
- 所有状态和窗口使用静态存储，不分配动态内存。

## 4. NONE

`SystemCalibration_Start(NONE)` 通过 lifecycle/busy 检查并选择/锁定 active IMU、使旧 Alignment 失效后，建立：

```text
calibration_mode = NONE
calibration_state = READY
calibration_ready = 1
accel_bias = [0,0,0]
gyro_bias = [0,0,0]
accel_scale = [1,1,1]
gyro_scale = [1,1,1]
```

当前契约不加载未定义的持久化校准数据，也不实现`USE_SAVED`或`FACTORY`。

## 5. ONE_FACE

ONE_FACE复用现有静态窗口算法和System/User的`SYSTEM_IMU_STARTUP_GRAVITY_DIRECTION`，默认Y+。必须保留：1秒settle、gyro magnitude、accel magnitude、重力方向、sample gap、最少190有效样本、至少0.9秒窗口、accel/gyro方差和retry检查。

若请求重力单位向量为`d`，窗口均值为`a_mean`和`w_mean`：

```text
accel_bias = a_mean - d * SYSTEM_LOCAL_GRAVITY_MPS2
gyro_bias = w_mean
accel_scale = [1,1,1]
gyro_scale = [1,1,1]
```

移动、方向错误、幅值错误、方差错误和数据间隔错误会重置采样窗口并增加相应reject/retry，不得产生伪READY。

## 6. SIX_FACE

用户以任意顺序显式发送`CAL FACE`。开始每个face前先检查最新样本的流状态、gyro静止度、accel幅值和重力方向；不合格时返回明确错误并记录`FAILED` face事件，但整个Calibration保持可恢复。合格后使用与ONE_FACE相同的settle、窗口、gap和方差门限。

完成某面后保存该面的accel/gyro均值并设置mask。在`WAIT_FACE`或六面已经`READY`时都允许重新采集已完成面。请求的face、状态和最新静止样本全部检查通过、真正准备开始采集时，才清除该face的completed bit、令Calibration不再READY并使旧Alignment失效；非法face、错误方向或运动等未接受请求不得提前清bit。其他五面的统计结果保持不变。新face通过后重新置bit；若六面再次集齐，则重新计算完整SIX_FACE correction并再次进入READY。

`CAL START SIX_FACE`语义不同：它始终从头开始新的六面事务，清除全部六面统计和mask。`CAL START NONE/ONE_FACE/SIX_FACE`在START前均可从旧READY或进行中的Calibration重新开始，并立即失效旧Alignment。最终SIX_FACE correction由`Algorithm/imu_six_face_calibration.*`计算：

```text
bias_i = (positive_i + negative_i) / 2
scale_i = 2*g / (positive_i - negative_i)

gyro_bias = 六个面gyro mean的总体平均
gyro_scale = [1,1,1]
```

分母必须finite、为正且大于epsilon；scale必须finite并位于System/User设置的0.5..1.5范围；所有输入输出必须finite。六面静态方法不估计陀螺仪scale，不得在报告中声称已校准gyro比例因子。

## 7. Correction消费

INS和AIR Telemetry的正式数据源都是`SystemCalibrationImuCorrection`，二者必须调用`SystemCalibration_ImuCorrectionApply()`，不得各自复制公式：

```text
acc_corrected = (acc_measured - accel_bias) * accel_scale
gyro_corrected = (gyro_measured - gyro_bias) * gyro_scale
```

`ImuSampleBusBiasSnapshot`如保留，只能作为旧诊断兼容视图，不能继续成为INS的唯一校准来源。

Calibration算法自身始终使用SystemInertial最终Virtual IMU提供的、未应用本次Mission Calibration correction的物理量，避免校准结果反馈到本次采集。Source/Factory Calibration和安装变换发生在Virtual IMU之前；Mission Calibration发生在Virtual IMU之后，两者不得混为一层。详见[`SYSTEM_INERTIAL.md`](SYSTEM_INERTIAL.md)。AIR仅在encoder最后一步将校正后的float物理量量化为`int16_t`，不得改变本模块内部的float bias/scale精度。

Calibration未READY时，`ImuCorrectionGet`返回NOT_READY。INS在预飞依赖满足前不开始机械化；Telemetry为了保持校准前`PREFLIGHT_STATE`可观测性，显式采用identity correction（bias=0、scale=1）。ONE_FACE或SIX_FACE进入READY后，Telemetry立即改用正式correction；六面采集中仍使用identity。

## 8. 对Alignment的单向失效规则

任何新Calibration事务开始或结果被清除时，必须在改变Calibration状态前使现有Alignment立即失效：

```text
CAL RESET
CAL START NONE
CAL START ONE_FACE
CAL START SIX_FACE
重新完成ONE_FACE或SIX_FACE（其开始动作已经失效旧Alignment）
```

失效范围包括initial attitude、GNSS origin、barometer origin和与本次Alignment关联的全部ready状态。重新完成Calibration后Alignment仍保持无效，必须再次`ALIGN START`。

`ALIGN START/STOP/RESET`不得清除或改写Calibration mode、bias、scale或ready，依赖只能是：

```text
Calibration -> Alignment -> START
```

## 9. 完成判据、异步通知与失败降级

`CAL FACE <direction>`的同步`OK ... accepted=1`只表示飞控接受了该面的采集请求。该面只有在settle、有效样本数、最短持续时间、静止/方向/幅值检查以及最终方差检查全部通过后，才设置`completed_face_mask`并产生最终结果。Console随后异步输出：

```text
EVENT CAL FACE face=X+ result=PASSED samples=<n> completed_face_mask=0x01
```

若`CAL FACE`在开始阶段因无数据、方向错误或静止条件不满足而被拒绝，则同步命令返回错误，并异步输出一次`result=FAILED reason=<wait_reason>`。采集窗口中途因运动、幅值、方差或sample gap被废弃属于可恢复retry，不把整个face事务声明FAILED，也不刷异步FAILED事件；飞控继续等待新的合格窗口。

ONE_FACE/NONE以及SIX_FACE最终总解算进入READY或FAILED时，Console异步输出`EVENT CAL COMPLETE mode=<...> result=PASSED|FAILED ...`。因此维护端应把命令ACK、异步最终结果和`CAL STATUS`区分开。

AIR继续使用既有`CALIBRATION`和`CALIBRATION_FACE`状态事件，不增加新帧长度。`CALIBRATION_FACE.arg1`定义为0=`FAILED`、1=`PASSED`；它只在该面的最终结果确定后发送。`CALIBRATION`的READY/FAILED表示整个Calibration事务最终结果。Capability ACK后，`PREFLIGHT_STATUS`周期报告当前mode、state、face mask、current face和ready，作为事件丢失后的权威状态恢复来源。事件与快照不得互相替代，也不重复发送事件占用低速半双工链路。

Capability的`calibration_mode_mask`来自本模块能力API。LOG继续记录`CALIBRATION_START`、`CALIBRATION_FACE_COMPLETE`、`CALIBRATION_READY`和`CALIBRATION_FAILED`事件，同时在Calibration进入READY或FAILED时追加固定72-byte的`CALIBRATION_RESULT(0x17)`快照，保存mode/state、样本统计以及完整accel/gyro bias和scale。结果绑定本次锁定的 active IMU；AIR M0 不增加高频多 IMU 字段，具体 source ID 与 SSLOG 记录实现由 FCCG 平台规范维护。旧分项`CALIBRATION_RESULT` EVENT继续保留兼容，但离线恢复参数应优先读取0x17记录。日志写入失败不得阻止Calibration、Alignment或START。

校准后的惯性结果与采样能力是不同概念；启用 SSLOG 时，NONE/Identity 也是合法 Required `CALIBRATION_RESULT`。完整日志流策略和二进制布局由 FCCG 平台规范维护，GSHC 不维护第二套 SSLOG schema。

硬件校准结果只有经过实际采样才能声明完成；Host测试和静态检查不得写成上板校准已验证。

## 10. Calibration diagnostic

`SystemCalibrationStatus` 额外提供只读诊断边沿：

```c
uint32_t diagnostic_sequence;
SystemCalibrationFace diagnostic_face;
SystemCalibrationWaitReason diagnostic_reason;
```

`diagnostic_reason` 的固定编码为：0=`NONE`、1=`NO_STREAM`、2=`GYRO_MOVING`、3=`ACCEL_MAGNITUDE`、4=`GRAVITY_DIRECTION`、5=`VARIANCE`、6=`SAMPLE_GAP`。ONE_FACE 或非特定面使用 `diagnostic_face=NONE(0xFF)`；SIX_FACE 使用当前 X+/X-/Y+/Y-/Z+/Z-。

模块只在 `(face, reason)` 有意义地变化时增加 `diagnostic_sequence`。同一 reason 在连续 IMU sample 中不得重复产生边沿；非 NONE 恢复为 NONE 必须产生一次清除边沿。`CAL START`、`CAL RESET` 和开始新的 SIX_FACE face 会重置诊断；`CAL FACE` 在命令时即可确定无数据、运动、比力模长或方向错误时，立即发布相应诊断状态。

运动、比力、方差或 sample gap 导致窗口废弃时只增加 reject/retry 并自动重新采集，不把 Calibration 整体置为 FAILED。最终结果仍只由 `CALIBRATION_FACE PASSED/FAILED` 和 `CALIBRATION READY/FAILED` 表示。

Telemetry 按 `diagnostic_sequence` 通过既有高优先级 STATUS queue 发送 `CALIBRATION_DIAGNOSTIC(0x0D)`，不增加周期包或线程。维护串口按相同边沿异步输出：

```text
EVENT CAL DIAG face=X+ reason=GRAVITY_DIRECTION
EVENT CAL DIAG face=NONE reason=GYRO_MOVING
EVENT CAL DIAG face=X+ reason=NONE
```

飞行日志格式0.0复用Event Record：`IMU_BIAS_WAIT.arg0=face`、`arg1=diagnostic_reason`。不改变文件头magic `SSLOG0`、Record公共头、Record ID、长度或二进制布局，也不增加高频日志流。
