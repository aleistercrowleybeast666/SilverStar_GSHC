# 二代飞控天地通信协议 V0.6

## 1. 范围与原则

本文件定义飞控端、地面站、Python 上位机三者之间的 AIR 空中应用层协议，以及 START 前/START 后的链路调度策略。

实现基准仍为 `Protocol/Inc/air_protocol.h` 与 `Protocol/Src/air_protocol.c`。本轮变更只新增短四元数遥测帧，不修改既有帧类型、固定长度、字段偏移、token 或 ACK result 枚举。

底层运行在 E28-2G4M12SX / SX1281 LoRa payload 内。所有多字节整数使用 little-endian。LoRa PHY 启用 CRC；AIR 应用层帧暂不增加独立 CRC。

## 2. LoRa 当前飞行配置

默认飞行链路配置：

| 项目 | 值 |
|---|---|
| 频率 | 2.473 GHz |
| 发射功率 | 12 dBm |
| Spreading Factor | SF10 |
| Bandwidth | 812.5 kHz |
| Coding Rate | CR 4/5 |
| Preamble | 16 symbols |
| Header | Explicit / Variable Length |
| PHY CRC | ON |
| IQ | Normal |
| 完整遥测周期 | 200 ms / 5 Hz |
| 完整遥测长度 | 50 bytes |

说明：

- 该配置用于“START 后 50B 完整遥测 5Hz”。
- START 前不主动发送 50B 完整遥测，避免上行命令被飞控遥测占用。
- 遥测帧是状态量，不允许旧帧排队堆积；如果发送忙，只保留最新状态或跳过本周期。
- ACK 与关键 STATUS 必须高优先级发送。

## 3. AIR 帧公共规则

| 项目 | 定义 |
|---|---|
| 最大应用帧 | 50 bytes |
| 最大 LoRa payload | 64 bytes |
| 序号 | u8，自然回绕 |
| 字节序 | little-endian |
| 帧边界 | LoRa packet payload |
| 应用层 CRC | 无；依赖 LoRa CRC + type + length + token/seq 检查 |

帧首字节决定类型和固定长度：

| 类型 | 值 | 固定长度 | 方向 | 用途 |
|---|---:|---:|---|---|
| `AIR_TYPE_FLIGHT_STATE` | `0x10` | 50 | 飞控 → 地面 | START 后 5Hz 完整遥测 |
| `AIR_TYPE_QUAT_STATE` | `0x11` | 14 | 飞控 → 地面 | START 前可选短四元数遥测 |
| `AIR_TYPE_STATUS` | `0x20` | 9 | 飞控 → 地面 | 关键状态/事件 |
| `AIR_TYPE_CMD` | `0x30` | 9 | 地面 → 飞控 | 地面命令 |
| `AIR_TYPE_ACK` | `0x40` | 9 | 飞控 → 地面 | 命令应答 |

## 4. 链路阶段策略

### 4.1 PRESTART_CMD 阶段

默认上电后进入 PRESTART_CMD：

- 飞控保持 RX continuous，优先接收地面站命令。
- 不主动发送 `AIR_TYPE_FLIGHT_STATE` 50B 完整遥测。
- 允许发送：
  - `AIR_TYPE_ACK`
  - `AIR_TYPE_STATUS`
  - 可选 `AIR_TYPE_QUAT_STATE` 短四元数包，用于地面确认 IMU 姿态是否正常。
- 如果启用短四元数包，推荐 2–5Hz，且必须低优先级；ACK/STATUS 优先。

推荐宏：

```c
#define FC_RADIO_PRESTART_FULL_TELEM_ENABLE      0U
#define FC_RADIO_PRESTART_QUAT_TELEM_ENABLE      1U
#define FC_RADIO_PRESTART_QUAT_PERIOD_MS         200U
```

如果需要“START 前完全静默”，把 `FC_RADIO_PRESTART_QUAT_TELEM_ENABLE` 设为 0。

### 4.2 START 转换

地面站发送 `AIR_CMD_START_MISSION` 后：

1. 飞控收到合法 START 后必须回复 `AIR_ACK_RESULT_OK`。
2. 飞控进入 mission test / started 状态。
3. 飞控发送 `AIR_STATUS_MISSION_START`，建议重复 3 次，间隔 50 ms。
4. 飞控停止短四元数包，开始 5Hz `AIR_TYPE_FLIGHT_STATE` 完整遥测。
5. 地面站收到 START ACK OK 后进入 flight receive mode，主要接收下行遥测。

### 4.3 FLIGHT_TELEM 阶段

START 后进入 FLIGHT_TELEM：

- 飞控发送 5Hz `AIR_TYPE_FLIGHT_STATE` 完整遥测。
- 飞控继续发送关键 `AIR_TYPE_STATUS`，关键事件建议重复 3 次。
- 默认不再处理普通上行命令；如后续增加紧急命令，需要单独设计短 RX slot 或降遥测占空。
- `AIR_TYPE_QUAT_STATE` 不在 START 后发送，避免重复占用链路。

## 5. FLIGHT_STATE，0x10，50 bytes

完整遥测包。该帧保持旧布局不变。

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---:|---:|---|---|---|
| 0 | 1 | u8 | type | `0x10` |
| 1 | 1 | u8 | seq | 帧序号 |
| 2 | 4 | u32 | time_ms | 飞控时间戳，ms |
| 6 | 2 | i16 | ax_raw | 原始加速度 X |
| 8 | 2 | i16 | ay_raw | 原始加速度 Y |
| 10 | 2 | i16 | az_raw | 原始加速度 Z |
| 12 | 2 | i16 | gx_raw | 原始角速度 X |
| 14 | 2 | i16 | gy_raw | 原始角速度 Y |
| 16 | 2 | i16 | gz_raw | 原始角速度 Z |
| 18 | 2 | i16 | qw_q15 | 四元数 W |
| 20 | 2 | i16 | qx_q15 | 四元数 X |
| 22 | 2 | i16 | qy_q15 | 四元数 Y |
| 24 | 2 | i16 | qz_q15 | 四元数 Z |
| 26 | 4 | float | vx | 速度 X，当前阶段可为 0 |
| 30 | 4 | float | vy | 速度 Y，当前阶段可为 0 |
| 34 | 4 | float | vz | 速度 Z，当前阶段可为 0 |
| 38 | 4 | float | x | 位置 X，当前阶段可为 0 |
| 42 | 4 | float | y | 位置 Y，当前阶段可为 0 |
| 46 | 4 | float | z | 位置 Z，当前阶段可为 0 |

说明：

- 当前帧不包含气压、气压高度、磁场、GNSS、电池电压或健康位。
- 若需要气压/高度实时下传，后续应新增帧或协议版本，不应把气压硬塞进旧字段。
- IMU 气压、磁场等全量数据建议记录到 TF 卡。

## 6. QUAT_STATE，0x11，14 bytes

短四元数包。用于 START 前确认 IMU 姿态与链路状态，不代替 START 后完整遥测。

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---:|---:|---|---|---|
| 0 | 1 | u8 | type | `0x11` |
| 1 | 1 | u8 | seq | 帧序号 |
| 2 | 4 | u32 | time_ms | 飞控时间戳，ms |
| 6 | 2 | i16 | qw_q15 | 四元数 W |
| 8 | 2 | i16 | qx_q15 | 四元数 X |
| 10 | 2 | i16 | qy_q15 | 四元数 Y |
| 12 | 2 | i16 | qz_q15 | 四元数 Z |

说明：

- 该帧只包含四元数，不包含 acc/gyro/气压/磁场/状态位。
- 若四元数 raw 全 0，上位机必须显示 invalid/raw=0，不应只显示归一化后的 1,0,0,0。
- START 后默认停止发送该帧，改为发送完整 `AIR_TYPE_FLIGHT_STATE`。

## 7. STATUS，0x20，9 bytes

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---:|---:|---|---|---|
| 0 | 1 | u8 | type | `0x20` |
| 1 | 1 | u8 | seq | 状态帧序号 |
| 2 | 1 | u8 | status_id | 状态编号 |
| 3 | 4 | u32 | time_ms | 事件时间戳 |
| 7 | 1 | u8 | arg0 | 状态自定义参数 |
| 8 | 1 | u8 | arg1 | 状态自定义参数 |

状态编号：

| 名称 | 值 |
|---|---:|
| `AIR_STATUS_BOOT` | `0x01` |
| `AIR_STATUS_SELFTEST_OK` | `0x02` |
| `AIR_STATUS_MISSION_START` | `0x03` |
| `AIR_STATUS_LAUNCH` | `0x04` |
| `AIR_STATUS_PARACHUTE_DEPLOY` | `0x05` |
| `AIR_STATUS_LANDING` | `0x06` |
| `AIR_STATUS_LOCKED` | `0x07` |
| `AIR_STATUS_UNLOCKED` | `0x08` |

## 8. CMD，0x30，9 bytes

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---:|---:|---|---|---|
| 0 | 1 | u8 | type | `0x30` |
| 1 | 1 | u8 | seq | 地面命令序号 |
| 2 | 1 | u8 | cmd_id | 命令编号 |
| 3 | 4 | u32 | token | 小端鉴权常量 |
| 7 | 1 | u8 | param0 | 保留/命令参数 |
| 8 | 1 | u8 | param1 | 保留/命令参数 |

命令：

| 命令 | 值 | token |
|---|---:|---:|
| `AIR_CMD_START_MISSION` | `0x01` | `0xA55A3CC3` |
| `AIR_CMD_PING` | `0x02` | 不校验，可填 0 |
| `AIR_CMD_LOCK` | `0x03` | `0xC33CA55A` |
| `AIR_CMD_UNLOCK` | `0x04` | `0x55AA6996` |

当前 Phase2 策略：

- 合法长度的 `AIR_TYPE_CMD` 必须明确 ACK。
- 合法 `cmd_id` 和合法 token 默认 `ACK OK`。
- START 在 Phase2 直接进入 mission started/test state，并触发完整遥测。
- 未知 `cmd_id` 返回 `BAD_CMD`。
- token 错误返回 `BAD_TOKEN`。

## 9. ACK，0x40，9 bytes

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---:|---:|---|---|---|
| 0 | 1 | u8 | type | `0x40` |
| 1 | 1 | u8 | seq | 飞控 ACK 帧序号 |
| 2 | 1 | u8 | ack_seq | 被确认的 CMD seq |
| 3 | 1 | u8 | ack_cmd_id | 被确认的 cmd_id |
| 4 | 1 | u8 | result | 结果码 |
| 5 | 4 | u32 | time_ms | 飞控生成 ACK 的时间 |

结果码：

| 名称 | 值 |
|---|---:|
| `AIR_ACK_RESULT_OK` | `0x00` |
| `AIR_ACK_RESULT_BAD_LEN` | `0x01` |
| `AIR_ACK_RESULT_BAD_CMD` | `0x02` |
| `AIR_ACK_RESULT_BAD_TOKEN` | `0x03` |
| `AIR_ACK_RESULT_BUSY` | `0x04` |
| `AIR_ACK_RESULT_REJECTED` | `0x05` |
| `AIR_ACK_RESULT_BAD_STATE` | `0x06` |
| `AIR_ACK_RESULT_LOCKED_REQUIRED` | `0x07` |
| `AIR_ACK_RESULT_ALREADY_LOCKED` | `0x08` |
| `AIR_ACK_RESULT_ALREADY_UNLOCKED` | `0x09` |

## 10. 地面站 GSP 转发规则

地面站不解析后重组 AIR 帧，合法 AIR 帧统一通过 `GSP_TYPE_AIR_RX` 转发给 Python 上位机。

`GSP_TYPE_AIR_RX` payload：

| 偏移 | 长度 | 字段 |
|---:|---:|---|
| 0 | 1 | RSSI dBm，i8 |
| 1 | 1 | SNR q4，i8 |
| 2 | 1 | air_len |
| 3 | air_len | AIR frame |

因此：

- 完整遥测 `AIR_TYPE_FLIGHT_STATE` 的 AIR frame 从 GSP payload[3] 开始。
- 短四元数 `AIR_TYPE_QUAT_STATE` 的 AIR frame 也从 GSP payload[3] 开始。
- 上位机必须按 `air_frame[0]` 判断类型和固定长度。

## 11. TF 卡与无线分工

无线链路优先传实时必要信息：

- START 前：ACK、STATUS、可选 QUAT_STATE。
- START 后：FLIGHT_STATE、STATUS。

TF 卡建议记录全量数据：

- acc、gyro、quat；
- pressure、height；
- mag；
- GNSS；
- 电池电压；
- 状态机；
- LoRa RSSI/SNR/丢包/重传统计。

当前无线完整包不包含气压和磁场；气压和磁场默认应记录到 TF 卡，是否无线回传留给后续协议版本评审。

## 12. 后续协议变更规则

后续新增字段时必须：

1. 明确字段、单位、量程、刷新率；
2. 决定新增帧类型还是新协议版本；
3. 同步修改飞控、地面站、Python 上位机；
4. 增加固定向量测试；
5. 验证旧帧、重复帧、丢包、错误长度、未知类型行为；
6. 不得把 C 结构体直接 `memcpy` 到无线帧。
