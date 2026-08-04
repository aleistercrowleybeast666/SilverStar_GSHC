# SilverStar 天地通信协议

> **项目：SilverStar**
> **文档版本：0.0.0**
> **状态：Draft / 未发布**
> **适用范围：SilverStar 0.0.0**

## 1. 范围和兼容目标

本协议定义飞控、地面站和可选上位机之间的AIR应用层帧。SilverStar 0.0.0保持当前工程的帧类型、固定字节数、字段偏移、token、ACK结果码和5 Hz完整遥测，不重新设计公共头，不增加应用层CRC，也不向50字节`FLIGHT_STATE`加入飞行状态机字段。

当前传输后端为SX1281 LoRa，但AIR编码不得依赖具体射频芯片。地面端支持两种工作模式：

1. **透明转发模式**：地面站将收到的完整AIR帧连同RSSI/SNR转发给电脑，由Python上位机解析；
2. **独立地面站模式**：没有电脑时，地面站本机直接解析固定AIR帧、持续与飞控通信、显示关键状态并发送必要命令。

两种模式使用同一AIR帧，不允许地面站解析后重新拼接为另一种空中格式。

## 2. 公共规则

| 项目 | 定义 |
|---|---|
| 最大AIR帧 | 50 bytes |
| 底层最大payload | 当前64 bytes |
| 字节序 | little-endian |
| 帧边界 | Transport packet payload |
| 应用层CRC | 无；当前依赖链路CRC、固定长度、type、token和sequence检查 |
| 序号 | u8，自然回绕 |

固定类型和长度：

| 类型 | 值 | 长度 | 方向 | 用途 |
|---|---:|---:|---|---|
| `AIR_TYPE_FLIGHT_STATE` | `0x10` | 50 | FC→GS | START后5 Hz完整遥测 |
| `AIR_TYPE_QUAT_STATE` | `0x11` | 14 | FC→GS | START前可选短四元数 |
| `AIR_TYPE_STATUS` | `0x20` | 9 | FC→GS | 状态和事件 |
| `AIR_TYPE_CMD` | `0x30` | 9 | GS→FC | 地面命令 |
| `AIR_TYPE_ACK` | `0x40` | 9 | FC→GS | 命令应答 |

## 3. 阶段调度

### 3.1 START前

- 飞控保持连续接收；
- 默认不发送50字节完整遥测；
- 允许发送ACK、STATUS和可选QUAT_STATE；
- ACK和状态变化通知高于短四元数优先级；
- GNSS是否定位成功只影响状态显示和导航量测可用性，**不得阻止START**。

### 3.2 START转换

合法START仍按当前工程规则检查必要的IMU与姿态条件。GNSS离线、未定位或定位精度不满足融合门限，不得成为拒绝START的理由。接受START后：

1. 返回ACK；
2. 发送`AIR_STATUS_MISSION_START`；
3. 停止START前短四元数；
4. 开始5 Hz `FLIGHT_STATE`。

### 3.3 START后

- `FLIGHT_STATE`只保存最新状态，忙时跳过旧帧；
- ACK和STATUS优先；
- 关键事件可以按既有策略重复数次；
- GNSS定位状态在每次丢失和恢复时都可以再次发送，不受“一次任务只发一次”限制。

## 4. FLIGHT_STATE，0x10，50 bytes

保持当前布局和长度不变：

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---:|---:|---|---|---|
| 0 | 1 | u8 | type | `0x10` |
| 1 | 1 | u8 | seq | 帧序号 |
| 2 | 4 | u32 | time_ms | START后任务相对时间 |
| 6 | 2 | i16 | ax_raw | 当前IMU原生/兼容原始X |
| 8 | 2 | i16 | ay_raw | 当前IMU原生/兼容原始Y |
| 10 | 2 | i16 | az_raw | 当前IMU原生/兼容原始Z |
| 12 | 2 | i16 | gx_raw | 当前IMU原生/兼容原始X |
| 14 | 2 | i16 | gy_raw | 当前IMU原生/兼容原始Y |
| 16 | 2 | i16 | gz_raw | 当前IMU原生/兼容原始Z |
| 18 | 2 | i16 | qw_q15 | W |
| 20 | 2 | i16 | qx_q15 | X |
| 22 | 2 | i16 | qy_q15 | Y |
| 24 | 2 | i16 | qz_q15 | Z |
| 26 | 4 | float | vx | ENU East速度，m/s |
| 30 | 4 | float | vy | ENU North速度，m/s |
| 34 | 4 | float | vz | ENU Up速度，m/s |
| 38 | 4 | float | x | ENU East相对位置，m |
| 42 | 4 | float | y | ENU North相对位置，m |
| 46 | 4 | float | z | ENU Up相对位置，m |

0.0.0不在该帧中加入GNSS状态、电源、气压、生命周期或健康位，避免改变长度和占用固定5 Hz链路。

0.0.0将这些字段定义为与当前JY901B配置兼容的固定线性量化，从而保持现有上位机换算不变：

```text
acc_raw = round(accel_g / accel_full_scale_g * 32768)
gyro_raw = round(gyro_dps / gyro_full_scale_dps * 32768)
quat_q15 = round(clamp(q, -1, 1) * 32768)
```

当前参考Profile使用JY901B的实际加速度和角速度量程，JY901B后端可直接透传原始值。未来MPU6050、BMI088等后端必须按System Profile记录的相同满量程量化到该线格式，不能把芯片私有原始计数直接发送。这样保持50字节布局和现有上位机物理量换算不变。若Profile选择了不同满量程，必须在地面站/上位机配置中同步使用同一Profile；0.0.0不在空中帧内额外发送量程字段。

## 5. QUAT_STATE，0x11，14 bytes

| 偏移 | 长度 | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 1 | u8 | type=`0x11` |
| 1 | 1 | u8 | seq |
| 2 | 4 | u32 | boot time_ms |
| 6 | 2 | i16 | qw_q15 |
| 8 | 2 | i16 | qx_q15 |
| 10 | 2 | i16 | qy_q15 |
| 12 | 2 | i16 | qz_q15 |

用于START前姿态和链路检查；START后默认停止发送。

## 6. STATUS，0x20，9 bytes

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---:|---:|---|---|---|
| 0 | 1 | u8 | type | `0x20` |
| 1 | 1 | u8 | seq | 状态帧序号 |
| 2 | 1 | u8 | status_id | 状态编号 |
| 3 | 4 | u32 | time_ms | START前用boot time；START后事件可用mission time |
| 7 | 1 | u8 | arg0 | 状态参数 |
| 8 | 1 | u8 | arg1 | 状态参数/保留 |

状态编号：

| 名称 | 值 | 触发规则 |
|---|---:|---|
| `AIR_STATUS_BOOT` | `0x01` | 启动事件 |
| `AIR_STATUS_SELFTEST_OK` | `0x02` | 自检完成 |
| `AIR_STATUS_MISSION_START` | `0x03` | 接受START |
| `AIR_STATUS_LAUNCH` | `0x04` | 发射事件 |
| `AIR_STATUS_PARACHUTE_DEPLOY` | `0x05` | 开伞事件 |
| `AIR_STATUS_LANDING` | `0x06` | 着陆事件 |
| `AIR_STATUS_LOCKED` | `0x07` | 锁定 |
| `AIR_STATUS_UNLOCKED` | `0x08` | 解锁 |
| `AIR_STATUS_GNSS_POSITION` | `0x09` | GNSS定位可用状态首次确定或发生变化 |

### 6.1 GNSS定位状态

`AIR_STATUS_GNSS_POSITION`使用现有`arg0`一个字节，不增加新帧或新字段：

```text
arg0 = 0：当前没有可用GNSS定位
arg0 = 1：当前GNSS定位可用
arg1 = 0：0.0.0保留
```

“不可用”包括设备离线、数据过期、无有效定位或不满足System对`position_usable`的当前判定。“可用”只表示可向导航层提供位置量测，不表示必须参与当前一次KF更新。

发送策略：

1. 系统首次确定GNSS定位状态后发送一次；
2. `0→1`重新定位时立即发送；
3. `1→0`定位丢失或设备断连时立即发送；
4. 每次边沿可以按关键状态策略重复发送，例如3次、间隔50 ms；
5. 后续再次断连/重连仍重复执行，不设置全任务一次性锁存；
6. 地面站成功PING后可以补发当前状态，使后上电地面站获得同步；
7. 该状态不参与START许可判断。

上位机只需增加对`status_id=0x09`的解析和定位图标更新，不修改50字节遥测解析。

## 7. CMD，0x30，9 bytes

| 偏移 | 长度 | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 1 | u8 | type=`0x30` |
| 1 | 1 | u8 | seq |
| 2 | 1 | u8 | cmd_id |
| 3 | 4 | u32 | token |
| 7 | 1 | u8 | param0 |
| 8 | 1 | u8 | param1 |

| 命令 | 值 | token |
|---|---:|---:|
| `AIR_CMD_START_MISSION` | `0x01` | `0xA55A3CC3` |
| `AIR_CMD_PING` | `0x02` | 不校验 |
| `AIR_CMD_LOCK` | `0x03` | `0xC33CA55A` |
| `AIR_CMD_UNLOCK` | `0x04` | `0x55AA6996` |

GNSS状态不得影响START ACK结果。START是否允许由生命周期、锁定状态和必需IMU/姿态条件决定。

## 8. ACK，0x40，9 bytes

| 偏移 | 长度 | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 1 | u8 | type=`0x40` |
| 1 | 1 | u8 | seq |
| 2 | 1 | u8 | ack_seq |
| 3 | 1 | u8 | ack_cmd_id |
| 4 | 1 | u8 | result |
| 5 | 4 | u32 | time_ms |

结果码保持当前定义：

```text
0x00 OK
0x01 BAD_LEN
0x02 BAD_CMD
0x03 BAD_TOKEN
0x04 BUSY
0x05 REJECTED
0x06 BAD_STATE
0x07 LOCKED_REQUIRED
0x08 ALREADY_LOCKED
0x09 ALREADY_UNLOCKED
```

重复相同命令sequence必须返回缓存ACK，不重复执行副作用。

## 9. 地面站两种实现模式

### 9.1 透明转发模式

地面站通过现有GSP包装把AIR帧交给电脑：

```text
GSP_TYPE_AIR_RX payload:
[0] RSSI dBm, i8
[1] SNR q4, i8
[2] air_len
[3..] AIR frame
```

上位机按`AIR frame[0]`和固定长度解析。新增GNSS状态只要求识别`STATUS/0x09`。

### 9.2 无电脑独立模式

地面站本机必须能够：

- 持续接收并校验固定长度AIR帧；
- 显示链路、四元数、位置速度和GNSS定位状态；
- 发送PING、LOCK、UNLOCK、START；
- 根据CMD sequence匹配ACK；
- 对重复STATUS和重复ACK去重但仍更新当前状态；
- 在无电脑连接时继续正常与飞控通信。

连接电脑时，可以同时本地显示并透明转发；不得因为上位机断开而停止空中链路状态机。

## 10. 事件重复与去重

关键事件和GNSS状态变化可以重复发送。接收端使用至少以下信息去重：

```text
type + seq
```

重复帧不得重复触发危险操作，但可以刷新显示和链路在线时间。GNSS状态以最后接收的`arg0`为准。

## 11. TF卡与无线分工

无线只传实时必要信息；原始传感器、GNSS详细字段、气压、磁场、KF创新、协方差和链路统计写入TF卡。0.0.0不为这些内容扩展固定50字节遥测。
