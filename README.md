# SS1 地面站上位机开发说明

本工程是二代飞控 SilverStar 的 PC 上位机。当前实现以
[`docs/AIR_PROTOCOL.md`](docs/AIR_PROTOCOL.md) 为唯一 AIR 协议依据，适配：

```text
SilverStar 0.0.8
AIR_PROFILE_COMPACT_V0 = 0
```

PC 仍通过串口连接地面站，GSP-MIN 只负责透明转发 AIR 帧，wire format 未改变。飞控 Debug UART、VOFA+ 调试字段、数据库和网络服务不属于本工程。

## 1. 主要能力

- 自动接收并确认 `CAPABILITY`，不提供人工 Capability ACK 按钮；
- 完整支持 `PREFLIGHT_STATE`、`PREFLIGHT_STATUS`、`STATUS`、`ACK` 和 `FLIGHT_STATE`；
- 按 Capability 动态提供 NONE / ONE_FACE / SIX_FACE 校准流程；
- 显示 `CALIBRATION_DIAGNOSTIC` 的当前采集问题，但不把诊断提示误判为校准失败；
- 支持 Alignment START / STOP / RESET，并以飞控快照为最终判据；
- 支持 Alignment `STALE`，失效后要求用户显式重新执行初对准；
- 使用“预飞行 / 飞行 / 后期处理”三个可随时手动切换的页面；
- START ACK、MISSION_START 或第一帧 FLIGHT_STATE 均可触发一次自动切换到飞行页；
- 预飞页保留完整事件历史，飞行页只显示事件驱动的 Mission State；
- 保持原有火箭模型、坐标轴、标签、颜色、默认相机、视角锁定和鼠标行为；
- 独立串口、协议、日志和 GUI 刷新路径，避免持续接收造成 Qt 事件积压；
- 支持简体中文 / English 运行时切换，选择由 QSettings 持久化，不需重启；
- 实时速度/位置曲线只保留最近 10 秒，JSONL 保留整个会话；
- 后处理生成曲线、摘要、manifest、姿态 GIF 和丢包统计；
- 模拟日志包含完整 Capability、预飞、校准、Alignment 和飞行流程。

## 2. 开发环境

建议 Windows 10/11，Python 3.11 或 3.12。

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py
```

运行测试：

```bat
python -m pytest -q
```

## 3. 工程结构

```text
main.py
app.py                              Controller、命令事务、状态更新、后处理调度
config.py                           有界队列、10 秒窗口和用户路径配置
protocol/
  air.py                            SilverStar 0.0.8 AIR Profile 0
  gsp_min.py                        GSP-MIN（wire format 不变）
  receive_pipeline.py               纯 GSP/AIR 解析核心与完整日志记录构造
services/
  logger.py                         有界 AsyncJsonlLogger
  i18n.py                           集中式中英文翻译与 QSettings 语言配置
  state_model.py                    FlightControllerState、EventHistory、实时窗口
transport/
  serial_backend.py                 只负责 open/read/write/close
  protocol_worker.py                独立协议线程、有界输入队列和 UI 状态邮箱
ui/
  main_window.py                    三页面 GUI 与共享 3D 视图
processing/
  flight_log_processor.py           Profile 0 离线处理
  flight_plotter.py                 曲线与姿态 GIF
  fake_log_generator.py             完整 0.0.8 模拟会话
docs/
  AIR_PROTOCOL.md                   AIR 唯一正式协议
  GSP_MIN_PROTOCOL.md               PC ↔ 地面站串口封装
  UPPER_COMPUTER_ARCHITECTURE.md    接收、记录和显示架构
```

## 4. 实时数据流

```text
SerialWorker
  -> ProtocolWorker / ReceivePipeline
  -> AsyncJsonlLogger（完整持久化）
  -> 有界 UI 状态邮箱
  -> Controller / FlightControllerState / EventHistory
  -> 100 ms GUI render timer
```

串口字节不再排入 GUI 线程解析。协议线程先把每个成功解析的正式 AIR 帧及 raw 信息写入异步日志队列，再交给状态邮箱。GUI 忙时，状态邮箱只会显式合并中间显示用的遥测/快照；完整数据已经进入 JSONL，不会随显示合并而丢失。详细设计见
[`docs/UPPER_COMPUTER_ARCHITECTURE.md`](docs/UPPER_COMPUTER_ARCHITECTURE.md)。

## 5. AIR 0.0.8 消息

```text
0x10 FLIGHT_STATE       50 B
0x11 PREFLIGHT_STATE    26 B
0x12 CAPABILITY          9 B
0x13 PREFLIGHT_STATUS    9 B
0x20 STATUS              9 B
0x30 CMD                 9 B
0x40 ACK                 9 B
```

AIR 无应用层 CRC；GSP-MIN 和 LoRa Transport 继续承担各自的完整性保护。

物理量换算只能使用当前兼容 Capability 中的：

```text
accel_full_scale_g
gyro_full_scale_dps
```

收到 Capability 前仍记录原始 `int16`，但 GUI 显示“等待 Capability”，不会猜测 16 g / 2000 dps。收到未知 profile 时显示 `AIR PROFILE UNSUPPORTED`，保留 raw 帧且不猜测解析、不自动 ACK。

## 6. Capability 与命令策略

连接串口后，上位机等待 Capability。收到兼容 Profile 0 后自动发送：

```text
CAPABILITY_ACK
param0 = 最新 Capability seq
param1 = air_profile_id
```

握手完成前 Calibration、Alignment、LOCK/UNLOCK、START 均不可用。Capability 重发带来更新的 seq 时，旧待确认事务会被最新 seq 替换。这里必须区分“Capability 帧 seq”和“PC 发出的 CAPABILITY_ACK 命令 seq”；只有 cmd id、待确认命令 seq 和 OK 结果全部匹配的 AIR ACK 才能完成握手。`PREFLIGHT_STATUS.capability_acked=1` 是 ACK 丢失时的权威恢复路径。

握手状态只由 `Controller / FlightControllerState` 管理。`ReceivePipeline` 和 `ProtocolWorker` 只解析、搬运并持久化数据，不判断握手成功、飞控重启或 session reset。已握手后队列中迟到的 Capability 只增加 stale/duplicate 诊断，不会清空状态、重发 ACK 或切换日志。若实机确实重启，应在 PC 端断开并重新连接串口，明确开始新会话。

当前 0.0.8 的 `command_policy=PREFLIGHT_ONLY`，任务开始后不再发送 AIR 命令。未来 `MISSION_ALLOWED` 是否允许链路发送由 Capability 决定，具体命令仍可能被飞控状态机拒绝。

## 7. Calibration / Alignment / START

校准模式列表来自 `calibration_mode_mask`：

- NONE：发送 CAL_START 后等待 `calibration_ready`；
- ONE_FACE：飞控自动推进，上位机不发送 CAL_FACE；
- SIX_FACE：用户摆放对应面后发送 CAL_FACE。ACK OK 仅代表 accepted；只有 `CALIBRATION_FACE PASSED` 或快照中的 `completed_face_mask` 才显示完成。

Alignment START 在 Calibration ready 后可用。ACK OK 仅代表 accepted；`PREFLIGHT_STATUS.alignment_ready` 是最终判据，Alignment STATUS event 只提供即时提示，不能在 STALE 后独立恢复 ready。Attitude ready 不能代替整个 Alignment ready。

校准采集中的当前问题来自 `CALIBRATION_DIAGNOSTIC`。`NONE` 会清除提示；其他 reason 只用于解释为何正在等待或重新采集，不会把 Calibration 状态改成 FAILED。六面校准会同时显示对应 face。

Alignment `STALE` 表示对准后检测到移动。上位机会立即撤销 ready、禁用 START 并重新启用“开始初对准”，但不会自动重启对准，也不会根据当前姿态自行恢复 READY。STALE 后只有新的 `PREFLIGHT_STATUS` 快照可以重新确认 `alignment_ready=1`。

START 按钮由权威预飞快照驱动，至少要求 Capability、Calibration、Alignment、System、UNLOCK 和 `start_block_reason=OK` 均满足。若 START ACK 丢失，MISSION_START 或第一帧 FLIGHT_STATE 会清除 START 重试、标记任务开始并自动切换一次飞行页。用户随后可手动切回任意页面，后续遥测不会抢焦点。

## 8. JSONL 与会话

默认用户数据根目录：

```text
C:\Users\<用户名>\Documents\SS1_host_computer_data
```

可通过环境变量 `SS1_HOST_COMPUTER_DATA_ROOT` 或 `config/user_paths.json` 的 `data_root` 修改。

串口连接时创建 provisional session 日志；只有明确的 PC 串口断开/重连才建立新会话。Capability 广播本身不再被推测为“飞控重启”，也不会触发自动日志 rollover。

每条记录包含 wall timestamp，并补充 `host_monotonic_ns`；RX 记录尽早保存 `host_rx_monotonic_ns`。日志包括：

```text
GSP raw / parsed
AIR raw
CAPABILITY
PREFLIGHT_STATUS
PREFLIGHT_STATE
STATUS
ACK
FLIGHT_STATE
CAPABILITY_ACK_TX（含 Capability seq、PC cmd seq、attempt、retry）
```

预飞页的“飞控 AIR 链路”只显示未发现飞控、等待握手、握手中、已连接、协议不兼容或链路异常等短状态。“详情”对话框集中显示 Capability RX、PC→GS AIR_TX 请求、串口实际写入、GSP AIR_TX ACK、地面站 TX/RX/CRC、AIR ACK、PREFLIGHT_STATUS 恢复、接收积压和 RSSI/SNR，避免把“PC 已连地面站串口”误认为“飞控已握手”。

完整 Event History 位于预飞页，按时间从旧到新排列并自动滚动到底部；GUI 只保留最近 200 条。飞行页显示 Mission State，只有 MISSION_START、LAUNCH、PARACHUTE_DEPLOY、LANDING 等权威事件才推进对应阶段。首帧 FLIGHT_STATE 最多回退显示 Mission Active，不会凭高度或速度臆造发射、开伞事件。

实时曲线删除 10 秒以前的数据只影响 GUI 内存，不影响 JSONL。

界面语言只影响显示。内部枚举、状态值、命令名和 JSONL 字段始终使用协议定义的规范英文名称；切换语言不会重建串口/协议线程、状态模型或 3D 场景，当前事件历史也会按所选语言重新渲染。

## 9. 后期处理

```bat
python -m processing.fake_log_generator --output logs\fake_flight_log.jsonl --duration 90 --seed 42
python -m processing.flight_log_processor logs\fake_flight_log.jsonl --output-root data
```

后处理优先读取 `AIR_PARSED`；仅在缺失时使用 Profile 0 raw fallback。没有 Capability 时不会猜测 IMU 量程。正式飞行窗口由 MISSION_START 或第一帧 FLIGHT_STATE 开始，PREFLIGHT_STATE 不混入飞行曲线。manifest 会保存 Capability、最终预飞状态、任务时长和丢包信息。

## 10. 绿色版打包

```bat
packaging\build_pyinstaller.bat
```

输出为 `dist/SS1GroundStation/`。必须复制整个目录，不能只复制 exe。发布前应在源码版和打包版各验证一次串口、Capability、预飞流程、START 恢复、3D、10 秒曲线和 JSONL 后处理。
