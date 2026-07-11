from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any

from .common import AirCmdId, AirStatusId, AirType

AIR_TYPE_FLIGHT_STATE = 0x10
AIR_TYPE_QUAT_STATE = 0x11
AIR_TYPE_STATUS = 0x20
AIR_TYPE_CMD = 0x30
AIR_TYPE_ACK = 0x40

AIR_FLIGHT_STATE_LEN = 50
AIR_QUAT_STATE_LEN = 14
AIR_STATUS_LEN = 9
AIR_CMD_LEN = 9
AIR_ACK_LEN = 9

TOKEN_START_MISSION = 0xA55A3CC3
TOKEN_LOCK = 0xC33CA55A
TOKEN_UNLOCK = 0x55AA6996


@dataclass(frozen=True)
class AirFrame:
    air_type: int
    seq: int
    payload: bytes
    raw: bytes


@dataclass(frozen=True)
class AirFlightStateMessage:
    seq: int
    time_ms: int
    accel_raw: tuple[int, int, int]
    gyro_raw: tuple[int, int, int]
    quat_q15: tuple[int, int, int, int]
    quat: tuple[float, float, float, float]
    quat_raw_zero: bool
    quat_valid: bool
    vel_mps: tuple[float, float, float]
    pos_m: tuple[float, float, float]


@dataclass(frozen=True)
class AirQuatStateMessage:
    seq: int
    time_ms: int
    quat_q15: tuple[int, int, int, int]
    quat: tuple[float, float, float, float]
    quat_raw_zero: bool
    quat_valid: bool


@dataclass(frozen=True)
class AirStatusMessage:
    seq: int
    status_id: int
    time_ms: int
    arg0: int
    arg1: int


@dataclass(frozen=True)
class AirAckMessage:
    seq: int
    ack_seq: int
    ack_cmd_id: int
    result: int
    time_ms: int


@dataclass(frozen=True)
class AirCmdMessage:
    seq: int
    cmd_id: int
    token: int
    param0: int
    param1: int


def q15_to_float(value: int) -> float:
    # 发送端约定 32767 对应 +1.0。
    return max(-1.0, min(1.0, float(value) / 32767.0))


def normalize_quat(quat: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    w, x, y, z = quat
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm <= 0.0:
        return 1.0, 0.0, 0.0, 0.0
    return w / norm, x / norm, y / norm, z / norm


def parse_air_frame(frame: bytes) -> tuple[AirFrame, Any]:
    if len(frame) < 2:
        raise ValueError("AIR frame too short")

    air_type = frame[0]
    seq = frame[1]
    payload = frame[2:]
    air = AirFrame(air_type=air_type, seq=seq, payload=payload, raw=bytes(frame))

    if air_type == AirType.FLIGHT_STATE:
        if len(frame) != AIR_FLIGHT_STATE_LEN:
            raise ValueError(f"bad AIR_FLIGHT_STATE length: {len(frame)}")

        unpacked = struct.unpack_from("<BBIhhhhhhhhhhffffff", frame, 0)
        (
            _type,
            seq,
            time_ms,
            ax,
            ay,
            az,
            gx,
            gy,
            gz,
            qw_i,
            qx_i,
            qy_i,
            qz_i,
            vx,
            vy,
            vz,
            px,
            py,
            pz,
        ) = unpacked

        quat_q15 = (int(qw_i), int(qx_i), int(qy_i), int(qz_i))
        quat_raw_zero = all(q == 0 for q in quat_q15)
        quat_valid = not quat_raw_zero
        quat = normalize_quat(tuple(q15_to_float(q) for q in quat_q15))  # type: ignore[arg-type]

        return air, AirFlightStateMessage(
            seq=int(seq),
            time_ms=int(time_ms),
            accel_raw=(int(ax), int(ay), int(az)),
            gyro_raw=(int(gx), int(gy), int(gz)),
            quat_q15=quat_q15,
            quat=quat,
            quat_raw_zero=quat_raw_zero,
            quat_valid=quat_valid,
            vel_mps=(float(vx), float(vy), float(vz)),
            pos_m=(float(px), float(py), float(pz)),
        )

    if air_type == AirType.QUAT_STATE:
        if len(frame) != AIR_QUAT_STATE_LEN:
            raise ValueError(f"bad AIR_QUAT_STATE length: {len(frame)}")

        _type, seq, time_ms, qw_i, qx_i, qy_i, qz_i = struct.unpack_from("<BBIhhhh", frame, 0)
        quat_q15 = (int(qw_i), int(qx_i), int(qy_i), int(qz_i))
        quat_raw_zero = all(q == 0 for q in quat_q15)
        quat_valid = not quat_raw_zero
        quat = normalize_quat(tuple(q15_to_float(q) for q in quat_q15))  # type: ignore[arg-type]

        return air, AirQuatStateMessage(
            seq=int(seq),
            time_ms=int(time_ms),
            quat_q15=quat_q15,
            quat=quat,
            quat_raw_zero=quat_raw_zero,
            quat_valid=quat_valid,
        )

    if air_type == AirType.STATUS:
        if len(frame) != AIR_STATUS_LEN:
            raise ValueError(f"bad AIR_STATUS length: {len(frame)}")
        _type, seq, status_id, time_ms, arg0, arg1 = struct.unpack_from("<BBBIBB", frame, 0)
        return air, AirStatusMessage(
            seq=int(seq),
            status_id=int(status_id),
            time_ms=int(time_ms),
            arg0=int(arg0),
            arg1=int(arg1),
        )

    if air_type == AirType.ACK:
        if len(frame) != AIR_ACK_LEN:
            raise ValueError(f"bad AIR_ACK length: {len(frame)}")
        _type, seq, ack_seq, ack_cmd_id, result, time_ms = struct.unpack_from("<BBBBBI", frame, 0)
        return air, AirAckMessage(
            seq=int(seq),
            ack_seq=int(ack_seq),
            ack_cmd_id=int(ack_cmd_id),
            result=int(result),
            time_ms=int(time_ms),
        )

    if air_type == AirType.CMD:
        if len(frame) != AIR_CMD_LEN:
            raise ValueError(f"bad AIR_CMD length: {len(frame)}")
        _type, seq, cmd_id, token, param0, param1 = struct.unpack_from("<BBBIBB", frame, 0)
        return air, AirCmdMessage(
            seq=int(seq),
            cmd_id=int(cmd_id),
            token=int(token),
            param0=int(param0),
            param1=int(param1),
        )

    raise ValueError(f"unknown AIR_TYPE: 0x{air_type:02X}")


def build_air_cmd(seq: int, cmd_id: int, token: int, param0: int = 0, param1: int = 0) -> bytes:
    return struct.pack(
        "<BBBIBB",
        int(AirType.CMD),
        seq & 0xFF,
        cmd_id & 0xFF,
        token & 0xFFFFFFFF,
        param0 & 0xFF,
        param1 & 0xFF,
    )
