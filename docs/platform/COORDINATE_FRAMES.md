# SilverStar 坐标系与四元数约定

> 本文件是GSHC仓库中的只读平台参考镜像；完整权威版本由SilverStar_FCCG `docs/platform/`维护。

> **项目：SilverStar**  
> **文档版本：0.0.10**
> **状态：Draft / 未发布**  
> **适用范围：SilverStar 0.0.10**

> 当前参考基线为 Platform 0.0.10。GSHC 对 AIR M0 的编码与换算严格遵循 [AIR_PROTOCOL](../AIR_PROTOCOL.md)，不因文档整理改变 wire。

## 1. 导航系

SilverStar统一使用ENU：

```text
X / East  向东为正
Y / North 向北为正
Z / Up    向上为正
```

GNSS NED速度转换：

```text
vE = velE
vN = velN
vU = -velD
```

## 2. 机体系

系统机体系必须由具体安装Profile定义，并通过固定轴变换将传感器坐标转换为body坐标。Algorithm只接收body坐标数据。

必须区分传感器外壳丝印轴与SilverStar归一化body轴。当前物理安装中，JY901B外壳图标`+Y`沿火箭纵轴指向头锥；设备配置为VERTICAL并经过现有安装/驱动映射后，交付给System与Algorithm的归一化body `+Z`才沿火箭纵轴指向头锥。FlightRecovery只使用后者，不直接解释传感器丝印轴。

`SYSTEM_FLIGHT_ROCKET_LONGITUDINAL_AXIS`当前正确默认值为`SYSTEM_BODY_AXIS_Z_POSITIVE`。未来安装方式或JY901B orientation/remap变化时，必须同步修改安装Profile/用户配置并重新验证，不能在FlightRecovery判据源文件中临时交换轴，也不能把外壳`+Y`直接写成内部body `+Y`。

## 3. 四元数

- 顺序：WXYZ；
- 代数：Hamilton；
- `q_nb`：将body向量主动旋转到navigation ENU；
- 软件增量右乘：`q_nb,new = q_nb,old ⊗ δq_body`；
- 所有输出前归一化；
- `q`与`-q`表示同一旋转；硬件四元数窗口求均值前必须统一hemisphere/sign。

运行期权威姿态只使用四元数。Euler angles仅允许调试显示、人工诊断和离线可视化，不参与Alignment、INS、KF、TILT、Landing或坐标变换。Calibration与Initial Alignment的完整契约见[CALIBRATION_AND_ALIGNMENT.md](CALIBRATION_AND_ALIGNMENT.md)。

## 4. SilverStar ENU yaw

SilverStar yaw是body +X在ENU水平面投影相对East的几何方位角：

```text
yaw = atan2(body_x_north, body_x_east)
```

- `0 deg`：East；
- `+90 deg`：North；
- `-90 deg`：South；
- `±180 deg`：West。

这不是`0=North, 90=East`的compass heading，也不是任一Euler sequence的分解角。需要compass语义时必须明确命名为`heading`。当body +X的水平投影接近零时yaw不可定义，统一helper返回invalid且不得产生NaN。

TILT先归一化`q_nb`，按`v_n = q_nb × v_b × conjugate(q_nb)`把配置的body纵轴旋转到ENU。默认`SYSTEM_TILT_REFERENCE_INITIAL_AXIS`在START冻结初始纵轴，初始偏差因此为0；`SYSTEM_TILT_REFERENCE_NAV_UP`兼容模式才与ENU `+Z`比较。运行期只用归一化向量dot和阈值余弦，不依赖Euler角。当前默认确认时间为0 ms，首个新的有效越阈Estimator样本立即锁存；正确认时间必须由连续新样本的时间戳跨度满足，重复快照不累计。

## 5. 比力与重力

IMU加速度为比力。机械化将补偿后body速度增量旋转到ENU，再加入重力项。工程唯一的本地重力配置是`SYSTEM_LOCAL_GRAVITY_MPS2=9.78f`，该值用于固件本地模型与算法配置。AIR 加速度量化的标准重力为 9.80665 m/s²，GSHC 解码继续使用 AIR 规范中的标准重力，不能用本地重力替换协议换算系数。

## 6. 原始值与统一值

日志和接口必须区分：

- sensor raw；
- 物理单位但仍在sensor frame；
- body frame；
- navigation ENU；
- 硬件原始四元数；
- System统一 `q_nb`。
