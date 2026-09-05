# 校准操作说明

连接地面站串口后，等待“飞控 AIR 链路”完成 Capability handshake。可用模式来自当前 `calibration_mode_mask`，完整契约见 [`AIR_CALIBRATION_CONTRACT.md`](AIR_CALIBRATION_CONTRACT.md)。

## 1. 菜单

| mask | “开始校准”行为 |
|---|---|
| `0x01` | 无需选择；飞控自动选择 NONE/单位校正，等待真实上报 |
| `0x03` | 使用默认校正、单面校准 |
| `0x05` | 使用默认校正、六面校准 |
| `0x07` | 使用默认校正、单面校准、六面校准 |

当build提供任何采样流程时，“使用默认校正（不进行采样）”始终存在，并发送现有 `CAL_START(NONE)`。它不是软件层把ready改成true；状态由飞控完成。

当mask仅为`0x01`时，不需要发送NONE，因为飞控启动/Reset已经自动`NONE/Identity/READY`。

## 2. 默认校正
1. 选择“使用默认校正”；
2. GSHC发送`CAL_START(NONE)`；
3. `ACK OK`只表示accepted；
4. 等待真实 `PREFLIGHT_STATUS` 的 `mode=NONE, state=READY, calibration_ready=1`；仅有 ACK OK 或缺少 ready 字段的状态事件不会将 NONE 提升为就绪；
5. 然后可开始初对准。

## 3. OneFace
仅bit1存在时可选。ACK OK后由飞控采集/检查/计算，最终看真实READY。

## 4. SixFace
仅bit2存在时可选。进入WAIT_FACE后按面发送现有`CAL_FACE(face)`；已完成面在READY下仍可单独重采。

## 5. Reset
继续发送现有`CAL_RESET`。无采样build自动恢复NONE/READY；有采样build重新等待用户选择默认NONE或采样procedure。GSHC不本地伪造READY。

## 6. ACK
- `OK`：accepted；
- `BAD_PARAM`：mode编码非法；
- `REJECTED`：合法OneFace/SixFace但当前build未编入；
- `BAD_STATE`：当前生命周期不允许；
- `BUSY`：事务忙。

这些结果都不等于链路断开，不清除 Capability，也不自动尝试其他模式。ACK 超时使用现有有界重试；快照可恢复丢失的 ACK。默认菜单高亮、连接、刷新或切换语言不会代替用户发送。

## 7. 初对准
`ALIGN_START`只要求飞控真实`calibration_ready=1`及其他预飞门条件。ACK OK同样只表示accepted，完成看alignment state/ready。

## 8. 链路停止时
打开“飞控 AIR 链路→详情”，依次检查Capability RX、PC AIR_TX request、serial write、GSP ACK、GS TX/RX/CRC、AIR ACK、PREFLIGHT_STATUS、RSSI/SNR和最近AIR年龄。GS仍发而AIR完全停止时，优先检查飞控HardFault/assert/stack overflow/runtime fault record。
