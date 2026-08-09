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
python -m processing.flight_log_processor logs/fake_flight_log.jsonl --output-root data
```

处理器优先使用 `AIR_PARSED`。仅当 parsed 记录缺失时才按 Profile 0 解析 GSP AIR_RX raw；没有兼容 CAPABILITY 时不会猜测 IMU 满量程，也不会用旧版固定 16 g / 2000 dps 解释未知日志。

飞行窗口从 MISSION_START 开始；若该边沿事件丢失，则从第一帧 FLIGHT_STATE 开始。PREFLIGHT_STATE 不进入正式飞行曲线，但最终预飞、Calibration、Alignment、Capability 和 GNSS usable 状态会进入 summary/manifest。

输出目录：

```text
data/yyyy-mm-dd-n/
├─ processed_data.txt
├─ summary.txt
├─ accel.png
├─ gyro.png
├─ euler.png
├─ velocity.png
├─ position.png
├─ link_quality.png
├─ packet_loss_per_second.png
├─ attitude_motion.gif
├─ gif_frames/
└─ manifest.json
```

## 姿态 GIF

- 原始采样约 5 Hz 或更快时使用原时间戳；
- 明显低于目标帧率时插值；
- 长断链处保持上一有效姿态，不外推；
- `quat_q15` 全零的样本标为 invalid，不把单位四元数 fallback 当作真实姿态。
