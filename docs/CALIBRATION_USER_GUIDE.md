# 校准操作说明

连接地面站串口后，等待“飞控 AIR 链路”显示已连接。可用的采样流程由本次 Capability 握手确认：

| calibration_mode_mask | 开始校准菜单 |
|---|---|
| 0x01 | 没有采样模式，开始校准禁用 |
| 0x03 | 单面校准（ONE_FACE） |
| 0x05 | 六面校准（SIX_FACE） |
| 0x07 | 单面校准、六面校准 |

0x01 显示“本工程不执行采样校准，使用单位校正（NONE）”。NONE 不出现在开始菜单，也不需要发送 CAL_START。当前模式为 NONE 时显示“单位校正（未执行单面/六面采样校准）”；是否就绪继续读取旁边的 ready 和 state。ready=0 时等待飞控的真实状态，不会自动执行校准或初对准。

ONE_FACE 由飞控自动采集。SIX_FACE 需要放置相应面并保持静止，再点击对应采集按钮；ACK OK 只代表接受，等待状态/完成面掩码后才显示通过。READY 后仍可点击单个已完成面重新采集，其余五面由飞控快照保留。

预飞“校准”面板的重置按钮和校准对话框中的重置按钮都使用原 CAL_RESET，0x01 同样保留。重置结果以飞控后续状态为准，不据 Capability 推测 ready。校准 ready 后可手动开始初对准；ALIGN_START ACK OK 也不表示完成，等待 alignment state/ready。

## 命令反馈与重新连接

- BAD_PARAM：该 build 不支持模式或参数错误，核对链路详情中的 Capability。上位机不会自动尝试另一种模式，也不会发送新查询命令。需要重新取得 Capability 时明确断开并重连串口。
- BAD_STATE / BUSY：飞控状态不允许或暂时忙，链路仍保持握手结果，按最新状态决定后续操作。
- ACK timeout：沿用 800 ms 超时与最多 3 次 retry（共 4 次 TX）；停止重试后恢复操作入口，保留握手和真实状态。无 ACK 不能推断命令从未执行。
- 切换不同飞控工程前断开再重连。隐藏的校准菜单也会更新，上一工程的单面/六面模式不会留到新会话。

## 校准/初对准时下行停止

打开“飞控 AIR 链路 → 详情”，依次对照 Capability RX、PC GSP AIR_TX 请求、串口实际写入、GSP ACK、GS TX/RX/CRC、AIR ACK 和 PREFLIGHT_STATUS，以及 RSSI/SNR。

新增的“距最近 AIR 下行”和“距最近快照”在无新下行时继续增长。若串口仍收到 GS 状态但 AIR 年龄增加，问题不等同于 PC 串口断开。请求增加而串口写入不增加应检查 PC 串口队列/驱动；GSP 成功但 GS TX 不增加应检查地面站发送；GS TX 增加但无 AIR 下行，应结合空口与飞控控制台判断。PC 诊断不能单独证明空口故障或飞控运行故障，真实确认步骤见 [验证说明](../tests/validation/README.md)。

未知能力位只在详情中显示，不影响已知位或生成未知操作。完整 AIR 布局见 [AIR_PROTOCOL.md](AIR_PROTOCOL.md)。
