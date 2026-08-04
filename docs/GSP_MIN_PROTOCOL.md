# GSP-MIN 地面站串口封装

本文档只定义 PC 上位机与地面站之间的 GSP-MIN 串口封装。AIR 应用层帧的唯一正式定义见 [AIR_PROTOCOL.md](AIR_PROTOCOL.md)，本文不复制 AIR 字段布局。

## 1. 通用规则

UART 是字节流，GSP-MIN 使用固定帧头、长度和 CRC 恢复帧边界。所有多字节整数均为 little-endian。

```text
Byte 0      SOF1 = 0xA5
Byte 1      SOF2 = 0x5A
Byte 2      GSP_TYPE
Byte 3      LEN，payload 长度，0~255
Byte 4..    PAYLOAD
最后2字节   CRC16，低字节在前
```

总长度为 `6 + LEN` 字节。CRC16 使用 CCITT-FALSE，初值 `0xFFFF`、多项式 `0x1021`，计算范围为 `GSP_TYPE + LEN + PAYLOAD`，不包含帧头和 CRC 字段。

## 2. GSP_TYPE

| 名称 | 值 | 方向 | 用途 |
|---|---:|---|---|
| `GSP_TYPE_GS_STATUS` | `0x01` | GS→PC | 地面站与无线状态 |
| `GSP_TYPE_AIR_RX` | `0x02` | GS→PC | 转发收到的完整 AIR 帧 |
| `GSP_TYPE_AIR_TX` | `0x03` | PC→GS | 请求发送完整 AIR 帧 |
| `GSP_TYPE_ACK` | `0x04` | 双向 | GSP 层接收结果 |

## 3. GSP_TYPE_GS_STATUS

payload 固定为 12 字节：

```text
Byte 0      GS_STATE
Byte 1      RADIO_STATE
Byte 2~5    TX_CNT，uint32
Byte 6~9    RX_CNT，uint32
Byte 10~11  CRC_ERR_CNT，uint16
```

`GS_STATE`：`0x00 IDLE`、`0x01 INIT_OK`、`0x02 RX_MODE`、`0x03 TX_MODE`、`0x04 ERROR`。

`RADIO_STATE`：`0x00 NOT_INIT`、`0x01 READY`、`0x02 RX`、`0x03 TX`、`0x04 BUSY`。

## 4. GSP_TYPE_AIR_RX

地面站把收到的完整 AIR 帧及链路质量转发给上位机：

```text
Byte 0      RSSI_DBM，int8，单位 dBm
Byte 1      SNR_Q4，int8，单位 0.25 dB
Byte 2      AIR_LEN，uint8
Byte 3..    AIR_FRAME，原始完整 AIR 帧
```

payload 长度为 `3 + AIR_LEN`，实际 `SNR = SNR_Q4 / 4.0`。AIR 帧始终从 `payload[3]` 开始，上位机按 [AIR_PROTOCOL.md](AIR_PROTOCOL.md) 中的类型与固定长度解析，不改变或重组 AIR 帧。

## 5. GSP_TYPE_AIR_TX

上位机请求地面站发送一帧完整 AIR 帧：

```text
Byte 0      AIR_LEN
Byte 1..    AIR_FRAME
```

payload 长度为 `1 + AIR_LEN`。地面站只负责检查长度并无线发送；AIR 命令语义及长度以 [AIR_PROTOCOL.md](AIR_PROTOCOL.md) 为准。

## 6. GSP_TYPE_ACK

GSP ACK 只确认串口封装接收结果，不替代 AIR ACK。payload 固定为 3 字节：

```text
Byte 0      ACK_GSP_TYPE
Byte 1      RESULT
Byte 2      DETAIL
```

`RESULT`：`0x00 OK`、`0x01 BAD_CRC`、`0x02 BAD_LEN`、`0x03 BAD_TYPE`、`0x04 BAD_PARAM`、`0x05 BUSY`。
