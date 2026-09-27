# 导航准备与实时健康联合状态

本轮用户授权三个仓库联合实现；本仓库负责 GSHC 状态模型、Controller 门禁、固定帧协议消费、GUI 与模拟回归。FCCG 的 AIR codec、遥测生产者和对应 Host fixture 同步实现，由联合主任务统一集成。FLP 的数值复算由独立工作流实现。精确测试结果集中在根 [VALIDATION](../VALIDATION.md)。

接口权威为 [AIR_PROTOCOL](AIR_PROTOCOL.md) 与逐字同步的 [navigation_v1.json](contracts/navigation_v1.json)。保留 AIR M0 基础身份、9-byte帧和 GSP 外封装；新 NAV_SUBSCRIBE 在基础握手成功后执行一次，通过新 nonce 对应的 NAV_CAPABILITY 确认。旧固件 BAD_CMD 不破坏基础握手，显示 UNSUPPORTED 并阻止 START。未知 schema、算法和字段不猜成 KF_6 或健康。

预飞页保留原动作与事件历史，增加九个准备步骤：链路、设备配置、校准、姿态、GNSS解、GNSS原点、气压参考、估计器初始化、START许可。ALIGN_START 的展示名称改为准备导航；ACK仅代表受理。导航位由固件实际 kernel 准备成功产生，不能由初对准完成代替。required mask 来自实际配置，Pure INS 可以不要求 GNSS。公共 START、通用命令和重试入口均检查当前会话/代次、原预飞前置条件及2秒内完整机载快照。校准/重准备立即失效，断线/BOOT丢弃旧nonce，重复帧不刷新TTL。

飞行页保留轨迹和姿态，新增折叠五组详情。Pos EN、Pos U、Vel EN、Vel U、Baro U 分别显示结果、质量、原因、成功融合年龄、R倍率、NIS、接收/物理有效/尝试/恢复年龄和恢复计数。主状态与成功年龄TTL为3秒，详情为10秒；字段独立，不合成虚假的原子快照。部分状态不显示绿色。

系统详情读取真实 GNSS有效字段与接收时刻、IMU质量标记和核验量程/配置代次、LoggerBus队列诊断。削顶、接收时刻代理、样本对时序等如实显示；未知配置和失效样本不会显示正常。Logger溢出、引导抑制、状态拒绝和容量拒绝分别显示。全部新消息进入既有JSONL，不修改原始数据。

保留 Light/Dark、简体中文/English、后台串口/协议工作线程和有界模型。模拟用 Qt offscreen 与repo本地设置/输出；没有访问串口，没有烧录或功率输出。已验证软件内的GSP透明封装，但未持有地面接收板固件/硬件验证，必须在 JY901B+NEO-M9N 下一次台架检查新type不被网关丢弃。当前硬件状态为 `NOT_FIELD_VALIDATED`，新芯片实物仍 `HARDWARE_UNVERIFIED`。

下一步台架只由用户另行执行：确认新nonce握手、移除GNSS不能START、原点/初始化完成才READY、失链及重启立即失效、低卫星有效解显示降权、持续拒绝显示退化/无效、五组年龄真实增长、IMU削顶/时序质量和日志溢出可见。检查无线吞吐，不能用延长主状态TTL隐藏掉帧。
