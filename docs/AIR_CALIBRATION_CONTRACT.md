# SilverStar AIR M0 Calibration Contract

> 适用：SilverStar Platform 0.0.10 / AIR M0  
> 状态：首次正式发布前冻结候选；不改变 AIR M0 wire layout

本文定义 FCCG、飞控和 GSHC 之间的 Calibration 共同契约。实现细节分别由各工程文档负责。

## 1. 三个必须分开的概念

1. **Build Capability**：FCCG 本次工程编入哪些采样校准流程，由 `CAPABILITY.calibration_mode_mask` 表示。
2. **Selected Calibration Transaction**：本次预飞实际选择 `NONE`、`ONE_FACE` 或 `SIX_FACE`。
3. **Effective Calibration State**：飞控真实的 `mode/state/ready` 和 bias/scale；飞控状态与 SSLOG `CALIBRATION_RESULT` 是权威。

GSHC 不得在本地伪造 `calibration_ready`。

## 2. Capability 位图

```text
bit0 = NONE
bit1 = ONE_FACE
bit2 = SIX_FACE
bit3..7 = reserved
```

| FCCG 采样流程选择 | AIR mask | GSHC 操作 |
|---|---:|---|
| 无 | `0x01` | 不需要选择，飞控上电自动 NONE |
| OneFace | `0x03` | 使用默认校正（NONE）、OneFace |
| SixFace | `0x05` | 使用默认校正（NONE）、SixFace |
| OneFace + SixFace | `0x07` | 使用默认校正（NONE）、OneFace、SixFace |

`NONE` bit 始终存在。未知高位只进入诊断，不产生未知操作，也不应单独导致 Capability 不兼容。

## 3. 没有采样流程时

当 `modes.calibration=[]`，飞控启动后自动建立：

```text
mode  = NONE
state = READY
ready = 1
accel_bias = {0,0,0}
gyro_bias  = {0,0,0}
accel_scale = {1,1,1}
gyro_scale  = {1,1,1}
completed_face_mask = 0
samples = rejects = retries = 0
```

GSHC 不发送 `CAL_START(NONE)`；收到真实 `NONE/READY` 后即可在其他预飞条件满足时执行 Alignment。

## 4. 编入采样流程时仍必须允许默认校正

当 build 含 OneFace 和/或 SixFace 时，GSHC 的“开始校准”菜单必须始终额外提供：

```text
使用默认校正（不进行采样）
```

该选项发送现有：

```text
CAL_START
param0 = NONE
```

由飞控完成真实事务，包括 active IMU source 选择/锁定、旧 Alignment 失效、Calibration runtime 重置以及 `NONE/Identity/READY`。这不是 GSHC 本地跳过。

## 5. OneFace / SixFace

- `ONE_FACE` 只有 bit1 置位时允许发送。
- `SIX_FACE` 只有 bit2 置位时允许发送。
- SIX_FACE 使用现有 `CAL_FACE(face)` 逐面采集；READY 后允许按现有协议重采单个面。
- GSHC Controller 必须在发包前再次检查 Capability，不得只依赖 GUI 下拉框。

## 6. ACK 语义

`ACK OK` 只表示 Calibration Transaction 被接受，不表示完成。

| 条件 | 当前 AIR ACK 语义 |
|---|---|
| 成功受理 | `OK` |
| mode 编码非法 | `BAD_PARAM` |
| 合法 OneFace/SixFace，但当前 build 未编入 | `REJECTED` |
| 生命周期/状态不允许 | `BAD_STATE` |
| 当前操作忙 | `BUSY` |

`REJECTED/BAD_STATE/BUSY` 不等于链路断开，也不得清除 Capability handshake。

## 7. Reset

`CAL_RESET` 保持现有 AIR M0 命令。

- build 无采样流程：Reset 后自动恢复 `NONE/Identity/READY`。
- build 有采样流程：Reset 后重新等待用户选择本次事务；可再次选择默认 NONE、OneFace 或 SixFace。

GSHC 不在本地伪造 Reset 后 READY。

## 8. Alignment 前置条件

Alignment 依赖的是飞控真实：

```text
calibration_ready == 1
```

而不是“用户是否执行过采样校准”。因此三条正常路径都合法：

```text
自动 NONE -> ALIGN_START
显式 CAL_START(NONE) -> ALIGN_START
OneFace/SixFace 完成 -> ALIGN_START
```

`ALIGN_START ACK OK` 也只表示 accepted，最终完成看 `alignment_state/alignment_ready`。

## 9. CALIBRATION_RESULT

启用 SSLOG 时，`CALIBRATION_RESULT` 始终是 Required Record，表示本次任务实际生效的 correction 快照。`NONE/Identity` 是完整合法结果，不代表“没有 Calibration”。

FLP 后续应区分：

- FCCG 配置允许哪些采样流程；
- 本次实际选择的 mode；
- 实际生效的 bias/scale。

## 10. 不新增协议

不需要 `CAL_SKIP`、`CAL_DEFAULT`、`CAL_ALREADY_DONE`、`CAL_QUERY` 或任何新 Capability 字段。现有 `CAPABILITY + PREFLIGHT_STATUS + CAL_* + ACK` 已足够。
