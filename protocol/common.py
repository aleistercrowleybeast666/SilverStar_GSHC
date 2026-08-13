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
    SENSOR_STATUS = 0x14
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


class AirSensorSummaryFlag(IntFlag):
    IMU_PRESENT = 1 << 0
    GNSS_PRESENT = 1 << 1
    AUX_SENSOR_PRESENT = 1 << 2
    SENSOR_STATUS_SNAPSHOT_SUPPORTED = 1 << 3


class AirSensorId(IntEnum):
    IMU = 0x01
    GNSS = 0x02
    BAROMETER = 0x03
    MAGNETOMETER = 0x04
    AIR_DATA = 0x05
    RANGEFINDER = 0x06
    RADAR_ALTIMETER = 0x07
    SUN_SENSOR = 0x08
    STAR_TRACKER = 0x09
    VISION = 0x0A
    EXTERNAL_ATTITUDE = 0x0B
    DUAL_GNSS_HEADING = 0x0C
    TEMPERATURE = 0x0D
    HUMIDITY = 0x0E


class AirSensorStatusFlag(IntFlag):
    REGISTERED = 1 << 0
    INITIALIZED = 1 << 1
    ONLINE = 1 << 2
    HEALTHY = 1 << 3
    DATA_VALID = 1 << 4
    CALIBRATION_OK = 1 << 5
    ALIGNMENT_USED = 1 << 6
    REQUIRED_FOR_START = 1 << 7


class AirSensorDetailCode(IntEnum):
    NONE = 0x00
    NOT_REGISTERED = 0x01
    INIT_FAILED = 0x02
    OFFLINE = 0x03
    UNHEALTHY = 0x04
    NO_VALID_DATA = 0x05
    CALIBRATION_REQUIRED = 0x06
    ALIGNMENT_INPUT_INVALID = 0x07
    IO_ERROR = 0x08
    CONFIG_ERROR = 0x09
    UNSUPPORTED = 0x0A
    OTHER = 0xFF


@dataclass(frozen=True)
class AirSensorDescriptor:
    sensor_id: int
    canonical_name: str


# The single canonical sensor registry used by parser/model/UI/logging. Unknown
# IDs deliberately remain valid AIR V0 data and are handled by the caller.
AIR_SENSOR_DESCRIPTORS: dict[int, AirSensorDescriptor] = {
    int(sensor_id): AirSensorDescriptor(int(sensor_id), sensor_id.name)
    for sensor_id in AirSensorId
}


def air_sensor_descriptor(sensor_id: int) -> AirSensorDescriptor | None:
    return AIR_SENSOR_DESCRIPTORS.get(int(sensor_id) & 0xFF)


def air_sensor_canonical_name(sensor_id: int) -> str:
    descriptor = air_sensor_descriptor(sensor_id)
    if descriptor is not None:
        return descriptor.canonical_name
    return f"UNKNOWN_SENSOR_0x{int(sensor_id) & 0xFF:02X}"


def air_sensor_sort_key(sensor_id: int, instance_id: int) -> tuple[int, int, int]:
    value = int(sensor_id) & 0xFF
    priority = 0 if value == int(AirSensorId.IMU) else 1 if value == int(AirSensorId.GNSS) else 2
    return priority, value, int(instance_id) & 0xFF


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
