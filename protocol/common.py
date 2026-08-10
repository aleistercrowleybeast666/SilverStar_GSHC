from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Optional


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class AirProfileId(IntEnum):
    COMPACT_V0 = 0


class AirType(IntEnum):
    FLIGHT_STATE = 0x10
    PREFLIGHT_STATE = 0x11
    CAPABILITY = 0x12
    PREFLIGHT_STATUS = 0x13
    STATUS = 0x20
    CMD = 0x30
    ACK = 0x40


class AirCommandPolicy(IntEnum):
    PREFLIGHT_ONLY = 1
    MISSION_ALLOWED = 2


class AirCalibrationMode(IntEnum):
    NONE = 0
    ONE_FACE = 1
    SIX_FACE = 2
    NOT_SELECTED = 0xFF


class AirCalibrationModeMask(IntFlag):
    NONE = 1 << AirCalibrationMode.NONE
    ONE_FACE = 1 << AirCalibrationMode.ONE_FACE
    SIX_FACE = 1 << AirCalibrationMode.SIX_FACE


class AirCalibrationState(IntEnum):
    IDLE = 0
    WAIT_FACE = 1
    COLLECTING = 2
    CHECKING = 3
    READY = 4
    FAILED = 5


class AirAlignmentState(IntEnum):
    IDLE = 0
    COLLECTING = 1
    CHECKING = 2
    READY = 3
    FAILED = 4
    STALE = 5


class AirCalibrationDiagnosticReason(IntEnum):
    NONE = 0
    NO_STREAM = 1
    GYRO_MOVING = 2
    ACCEL_MAGNITUDE = 3
    GRAVITY_DIRECTION = 4
    VARIANCE = 5
    SAMPLE_GAP = 6


class AirAlignmentCapability(IntFlag):
    ATTITUDE = 1 << 0
    GNSS_ORIGIN = 1 << 1
    BARO_ORIGIN = 1 << 2


class AirLifecycleState(IntEnum):
    BOOT = 0
    SELF_TEST = 1
    PREFLIGHT = 2
    READY = 3
    FLIGHT = 4
    RECOVERY = 5
    LANDED = 6
    POSTFLIGHT = 7
    FAULT = 8


class AirStatusId(IntEnum):
    BOOT = 0x01
    SELFTEST_COMPLETE = 0x02
    # Compatibility alias for old callers; the 0.0.8 protocol name is
    # SELFTEST_COMPLETE.
    SELFTEST_OK = SELFTEST_COMPLETE
    MISSION_START = 0x03
    LAUNCH = 0x04
    PARACHUTE_DEPLOY = 0x05
    LANDING = 0x06
    LOCKED = 0x07
    UNLOCKED = 0x08
    GNSS_POSITION = 0x09
    ALIGNMENT = 0x0A
    CALIBRATION = 0x0B
    CALIBRATION_FACE = 0x0C
    CALIBRATION_DIAGNOSTIC = 0x0D


class AirCmdId(IntEnum):
    START_MISSION = 0x01
    PING = 0x02
    LOCK = 0x03
    UNLOCK = 0x04
    CAPABILITY_ACK = 0x05
    CAL_START = 0x07
    CAL_FACE = 0x08
    CAL_STOP = 0x09
    CAL_RESET = 0x0A
    ALIGN_START = 0x0B
    ALIGN_STOP = 0x0C
    ALIGN_RESET = 0x0D


class AirAckResult(IntEnum):
    OK = 0x00
    BAD_LEN = 0x01
    BAD_CMD = 0x02
    BAD_TOKEN = 0x03
    BUSY = 0x04
    REJECTED = 0x05
    BAD_STATE = 0x06
    LOCKED_REQUIRED = 0x07
    ALREADY_LOCKED = 0x08
    ALREADY_UNLOCKED = 0x09
    CAPABILITY_REQUIRED = 0x0A
    CALIBRATION_REQUIRED = 0x0B
    ALIGNMENT_REQUIRED = 0x0C
    SYSTEM_NOT_READY = 0x0D
    ATTITUDE_NOT_READY = 0x0E
    ATTITUDE_INVALID = 0x0F
    ATTITUDE_STALE = 0x10
    ORIGIN_FAILED = 0x11
    NAVIGATION_FAILED = 0x12
    QUEUE_FAILED = 0x13
    BAD_PARAM = 0x14
    HOOKS_UNAVAILABLE = 0x15
    PREPARE_FAILED = 0x16


class GspType(IntEnum):
    GS_STATUS = 0x01
    AIR_RX = 0x02
    AIR_TX = 0x03
    ACK = 0x04


class GspAckResult(IntEnum):
    OK = 0x00
    BAD_CRC = 0x01
    BAD_LEN = 0x02
    BAD_TYPE = 0x03
    BAD_PARAM = 0x04
    BUSY = 0x05


@dataclass
class LinkQuality:
    rssi_dbm: Optional[int] = None
    snr_db: Optional[float] = None


def enum_name(enum_type: type[IntEnum], value: int, *, prefix: str = "UNKNOWN") -> str:
    try:
        return enum_type(int(value)).name
    except ValueError:
        return f"{prefix}(0x{int(value) & 0xFF:02X})"
