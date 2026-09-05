# SilverStar_GSHC 接收与显示架构

AIR字段以 [`AIR_PROTOCOL.md`](AIR_PROTOCOL.md) 为准，GSP以 [`GSP_MIN_PROTOCOL.md`](GSP_MIN_PROTOCOL.md) 为准。

## 1. 数据流
```text
SerialWorker(QThread): open/read/write/close
  ↓ bounded direct enqueue
ProtocolWorker(QThread)
  → GSP parser
  → AIR parser
  → immutable ProtocolEvent
  → Async JSONL queue (先持久化)
  → bounded UI mailbox
  ↓ GUI timer pull
Controller
  → FlightControllerState
  → EventHistory
  → command/handshake transactions
  ↓ render timer
MainWindow / plots / one OpenGL view
```
SerialWorker不访问Widget/协议；ProtocolWorker不访问Widget；MainWindow不解析wire。

## 2. 有界队列
所有实时容器有明确上限。正式数据在UI合并前进入JSONL；GUI忙时只允许合并中间显示用PREFLIGHT/FLIGHT状态，不能静默丢持久化数据。

## 3. Session/Handshake
Controller是Capability/command/session唯一状态所有者。第一次兼容Capability绑定会话；更新seq在未握手时替换pending Capability ACK。握手成功后迟到Capability只做诊断，不自动清空状态或rollover日志。新飞控会话通过明确断开/重连建立。

握手完成：匹配`ACK(CAPABILITY_ACK,OK)`或`PREFLIGHT_STATUS.capability_acked=1`恢复。Capability自身seq与PC命令seq严格区分。

## 4. Calibration / Alignment
Capability决定build支持的流程；当前状态由PREFLIGHT_STATUS决定。最终NONE/OneFace/SixFace规则见 [`AIR_CALIBRATION_CONTRACT.md`](AIR_CALIBRATION_CONTRACT.md)。当build含采样procedure时，GSHC可发送`CAL_START(NONE)`选择默认校正；仅0x01时飞控自动NONE。

`FlightControllerState.sampling_calibration_modes()` 只返回采样流程；`calibration_start_modes()` 返回当前可启动事务。Controller 在公共入口、通用入口及每次重试发送前使用 `check_calibration_start()`；无采样 build 的 NONE 返回 `AUTOMATIC_NONE`，不发重复命令。

NONE 必须由真实 `PREFLIGHT_STATUS` 的 mode/state/ready 确认；Calibration 状态事件继续显示状态，但不补造其 ready 标志。CAL_RESET 的丢 ACK 恢复也区分自动 NONE/READY 和等待选择的 NOT_SELECTED/IDLE。两个分支都由快照驱动。

ALIGN_START ACK只表示accepted；最终看alignment state/ready。普通ACK错误/timeout不改变握手状态。

## 5. 诊断
AIR Link Details集中显示Capability RX、PC→GS request、serial writes、GSP ACK、GS TX/RX/CRC、AIR ACK/PREFLIGHT恢复、队列积压、RSSI/SNR、最后AIR/快照年龄。用于区分PC/GSP/空口/飞控运行故障。

## 6. GUI
预飞/飞行/后处理三页可手动切换。左侧OpenGL context只创建一次。飞行首次由START ACK、MISSION_START或FLIGHT_STATE确认时自动切页一次；之后不抢焦点。

## 7. Persistent vs live
JSONL记录整个会话；EventHistory和实时曲线是有界GUI缓存。实时窗口裁剪不影响持久化。

## 8. Thread safety
长后处理/模拟/数据迁移在独立worker thread；UI线程只更新状态。所有退出/取消有界，不能遗留运行QThread对象。
