from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class AirType(IntEnum):
    FLIGHT_STATE = 0x10
    QUAT_STATE = 0x11
    STATUS = 0x20
    CMD = 0x30
    ACK = 0x40


class AirStatusId(IntEnum):
    BOOT = 0x01
    SELFTEST_OK = 0x02
    MISSION_START = 0x03
    LAUNCH = 0x04
    PARACHUTE_DEPLOY = 0x05
    LANDING = 0x06
    LOCKED = 0x07
    UNLOCKED = 0x08


class AirCmdId(IntEnum):
    START_MISSION = 0x01
    PING = 0x02
    LOCK = 0x03
    UNLOCK = 0x04


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
