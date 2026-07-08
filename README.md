# SS1 地面站上位机开发说明

本项目是二代飞控/地面站系统的 PC 上位机程序，主要用于：

- 通过串口连接地面站；
- 接收并解析 GSP-MIN 串口协议；
- 解析地面站透传的 AIR 无线协议；
- 实时显示飞行状态、姿态、速度和位置曲线；
- 下发 PING / LOCK / UNLOCK / START 命令；
- 保存 JSONL 原始日志；
- 对飞行日志进行离线数据处理，生成 txt、曲线图和 GIF；
- 生成模拟验证数据，用于验证后处理流程。

## 1. 推荐开发环境

建议使用 Windows 10/11。

推荐 Python 版本：

```text
Python 3.11 或 3.12
```

项目使用的主要依赖：

```text
PySide6
pyserial
pyqtgraph
PyOpenGL
numpy
matplotlib
pillow
```

安装依赖：

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

启动源码版：

```bat
python main.py
```

## 2. 目录结构

```text
地面站上位机SS1/
├─ main.py                         # 程序入口
├─ app.py                          # 主控制逻辑：串口、协议、日志、数据处理调度
├─ config.py                       # 全局配置与用户数据路径
├─ requirements.txt                # Python 依赖
├─ README.md                       # 开发说明
├─ README.txt                      # 用户使用说明
├─ config/                         # 绿色版配置目录，可放 user_paths.json
├─ protocol/
│  ├─ air.py                       # AIR 无线协议解析/构造
│  ├─ gsp_min.py                   # GSP-MIN 串口协议解析/构造
│  └─ common.py                    # 协议公共定义
├─ services/
│  └─ logger.py                    # JSONL 日志记录
├─ transport/
│  └─ serial_backend.py            # 串口收发后台线程
├─ ui/
│  └─ main_window.py               # PySide6 主窗口与界面控件
├─ processing/
│  ├─ fake_log_generator.py        # 5 Hz 模拟验证数据生成
│  ├─ flight_log_processor.py      # 离线数据处理入口
│  ├─ flight_plotter.py            # 曲线图/GIF 绘制
│  └─ README.md                    # 数据处理模块说明
└─ packaging/
   ├─ build_pyinstaller.bat        # 生成绿色版 dist/SS1GroundStation
   └─ README_packaging.md          # 打包说明
```

如果确定只发布绿色版，可以删除源码目录里的：

```text
installer/
logs/
data/
```

其中 `logs/` 和 `data/` 如果里面只有 `.gitkeep`，已经没有实际运行意义。程序默认不会再把数据写到源码目录下。

`config/` 建议保留，因为绿色版可通过 `config/user_paths.json` 指定日志和数据保存位置。

## 3. 用户数据目录

程序通过 `config.py` 决定日志和处理结果保存路径。

默认用户数据根目录为：

```text
C:\Users\<用户名>\Documents\SS1_host_computer_data
```

内部结构：

```text
SS1_host_computer_data/
├─ logs/       # 原始 JSONL 日志、模拟验证日志
└─ data/       # 离线处理输出结果
```

程序启动时会自动创建这些目录。

## 4. 自定义用户数据目录

优先级如下：

1. 环境变量 `SS1_HOST_COMPUTER_DATA_ROOT`
2. 程序目录下的 `config/user_paths.json`
3. 默认值 `Documents/SS1_host_computer_data`

绿色版推荐使用 `config/user_paths.json`。

例如想把数据保存到 `D:\SS1_host_computer_data`，则创建：

```text
config/user_paths.json
```

内容：

```json
{
  "data_root": "D:\\SS1_host_computer_data",
  "logs_dir": "D:\\SS1_host_computer_data\\logs",
  "data_dir": "D:\\SS1_host_computer_data\\data"
}
```

程序实际只依赖 `data_root`，`logs_dir` 和 `data_dir` 主要用于人工查看，建议保持一致。

## 5. 协议层说明

当前上位机使用两层协议：

```text
PC 上位机 <-> 地面站：GSP-MIN
地面站 <-> 空端：AIR
```

地面站通过 `GSP_AIR_RX` 把收到的 AIR 包转发给上位机；上位机通过 `GSP_AIR_TX` 让地面站发送 AIR 命令。

当前主要 AIR 包：

```text
AIR_FLIGHT_STATE = 0x10
AIR_STATUS       = 0x20
AIR_CMD          = 0x30
AIR_ACK          = 0x40
```

实时主遥测 `AIR_FLIGHT_STATE` 包含：

```text
accel_raw int16 x 3
gyro_raw  int16 x 3
quat_q15  int16 x 4
velocity  float32 x 3
position  float32 x 3
```

上位机根据界面中的加速度/角速度量程选项，把 raw 数据换算成物理量。

## 6. 日志格式

原始日志为 JSONL，每行一个 JSON 对象。

主要类型：

```text
GSP             # 原始 GSP 串口帧
AIR_PARSED      # 已解析 AIR 数据
SIMULATION      # 模拟验证数据标记
```

模拟验证数据会带：

```json
{
  "simulated": true,
  "simulation_label": "SIMULATION_VALIDATION"
}
```

离线处理结果的 `summary.txt`、`processed_data.txt`、`manifest.json` 也会区分真实数据和模拟数据。

## 7. 数据处理模块

源码运行：

```bat
python -m processing.fake_log_generator --output logs/fake_flight_log.jsonl --duration 90 --seed 42
python -m processing.flight_log_processor logs/fake_flight_log.jsonl --output-root data
```

正常程序中建议直接用界面按钮：

```text
生成模拟验证数据
处理数据
打开 logs
打开 data
```

处理输出目录形如：

```text
SS1_host_computer_data/data/yyyy-mm-dd-n/
├─ processed_data.txt
├─ summary.txt
├─ accel.png
├─ gyro.png
├─ euler.png
├─ velocity.png
├─ position.png
├─ link_quality.png
├─ attitude_motion.gif
├─ gif_frames/
└─ manifest.json
```

没有 `PARACHUTE_DEPLOY` 或 `LANDING` 不视为错误；程序会处理到日志末尾。

没有 `MISSION_START` 则无法确定任务起点，会提示文件有问题。

## 8. 绿色版打包

推荐只做绿色版，不做安装器。

打包命令：

```bat
packaging\build_pyinstaller.bat
```

输出：

```text
dist/SS1GroundStation/
├─ SS1GroundStation.exe
├─ _internal/
└─ config/              # 可选
```

把整个 `dist/SS1GroundStation/` 文件夹复制到目标电脑即可运行，不需要目标电脑安装 Python。

注意不要只复制 exe，必须复制整个文件夹。

## 9. 绿色版发布建议

建议最终发布包结构：

```text
SS1GroundStation/
├─ SS1GroundStation.exe
├─ _internal/
├─ config/
│  └─ user_paths.json   # 可选，不放则默认使用 Documents/SS1_host_computer_data
└─ README.txt
```

不要把源码目录里的 `logs/`、`data/`、`__pycache__/`、`build/`、`installer/` 混入发布包。

## 10. 开发注意事项

- 修改协议时，需要同步修改 `protocol/air.py`、地面站固件、空端固件和数据处理模块。
- 修改日志字段时，需要同步检查 `processing/flight_log_processor.py`。
- 修改 `AIR_FLIGHT_STATE` 的 IMU 量程时，要同步修改界面默认量程或飞控配置。
- 打包前先用源码运行一遍，确认串口、3D、曲线、模拟数据生成和数据处理都正常。
- 打包后必须在 `dist/SS1GroundStation/SS1GroundStation.exe` 中再完整测试一次。
