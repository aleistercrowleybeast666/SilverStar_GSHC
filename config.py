from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "二代飞控地面站"
APP_EN_NAME = "SS1GroundStation"
DEFAULT_BAUDRATE = 230400
PLOT_HISTORY = 400
STATUS_PERIOD_EXPECT_MS = 1000

# IMU 原始数据换算配置
# 飞控端 AIR_FLIGHT_STATE 中加速度、角速度传的是 int16_t 原始计数。
# 这里必须与飞控端 IMU 的实际量程配置一致。
ACCEL_FULL_SCALE_G = 16.0
GYRO_FULL_SCALE_DPS = 2000.0
STANDARD_GRAVITY_MPS2 = 9.80665


def _app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_BASE_DIR = _app_base_dir()
CONFIG_DIR = APP_BASE_DIR / "config"
USER_PATHS_FILE = CONFIG_DIR / "user_paths.json"
DEFAULT_USER_DATA_ROOT = Path.home() / "Documents" / "SS1_host_computer_data"


def _read_user_data_root() -> Path:
    env_value = os.environ.get("SS1_HOST_COMPUTER_DATA_ROOT", "").strip()
    if env_value:
        return Path(env_value).expanduser()

    try:
        if USER_PATHS_FILE.exists():
            obj = json.loads(USER_PATHS_FILE.read_text(encoding="utf-8"))
            value = str(obj.get("data_root", "")).strip()
            if value:
                return Path(value).expanduser()
    except Exception:
        pass

    return DEFAULT_USER_DATA_ROOT


USER_DATA_ROOT = _read_user_data_root()
LOG_DIR = str(USER_DATA_ROOT / "logs")
DATA_DIR = str(USER_DATA_ROOT / "data")

# 确保用户数据目录存在。安装版由安装器创建；开发运行时这里兜底创建。
for _path in (CONFIG_DIR, Path(LOG_DIR), Path(DATA_DIR)):
    try:
        _path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
