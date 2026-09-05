# Calibration 与 Initial Alignment

> 本文件是GSHC仓库中的只读平台参考镜像；完整权威版本由SilverStar_FCCG `docs/platform/`维护。

本文是 SilverStar 0.0.10 的 Sensor Calibration、Initial Alignment 和任务初始姿态契约。坐标系定义以 [COORDINATE_FRAMES.md](COORDINATE_FRAMES.md) 为准，运行期导航以 FCCG `docs/platform/details/NAVIGATION_AND_ESTIMATION.md` 为准，该文件不在 GSHC 镜像范围。Calibration 的自动/显式 NONE、build 门禁与 ACK 语义以[共同契约](../AIR_CALIBRATION_CONTRACT.md)为准。

## 1. 阶段边界

```text
Device configuration
    -> Sensor Calibration
    -> Initial Alignment window
    -> final q_nb
    -> READY
    -> START
    -> software quaternion propagation
```

- Calibration 修正传感器测量误差，输出 corrected sensor measurements；它不决定导航系方向，也不输出任务初始姿态。
- Alignment 使用 START 前的 corrected measurements、可选传感器和用户配置，直接建立唯一的任务初始姿态 `q_nb`。
- START 后以冻结的 `q_nb` 为初值，只用校正后的陀螺增量进行软件四元数传播。JY901B hardware quaternion 可继续用于诊断、日志或显式选择的兼容对准模式，但不是默认运行期姿态权威。
- 正式依赖为 `Calibration READY -> Alignment READY -> START READY`。Calibration 事务开始或 correction 改变会使 Alignment 失效；Alignment reset/restart 不清除 Calibration。

## 2. Calibration

### 2.1 当前能力与输出

`SystemCalibration` 支持 `NONE`、`ONE_FACE` 和 `SIX_FACE`。输出保存在 `SystemCalibrationImuCorrection`：

```c
float accel_bias_mps2[3];
float accel_scale[3];
float gyro_bias_radps[3];
float gyro_scale[3];
```

校正公式逐轴为：

```text
accel_corrected = (accel_raw - accel_bias) * accel_scale
gyro_corrected  = (gyro_raw  - gyro_bias)  * gyro_scale
```

`NONE` 是一次明确完成的 Calibration 事务：bias 全为 0、scale 全为 1，并进入 READY。它不是绕过 Calibration 状态机。

磁力计校准不由当前`SystemCalibration`求解；`GRAVITY_MAG_TRIAD`只接受Magnetometer Interface已标记为calibrated/valid的磁场样本。

### 2.2 静态窗口和拒绝条件

当前实现从 `System/User/system_user_config.h` 读取以下基线：

| 项目 | 当前值 |
|---|---:|
| 本地重力 | `9.78 m/s^2` |
| 静置时间 | `1,000,000 us` |
| 最少有效样本 | `190` |
| 最短窗口时长 | `900,000 us` |
| 最大样本间隔 | `50,000 us` |
| 最大陀螺模长 | `0.05 rad/s` |
| 加速度模长容差 | `0.50 m/s^2` |
| 单轴加速度方差上限 | `0.040 (m/s^2)^2` |
| 单轴陀螺方差上限 | `0.0001 (rad/s)^2` |
| 加速度 scale 合法范围 | `[0.5, 1.5]` |

窗口遇到无数据、运动、比力模长异常、方向错误、方差异常或 sample gap 时废弃当前窗口并等待/自动重采；这类诊断不等价于整个 Calibration FAILED。默认候选面为 `Y+`，方向锥半角为 20°。这只定义校准摆放面，不冻结任务初始姿态。

### 2.3 ONE_FACE

ONE_FACE 对窗口均值执行：

```text
accel_bias[i] = mean_accel[i] - expected_gravity_direction[i] * local_g
gyro_bias[i]  = mean_gyro[i]
accel_scale[i] = 1
gyro_scale[i]  = 1
```

### 2.4 SIX_FACE

六个面分别保存加速度和陀螺窗口统计。所有面完成后逐轴计算：

```text
accel_scale = 2 * local_g / (mean_positive - mean_negative)
accel_bias  = (mean_positive + mean_negative) / 2
gyro_bias   = six face gyro means 的平均值
gyro_scale  = 1
```

已完成的单面允许重采。只有新 `CAL FACE` 请求通过模式、状态、参数和即时静态/方向检查并真正进入该面事务时，旧 completed bit 才清除；其他五面统计保留。`CAL START SIX_FACE` 则从头清空全部六面。

### 2.5 生命周期与可观测性

结果和状态由 `SystemCalibration` 持有，通过 `CAL STATUS`、`CAL DETAIL`、Calibration AIR status edge 和飞行日志格式0.0的`CALIBRATION_RESULT`/事件路径暴露。诊断 reason 只在变化时发出；从非 NONE 恢复为 NONE 也发一次清除事件。

## 3. Initial Alignment 公共规则

### 3.1 权威输出

全部 Alignment 算法最终直接构造并归一化：

```text
q_nb = body frame -> ENU navigation frame
```

`q` 与 `-q` 表示同一旋转。硬件四元数窗口求均值前必须先统一 hemisphere/sign，再归一化并检查离散度。

Euler angles 不是 SilverStar 权威姿态状态的一部分。Euler 只允许用于调试显示、人工诊断和离线可视化；不得参与 INS mechanization、姿态传播、Alignment 核心数学、TILT deploy、Landing detector、KF state 或坐标变换。现存 Euler helper 均应视为 diagnostic-only。

### 3.2 统一静态窗口

四种姿态模式共享非阻塞窗口管理，按模式采集 corrected accel、corrected gyro、calibrated mag 和/或 hardware quaternion。当前默认配置位于 `System/User/system_user_alignment_config.h`：

| 项目 | 当前值 |
|---|---:|
| 最少样本 | `100` |
| 最大窗口样本 | `128` |
| 最短窗口时长 | `500,000 us` |
| 最大样本间隔 | `50,000 us` |
| 最大陀螺模长 | `0.10 rad/s` |
| 加速度模长容差 | `0.50 m/s^2` |
| 最大四元数离散/tilt error | `5 deg` |

窗口记录 sample/accepted/rejected count、起止时间、均值向量、四元数均值和质量结果。样本不足、运动、比力无效或模式必需源无效时不得 READY。

### 3.3 支持的算法

正式列表只有以下四项；旧草案名`HW_QUAT_6AXIS_MAG_TRIAD`已删除，不保留alias：

| 模式 | 数值 | 数学输入 | 定位 |
|---|---:|---|---|
| `HW_QUAT_6AXIS_KNOWN_YAW` | 0 | hardware q window + known yaw | qualified preflight compatibility |
| `GRAVITY_MAG_TRIAD` | 1 | corrected accel/gyro + calibrated mag | software alignment；沿用旧 TRIAD 数值 |
| `HW_QUAT_9AXIS` | 2 | hardware q window | qualified preflight compatibility |
| `GRAVITY_KNOWN_YAW` | 3 | corrected accel/gyro + known ENU yaw | 默认 |

默认 `SYSTEM_ALIGNMENT_GRAVITY_KNOWN_YAW`，默认 known yaw 为 `0.0 deg`。

### 3.4 Gravity + Known ENU Yaw

窗口平均 corrected acceleration 建立 gravity/up 方向；corrected gyro 只用于静止判据。算法先建立 tilt rotation，再计算临时 `q_nb` 下 body +X 的 ENU yaw，最后绕 navigation +Z 施加：

```text
delta_yaw = configured_yaw - current_yaw
```

输出归一化后的 `q_nb`，并复查 gravity alignment 和统一 yaw。该模式不读取磁力计，不应用 magnetic declination，也不依赖 JY901B AHRS quaternion。

优点是对 AHRS 和本地磁场独立、容易验证；代价是发射架方位必须人工测量，known-yaw 误差会直接成为初始航向误差。

### 3.5 Gravity + Magnetometer TRIAD

TRIAD 使用窗口内 corrected acceleration 建立竖直基准、calibrated magnetic field 建立水平方向，corrected gyro 只负责静止判断。它不以 hardware quaternion 为输入或成功条件。

磁场质量检查包括 finite、calibration-valid、`10..100 uT` 模长范围、窗口模长相对偏差不超过 `0.20`、方向 dot 不低于 `0.90`、水平分量比例不低于 `0.10`，并拒绝 gravity/magnetic field 近共线情况。这些阈值集中在用户 Alignment 配置中。

`SYSTEM_ALIGNMENT_MAGNETIC_DECLINATION_DEG` 只用于 TRIAD，将 magnetic north 修正到 true north；正值表示 east declination。不加入 WMM。最终 `q_nb` 仍须满足本文统一 yaw 定义。

### 3.6 Hardware quaternion compatibility modes

`HW_QUAT_9AXIS` 和 `HW_QUAT_6AXIS_KNOWN_YAW` 保留用于任务开始前静态Initial Alignment、兼容诊断和对比。两者都使用完整 hardware quaternion window，不使用最后一帧，并且只形成一次可冻结的初始`q_nb`。6-axis known-yaw 模式保留旧的 body-axis 配置接口；该接口对新的 `GRAVITY_KNOWN_YAW` 无效并视为 legacy/deprecated。这两种静态资格不代表hardware quaternion可以成为START后的权威姿态源。

### 3.7 构建期算法资格

运行期直接接口的`CapabilitiesGet()`只说明设备能否输出某字段或执行某操作；它不证明该数据已经满足某个飞行算法的标定、带宽、延迟或权威性要求。具体Device包在`*_build_capabilities.h`声明静态资格，`Targets/<target>/Inc/target_system_config.h`映射为`SYSTEM_SELECTED_*`，最后由`system_user_capability_validation.h`集中校验。运行期仍必须另外检查Device是否online、healthy、valid和fresh。

当前JY901B组合的正式资格是：accel/gyro可用于软件传播与Gravity alignment；Mag可输出raw和µT，但没有本机绝对矢量标定资格；hardware quaternion的6轴known-yaw和9轴静态Initial Alignment资格均为1，而对应飞行全程authoritative资格均为0。因此当前`GRAVITY_KNOWN_YAW`和两种hardware quaternion预飞静态策略均可通过构建，`GRAVITY_MAG_TRIAD`仍被编译期拒绝。资格门只允许静态窗口生成并冻结一次初始`q_nb`，不会开放飞行中的hardware quaternion回注或source切换。

## 4. ENU yaw

SilverStar yaw 是 body +X 在 ENU 水平面的投影相对 East 的几何方位角：

```text
yaw = atan2(body_x_north, body_x_east)
```

推荐范围为 `[-180 deg, +180 deg)`：

```text
  0 deg : body +X 水平投影指向 East
+90 deg : 指向 North
-90 deg : 指向 South
±180 deg: 指向 West
```

它不是 `0=North, 90=East` 的 compass heading，也不是任何 ZYX/ZXZ/XYZ Euler sequence 的“第三个角”。绕 body +X 自转不改变这个几何定义。

权威 helper 为 `Attitude_YawEnuFromQuaternion()`。它先归一化 `q_nb`，旋转 body +X 后计算上述公式；当水平投影模长小于 epsilon 时返回 invalid，不产生 NaN。

## 5. READY、冻结和 Guard

Alignment READY 时一次性冻结 `final_alignment_q_nb` 以及算法、source、样本统计、窗口时间、均值向量、known yaw/declination 和 final-yaw diagnostic。后续 hardware quaternion 不得覆盖它。

默认 selected sources 为 attitude、GNSS origin、barometer origin；required sources 为 attitude 和 barometer origin。GNSS 保持 optional：无预飞原点时不阻止 Alignment READY，但本任务不启用 GNSS fusion。

READY 后到 START 前 validity guard 非阻塞运行。软件 Alignment 模式只用 corrected gyro 和 corrected accel；不会因为 hardware q 变化而 STALE。当前 guard 阈值为 gyro `0.05 rad/s`、accel magnitude tolerance `0.50 m/s^2`、确认 `100,000 us`、freshness `100,000 us`。`2 deg` hardware-attitude delta gate 只适用于 hardware quaternion 模式。

持续运动会锁存 STALE，整体 `alignment_ready=0`，但 source-ready mask 可保留用于诊断；不得自动恢复，必须重新 `ALIGN START`。START 成功后 guard 停止。
STALE 只使该结果不再具备当前权威性；已经冻结的 final quaternion 保留在详细状态和日志中作为历史诊断结果，不由 live hardware quaternion 覆盖。但是 START 前的 `PREFLIGHT_STATE` 必须立即停止使用该旧结果并回退到最新有效 JY901B hardware quaternion。

## 6. START 与运行期姿态

START 只接受冻结的 `final_alignment_q_nb` 作为 INS initial attitude。任务成功开始后，姿态权威链为：

```text
final_alignment_q_nb
    + corrected gyro increments
    -> software quaternion propagation
```

运行期不存在 software-to-hardware quaternion transition；硬件四元数无效、变化或恢复均不得切换姿态权威。

START 前也不持续积分软件陀螺：attitude component 尚无有效冻结结果时，预飞显示使用最新有效 hardware quaternion；attitude component 的 final quaternion 一旦有效、冻结且未失效，预飞显示改用该冻结值，即使GNSS或气压等其他source仍使overall Alignment未READY；进入STALE或结果失效后再回退hardware quaternion。重新Alignment完成后切换到新的冻结结果。这个fallback只属于PREFLIGHT显示/诊断，不延伸到FLIGHT或RECOVERY。

## 7. Telemetry、Console 与日志

- Alignment进入READY或FAILED时，System按Target静态Sensor Descriptor冻结snapshot；Telemetry按IMU、GNSS、其余sensor ID/instance升序发送固定9-byte`SENSOR_STATUS`，最后发送`STATUS ALIGNMENT`并以arg1关联snapshot ID。STALE不生成新snapshot，arg1固定`0xFF`。该机制不改变`PREFLIGHT_STATE`或任何旧固定帧。
- Alignment READY 权威切换仍主动安排现有 `PREFLIGHT_STATE` 26-byte固定包，回传冻结的final quaternion；没有新增yaw AIR字段，也没有改变type、长度、offset、token、字节序或CRC约定。
- 有效冻结结果保持权威时，hardware quaternion不可用或变化均不影响`PREFLIGHT_STATE.q_nb`；一旦Alignment锁存STALE或该结果失效，预飞四元数权威立即切回hardware quaternion，历史final q只留作诊断。HARDWARE与ALIGNMENT之间的权威切换均应立即安排一帧`PREFLIGHT_STATE`，不等待完整5 Hz周期。
- Console 的 `ALIGN STATUS`/`ALIGN DETAIL` 显示 algorithm、source、window、final quaternion、unified yaw 和配置；Euler 只可作为 diagnostic-only 文本。
- 飞行日志格式0.0保持`SSLOG0` header magic、record common header和既有record layout。既有`CALIBRATION_RESULT`、`ALIGNMENT_RESULT`保持原布局；每次START边沿追加`MISSION_CONFIG`，保存本次alignment/deploy/landing配置。

## 8. 配置所有权

- 常用 Alignment 算法、known ENU yaw、declination、window/static/magnetic quality/guard 阈值集中在 `System/User/system_user_alignment_config.h`。
- Calibration 现有算法阈值继续由 `System/User/system_user_config.h` 管理，本轮只记录和验证，不重写成熟校准逻辑。
- Deploy、tilt reference 和 landing 参数集中在 `System/User/system_user_flight_config.h`。
- 所有流程静态分配、非阻塞推进；不得新增 FreeRTOS task、动态内存或 delay/busy wait。
