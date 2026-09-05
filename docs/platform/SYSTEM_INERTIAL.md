# SystemInertial 输入边界参考

> 本文件是 GSHC 仓库中的只读平台参考镜像；完整权威版本由 SilverStar_FCCG `docs/platform/` 维护。基线：Platform 0.0.10。

## 数据与事务边界

```text
设备物理样本 -> Source / Factory correction -> 安装坐标变换
    -> Virtual IMU -> 本次 Mission Calibration -> INS / AIR Telemetry
```

Source / Factory correction 与本次 Mission Calibration 是不同层。前者描述设备固有误差及安装，后者在最终 Virtual IMU 上选择 NONE/ONE_FACE/SIX_FACE，规则见[共同校准契约](../AIR_CALIBRATION_CONTRACT.md)。

存在多个 IMU 实例时，飞控在 Calibration 事务前选择并锁定 active IMU；显式 NONE 同样执行选择/锁定，后续 Alignment 使用该事务对应的 correction。GSHC 不根据当前显示数值自行选择 IMU、不在本地做冗余切换，也不把 Capability mask 当作 source 或 ready。

## GSHC 可观察内容

AIR M0 继续只携带一份 canonical inertial/attitude 数据，不因多实例增加高频帧。通用 Sensor Snapshot 可使用 sensor ID 与 instance ID 区分设备，GSHC 保留未知 ID。

加速度/角速度的单位、量程与 WXYZ 四元数编码以 [AIR_PROTOCOL](../AIR_PROTOCOL.md) 为准；机体到 ENU 的关系见[坐标系](COORDINATE_FRAMES.md)。GSHC 显示与记录飞控传来的校正后数据，不重复应用飞控 calibration bias/scale。

Calibration 尚未 READY 时，飞控可用 identity correction 保持预飞遥测。这不表示 Calibration 已经 READY；是否可 Alignment 仍由真实状态标志决定。

## 不在镜像维护的内容

多源描述符、FIFO、Assembler、时间同步、Device Adapter 与具体硬件资格均由 FCCG 平台规范负责。本文件不声明某个具体 build 的设备数量或选型，不维护 MCU/Board/Build/Storage 规则。
