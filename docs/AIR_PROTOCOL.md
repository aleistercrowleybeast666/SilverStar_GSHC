# SilverStar AIR 应用层协议

> 文档版本：0.0.8
>
> 当前 AIR Profile：`AIR_PROFILE_COMPACT_V0 = 0`
>
> 适用范围：SilverStar 0.0.8 飞控与地面站实现

## 1. 固定约束

AIR 是飞控与地面站之间的定长二进制应用层协议。多字节整数和 IEEE-754 `float32` 均为 little-endian；每个 `type` 只有一个固定长度，不设置公共可变长头，不使用 TLV。

AIR 应用层不附加 CRC8、CRC16 或 CRC32。当前 SX1281 Transport 使用 LoRa 硬件 CRC 提供空口完整性保护；Parser 检查帧长度、`type`、command/status ID、token、参数和字段合法性。未来 Transport 必须自行提供 CRC 或等效完整性保护，不得静默改变本文固定帧格式。

```c
AIR_PROFILE_COMPACT_V0 = 0U
AIR_MAX_FRAME_LEN = 50U
AIR_PROTOCOL_APPLICATION_CRC_SIZE = 0U
```

SilverStar `0.0.8` 只属于固件信息、构建信息和日志元数据，不编码进 AIR Capability。当前 SX1281 Transport MTU 为 64 字节，必须保持：

```text
所有 AIR 固定帧长度 <= AIR_MAX_FRAME_LEN <= Transport MTU
```

## 2. AIR Profile 的职责

`air_profile_id` 唯一确定整套 AIR 消息集合、字段布局和数值编码。Profile 0 同时定义：

- `CAPABILITY`、`PREFLIGHT_STATE`、`PREFLIGHT_STATUS`、`FLIGHT_STATE`；
- `STATUS` event、`CMD` 和 `ACK`；
- 加速度/角速度的 `int16_t` 线性编码；
- WXYZ 四元数的 `int16_t Q15` 编码；
- ENU 速度和位置的 little-endian `float32` 编码。

以下变化不改变 `air_profile_id`：

- 16 g 改为其他加速度满量程，或 2000 dps 改为其他角速度满量程，因为实际满量程由 Capability 字段声明；
- LoRa SF、BW、CR、发射功率或设备型号变化；
- `command_policy` 从 `PREFLIGHT_ONLY` 改为 `MISSION_ALLOWED`；
- Provider 的可用组合变化，因为能力由 mask 声明。

以下不兼容变化必须分配新的 `air_profile_id`：

- accel/gyro 的数据类型、位宽或线性编码规则变化；
- 四元数从 Q15 改为其他编码；
- 固定帧字段顺序或字段长度变化；
- 为既有固定帧增加导致长度变化的必需字段。

## 3. 帧类型和固定长度

| type | 名称 | 长度 | 发送阶段 |
|---:|---|---:|---|
| `0x10` | `FLIGHT_STATE` | 50 | START 成功后，5 Hz |
| `0x11` | `PREFLIGHT_STATE` | 26 | START 前，5 Hz |
| `0x12` | `CAPABILITY` | 9 | START 前且未 ACK，立即一次、随后 1 Hz |
| `0x13` | `PREFLIGHT_STATUS` | 9 | Capability ACK 后至 START 成功，立即一次、随后 1 Hz |
| `0x20` | `STATUS` | 9 | 边沿事件发生时 |
| `0x30` | `CMD` | 9 | 由 `command_policy` 决定入站阶段 |
| `0x40` | `ACK` | 9 | 响应已解析的入站命令 |

## 4. CAPABILITY（`0x12`，9 字节）

| offset | size | 类型 | 字段 | 当前值/语义 |
|---:|---:|---|---|---|
| 0 | 1 | `u8` | `type` | `0x12` |
| 1 | 1 | `u8` | `seq` | 发送序号 |
| 2 | 1 | `u8` | `air_profile_id` | `0=AIR_PROFILE_COMPACT_V0` |
| 3 | 1 | `u8` | `command_policy` | 当前为 `1=PREFLIGHT_ONLY` |
| 4 | 1 | `u8` | `calibration_mode_mask` | 当前为 `0x07` |
| 5 | 1 | `u8` | `alignment_capability_mask` | 根据当前启动会话动态生成 |
| 6 | 1 | `u8` | `accel_full_scale_g` | 当前为 16 |
| 7 | 2 | `u16` | `gyro_full_scale_dps` | 当前为 2000，little-endian |

`command_policy` 与 AIR Profile 完全独立：

| 值 | 名称 | 语义 |
|---:|---|---|
| 1 | `PREFLIGHT_ONLY` | START 前允许 GS→FC 命令；START 成功后不解析、不执行、不 ACK |
| 2 | `MISSION_ALLOWED` | Profile 允许任务中命令；具体命令权限仍由系统策略检查 |

`AIR_PROFILE_COMPACT_V0` 不隐含 `PREFLIGHT_ONLY`。0.0.8 的配置仍为 `PREFLIGHT_ONLY`，因此实际飞行行为不变。

`calibration_mode_mask` 表示固件支持的模式，而非当前选中模式：bit0=`NONE`、bit1=`ONE_FACE`、bit2=`SIX_FACE`。该值由 SystemCalibration capability API 提供，当前为 `0x07`。

`alignment_capability_mask` 表示当前飞控启动会话具备的来源，而非当前 ready 状态：bit0=`ATTITUDE`、bit1=`GNSS_ORIGIN`、bit2=`BARO_ORIGIN`。它来自SystemAlignment内部32-bit `capability_mask`的低三位；Provider尚无样本或GNSS尚无fix不清除此能力，Provider未注册、缺少所需capability、初始化失败或启动失败时清除对应bit。实时ready状态由`PREFLIGHT_STATUS`表示。内部未来新增MAG等source时不得改变本profile的长度或偏移；需要空口公开新bit时必须分配新profile。

### 4.1 Capability 握手

状态固定为：

```text
NOT_ACKED -> ACKED -> DISABLED_FOR_FLIGHT
```

- 进入 PREFLIGHT 后立即发送 Capability；未确认时每 1 秒重发，`seq` 使用最近一次成功发送的值。
- `CAPABILITY_ACK.param0` 必须等于最近一次成功发送的 Capability `seq`。
- `CAPABILITY_ACK.param1` 必须等于 `air_profile_id`，当前为 0。
- 错误 seq 或 profile 返回 `BAD_PARAM`；已经 ACK 后再次 ACK 返回 `BAD_STATE`。
- 有效 ACK 后永久停止本次上电周期的 Capability 广播，并开始发送 `PREFLIGHT_STATUS`。
- START 成功后进入 `DISABLED_FOR_FLIGHT`，不得恢复握手。
- 一个飞控上电周期只对应一个 Ground Station session。地面站会话异常时应重新启动整个飞控任务。
- 未 ACK 时只允许 `PING` 和 `CAPABILITY_ACK`；其他命令返回 `CAPABILITY_REQUIRED`。

## 5. PREFLIGHT_STATUS（`0x13`，9 字节）

| byte | 字段 | 编码 |
|---:|---|---|
| 0 | `type` | `0x13` |
| 1 | `seq` | 发送序号 |
| 2 | `lifecycle_state` | 见 5.1 |
| 3 | Calibration | bit0..3=`calibration_state`；bit4..7=`calibration_mode` |
| 4 | `completed_face_mask` | bit0..5=`X+ X- Y+ Y- Z+ Z-`；bit6..7=0 |
| 5 | `current_face` | 0..5=`X+ X- Y+ Y- Z+ Z-`；`0xFF=none` |
| 6 | Alignment | bit0..3=`alignment_state`；bit4=attitude ready；bit5=GNSS origin candidate ready；bit6=barometer origin candidate ready；bit7=0 |
| 7 | flags | 见 5.2 |
| 8 | `start_block_reason` | 见 5.3 |

Calibration `NOT_SELECTED=0xFF` 在 byte3 高半字节编码为 `0xF`，Parser 还原为 `0xFF`。其余 mode 直接编码为 0..2。

### 5.1 状态枚举

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

Calibration state：0=`IDLE`、1=`WAIT_FACE`、2=`COLLECTING`、3=`CHECKING`、4=`READY`、5=`FAILED`。Calibration mode：0=`NONE`、1=`ONE_FACE`、2=`SIX_FACE`、`0xFF=NOT_SELECTED`。

Alignment state：0=`IDLE`、1=`COLLECTING`、2=`CHECKING`、3=`READY`、4=`FAILED`。

### 5.2 flags

| bit | 名称 |
|---:|---|
| 0 | `system_ready` |
| 1 | `start_unlocked` |
| 2 | `selftest_passed` |
| 3 | `gnss_position_usable` |
| 4 | `capability_acked` |
| 5 | `calibration_ready` |
| 6 | `alignment_ready` |
| 7 | 保留，必须为 0 |

### 5.3 start_block_reason

该字段表示“此刻通过 AIR 执行 START 时，最优先的阻止原因”。它复用 START ACK result 的数值，不维护第二套枚举：

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

预飞快照和实际 AIR START 使用同一 Lifecycle 预检查及同一 result/reason→AIR 映射。`HOOKS_UNAVAILABLE` 和 `PREPARE_FAILED` 不得降级为模糊的 `REJECTED`。执行期才可能出现的失败由实际 START ACK 返回；其后快照仍报告当前可预检查的首要原因。

### 5.4 发送规则

`PREFLIGHT_STATUS` 只在 Capability ACK 成功后到 START 成功前发送。ACK 后在不打断 ACK 或已排队关键事件的首个安全发送机会发送一次，之后以 1 Hz 发送。Calibration state/face、Alignment state、LOCK、System ready 或 GNSS usable 变化时可提前发送下一帧。它不替代任何 `STATUS` event。

## 6. Profile 0 的 IMU 与姿态编码

AIR accel/gyro 必须来自校准后的机体系物理量，不能透传芯片私有原始计数：

```text
SystemImuProvider raw physical sample
    -> SystemCalibration correction
    -> corrected accel/gyro physical values
    -> AIR int16 quantization
```

INS 和 Telemetry 必须共同调用 `SystemCalibration_ImuCorrectionApply()`：

```text
acc_corrected = (acc_measured - accel_bias) * accel_scale
gyro_corrected = (gyro_measured - gyro_bias) * gyro_scale
```

Calibration 未 READY（包括六面采集中）时，Telemetry 使用 identity correction：bias=0、scale=1，保证校准前仍可观察 `PREFLIGHT_STATE`。Calibration READY 后立即使用正式 correction。Calibration 算法自身始终消费未应用本次 correction 的原始物理量。内部 bias/scale 保持 `float`；`int16_t` 只用于 AIR encoder 的最后一步。

```text
STANDARD_GRAVITY = 9.80665 m/s²
acc_i16 = clamp(round(acc_corrected_mps2 /
                      (accel_full_scale_g * STANDARD_GRAVITY) * 32768))
gyro_i16 = clamp(round(gyro_corrected_radps /
                       (gyro_full_scale_dps * pi / 180) * 32768))
```

超量程饱和到 `INT16_MIN..INT16_MAX`。四元数为归一化 WXYZ Q15：`clamp(round(q*32768))`，`+1` 编码为 32767，`-1` 编码为 -32768。速度/位置保持 little-endian `float32`。

## 7. PREFLIGHT_STATE（`0x11`，26 字节）

| offset | size | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 1 | `u8` | `type=0x11` |
| 1 | 1 | `u8` | `seq` |
| 2 | 4 | `u32` | `boot_time_ms` |
| 6/8/10 | 各 2 | `i16` | `ax/ay/az`，校准后机体系加速度 |
| 12/14/16 | 各 2 | `i16` | `gx/gy/gz`，校准后机体系角速度 |
| 18/20/22/24 | 各 2 | `i16` | `qw/qx/qy/qz`，WXYZ Q15 |

START 前以 5 Hz 发送，START 成功后永久停止。

## 8. FLIGHT_STATE（`0x10`，50 字节）

| offset | size | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 1 | `u8` | `type=0x10` |
| 1 | 1 | `u8` | `seq` |
| 2..25 | 24 | 同第 7 节 | 时间、校准后 IMU、Q15 四元数 |
| 26/30/34 | 各 4 | `f32` | `velocity_e/n/u_mps` |
| 38/42/46 | 各 4 | `f32` | `position_e/n/u_m` |

START 成功后以 5 Hz 发送。START 前不发送。

## 9. STATUS（`0x20`，9 字节）

布局：byte0=`type`、byte1=`seq`、byte2=`status_id`、byte3..6=`time_ms u32`、byte7=`arg0`、byte8=`arg1`。

| ID | 名称 | `arg0` | `arg1` |
|---:|---|---|---|
| `0x01` | `BOOT` | 保留 | 保留 |
| `0x02` | `SELFTEST_COMPLETE` | `mission_capable` | 0 |
| `0x03` | `MISSION_START` | 0 | 0 |
| `0x04` | `LAUNCH` | 保留 | 保留 |
| `0x05` | `PARACHUTE_DEPLOY` | 保留 | 保留 |
| `0x06` | `LANDING` | 保留 | 保留 |
| `0x07` | `LOCKED` | 0 | 0 |
| `0x08` | `UNLOCKED` | 0 | 0 |
| `0x09` | `GNSS_POSITION` | 0/1 | 0 |
| `0x0A` | `ALIGNMENT` | Alignment state | ready mask |
| `0x0B` | `CALIBRATION` | Calibration state | Calibration mode |
| `0x0C` | `CALIBRATION_FACE` | face 0..5 | 0=`FAILED`、1=`PASSED` |

预飞控制命令的`ACK=OK`只表示请求已被飞控接受，不表示异步Calibration/Alignment已经完成。`CALIBRATION_FACE`的`PASSED`只在该面完整采样和检查通过后产生；`CALIBRATION`和`ALIGNMENT`进入READY/FAILED时表示对应事务最终结果。`PREFLIGHT_STATUS`是当前状态的周期权威快照，用于恢复可能丢失的边沿事件。

这些是边沿事件：表示“刚才发生了什么”。`PREFLIGHT_STATUS` 是状态快照：表示“现在是什么状态”。两者必须同时保留。

## 10. CMD（`0x30`，9 字节）

布局：byte0=`type`、byte1=`seq`、byte2=`cmd_id`、byte3..6=`token u32`、byte7=`param0`、byte8=`param1`。

| ID | 命令 | token | `param0` | `param1` |
|---:|---|---:|---|---|
| `0x01` | `START_MISSION` | `0xA55A3CC3` | 0 | 0 |
| `0x02` | `PING` | 不检查 | 0 | 0 |
| `0x03` | `LOCK` | `0xC33CA55A` | 0 | 0 |
| `0x04` | `UNLOCK` | `0x55AA6996` | 0 | 0 |
| `0x05` | `CAPABILITY_ACK` | 不检查 | 最近成功 Capability seq | `air_profile_id=0` |
| `0x07` | `CAL_START` | `0x43414C30` | mode 0..2 | 0 |
| `0x08` | `CAL_FACE` | `0x43414C30` | face 0..5 | 0 |
| `0x09` | `CAL_STOP` | `0x43414C30` | 0 | 0 |
| `0x0A` | `CAL_RESET` | `0x43414C30` | 0 | 0 |
| `0x0B` | `ALIGN_START` | `0x414C4947` | 0 | 0 |
| `0x0C` | `ALIGN_STOP` | `0x414C4947` | 0 | 0 |
| `0x0D` | `ALIGN_RESET` | `0x414C4947` | 0 | 0 |

`0x06` 未定义，必须按未知命令处理。相同 `seq+cmd_id` 的重发返回 ACK cache 的原结果，不重复执行副作用。

## 11. ACK（`0x40`，9 字节）

布局：byte0=`type`、byte1=ACK 帧 `seq`、byte2=被响应 command `seq`、byte3=被响应 `cmd_id`、byte4=`result`、byte5..8=`time_ms u32`。

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

当 `result=BAD_CMD` 时，byte3 必须原样回显收到的未知 command byte；其他 result 仍要求 byte3 是已定义命令。ACK 长度始终为 9 字节。

## 12. START、命令策略与调度

所有 START 的核心依赖固定为：

```text
Calibration READY -> Alignment READY -> START READY
```

AIR START 额外要求 Capability ACKED 和 interlock UNLOCKED。GNSS 是 Optional：无预飞 GNSS origin 不阻止 START，但本次任务不启用 GNSS 融合。

0.0.8 的 START 前发送优先级：

```text
1. ACK
2. critical STATUS event
3. Capability（未 ACK 时）
4. PREFLIGHT_STATUS（ACK 后）
5. PREFLIGHT_STATE
```

START 成功后：Capability、`PREFLIGHT_STATUS`、`PREFLIGHT_STATE` 永久停止；关键 `STATUS` 优先于 5 Hz `FLIGHT_STATE`。当前 `command_policy=PREFLIGHT_ONLY`，因此 Transport 可继续排空 RX，但应用层不解析、不执行、不 ACK 入站命令。该行为由 `command_policy` 决定，不由 profile 0 决定。

典型空口组合：握手前为 Capability 1 Hz + `PREFLIGHT_STATE` 5 Hz；握手后为 `PREFLIGHT_STATUS` 1 Hz + `PREFLIGHT_STATE` 5 Hz；START 后为 `FLIGHT_STATE` 5 Hz。

## 13. Ground Station/PC 记录建议

本轮不修改 PC 工程。后续 consumer 建议分别记录：

- `SESSION/CAPABILITY`：每个会话至少保存一次，用于解释该会话全部 AIR 帧；
- `PREFLIGHT_STATUS snapshots`：每个收到的快照均可记录；
- `STATUS EVENT`：全部记录，不能被快照替代；
- `PREFLIGHT_STATE` 和 `FLIGHT_STATE`：记录解析后的物理量，并同时保留 profile 与量程上下文。

飞控本地 TF/LOG 不需要机械保存每个 1 Hz `PREFLIGHT_STATUS` 广播；继续记录实际 Calibration、Alignment 和 System 状态变化事件。
