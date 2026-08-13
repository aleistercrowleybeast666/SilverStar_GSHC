# SS1 上位机接收与显示架构

本文描述 SilverStar AIR V0 / Profile 0 上位机的实时接收、状态、日志和 GUI 边界。AIR 字段以 [AIR_PROTOCOL.md](AIR_PROTOCOL.md) 为准，GSP wire format 以 [GSP_MIN_PROTOCOL.md](GSP_MIN_PROTOCOL.md) 为准。

## 1. 卡顿根因

旧链路为：

```text
SerialWorker 每个 chunk 发 queued Qt signal
  -> GUI 线程解析 GSP
  -> GUI 线程解析 AIR
  -> GUI 线程 json.dumps / file.write / 每条 flush
  -> GUI 线程更新 QLabel / OpenGL
  -> 每次 vector push 重绘全部六张曲线
```

一帧 FLIGHT_STATE 分别 push velocity 和 position，因此会重复全量绘图两次。持续输入时，生产速度高于 GUI 消费速度，Qt 事件队列逐渐增长；停止发送后队列被慢慢处理完，界面恢复。这与实机“持续接收后变卡，停发后恢复”的表现一致。

## 2. 当前数据流

```text
SerialWorker (QThread)
  open / read / write / close
          |
          | DirectConnection：仅做有界 enqueue，不进入 GUI event queue
          v
ProtocolWorker (QThread)
  bounded raw-chunk queue
  -> ReceivePipeline
     -> GSP parser
     -> AIR Profile 0 parser
     -> immutable ProtocolEvent
  -> AsyncJsonlLogger queue（先持久化）
  -> bounded UI mailbox
          |
          | GUI 20 ms timer pull，最多 512 events/tick
          v
Controller
  -> FlightControllerState
  -> EventHistory(maxlen=200)
  -> command / handshake transactions
          |
          | GUI 100 ms render timer
          v
MainWindow
  labels / latest 3D attitude / six plots
```

SerialWorker 不访问 Widget，不解析协议，不写文件。ProtocolWorker 不访问 Widget。MainWindow 不解析 wire data，也不承担持久化。

握手、命令事务和 session 的唯一状态所有者是 Controller。ReceivePipeline / ProtocolWorker 不保存 `capability_acked`，不根据 Capability 推测重启，也不触发状态清理或 logger rollover。

## 3. 有界队列与背压

实时路径中的容器都有明确上限：

| 容器 | 上限 | 饱和行为 |
|---|---:|---|
| Serial TX queue | 128 frames | 显式发送错误，不无限增长 |
| Protocol raw input queue | 1024 chunks | 告警并对串口线程施加有界背压，不静默丢 RX |
| Async JSONL queue | 20000 records | 告警并等待 writer，不静默丢日志 |
| UI state mailbox | 4096 events + 少量 latest slots | 正式数据已写日志；合并中间显示帧并计数告警 |
| EventHistory | 200 events | GUI 删除最旧项；JSONL 保留全部 |
| Live plot buffer | 10 s、最多 2000 points | 按时间裁剪，另有防御性点数上限 |
| Pending AIR command | 1 ordinary + 1 special handshake | 新人工命令被拒绝，Capability ACK 优先 |

UI mailbox 不使用“每帧一个 queued Qt signal”。当 GUI 暂时忙碌时，FLIGHT_STATE、PREFLIGHT_STATE 和 PREFLIGHT_STATUS 的中间显示副本可被合并为最新副本；对应 raw/parsed JSONL 在合并前已经完整排入 writer。因此这是 UI coalescing，不是正式数据丢失。

## 4. Receive Pipeline 诊断

状态页的 `PC处理状态` 显示 NORMAL / BACKLOG，tooltip 包含：

```text
serial_rx_bytes
serial_rx_chunks
gsp_frames
gsp_parse_errors
gsp_crc_errors
gsp_resyncs
parser_buffer_size
air_frames
air_parse_errors
protocol_queue_depth / capacity
ui_mailbox_depth / capacity
ui_coalesced_events
logger_queue_depth / capacity
last_rx_age_ms
max_processing_lag_ms
warning
```

GSP parser 在无完整 SOF 时保留末尾单个 `0xA5`，避免帧头恰好在 A5/5A 之间分片时丢帧。CRC 错误、重同步和丢弃字节均可诊断。

## 5. 异步 JSONL

`AsyncJsonlLogger` 使用：

```text
thread-safe bounded queue
  -> one writer thread
  -> ordered JSON serialization
  -> max 64 records or 200 ms batch flush
```

正常接收路径不执行 file flush。session close 会按顺序 drain、flush、fsync 边界并关闭文件。文件异常保存在显式 error 状态，不会伪装为正常。新 JSONL 由明确的串口会话边界建立，不由重复 Capability 自动切换。

每条记录自动带：

```text
ts                  host wall time
host_monotonic_ns   enqueue monotonic time
```

RX pipeline 另带：

```text
host_rx_monotonic_ns
host_processed_monotonic_ns
processing_lag_ms
```

## 6. Persistent Recording 与 Live Display

两个生命周期严格分离：

```text
Persistent Recording
  全部 GSP raw、AIR raw、CAPABILITY、PREFLIGHT_STATUS、
  PREFLIGHT_STATE、SENSOR_STATUS、STATUS、ACK、FLIGHT_STATE
  -> 整个飞控会话

Live Display
  latest sensor / latest quaternion
  EventHistory <= 200
  velocity/position latest 10 s and <= 2000 points
```

实时曲线的 cutoff 为：

```text
sample_time >= latest_time - 10.0 s
```

采样率从 5 Hz 改变后仍保持 10 秒窗口，而不是固定 400 点。一次 FLIGHT_STATE 同时 append velocity 和 position，只增加一个 plot revision；100 ms timer 一次更新六条曲线。

3D 只读取 `SensorSnapshot` 中最新有效四元数。旧姿态不在 GUI 内存中形成历史，完整姿态仍在 JSONL。

## 7. State 与 Event

`FlightControllerState` 保存当前权威状态：

- SessionCapability 与 command policy；
- lifecycle；
- Calibration mode/state/faces/ready；
- Alignment state/overall ready，以及最新的 Alignment 终止 Sensor Snapshot；
- system/selftest/GNSS/UNLOCK/start block；
- 最新传感器、链路、丢包和任务时间；
- ReceiveHealth。
- HandshakeDiagnostics（最新序号/时间、尝试次数和有界计数器）。

`EventHistory` 独立保存 STATUS 边沿事件。PREFLIGHT_STATUS 是“现在是什么状态”，STATUS 是“刚才发生了什么”，两者不会相互替代。快照可以纠正丢失的 Calibration face、LOCK 或 Alignment 边沿事件。

`AlignmentSensorSnapshotCache` 按 `snapshot_id` 保存少量有界 accumulator，以 `index/total` 合并乱序帧。重复 index 采用 latest-wins 并累计诊断；total 不一致的帧不混入既有快照。只有 Alignment READY / FAILED STATUS 才终止对应快照并判定完整性。STALE `arg1=0xFF` 不建立新快照，详情继续展示上一次终止快照。建立新的串口会话会构造新的 `FlightControllerState`，因此 accumulator 和旧终止快照不会跨会话泄漏。

Sensor registry 只包含 AIR V0 的 canonical ID；未知 ID 仍是合法数据。详情视图从缓存按 IMU、GNSS、其余 sensor ID、instance 排序，并显示通用状态 flags、detail 和 raw flags。详情按钮没有 Controller 命令回调，不会产生 AIR 请求。

## 8. Capability 与 session

串口连接创建 provisional JSONL。第一次兼容 Capability 绑定当前会话。未完成握手时，新的 Capability seq 替换旧 CAPABILITY_ACK transaction；普通人工命令不能抢占握手。

握手完成条件：

```text
matching ACK(CAPABILITY_ACK, OK)
or PREFLIGHT_STATUS.capability_acked == 1
```

第一条条件还要求 ACK 的 `ack_cmd_id=CAPABILITY_ACK` 且 `ack_seq` 等于当前 PC 待确认命令的 seq；Capability 自己的 seq 只放在命令 param0 中，二者不混用。未握手时，更新的 Capability seq 替换当前 transaction。已握手后迟到或重复的 Capability 只记录 `STALE_OR_DUPLICATE_CAPABILITY_AFTER_ACK` 并增加计数，不重置 Controller、不重发 ACK、不清空 UI、不切换 JSONL。需要新飞控 session 时由操作者断开并重连 PC 串口。

预飞页 AIR Link 的 tooltip 是一条可核对的诊断链：

```text
Capability RX seq/time
  -> CAPABILITY_ACK PC cmd seq / attempts
  -> PC→GS AIR_TX requests
  -> serial writes / bytes
  -> GSP AIR_TX ACK OK/FAIL
  -> GS STATUS TX/RX/CRC
  -> AIR ACK result or PREFLIGHT_STATUS recovery
```

`CAPABILITY_ACK_TX` 作为显式 JSONL 记录保存 Capability seq、PC cmd seq、attempt 和 retry；所有诊断只保存最新值或累计计数，不形成第二个无限历史。

## 9. GUI 渲染

右侧为三个始终可点击的 QTabWidget 页面：

```text
预飞行：System / Calibration / Alignment / GNSS / Inertial-Attitude / commands / EventHistory
飞行：重要状态 / 当前数据 / Mission State / 6 plots
后期处理：模拟数据 / 处理数据 / 打开 logs / 打开 data
```

左侧只创建一个 OpenGL 3D widget，页面切换不重建 context。原 mesh、faces、colors、world/body axes、E/W/N/S/U、NOSE、相机参数、锁定/解锁、重置和鼠标行为保持不变。

任务第一次由 START ACK、MISSION_START 或 FLIGHT_STATE 确认时，GUI 自动切到飞行页一次。记录 `session_generation` 后不再抢焦点；用户可手动返回预飞或后期处理。只有全新飞控 session 才允许下一次自动切换。

界面文字统一由 `services/i18n.py` 的翻译 key 渲染，支持 `zh_CN` / `en_US` 无重启切换，语言通过 QSettings 保存。状态模型和 EventHistory 只保存规范枚举/原始参数；切换语言时重译当前标签、按钮、tooltip、事件历史、校准对话框和曲线标题，不重建 worker、协议状态或 OpenGL 场景。E/W/N/S/U、NOSE 和 body axis 技术标识保持不翻译。JSONL 的 kind、enum name 和字段值始终是规范英文，不受界面语言影响。

Alignment 主面板只显示总体 state/ready、快照完整性和详情入口，不写死 Attitude/GNSS origin/Baro origin 三个 source。GNSS 动态 usable 状态仍在独立主面板显示；Barometer 等硬件 inventory 仅在实际 Sensor Snapshot 中出现。AIR Link Details 仅在内容变化时更新文本，更新前后保存滚动位置和 selection；原本位于底部时跟随新底部，否则保持用户阅读位置。

## 10. 后处理边界

后处理在独立 ProcessingWorker QThread 运行。飞行中禁用模拟生成和正式处理按钮，但页面与日志/data 文件夹仍可查看。

处理器优先消费 AIR_PARSED，raw fallback 仅实现 Profile 0。没有 Capability 时只保留 raw，绝不猜测量程。飞行图从 MISSION_START 或第一帧 FLIGHT_STATE 开始；预飞样本不混入 velocity/position 曲线。manifest 保存 Capability、最终预飞状态、任务持续时间和 packet loss。
