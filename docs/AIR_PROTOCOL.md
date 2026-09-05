# SilverStar AIR 应用层协议 M0

> 正式名称：AIR 遥测协议 M0
> wire兼容标识：`AIR_PROFILE_COMPACT_V0 = 0`
> 当前技术profile枚举：`AIR_PROFILE_COMPACT_V0 = 0`
> 适用范围：SilverStar 飞控与地面站  
> 状态：首次发布前冻结候选；Platform 0.0.10 保持 M0 / 0，文档对齐不改变现有 wire layout

## 1. 固定原则

AIR 是飞控与地面站之间的定长二进制应用层协议。

所有多字节整数和 IEEE-754 `float32` 均为 little-endian。

每个 `type` 对应唯一固定帧长度：

- 不使用公共可变长头；
- 不使用 TLV；
- 不在已有固定帧尾部随意增加字段；
- 不因增加普通传感器而改变既有固定帧；
- 不因更换具体传感器型号而改变AIR M0。

AIR 应用层不附加 CRC8/CRC16/CRC32。

当前 SX1281 Transport 使用 LoRa hardware CRC 提供空口完整性保护。未来如果更换 Transport，新 Transport 必须自行提供 CRC 或等效完整性保护，不得静默改变 AIR M0 的固定帧布局。

```c
AIR_PROFILE_COMPACT_V0 = 0U
AIR_MAX_FRAME_LEN = 50U
AIR_PROTOCOL_APPLICATION_CRC_SIZE = 0U
```

当前 Transport MTU 为 64 bytes，因此必须满足：

```text
all AIR frame lengths
<= AIR_MAX_FRAME_LEN
<= Transport MTU
```

SilverStar firmware version 不编码进 AIR profile。

---

## 2. AIR M0 设计目标

AIR M0 将飞行状态、传感器类别、具体传感器型号、Alignment 算法与 Transport 实现解耦。

AIR 周期主链只关心长期稳定的 canonical information：

- IMU / attitude；
- GNSS；
- system state；
- calibration；
- alignment；
- flight state。

普通传感器使用通用 `SENSOR_STATUS` 描述。未来新增 barometer、magnetometer、air-data sensor、rangefinder、radar altimeter、sun sensor、star tracker、vision sensor、external attitude sensor 等，不得要求修改固定 AIR M0 frame layout。

Ground Station 遇到尚未认识的 sensor ID 时应显示 `Unknown Sensor 0xNN`，而不是拒绝整个会话。

当前 0.0.10 契约内保持 M0 wire layout 冻结；后续扩展遵循下一节 Profile 边界。

---

## 3. 什么情况下需要新的 AIR Profile

正式发布后，以下变化不改变 `air_profile_id`：

- IMU / GNSS / Barometer / Magnetometer 型号变化；
- 新增普通传感器 class 或 sensor ID；
- Target Device Adapter/Board Service组合变化；
- Alignment / Calibration 算法变化；
- accel / gyro 实际 full scale 变化；
- LoRa SF / BW / CR / TxPower 变化；
- Target静态sensor descriptor集合变化；
- `command_policy` 变化；
- Ground Station 对新 sensor class 增加显示支持。

以下变化才需要新的 AIR Profile：

- 固定 frame 字段顺序或长度变化；
- accel / gyro 编码规则变化；
- quaternion 编码变化；
- ENU velocity / position 数值编码变化；
- command / ACK 基础布局变化；
- application fragmentation；
- encryption/authentication 改变应用层 framing；
- Transport MTU 变化导致 AIR framing 必须重新设计；
- 多机组网等需要新的应用层寻址体系。

---

## 4. 帧类型

| type | 名称 | 长度 | 发送阶段 |
|---:|---|---:|---|
| `0x10` | `FLIGHT_STATE` | 50 | START 后，5 Hz |
| `0x11` | `PREFLIGHT_STATE` | 26 | START 前，5 Hz |
| `0x12` | `CAPABILITY` | 9 | START 前且未 ACK，立即一次 + 1 Hz |
| `0x13` | `PREFLIGHT_STATUS` | 9 | Capability ACK 后至 START |
| `0x14` | `SENSOR_STATUS` | 9 | Alignment 事务结束时组成一次 snapshot |
| `0x20` | `STATUS` | 9 | 边沿事件 |
| `0x30` | `CMD` | 9 | GS→FC |
| `0x40` | `ACK` | 9 | FC→GS command response |

---

## 5. CAPABILITY（`0x12`，9 bytes）

| offset | size | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 1 | `u8` | `type = 0x12` |
| 1 | 1 | `u8` | `seq` |
| 2 | 1 | `u8` | `air_profile_id = 0` |
| 3 | 1 | `u8` | `command_policy` |
| 4 | 1 | `u8` | `calibration_mode_mask` |
| 5 | 1 | `u8` | `sensor_summary_flags` |
| 6 | 1 | `u8` | `accel_full_scale_g` |
| 7 | 2 | `u16` | `gyro_full_scale_dps` |

### 5.1 command_policy

| 值 | 名称 | 语义 |
|---:|---|---|
| `1` | `PREFLIGHT_ONLY` | START 前允许命令；START 后不解析、不执行、不 ACK |
| `2` | `MISSION_ALLOWED` | Profile 允许任务中命令，具体命令仍受系统策略限制 |

### 5.2 calibration_mode_mask

```text
bit0 = NONE
bit1 = ONE_FACE
bit2 = SIX_FACE
bit3..7 = reserved
```

该字段由FCCG build动态生成：

| FCCG采样procedure | mask |
|---|---:|
| 无 | `0x01` |
| OneFace | `0x03` |
| SixFace | `0x05` |
| OneFace+SixFace | `0x07` |

NONE始终属于合法Calibration mode。无采样procedure的build启动时自动NONE/READY；build含采样procedure时，GSHC仍可用现有`CAL_START(NONE)`显式选择默认/单位校正。详细跨组件行为见 [AIR_CALIBRATION_CONTRACT.md](AIR_CALIBRATION_CONTRACT.md)。

未知bit3..7仅用于诊断，不生成未知操作，也不单独导致profile拒绝。

### 5.3 sensor_summary_flags

```text
bit0 = IMU_PRESENT
bit1 = GNSS_PRESENT
bit2 = AUX_SENSOR_PRESENT
bit3 = SENSOR_STATUS_SNAPSHOT_SUPPORTED
bit4..7 = reserved
```

`IMU_PRESENT`：Target启用了canonical IMU Interface。
`GNSS_PRESENT`：Target启用了GNSS Interface。
`AUX_SENSOR_PRESENT`：至少存在一个不属于 IMU/GNSS 的 sensor class。
`SENSOR_STATUS_SNAPSHOT_SUPPORTED`：AIR M0 当前定义固定为 1。

该字段不枚举具体辅助传感器；具体传感器通过 `SENSOR_STATUS.sensor_id` 表示。

---

## 6. Capability handshake

```text
NOT_ACKED
    ↓
ACKED
    ↓
DISABLED_FOR_FLIGHT
```

进入 PREFLIGHT 后立即发送 `CAPABILITY`；未 ACK 时 1 Hz 重发。

`CAPABILITY_ACK.param0` = 最近一次成功发送的 Capability seq。  
`CAPABILITY_ACK.param1` = `AIR_PROFILE_COMPACT_V0 = 0`。

错误 seq/profile → `BAD_PARAM`。  
已经 ACK → `BAD_STATE`。

成功 ACK 后：

- 停止 Capability 广播；
- 开始 `PREFLIGHT_STATUS`；
- 允许发送 pending Alignment sensor snapshot；
- 继续 `PREFLIGHT_STATE`。

START 后进入 `DISABLED_FOR_FLIGHT`。

未 ACK 时只允许 `PING` 与 `CAPABILITY_ACK`；其他命令返回 `CAPABILITY_REQUIRED`。

---

## 7. PREFLIGHT_STATUS（`0x13`，9 bytes）

| byte | 字段 | 编码 |
|---:|---|---|
| 0 | `type` | `0x13` |
| 1 | `seq` | tx sequence |
| 2 | `lifecycle_state` | Lifecycle |
| 3 | Calibration | low4=`state`，high4=`mode` |
| 4 | `completed_face_mask` | X+/X-/Y+/Y-/Z+/Z- |
| 5 | `current_face` | 0..5，`0xFF=none` |
| 6 | Alignment | low4=`alignment_state`，high4=reserved |
| 7 | flags | 见 7.2 |
| 8 | `start_block_reason` | 见 7.3 |

AIR M0 不再把 ATTITUDE ready、GNSS origin ready、BARO origin ready 写死为固定 Alignment source bits。

具体 sensor/source detail 由 `SENSOR_STATUS` 和飞控串口详细诊断提供。

### 7.1 状态枚举

Lifecycle：

| 值 | 状态 |
|---:|---|
| 0 | `BOOT` |
| 1 | `SELF_TEST` |
| 2 | `PREFLIGHT` |
| 3 | `READY` |
| 4 | `FLIGHT` |
| 5 | `RECOVERY` |
| 6 | `LANDED` |
| 7 | `POSTFLIGHT` |
| 8 | `FAULT` |

Calibration：0=`IDLE`、1=`WAIT_FACE`、2=`COLLECTING`、3=`CHECKING`、4=`READY`、5=`FAILED`。  
Calibration Mode：0=`NONE`、1=`ONE_FACE`、2=`SIX_FACE`；本帧 high4 中 `0xF` 解码为 `NOT_SELECTED=0xFF`，STATUS 的完整 mode 字节直接使用 `0xFF`。

Alignment：0=`IDLE`、1=`COLLECTING`、2=`CHECKING`、3=`READY`、4=`FAILED`、5=`STALE`。

### 7.2 flags

| bit | 名称 |
|---:|---|
| 0 | `system_ready` |
| 1 | `start_unlocked` |
| 2 | `selftest_passed` |
| 3 | `gnss_position_usable` |
| 4 | `capability_acked` |
| 5 | `calibration_ready` |
| 6 | `alignment_ready` |
| 7 | reserved |

GNSS 保持独立 dynamic status，因为它可能动态获得/失去定位，是任务中的特殊 navigation measurement。

### 7.3 start_block_reason

| 值 | 原因 |
|---:|---|
| `0x00` | `OK/NONE` |
| `0x04` | `BUSY` |
| `0x07` | `LOCKED_REQUIRED` |
| `0x0A` | `CAPABILITY_REQUIRED` |
| `0x0B` | `CALIBRATION_REQUIRED` |
| `0x0C` | `ALIGNMENT_REQUIRED` |
| `0x0D` | `SYSTEM_NOT_READY` |
| `0x0E` | `ATTITUDE_NOT_READY` |
| `0x0F` | `ATTITUDE_INVALID` |
| `0x10` | `ATTITUDE_STALE` |
| `0x11` | `ORIGIN_FAILED` |
| `0x12` | `NAVIGATION_FAILED` |
| `0x13` | `QUEUE_FAILED` |
| `0x15` | `HOOKS_UNAVAILABLE` |
| `0x16` | `PREPARE_FAILED` |

---

## 8. SENSOR_STATUS（`0x14`，9 bytes）

`SENSOR_STATUS` 是 AIR M0 的通用 sensor snapshot frame，用于描述一个逻辑 sensor class / instance 的状态，而不是具体芯片型号。

System Sensor Status从Generated Capability Endpoint Descriptor生成当前条目，使用descriptor的`device_class + instance_id`，再通过静态instance facade读取健康和样本。排序仍为IMU优先、GNSS其次、其余按Sensor ID和instance。一个JY901B可通过同一Device组件暴露IMU、BAROMETER、EXTERNAL_ATTITUDE，以及显式启用时的MAGNETOMETER逻辑sensor class；Target未启用的逻辑端点不出现在snapshot中。

`sensor_id + instance_id`已经足以轮询未来同类多个能力实例；Physical Device ID不进入AIR M0。多个能力端点可以在本地descriptor中链接到同一物理设备，但AIR只报告逻辑sensor状态。

### 8.1 Frame layout

| offset | size | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 1 | `u8` | `type = 0x14` |
| 1 | 1 | `u8` | frame `seq` |
| 2 | 1 | `u8` | `snapshot_id` |
| 3 | 1 | `u8` | `sensor_id` |
| 4 | 1 | `u8` | `instance_id` |
| 5 | 1 | `u8` | `status_flags` |
| 6 | 1 | `u8` | `detail_code` |
| 7 | 1 | `u8` | `index` |
| 8 | 1 | `u8` | `total` |

---

## 9. Sensor ID表

`0x00 = INVALID / NONE`

| ID | Sensor Class |
|---:|---|
| `0x01` | `IMU` |
| `0x02` | `GNSS` |
| `0x03` | `BAROMETER` |
| `0x04` | `MAGNETOMETER` |
| `0x05` | `AIR_DATA` |
| `0x06` | `RANGEFINDER` |
| `0x07` | `RADAR_ALTIMETER` |
| `0x08` | `SUN_SENSOR` |
| `0x09` | `STAR_TRACKER` |
| `0x0A` | `VISION` |
| `0x0B` | `EXTERNAL_ATTITUDE` |
| `0x0C` | `DUAL_GNSS_HEADING` |
| `0x0D` | `TEMPERATURE` |
| `0x0E` | `HUMIDITY` |

增加 sensor ID 不改变 AIR M0。

Ground Station 不认识 ID 时显示 `Unknown Sensor 0xNN`，同时继续解析 instance、flags 和 detail code。

`instance_id` 支持同一类多个设备。

---

## 10. SENSOR_STATUS status_flags

| bit | 名称 | 语义 |
|---:|---|---|
| 0 | `REGISTERED` | sensor已由Target启用并存在；wire名称兼容保留 |
| 1 | `INITIALIZED` | 初始化完成 |
| 2 | `ONLINE` | 当前 online |
| 3 | `HEALTHY` | health check 正常 |
| 4 | `DATA_VALID` | 当前存在有效数据 |
| 5 | `CALIBRATION_OK` | 所需校准已完成，或该 sensor 不要求校准 |
| 6 | `ALIGNMENT_USED` | 当前 Alignment transaction 使用该 sensor |
| 7 | `REQUIRED_FOR_START` | 当前 profile 下 START 必需 |

---

## 11. SENSOR_STATUS detail_code

| 值 | 名称 |
|---:|---|
| `0x00` | `NONE/OK` |
| `0x01` | `NOT_REGISTERED` |
| `0x02` | `INIT_FAILED` |
| `0x03` | `OFFLINE` |
| `0x04` | `UNHEALTHY` |
| `0x05` | `NO_VALID_DATA` |
| `0x06` | `CALIBRATION_REQUIRED` |
| `0x07` | `ALIGNMENT_INPUT_INVALID` |
| `0x08` | `IO_ERROR` |
| `0x09` | `CONFIG_ERROR` |
| `0x0A` | `UNSUPPORTED` |
| `0xFF` | `OTHER` |

---

## 12. Sensor snapshot

Sensor status 不周期广播。

AIR M0 不定义 `SENSOR_STATUS_REQUEST` 或 `DEVICE_INFO_REQUEST` 命令。

Sensor snapshot 只在 Alignment 事务结束时自动生成。

### 12.1 snapshot_id

每次新的 sensor snapshot：`snapshot_id++`，`u8` 自然回绕。

### 12.2 Sensor ordering

固定顺序：

1. IMU
2. GNSS
3. remaining sensors

其余 sensor 按 `sensor_id`、`instance_id` 升序发送。

### 12.3 index / total

`index` 为 0-based，`total` 为本 snapshot 总 SENSOR_STATUS frame 数。

Ground Station 可以通过 `received unique indices != total` 判断 snapshot 不完整。

---

## 13. Alignment 与 Sensor snapshot 的发送事务

当 Alignment 进入 `READY` 或 `FAILED` 时：

1. 冻结当前 sensor status snapshot；
2. 分配新的 `snapshot_id`；
3. 按顺序发送所有 `SENSOR_STATUS`；
4. 最后一帧后发送 `STATUS ALIGNMENT`；
5. `STATUS ALIGNMENT` 是此次 snapshot 的逻辑终止标志。

### 13.1 READY

```text
SENSOR_STATUS IMU
SENSOR_STATUS GNSS
SENSOR_STATUS ...
STATUS ALIGNMENT READY
```

### 13.2 FAILED

同样发送 sensor snapshot，最后发送 `STATUS ALIGNMENT FAILED`。

### 13.3 STALE

STALE 不重新生成 snapshot：

```text
STATUS ALIGNMENT
arg0 = STALE
arg1 = 0xFF
```

### 13.4 Capability ACK 前已完成 Alignment

保存 pending snapshot。Capability ACK 后在第一个安全发送机会发送完整 snapshot，再发送 terminal `STATUS ALIGNMENT`。

---

## 14. STATUS（`0x20`，9 bytes）

| offset | 字段 |
|---:|---|
| 0 | `type = 0x20` |
| 1 | `seq` |
| 2 | `status_id` |
| 3..6 | `time_ms u32` |
| 7 | `arg0` |
| 8 | `arg1` |

| ID | 名称 | arg0 | arg1 |
|---:|---|---|---|
| `0x01` | `BOOT` | reserved | reserved |
| `0x02` | `SELFTEST_COMPLETE` | `mission_capable` | 0 |
| `0x03` | `MISSION_START` | 0 | 0 |
| `0x04` | `LAUNCH` | reserved | reserved |
| `0x05` | `PARACHUTE_DEPLOY` | reserved | reserved |
| `0x06` | `LANDING` | reserved | reserved |
| `0x07` | `LOCKED` | 0 | 0 |
| `0x08` | `UNLOCKED` | 0 | 0 |
| `0x09` | `GNSS_POSITION` | 0/1 | 0 |
| `0x0A` | `ALIGNMENT` | Alignment state | snapshot ID / `0xFF` |
| `0x0B` | `CALIBRATION` | Calibration state | Calibration mode |
| `0x0C` | `CALIBRATION_FACE` | face | pass/fail |
| `0x0D` | `CALIBRATION_DIAGNOSTIC` | face | diagnostic reason |

---

## 15. ALIGNMENT STATUS

对于 `READY/FAILED` 且此次事件前存在 sensor snapshot：

```text
arg1 = snapshot_id
```

对于 `STALE` 或没有新 snapshot 的其他 Alignment event：

```text
arg1 = 0xFF
```

Ground Station 使用该字段关联 `SENSOR_STATUS.snapshot_id`。

---

## 16. Sensor snapshot packet loss

Sensor snapshot 是辅助详情，不影响 Alignment READY、START permission 或 flight control。

Ground Station 根据 `snapshot_id/index/total` 检测丢包。

如果 terminal ALIGNMENT STATUS 已收到，但 unique indices 少于 total，则显示：

```text
Sensor snapshot incomplete
```

不定义额外重发命令。

---

## 17. Calibration diagnostic reason

| 值 | 名称 |
|---:|---|
| `0x00` | `NONE` |
| `0x01` | `NO_STREAM` |
| `0x02` | `GYRO_MOVING` |
| `0x03` | `ACCEL_MAGNITUDE` |
| `0x04` | `GRAVITY_DIRECTION` |
| `0x05` | `VARIANCE` |
| `0x06` | `SAMPLE_GAP` |

只在 reason 变化时发送。恢复到 NONE 时发送一次用于清除旧提示。

---

## 18. PREFLIGHT_STATE（`0x11`，26 bytes）

| offset | size | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 1 | `u8` | `type=0x11` |
| 1 | 1 | `u8` | `seq` |
| 2 | 4 | `u32` | `boot_time_ms` |
| 6/8/10 | each 2 | `i16` | corrected `ax/ay/az` |
| 12/14/16 | each 2 | `i16` | corrected `gx/gy/gz` |
| 18/20/22/24 | each 2 | `i16` | WXYZ quaternion Q15 |

START 前 5 Hz；START 后永久停止。

---

## 19. PREFLIGHT quaternion authority

Alignment 尚未获得有效 final attitude：使用 hardware quaternion。

Alignment final attitude 有效：使用 final alignment `q_nb`。

Alignment 进入 STALE 或 final attitude invalid：立即回退 hardware quaternion。

重新 Alignment 成功：切换到新的 final alignment `q_nb`。

Quaternion authority 在 hardware ↔ final alignment 间变化时，应立即安排一帧 PREFLIGHT_STATE。

---

## 20. START 后 quaternion

START 后：

```text
initial alignment q_nb
+
corrected gyro
→ software quaternion propagation
```

`FLIGHT_STATE.quaternion` 只使用 software propagated `q_nb`。

飞行过程中不得自动切回 hardware quaternion。

---

## 21. IMU 与姿态编码

AIR accel/gyro 来自：

```text
SystemInertial Virtual IMU sample
↓
SystemCalibration correction
↓
AIR quantization
```

Calibration 未 READY 时使用 identity correction。

Acceleration：

```text
STANDARD_GRAVITY = 9.80665 m/s²

acc_i16 =
clamp(round(
    acc_corrected_mps2 /
    (accel_full_scale_g * STANDARD_GRAVITY)
    * 32768
))
```

Angular rate：

```text
gyro_i16 =
clamp(round(
    gyro_corrected_radps /
    (gyro_full_scale_dps * pi / 180)
    * 32768
))
```

Quaternion：

```text
q_nb
body -> ENU
WXYZ
normalized
Q15
```

---

## 22. FLIGHT_STATE（`0x10`，50 bytes）

| offset | size | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 1 | `u8` | `type=0x10` |
| 1 | 1 | `u8` | `seq` |
| 2..25 | 24 | same as PREFLIGHT | time + IMU + quaternion |
| 26 | 4 | `f32` | `velocity_e_mps` |
| 30 | 4 | `f32` | `velocity_n_mps` |
| 34 | 4 | `f32` | `velocity_u_mps` |
| 38 | 4 | `f32` | `position_e_m` |
| 42 | 4 | `f32` | `position_n_m` |
| 46 | 4 | `f32` | `position_u_m` |

START 后 5 Hz。

---

## 23. GNSS 独立动态状态

GNSS 保持主界面独立动态可用性显示。

`PREFLIGHT_STATUS.flags.gnss_position_usable` 是周期权威状态。  
`STATUS GNSS_POSITION` 是边沿事件。

Alignment sensor snapshot 中的 GNSS 状态只表示 Alignment 完成时的设备快照，不能替代运行期 GNSS dynamic status。

---

## 24. CMD（`0x30`，9 bytes）

| offset | 类型 | 字段 |
|---:|---|---|
| 0 | u8 | `type=0x30` |
| 1 | u8 | `seq` |
| 2 | u8 | `cmd_id` |
| 3..6 | u32 little-endian | `token` |
| 7 | u8 | `param0` |
| 8 | u8 | `param1` |

| ID | 命令 | token | param0 | param1 |
|---:|---|---:|---|---|
| `0x01` | `START_MISSION` | `0xA55A3CC3` | 0 | 0 |
| `0x02` | `PING` | no check | 0 | 0 |
| `0x03` | `LOCK` | `0xC33CA55A` | 0 | 0 |
| `0x04` | `UNLOCK` | `0x55AA6996` | 0 | 0 |
| `0x05` | `CAPABILITY_ACK` | no check | Capability seq | `air_profile_id=0` |
| `0x07` | `CAL_START` | `0x43414C30` | mode | 0 |
| `0x08` | `CAL_FACE` | `0x43414C30` | face | 0 |
| `0x09` | `CAL_STOP` | `0x43414C30` | 0 | 0 |
| `0x0A` | `CAL_RESET` | `0x43414C30` | 0 | 0 |
| `0x0B` | `ALIGN_START` | `0x414C4947` | 0 | 0 |
| `0x0C` | `ALIGN_STOP` | `0x414C4947` | 0 | 0 |
| `0x0D` | `ALIGN_RESET` | `0x414C4947` | 0 | 0 |

`0x06` undefined。

AIR M0 不定义 SENSOR_STATUS_REQUEST / DEVICE_INFO_REQUEST。

---

## 25. ACK（`0x40`，9 bytes）

| offset | 类型 | 字段 |
|---:|---|---|
| 0 | u8 | `type=0x40` |
| 1 | u8 | `seq`（飞控发送序号） |
| 2 | u8 | `ack_seq`（被响应命令序号） |
| 3 | u8 | `ack_cmd_id` |
| 4 | u8 | `result` |
| 5..8 | u32 little-endian | `time_ms` |

CAL_START 的 `BAD_PARAM` 表示 mode 编码非法，`REJECTED` 表示合法采样模式未编入 build；`BAD_STATE/BUSY` 保持状态不允许/忙语义。NONE、ONE_FACE、SIX_FACE 的 `OK` 均仅表示接受，完成依据真实飞控状态。跨组件行为以[共同契约](AIR_CALIBRATION_CONTRACT.md)为准。

| 值 | result |
|---:|---|
| `0x00` | `OK` |
| `0x01` | `BAD_LEN` |
| `0x02` | `BAD_CMD` |
| `0x03` | `BAD_TOKEN` |
| `0x04` | `BUSY` |
| `0x05` | `REJECTED` |
| `0x06` | `BAD_STATE` |
| `0x07` | `LOCKED_REQUIRED` |
| `0x08` | `ALREADY_LOCKED` |
| `0x09` | `ALREADY_UNLOCKED` |
| `0x0A` | `CAPABILITY_REQUIRED` |
| `0x0B` | `CALIBRATION_REQUIRED` |
| `0x0C` | `ALIGNMENT_REQUIRED` |
| `0x0D` | `SYSTEM_NOT_READY` |
| `0x0E` | `ATTITUDE_NOT_READY` |
| `0x0F` | `ATTITUDE_INVALID` |
| `0x10` | `ATTITUDE_STALE` |
| `0x11` | `ORIGIN_FAILED` |
| `0x12` | `NAVIGATION_FAILED` |
| `0x13` | `QUEUE_FAILED` |
| `0x14` | `BAD_PARAM` |
| `0x15` | `HOOKS_UNAVAILABLE` |
| `0x16` | `PREPARE_FAILED` |

---

## 26. START dependency

```text
Calibration READY
↓
Alignment READY
↓
START READY
```

Alignment `READY → STALE` 后重新阻止 START，必须显式重新 `ALIGN_START`。

AIR START 额外要求 Capability ACKED 与 interlock UNLOCKED。

GNSS 是 Optional；无预飞 GNSS origin 不阻止 START，本任务可关闭 GNSS fusion。

---

## 27. Alignment READY 后 sensor status burst

```text
ALIGN_START ACK
↓
Alignment COLLECTING
↓
Alignment CHECKING
↓
SENSOR_STATUS IMU
↓
SENSOR_STATUS GNSS
↓
SENSOR_STATUS BAROMETER
↓
SENSOR_STATUS [other enabled sensors...]
↓
STATUS ALIGNMENT READY
```

Ground Station 以 terminal `STATUS ALIGNMENT READY` 判断本次 sensor snapshot 结束。

---

## 28. Ground Station Alignment Details

Ground Station 不通过 AIR command 请求传感器详情。

Alignment 界面缓存最近一次 SENSOR_STATUS snapshot。

主面板显示 Alignment state，并提供 `Sensor Details` 按钮。

按钮只显示本地缓存，不产生无线命令。

显示顺序：

1. IMU
2. GNSS
3. remaining sensors by sensor_id

未知 ID 显示：

```text
Unknown Sensor 0xNN
```

---

## 29. Ground Station 主界面传感器抽象

主 GUI 面向 canonical capability，而不是具体芯片型号。

主页面建议：

```text
Inertial / Attitude
GNSS
Alignment
Flight
Link
```

具体传感器状态由 Alignment → Sensor Details 展示。

---

## 30. Sensor Status 与具体设备型号

AIR M0 不发送具体型号字符串。

只发送：

```text
canonical sensor class
instance
generic status
```

具体Device/model使用System Console INFO、Flight Log或Target配置查看。

---

## 31. 预飞调度优先级

未 Capability ACK：

```text
1. ACK
2. critical STATUS
3. CAPABILITY
4. PREFLIGHT_STATE
```

Capability ACK 后：

```text
1. ACK
2. active Sensor Snapshot transaction
3. corresponding terminal ALIGNMENT STATUS
4. other critical STATUS
5. PREFLIGHT_STATUS
6. PREFLIGHT_STATE
```

Sensor snapshot transaction 期间，PREFLIGHT_STATUS / PREFLIGHT_STATE 不插入 snapshot 中间；ACK 可以抢占。

---

## 32. START 后调度

START 后停止：

```text
CAPABILITY
PREFLIGHT_STATUS
PREFLIGHT_STATE
SENSOR_STATUS
```

继续：

```text
critical STATUS
FLIGHT_STATE
```

FLIGHT_STATE 当前 5 Hz。

---

## 33. STATUS event 与状态 snapshot

`STATUS` 表示“发生了什么”；`PREFLIGHT_STATUS` 表示“现在是什么状态”。

二者同时保留。

---

## 34. Alignment STALE

START 前持续 validity guard。

发生足以使初始状态失效的运动：

```text
ALIGNMENT READY
↓
STALE
```

结果：

```text
alignment_ready = 0
system_ready recalculated
START blocked
```

发送：

```text
STATUS ALIGNMENT
arg0 = STALE
arg1 = 0xFF
```

STALE 不自动恢复，必须显式重新 `ALIGN_START`。

---

## 35. Sensor snapshot 与 Alignment source

`SENSOR_STATUS.ALIGNMENT_USED` 表示当前 Alignment transaction 是否实际使用该 sensor。

Gravity + Known Yaw：IMU 使用，Magnetometer 不使用。  
Gravity + Magnetic TRIAD：IMU 与 Magnetometer 使用。

因此 AIR 不再写死具体 Alignment source bit。

当前默认JY901B构建发送IMU、GNSS、BAROMETER及其他实际启用的sensor；Magnetometer默认未启用，因此不发送其`SENSOR_STATUS`。这只改变snapshot成员集合，不改变`SENSOR_STATUS`的9-byte布局、Sensor ID表或任何AIR M0固定帧。

---

## 36. Calibration 与 Sensor snapshot

`CALIBRATION_OK` 表示该 sensor 当前所需校准已经满足，或该 sensor 不要求独立校准。

Calibration 事务本身仍由 PREFLIGHT_STATUS 和 Calibration STATUS events 表示。

---

## 37. Ground Station/PC session logging

建议记录：

- SESSION/CAPABILITY；
- SENSOR_STATUS snapshots；
- PREFLIGHT_STATUS；
- STATUS；
- PREFLIGHT_STATE；
- FLIGHT_STATE。

Unknown sensor ID 必须保留原值。

---

## 38. 飞控本地日志

本地 LOG 不需要机械记录每次 AIR broadcast。

继续记录 Calibration、Alignment、Sensor health changes、Mission start、Deploy、Landing、Navigation state 等实际状态变化。

---

## 39. M0 扩展原则

本轮Capability Instance Addressing对AIR M0的wire修改为NONE：没有新增message、字段、ID或CRC，没有改变任何帧长度、offset、量化、发送频率或MTU要求，当前golden frame保持逐字节一致。System Sensor Status从Generated descriptor/facade枚举全部启用能力实例，既有9-byte `SENSOR_STATUS.sensor_id + instance_id`可表达instance 1；正式F407默认成员和输出字节不变。AIR继续只承载一份绑定instance 0的Canonical flight state和低频sensor health；不会通过M0发送多个IMU/GNSS的高频原始数据。多实例raw数据属于本地飞行日志和维护协议。

未来新增 STAR_TRACKER、SUN_SENSOR、AIR_DATA 或其他普通传感器，只需：

1. 分配稳定`sensor_id`；
2. 在Target descriptor中静态列出实例；
3. 通过直接Interface提供generic sensor status；
4. Ground Station可选增加友好名称。

不需要改变 AIR M0 固定帧。

---

## 40. AIR M0 冻结目标

AIR M0 wire在0.0.10当前契约内冻结；普通物理设备或能力实例扩展只能复用既有`SENSOR_STATUS.sensor_id + instance_id`，不能顺带调整现有帧。

正式发布后，普通硬件扩展不得要求修改 wire format。

只有 application framing、fragmentation、encryption/authentication framing、多节点寻址、重大 telemetry encoding redesign 等级别变化，才考虑未来新的 Profile。
