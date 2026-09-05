# Telemetry Service 与 Transport 接口

> 本文件是GSHC仓库中的只读平台参考镜像；完整权威版本由SilverStar_FCCG `docs/platform/`维护。
> **0.0.10增量**：AIR M0可绑定ordered telemetry候选链，但同时只收发一个active transport。连续10次真实本地TX timeout才切换，任一成功发送清零；只有一个链路或备用耗尽时继续按正常任务周期有界重试。该机制不判断地面站是否真正收到，不是RF端到端健康。

> 文档版本：0.0.10
>
> 适用范围：SilverStar 0.0.10

## 1. 分层

```text
TelemetryService
  -> AIR遥测协议M0编解码、Capability、ACK cache、状态事件和调度
SystemTelemetry_* direct interface
  -> 字节帧收发、MTU、链路健康、完整性能力
SX1281 Telemetry Adapter
  -> MCU-independent SX1281 Device -> Platform SPI/GPIO/time -> STM32F4 backend
```

TelemetryService 不依赖 HAL 或 SX1281 具体类型；Transport 不解释 AIR 命令。AIR 应用层不含软件 CRC，当前完整性由 SX1281 LoRa 硬件 CRC 提供。

## 2. Transport直接接口与约束

```c
const char *SystemTelemetry_NameGet(void);
SystemDeviceResult SystemTelemetry_Init(void);
SystemDeviceResult SystemTelemetry_Start(void);
SystemDeviceResult SystemTelemetry_Stop(void);
SystemDeviceResult SystemTelemetry_Send(const uint8_t *data, uint16_t length);
SystemDeviceResult SystemTelemetry_Receive(uint8_t *data,
                                           uint16_t capacity,
                                           uint16_t *length);
void SystemTelemetry_Process(void);
SystemDeviceResult SystemTelemetry_InfoGet(SystemDeviceInfo *info);
SystemDeviceResult SystemTelemetry_CapabilitiesGet(uint32_t *capability_mask);
SystemDeviceResult SystemTelemetry_HealthGet(SystemTelemetryHealth *health);
SystemDeviceResult SystemTelemetry_IoDiagnosticsGet(
    SystemDeviceIoDiagnostics *diagnostics);
SystemDeviceResult SystemTelemetry_SelfTestRun(SystemDeviceSelfTestResult *result);
SystemDeviceResult SystemTelemetry_MtuGet(uint16_t *mtu);
```

System通过这些符号直接调用所选Device Adapter，不存在Ops对象或运行期注册。不支持功能返回`SYSTEM_DEVICE_UNSUPPORTED`。`SystemTelemetry_Send()`只在调用期间借用输入buffer；`SystemTelemetry_Receive()`把一帧复制到调用者buffer并填写长度；NULL参数返回`SYSTEM_DEVICE_INVALID_ARGUMENT`；`SystemTelemetry_Process()`非阻塞推进状态机。

FCCG 配置可提供 ordered telemetry 候选链；同一时刻只有一个 active transport。连续真实本地 TX timeout 达到切换条件后选择后备候选，成功发送清零。GSHC 通过当前通道接收既有 AIR，不创建跨通道拼接或新的握手规则。

Transport 必须完整容纳现有 AIR 帧并提供完整性保护。具体设备射频参数、MTU 资格和目标组合由 FCCG 维护；GSHC 不推断或修改射频配置。

## 3. Capability 与命令策略

Capability 固定9字节，包含`air_profile_id`、`command_policy`、Calibration mode mask、`sensor_summary_flags`和accel/gyro满量程。byte5只声明IMU/GNSS/AUX存在性及Sensor Snapshot支持，不再编码固定Alignment source。`air_profile_id=0`决定全部AIR布局及编码；`command_policy`独立决定任务阶段是否处理入站命令。当前配置为`PREFLIGHT_ONLY`。

`TelemetryCapabilityState` 为 `NOT_ACKED/ACKED/DISABLED_FOR_FLIGHT`。进入 PREFLIGHT 后立即发送 Capability，未 ACK 时 1 Hz 重发。ACK 必须匹配最近一次成功发送的 seq 和AIR M0的技术profile numeric值0；成功后停止广播，并在 ACK 和已排队关键事件之后的首个安全 TX 机会发送 `PREFLIGHT_STATUS`。本上电周期不支持重新接管握手。

`calibration_mode_mask`来自`SystemCalibration_CapabilityMaskGet()`。`sensor_summary_flags`来自Target静态Sensor Descriptor与直接Interface状态；Barometer、Magnetometer等辅助传感器不占独立Capability bit，其实际状态由`SENSOR_STATUS.sensor_id/instance_id`描述。

未 ACK 时只接受 PING 和 Capability ACK；AIR START 还要求 interlock UNLOCK。Console/Local START 不依赖 AIR 会话状态。

## 4. 状态、事件与数据流

- `PREFLIGHT_STATUS`：9 字节状态快照；Capability ACK 后立即一次、之后 1 Hz，重要状态变化可提前；START 成功后停止。
- `SENSOR_STATUS`：9字节逻辑Sensor Snapshot成员；只在Alignment READY/FAILED事务结束时自动发送，不周期广播，也没有request命令。
- `STATUS`：边沿事件；Calibration face/ready、Calibration diagnostic、Alignment ready/failed/stale、GNSS fix 和 SELFTEST 等事件继续发送，不能被快照替代。
- `PREFLIGHT_STATE`：26 字节，START 前 5 Hz。
- `FLIGHT_STATE`：50 字节，START 后 5 Hz。

所有AIR IMU字段使用校准后的物理量。Telemetry从Virtual IMU入口取得未校准物理样本，读取READY correction，与INS共用`SystemCalibration_ImuCorrectionApply()`，最后按AIR M0量化为`int16_t`。Calibration未READY时仅Telemetry使用identity correction，保证预飞数据流不中断；Calibration采集算法仍消费原始物理样本。量化饱和次数只保存在`TelemetryServiceDiagnostics`，不增加AIR字段。

## 5. 队列与调度

ACK队列、ACK cache、STATUS队列和System拥有的Sensor Snapshot cache均为静态有界存储。重复command `seq+cmd_id`只重发缓存ACK，不重复副作用。未知command的`BAD_CMD` ACK原样回显未知command byte，且仍为9字节。

START 前优先级：

```text
ACK > active SENSOR_STATUS burst > snapshot terminal ALIGNMENT STATUS > critical STATUS > 到期 Capability > PREFLIGHT_STATUS > PREFLIGHT_STATE
```

START 后优先级：

```text
已在预飞接受的START最终ACK > critical STATUS > FLIGHT_STATE
```

1 Hz 广播不得破坏 5 Hz 数据流。Calibration、face、Alignment 和 GNSS 事件继续按真实变化排队。`CALIBRATION_DIAGNOSTIC(0x0D)`复用critical STATUS队列：只有`face+reason`发生有意义变化时排队，同一reason不得随IMU采样重复发送，非NONE变为NONE时必须发送一次清除事件。它只解释等待或窗口重采原因，不把Calibration事务改为FAILED。

Alignment进入READY/FAILED时冻结一次Sensor Snapshot并分配递增snapshot ID；发送顺序固定为IMU、GNSS、其余sensor ID/instance升序，最后发送`STATUS ALIGNMENT.arg1=snapshot_id`。Alignment在Capability ACK前完成时保留pending snapshot；ACK允许抢占，其他STATUS和周期包不插入burst。进入`STALE(5)`只排队一次`STATUS ALIGNMENT(arg1=0xFF)`，不重新生成snapshot。周期`PREFLIGHT_STATUS.byte6`高四位固定0、低四位只编码整体Alignment state；source局部ready仅留在System/Console诊断。STALE只能由显式新`ALIGN START`清除；START成功后有效性监视停止。

## 6. START 判断与 START 后 RX

`PREFLIGHT_STATUS.start_block_reason` 与实际 AIR START ACK 调用同一个 Lifecycle 预检查和同一个 result/reason 映射。Capability 和 lock 是 AIR 层附加条件；Calibration、Alignment、System、Attitude 和 hooks 由 Lifecycle 判断。`HOOKS_UNAVAILABLE` 与 `PREPARE_FAILED` 有独立 AIR result，不能落入 `REJECTED`。

START 成功后停止 Capability、`PREFLIGHT_STATUS` 和 `PREFLIGHT_STATE`。预飞应用RX许可与已经接受的AIR START事务分别记账：Lifecycle切换到FLIGHT和关闭预飞RX不得清除尚未排入ACK队列的START最终响应，也不得清空已有ACK队列；响应入队并缓存最终结果后才清START pending。当前 `command_policy=PREFLIGHT_ONLY`：Transport 继续维持接收状态，但应用层丢弃新的入站帧，不调用 AIR Parser，不执行命令，也不产生新CMD ACK；这不阻止发送FLIGHT前已接受START的最终ACK。未来允许任务期命令时可保持AIR M0和技术profile numeric值0，只修改独立的command policy和对应权限配置。

## 7. 诊断

`TelemetryService_DiagnosticsGet()` 返回值拷贝，包含 STATUS drop、量化饱和次数、Capability 发送次数、Capability 状态和 ACK 标志；不会改变链路或握手状态。

完整字节布局以 [AIR_PROTOCOL.md](../AIR_PROTOCOL.md) 为唯一依据。
