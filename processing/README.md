# processing

SilverStar 0.0.8 / `AIR_PROFILE_COMPACT_V0` 离线飞行日志处理模块。

## 模拟完整会话

```bash
python -m processing.fake_log_generator --output logs/fake_flight_log.jsonl --duration 90 --seed 42
```

模拟日志明确带有 `SIMULATION_VALIDATION` 标识，并包含：

- CAPABILITY；
- PREFLIGHT_STATUS / PREFLIGHT_STATE；
- 六面 Calibration 事件与最终 READY；
- Alignment READY；
- MISSION_START；
- 5 Hz FLIGHT_STATE、链路质量、开伞和着陆。

## 处理日志

```bash
python -m processing.flight_log_processor logs/fake_flight_log.jsonl --output-root data --language zh_CN --theme dark
```

处理器优先使用 `AIR_PARSED`。仅当 parsed 记录缺失时才按 Profile 0 解析 GSP AIR_RX raw；没有兼容 CAPABILITY 时不会猜测 IMU 满量程，也不会用旧版固定 16 g / 2000 dps 解释未知日志。

飞行窗口从 MISSION_START 开始；若该边沿事件丢失，则从第一帧 FLIGHT_STATE 开始。PREFLIGHT_STATE 不进入正式飞行曲线，但最终预飞、Calibration、Alignment、Capability 和 GNSS usable 状态会进入 summary/manifest。

输出目录：

```text
data/yyyy-mm-dd-n/
├─ processed_data_ZH.txt
├─ summary_ZH.txt
├─ accel_ZH.png
├─ gyro_ZH.png
├─ euler_ZH.png
├─ velocity_ZH.png
├─ position_ZH.png
├─ link_quality_ZH.png
├─ packet_loss_per_second_ZH.png
├─ attitude_motion_ZH.gif
├─ gif_frames_ZH/
└─ manifest_ZH.json
```

GUI 使用默认全选的勾选框决定输出内容；命令行使用 `--export-items`。导出语言可选 `zh_CN` / `en_US`，所有文件及 3D 帧统一使用 `_ZH` / `_EN`，图片内标题、坐标轴、图例和注释使用相同语言。`--theme light|dark` 同时控制 2D/3D 图背景、网格、标签和文字颜色；单个导出失败不会阻断其他已选项目。

## 姿态 GIF

- 原始采样约 5 Hz 或更快时使用原时间戳；
- 明显低于目标帧率时插值；
- 长断链处保持上一有效姿态，不外推；
- `quat_q15` 全零的样本标为 invalid，不把单位四元数 fallback 当作真实姿态。
