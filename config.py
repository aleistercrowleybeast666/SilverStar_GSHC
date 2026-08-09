from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "二代飞控地面站"
APP_EN_NAME = "SS1GroundStation"
DEFAULT_BAUDRATE = 230400
PLOT_WINDOW_SECONDS = 10.0
MAX_LIVE_POINTS = 2000
PLOT_REFRESH_INTERVAL_MS = 100
UI_EVENT_HISTORY_LIMIT = 200
STATUS_PERIOD_EXPECT_MS = 1000
STANDARD_GRAVITY_MPS2 = 9.80665

# 接收与持久化队列均有明确上限。达到上限时会产生显式告警并施加背压，
# 不通过静默丢弃正式 AIR 数据来维持表面流畅。
PROTOCOL_INPUT_QUEUE_MAX_CHUNKS = 1024
LOGGER_QUEUE_MAX_RECORDS = 20000
LOGGER_BATCH_RECORDS = 64
LOGGER_FLUSH_INTERVAL_S = 0.2


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
