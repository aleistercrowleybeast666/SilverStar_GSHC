# SilverStar AIR 应用层协议

> 协议版本：V0  
> 当前 AIR Profile：`AIR_PROFILE_COMPACT_V0 = 0`  
> 适用范围：SilverStar 飞控与地面站  
> 状态：尚未正式发布；正式发布前协议版本与 `air_profile_id` 均保持 V0 / 0，可继续调整 V0 内部定义

## 1. 固定原则

AIR 是飞控与地面站之间的定长二进制应用层协议。

所有多字节整数和 IEEE-754 `float32` 均为 little-endian。

每个 `type` 对应唯一固定帧长度：

- 不使用公共可变长头；
- 不使用 TLV；
- 不在已有固定帧尾部随意增加字段；
- 不因增加普通传感器而改变既有固定帧；
- 不因更换具体传感器型号而改变 AIR Profile。

AIR 应用层不附加 CRC8/CRC16/CRC32。

当前 SX1281 Transport 使用 LoRa hardware CRC 提供空口完整性保护。未来如果更换 Transport，新 Transport 必须自行提供 CRC 或等效完整性保护，不得静默改变 AIR V0 的固定帧布局。

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

## 2. AIR V0 设计目标

AIR V0 将飞行状态、传感器类别、具体传感器型号、Alignment 算法与 Transport 实现解耦。

AIR 周期主链只关心长期稳定的 canonical information：

- IMU / attitude；
- GNSS；
- system state；
- calibration；
- alignment；
- flight state。

普通传感器使用通用 `SENSOR_STATUS` 描述。未来新增 barometer、magnetometer、air-data sensor、rangefinder、radar altimeter、sun sensor、star tracker、vision sensor、external attitude sensor 等，不得要求修改固定 AIR V0 frame layout。

Ground Station 遇到尚未认识的 sensor ID 时应显示 `Unknown Sensor 0xNN`，而不是拒绝整个会话。

由于当前协议尚未正式发布，V0 定义仍可调整；正式发布后再冻结 V0 wire layout。

---

## 3. 什么情况下需要新的 AIR Profile

正式发布后，以下变化不改变 `air_profile_id`：

- IMU / GNSS / Barometer / Magnetometer 型号变化；
- 新增普通传感器 class 或 sensor ID；
- Provider 组合变化；
- Alignment / Calibration 算法变化；
- accel / gyro 实际 full scale 变化；
- LoRa SF / BW / CR / TxPower 变化；
- Device Registry 变化；
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

该字段是 build 能力，兼容 FCCG 0.0.10 的动态组合，不表示当前校准模式或校准完成：

| mask | build 能力 | GSHC 用户可启动采样流程 |
|---|---|---|
| `0x01` | NONE / identity correction | 无 |
| `0x03` | NONE + ONE_FACE | ONE_FACE |
| `0x05` | NONE + SIX_FACE | SIX_FACE |
| `0x07` | NONE + ONE_FACE + SIX_FACE | ONE_FACE、SIX_FACE |

NONE bit 在 parser/raw/日志中保留。GSHC 不提供或发送 `CAL_START(NONE)`；0x01 工程禁用开始采样入口，仍保留既有 `CAL_RESET`。未知 bit3..7 不导致 Capability 拒绝，只在诊断中显示，已知位正常使用。

成功握手后按最新接受的 mask 重建操作；已握手会话中的迟到 Capability 沿用既有诊断策略，不自动重建会话。切换飞控需 PC 断开/重连。此兼容改动不更改 Profile、帧长度、布局、字节序、CRC、命令 ID 或握手延时，也不增加查询校准模式命令或 `.ssdecoder` 依赖。

### 5.3 sensor_summary_flags

```text
bit0 = IMU_PRESENT
bit1 = GNSS_PRESENT
bit2 = AUX_SENSOR_PRESENT
bit3 = SENSOR_STATUS_SNAPSHOT_SUPPORTED
bit4..7 = reserved
```

`IMU_PRESENT`：至少有一个 canonical IMU provider。  
`GNSS_PRESENT`：至少有一个 GNSS provider。  
`AUX_SENSOR_PRESENT`：至少存在一个不属于 IMU/GNSS 的 sensor class。  
`SENSOR_STATUS_SNAPSHOT_SUPPORTED`：AIR V0 当前定义固定为 1。

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

AIR V0 不再把 ATTITUDE ready、GNSS origin ready、BARO origin ready 写死为固定 Alignment source bits。

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
Calibration Mode：0=`NONE`、1=`ONE_FACE`、2=`SIX_FACE`、`0xFF=NOT_SELECTED`。  
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

Calibration current mode/state/calibration_ready 来自本帧或现有 Calibration STATUS，不能由 Capability mask 推断。NONE + ready=1 表示有效单位校正（未执行单面/六面采样校准）；NONE + ready=0 仍表示未就绪，上位机不自动发送 CAL_START 或 ALIGN_START。

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

`SENSOR_STATUS` 是 AIR V0 的通用 sensor snapshot frame，用于描述一个逻辑 sensor class / instance 的状态，而不是具体芯片型号。

例如一个 JY901B 可以在 System 层暴露 IMU、BAROMETER、MAGNETOMETER 三个逻辑 sensor class。

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

## 9. Sensor ID Registry

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

增加 sensor ID 不改变 AIR V0。

Ground Station 不认识 ID 时显示 `Unknown Sensor 0xNN`，同时继续解析 instance、flags 和 detail code。

`instance_id` 支持同一类多个设备。

---

## 10. SENSOR_STATUS status_flags

| bit | 名称 | 语义 |
|---:|---|---|
| 0 | `REGISTERED` | sensor/provider 已存在 |
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

AIR V0 不定义 `SENSOR_STATUS_REQUEST` 或 `DEVICE_INFO_REQUEST` 命令。

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
SystemImuProvider physical sample
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

`CAL_START` wire mode 编码保持不变；GSHC 发送前必须在 Controller 校验握手完成、mode 为 ONE_FACE/SIX_FACE 且对应 Capability bit 已置位。通用入口与 retry 同样执行该门禁。`CAPABILITY_ACK` 继续使用最新 Capability seq 与 air_profile_id。`CAL_RESET` 和 `ALIGN_START` 仍使用上表原始字节。

`0x06` undefined。

AIR V0 不定义 SENSOR_STATUS_REQUEST / DEVICE_INFO_REQUEST。

---

## 25. ACK（`0x40`，9 bytes）

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

CAL_START / ALIGN_START 的 ACK OK 只代表命令被接受。Calibration 完成由后续 state/ready 确认；Alignment ready 保持由 PREFLIGHT_STATUS 确认。CAL_START BAD_PARAM 表示 build 不支持或参数错误，不自动回退其他模式；BAD_STATE/BUSY 不意味着链路断开。普通命令 ACK timeout 使用既有有界 retry，结束 pending 后不清除 Capability。

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
SENSOR_STATUS MAGNETOMETER
↓
...
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

AIR V0 不发送具体型号字符串。

只发送：

```text
canonical sensor class
instance
generic status
```

具体 Provider/model 使用 System Console INFO、Flight Log 或工程配置查看。

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

## 39. V0 扩展原则

未来新增 STAR_TRACKER、SUN_SENSOR、AIR_DATA 或其他普通传感器，只需：

1. System Device Registry 注册；
2. 分配 sensor_id；
3. 提供 generic sensor status；
4. Ground Station 可选增加友好名称。

不需要改变 AIR V0 固定帧。

---

## 40. AIR V0 冻结目标

AIR V0 当前尚未正式发布，因此仍可调整。

正式发布后，普通硬件扩展不得要求修改 wire format。

只有 application framing、fragmentation、encryption/authentication framing、多节点寻址、重大 telemetry encoding redesign 等级别变化，才考虑未来新的 Profile。
