# SilverStar_GSHC 开发与发布说明

本工程的正式应用名称为 **SilverStar_GSHC**，用于连接 SilverStar 飞控与地面站。当前实现以
[`docs/AIR_PROTOCOL.md`](docs/AIR_PROTOCOL.md) 为唯一 AIR 协议依据，适配：

```text
SilverStar AIR V0
AIR_PROFILE_COMPACT_V0 = 0
```

PC 仍通过串口连接地面站，GSP-MIN 只负责透明转发 AIR 帧，wire format 未改变。飞控 Debug UART、VOFA+ 调试字段、数据库和网络服务不属于本工程。

## 1. 主要能力

- 自动接收并确认 `CAPABILITY`，不提供人工 Capability ACK 按钮；
- 完整支持 `PREFLIGHT_STATE`、`PREFLIGHT_STATUS`、`SENSOR_STATUS`、`STATUS`、`ACK` 和 `FLIGHT_STATE`；
- 兼容 FCCG 0.0.10 动态校准能力：按 Capability 提供 ONE_FACE / SIX_FACE 采样流程，NONE 表示单位校正；
- SIX_FACE 在 `WAIT_FACE` 和全部完成后的 `READY` 都允许点选任意单面重采，其余五面状态由飞控快照保持；
- 显示 `CALIBRATION_DIAGNOSTIC` 的当前采集问题，但不把诊断提示误判为校准失败；
- 支持 Alignment START / STOP / RESET，并以飞控快照为最终判据；Alignment 结束时缓存通用 Sensor Snapshot；
- 支持 Alignment `STALE`，失效后要求用户显式重新执行初对准；
- 使用“预飞行 / 飞行 / 后期处理”三个可随时手动切换的页面；
- START ACK、MISSION_START 或第一帧 FLIGHT_STATE 均可触发一次自动切换到飞行页；
- 预飞页保留完整事件历史，飞行页只显示事件驱动的 Mission State；
- 保持原有火箭模型、坐标轴、标签、颜色、默认相机、视角锁定和鼠标行为；
- 独立串口、协议、日志和 GUI 刷新路径，避免持续接收造成 Qt 事件积压；
- 支持简体中文 / English 运行时切换，选择由 QSettings 持久化，不需重启；
- 支持全局浅色 / 深色主题，普通控件、弹窗、实时曲线和 OpenGL 3D 背景同步切换；
- 实时速度/位置曲线只保留最近 10 秒，JSONL 保留整个会话；
- 后处理使用默认全选的多选框导出数据、摘要、图表、3D 姿态/轨迹 GIF 和 manifest；
- 导出语言可独立选择 Follow UI / 简体中文 / English，文件统一带 `_ZH` / `_EN`，图内文字也使用所选语言；
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
  air.py                            SilverStar AIR V0 / Profile 0
  gsp_min.py                        GSP-MIN（wire format 不变）
  receive_pipeline.py               纯 GSP/AIR 解析核心与完整日志记录构造
services/
  logger.py                         有界 AsyncJsonlLogger
  i18n.py                           集中式中英文翻译与 QSettings 语言配置
  preferences.py                    主题、导出语言和导出项偏好
  state_model.py                    FlightControllerState、EventHistory、实时窗口
transport/
  serial_backend.py                 只负责 open/read/write/close
  protocol_worker.py                独立协议线程、有界输入队列和 UI 状态邮箱
ui/
  main_window.py                    三页面 GUI 与共享 3D 视图
  theme.py                          全局控件、曲线与 3D 主题配色
processing/
  flight_log_processor.py           Profile 0 离线处理
  flight_plotter.py                 曲线与姿态 GIF
  fake_log_generator.py             完整 AIR V0 模拟会话
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

## 5. AIR V0 消息

```text
0x10 FLIGHT_STATE       50 B
0x11 PREFLIGHT_STATE    26 B
0x12 CAPABILITY          9 B
0x13 PREFLIGHT_STATUS    9 B
0x14 SENSOR_STATUS       9 B
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

`CAPABILITY.byte5` 是 `sensor_summary_flags`，只概括 IMU、GNSS、辅助传感器和快照支持能力；保留位不参与判断。`PREFLIGHT_STATUS.byte6` 仅使用低四位的 Alignment state，高四位保留且忽略。上位机不再解释旧的 Alignment source mask，也不从该字节推导固定 Attitude/GNSS/Baro source 状态。

## 6. Capability 与命令策略

连接串口后，上位机等待 Capability。收到兼容 Profile 0 后自动发送：

```text
CAPABILITY_ACK
param0 = 最新 Capability seq
param1 = air_profile_id
```

握手完成前 Calibration、Alignment、LOCK/UNLOCK、START 均不可用。Capability 重发带来更新的 seq 时，旧待确认事务会被最新 seq 替换。这里必须区分“Capability 帧 seq”和“PC 发出的 CAPABILITY_ACK 命令 seq”；只有 cmd id、待确认命令 seq 和 OK 结果全部匹配的 AIR ACK 才能完成握手。`PREFLIGHT_STATUS.capability_acked=1` 是 ACK 丢失时的权威恢复路径。

握手状态只由 `Controller / FlightControllerState` 管理。`ReceivePipeline` 和 `ProtocolWorker` 只解析、搬运并持久化数据，不判断握手成功、飞控重启或 session reset。已握手后队列中迟到的 Capability 只增加 stale/duplicate 诊断，不会清空状态、重发 ACK 或切换日志。若实机确实重启，应在 PC 端断开并重新连接串口，明确开始新会话。

当前 AIR V0 的 `command_policy=PREFLIGHT_ONLY`，任务开始后不再发送 AIR 命令。未来 `MISSION_ALLOWED` 是否允许链路发送由 Capability 决定，具体命令仍可能被飞控状态机拒绝。

## 7. Calibration / Alignment / START

可启动的采样校准列表来自本次握手确认的 `calibration_mode_mask`，不会从 mask 推断当前模式或完成状态：

| mask | Start Calibration 可选项 |
|---|---|
| `0x01` | 无；显示“本工程不执行采样校准，使用单位校正（NONE）”，禁用开始按钮 |
| `0x03` | ONE_FACE |
| `0x05` | SIX_FACE |
| `0x07` | ONE_FACE、SIX_FACE |

未知高位保留并在链路详情中显示，已知位照常使用。每次断开/重连及成功握手刷新校准控件，包括隐藏的对话框；不复用上一飞控的模式。重置校准在预飞面板与原对话框中均保留，使用既有 `CAL_RESET`。

NONE 不属于用户采样操作，GSHC 永不发送 `CAL_START(NONE)`。当前模式由 `PREFLIGHT_STATUS` / Calibration STATUS 给出；NONE 就绪时显示“单位校正（未执行单面/六面采样校准）”，ready=0 时仍保留飞控的真实 state/ready，不自动推进。

- ONE_FACE：飞控自动推进，上位机不发送 CAL_FACE；
- SIX_FACE：用户摆放对应面后发送 CAL_FACE。ACK OK 仅代表 accepted；只有 `CALIBRATION_FACE PASSED` 或快照中的 `completed_face_mask` 才显示完成。
  六面全部完成进入 `READY` 后，六个面仍可单独点击重采；上位机继续发送既有 `CAL_FACE`，不新增命令，也不会自行清除其他五面的完成标记。

Controller 在 CAL_START 的公共入口、通用命令入口和每次重试发送前核对：Capability 握手完成、mode 为 ONE_FACE/SIX_FACE、对应 bit 已置位；本地拒绝不生成 TX。`CAL_START ACK OK` 仅表示接受，完成等待后续状态；`BAD_PARAM` 提示 build 不支持或参数错误，不自动尝试其他模式。`BAD_STATE` / `BUSY` 不视为断链；ACK 超时保持现有 800 ms、最多 3 次重试，保留 Capability。

详细操作见 [校准用户说明](docs/CALIBRATION_USER_GUIDE.md)，自动验证与 SS0.5 实机检查见 [验证记录](tests/validation/README.md)。

Alignment START 在 Calibration ready 后可用。ACK OK 仅代表 accepted；`PREFLIGHT_STATUS.alignment_ready` 是最终判据，Alignment STATUS event 只提供即时提示，不能在 STALE 后独立恢复 ready。

Alignment READY / FAILED 的 STATUS `arg1` 指向一次 Sensor Snapshot。`SENSOR_STATUS` 帧按 `snapshot_id/index/total` 有界归集，终止事件到达后标记完整或不完整；即使缺帧，详情仍展示已收到的项目。详情按 IMU、GNSS、其他 sensor ID/instance 排序。未知 sensor/detail 原样接受并显示为 `Unknown Sensor 0xNN` / `Unknown detail 0xNN`，不会被误判为 profile 不兼容。STALE 的 `arg1=0xFF` 不创建空快照，界面继续注明并展示上一次 Alignment 快照。

“传感器状态 / 详情”只读取本机会话缓存，AIR V0 没有也不会发送 sensor query command。GNSS 的动态可用状态仍单独保留在主页面；惯性数据显示采用通用的“惯性 / 姿态”名称。Barometer 只在实际快照中作为 inventory 项出现，不再是写死的 Alignment source 行。

校准采集中的当前问题来自 `CALIBRATION_DIAGNOSTIC`。`NONE` 会清除提示；其他 reason 只用于解释为何正在等待或重新采集，不会把 Calibration 状态改成 FAILED。六面校准会同时显示对应 face。

Alignment `STALE` 表示对准后检测到移动。上位机会立即撤销 ready、禁用 START 并重新启用“开始初对准”，但不会自动重启对准，也不会根据当前姿态自行恢复 READY。STALE 后只有新的 `PREFLIGHT_STATUS` 快照可以重新确认 `alignment_ready=1`。

START 按钮由权威预飞快照驱动，至少要求 Capability、Calibration、Alignment、System、UNLOCK 和 `start_block_reason=OK` 均满足。若 START ACK 丢失，MISSION_START 或第一帧 FLIGHT_STATE 会清除 START 重试、标记任务开始并自动切换一次飞行页。用户随后可手动切回任意页面，后续遥测不会抢焦点。

## 8. JSONL 与会话

默认用户数据根目录：

```text
D:\SilverStar_GSHC_Data
```

可在“后期处理”页通过“选择数据目录”打开应用内设置弹窗；路径框默认显示当前目录，只有“浏览”按钮会打开系统文件夹选择器。目录改变时可勾选迁移旧日志和结果，并选择同名文件处理方式：覆盖（默认）、重命名并添加 `(1)`、跳过或停止并报错。迁移是移动操作；跨磁盘时会先把文件安全写入新目录，JSON 配置切换成功后再删除源文件，选择“跳过”的冲突文件会保留在旧目录。目录、子目录、迁移选择和冲突策略记录在 `config/user_paths.json`；也可通过环境变量 `SILVERSTAR_GSHC_DATA_ROOT` 覆盖。QSettings 使用 organization=`SilverStar`、application=`SilverStar_GSHC`。

串口连接时创建 provisional session 日志；只有明确的 PC 串口断开/重连才建立新会话。Capability 广播本身不再被推测为“飞控重启”，也不会触发自动日志 rollover。

每条记录包含 wall timestamp，并补充 `host_monotonic_ns`；RX 记录尽早保存 `host_rx_monotonic_ns`。日志包括：

```text
GSP raw / parsed
AIR raw
CAPABILITY
PREFLIGHT_STATUS
SENSOR_STATUS
PREFLIGHT_STATE
STATUS
ACK
FLIGHT_STATE
CAPABILITY_ACK_TX（含 Capability seq、PC cmd seq、attempt、retry）
```

预飞页的“飞控 AIR 链路”只显示未发现飞控、等待握手、握手中、已连接、协议不兼容或链路异常等短状态。“详情”对话框集中显示 Capability RX、PC→GS AIR_TX 请求、串口实际写入、GSP AIR_TX ACK、地面站 TX/RX/CRC、AIR ACK、PREFLIGHT_STATUS 恢复、接收积压和 RSSI/SNR，避免把“PC 已连地面站串口”误认为“飞控已握手”。同时显示未知校准能力位、Capability/PREFLIGHT_STATUS 接收计数、最近 AIR 命令反馈、AIR 下行和预飞快照距今时间；即使 GS 状态仍在更新，也能看到飞控下行是否停滞。周期刷新仅在内容变化时改写文本，并保留用户滚动位置和文字选择；用户原本位于底部时继续跟随新底部。

完整 Event History 位于预飞页，按时间从旧到新排列并自动滚动到底部；GUI 只保留最近 200 条。飞行页显示 Mission State，只有 MISSION_START、LAUNCH、PARACHUTE_DEPLOY、LANDING 等权威事件才推进对应阶段。首帧 FLIGHT_STATE 最多回退显示 Mission Active，不会凭高度或速度臆造发射、开伞事件。

实时曲线删除 10 秒以前的数据只影响 GUI 内存，不影响 JSONL。

界面语言只影响显示。内部枚举、状态值、命令名和 JSONL 字段始终使用协议定义的规范英文名称；切换语言不会重建串口/协议线程、状态模型或 3D 场景，当前窗口、弹窗、详情框和事件历史会立即重译。主题同样由 QSettings 保存，并同步普通控件、详情框、实时曲线、3D 背景、网格和标签颜色。

## 9. 后期处理

```bat
python -m processing.fake_log_generator --output logs\fake_flight_log.jsonl --duration 90 --seed 42
python -m processing.flight_log_processor logs\fake_flight_log.jsonl --output-root data --language en_US --theme light
```

后处理优先读取 `AIR_PARSED`；仅在缺失时使用 Profile 0 raw fallback。没有 Capability 时不会猜测 IMU 量程。正式飞行窗口由 MISSION_START 或第一帧 FLIGHT_STATE 开始，PREFLIGHT_STATE 不混入飞行曲线。manifest 会保存 Capability、最终预飞状态、任务时长、丢包、导出语言、主题、勾选项和部分失败信息。

GUI 的导出项为：处理后数据 TXT、摘要 TXT、图表 PNG、姿态/轨迹 3D GIF、会话 manifest；首次打开默认全部勾选。导出语言默认 Follow UI，也可与界面语言独立选择。中文产物统一使用 `_ZH`，英文产物使用 `_EN`；PNG、GIF 及其 3D 帧同样带语言后缀，图标题、坐标轴、标注和 3D 文字按导出语言渲染。图表与 3D 背景采用当前应用主题，深浅色下均使用相应文字、网格和坐标轴颜色。单项生成失败会记录并继续其他项目。

## 10. 绿色版打包

```bat
packaging\build_pyinstaller.bat
```

输出为 `dist/SilverStar_GSHC/SilverStar_GSHC.exe`。必须复制整个目录，不能只复制 exe。`SilverStar_GSHC.spec` 和 `packaging/version_info.txt` 固定产物名与 Windows metadata；`installer/SilverStar_GSHC.iss` 生成 `SilverStar_GSHC_Setup_v0.0.3.exe`。发布前应在源码版和打包版各验证一次串口、Capability、预飞流程、START 恢复、主题、3D、10 秒曲线和多语言 JSONL 后处理。
