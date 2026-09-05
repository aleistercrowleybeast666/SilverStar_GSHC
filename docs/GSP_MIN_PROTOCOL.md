# GSP-MIN 地面站串口封装

本文只定义PC上位机与地面站之间的GSP-MIN。AIR应用层帧以 [`AIR_PROTOCOL.md`](AIR_PROTOCOL.md) 为唯一正式定义。

## Frame
所有多字节整数little-endian。
```text
Byte0  SOF1=0xA5
Byte1  SOF2=0x5A
Byte2  GSP_TYPE
Byte3  LEN
Byte4.. payload
最后2字节 CRC16 little-endian
```
总长`6+LEN`。CRC16 CCITT-FALSE，init `0xFFFF`，poly `0x1021`，覆盖`GSP_TYPE+LEN+PAYLOAD`。

## Types
| type | value | direction | meaning |
|---|---:|---|---|
| GS_STATUS | `0x01` | GS→PC | 地面站/无线状态 |
| AIR_RX | `0x02` | GS→PC | 转发完整AIR帧 |
| AIR_TX | `0x03` | PC→GS | 请求发送完整AIR帧 |
| ACK | `0x04` | 双向 | GSP层结果 |

## GS_STATUS payload (12 B)
```text
0 GS_STATE
1 RADIO_STATE
2..5 TX_CNT u32
6..9 RX_CNT u32
10..11 CRC_ERR_CNT u16
```
GS_STATE：IDLE/INIT_OK/RX_MODE/TX_MODE/ERROR。RADIO_STATE：NOT_INIT/READY/RX/TX/BUSY。

## AIR_RX
```text
0 RSSI_DBM int8
1 SNR_Q4 int8 (0.25 dB)
2 AIR_LEN
3.. AIR_FRAME
```
Ground station不得重组AIR帧。

## AIR_TX
```text
0 AIR_LEN
1.. AIR_FRAME
```
GSP只检查封装长度并交给无线层，AIR命令语义不属于GSP。

## ACK
固定3字节：`ACK_GSP_TYPE, RESULT, DETAIL`。RESULT：OK/BAD_CRC/BAD_LEN/BAD_TYPE/BAD_PARAM/BUSY。GSP ACK只确认串口封装，不替代AIR ACK。
