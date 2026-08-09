from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any

from config import STANDARD_GRAVITY_MPS2

from .common import AirCmdId, AirProfileId, AirStatusId, AirType

AIR_PROFILE_COMPACT_V0 = int(AirProfileId.COMPACT_V0)

AIR_TYPE_FLIGHT_STATE = int(AirType.FLIGHT_STATE)
AIR_TYPE_PREFLIGHT_STATE = int(AirType.PREFLIGHT_STATE)
AIR_TYPE_CAPABILITY = int(AirType.CAPABILITY)
AIR_TYPE_PREFLIGHT_STATUS = int(AirType.PREFLIGHT_STATUS)
AIR_TYPE_STATUS = int(AirType.STATUS)
AIR_TYPE_CMD = int(AirType.CMD)
AIR_TYPE_ACK = int(AirType.ACK)

AIR_FLIGHT_STATE_LEN = 50
AIR_PREFLIGHT_STATE_LEN = 26
AIR_CAPABILITY_LEN = 9
AIR_PREFLIGHT_STATUS_LEN = 9
AIR_STATUS_LEN = 9
AIR_CMD_LEN = 9
AIR_ACK_LEN = 9
AIR_MAX_FRAME_LEN = AIR_FLIGHT_STATE_LEN

TOKEN_START_MISSION = 0xA55A3CC3
TOKEN_LOCK = 0xC33CA55A
TOKEN_UNLOCK = 0x55AA6996
TOKEN_CALIBRATION = 0x43414C30
TOKEN_ALIGNMENT = 0x414C4947


@dataclass(frozen=True)
class AirFrame:
    air_type: int
    seq: int
    payload: bytes
    raw: bytes


@dataclass(frozen=True)
class CompactV0InertialAttitudePrefix:
    seq: int
    time_ms: int
    accel_raw: tuple[int, int, int]
    gyro_raw: tuple[int, int, int]
    quat_q15: tuple[int, int, int, int]
    quat: tuple[float, float, float, float]
    quat_raw_zero: bool
    quat_valid: bool


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
class AirPreflightStateMessage:
    seq: int
    time_ms: int
    accel_raw: tuple[int, int, int]
    gyro_raw: tuple[int, int, int]
    quat_q15: tuple[int, int, int, int]
    quat: tuple[float, float, float, float]
    quat_raw_zero: bool
    quat_valid: bool


@dataclass(frozen=True)
class AirCapabilityMessage:
    seq: int
    air_profile_id: int
    command_policy: int
    calibration_mode_mask: int
    alignment_capability_mask: int
    accel_full_scale_g: int
    gyro_full_scale_dps: int

    @property
    def profile_supported(self) -> bool:
        return self.air_profile_id == AIR_PROFILE_COMPACT_V0


@dataclass(frozen=True)
class AirPreflightStatusMessage:
    seq: int
    lifecycle_state: int
    calibration_state: int
    calibration_mode: int
    completed_face_mask: int
    current_face: int
    alignment_state: int
    attitude_ready: bool
    gnss_origin_ready: bool
    baro_origin_ready: bool
    system_ready: bool
    start_unlocked: bool
    selftest_passed: bool
    gnss_position_usable: bool
    capability_acked: bool
    calibration_ready: bool
    alignment_ready: bool
    start_block_reason: int


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
    """Decode signed Q15 as defined by AIR_PROFILE_COMPACT_V0."""
    return max(-1.0, min(1.0, float(value) / 32768.0))


def normalize_quat(quat: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    w, x, y, z = quat
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm <= 0.0 or not math.isfinite(norm):
        return 1.0, 0.0, 0.0, 0.0
    return w / norm, x / norm, y / norm, z / norm


def parse_compact_v0_inertial_attitude_prefix(frame: bytes) -> CompactV0InertialAttitudePrefix:
    """Parse the byte-identical first 26 bytes of preflight/flight state."""
    if len(frame) < AIR_PREFLIGHT_STATE_LEN:
        raise ValueError(f"AIR inertial/attitude prefix too short: {len(frame)}")

    (
        _air_type,
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
    ) = struct.unpack_from("<BBIhhhhhhhhhh", frame, 0)

    quat_q15 = (int(qw_i), int(qx_i), int(qy_i), int(qz_i))
    quat_raw_zero = all(value == 0 for value in quat_q15)
    quat = normalize_quat(tuple(q15_to_float(value) for value in quat_q15))  # type: ignore[arg-type]
    return CompactV0InertialAttitudePrefix(
        seq=int(seq),
        time_ms=int(time_ms),
        accel_raw=(int(ax), int(ay), int(az)),
        gyro_raw=(int(gx), int(gy), int(gz)),
        quat_q15=quat_q15,
        quat=quat,
        quat_raw_zero=quat_raw_zero,
        quat_valid=not quat_raw_zero,
    )


def accel_raw_to_mps2(
    raw: tuple[int, int, int],
    accel_full_scale_g: float,
) -> tuple[float, float, float]:
    if not math.isfinite(accel_full_scale_g) or accel_full_scale_g <= 0.0:
        raise ValueError("accel_full_scale_g must be positive")
    scale = accel_full_scale_g * STANDARD_GRAVITY_MPS2 / 32768.0
    return tuple(float(value) * scale for value in raw)  # type: ignore[return-value]


def gyro_raw_to_radps(
    raw: tuple[int, int, int],
    gyro_full_scale_dps: float,
) -> tuple[float, float, float]:
    if not math.isfinite(gyro_full_scale_dps) or gyro_full_scale_dps <= 0.0:
        raise ValueError("gyro_full_scale_dps must be positive")
    scale = gyro_full_scale_dps * math.pi / 180.0 / 32768.0
    return tuple(float(value) * scale for value in raw)  # type: ignore[return-value]


def parse_air_frame(frame: bytes) -> tuple[AirFrame, Any]:
    if len(frame) < 2:
        raise ValueError("AIR frame too short")

    air_type = int(frame[0])
    seq = int(frame[1])
    air = AirFrame(air_type=air_type, seq=seq, payload=bytes(frame[2:]), raw=bytes(frame))

    if air_type == AirType.FLIGHT_STATE:
        if len(frame) != AIR_FLIGHT_STATE_LEN:
            raise ValueError(f"bad AIR_FLIGHT_STATE length: {len(frame)}")
        prefix = parse_compact_v0_inertial_attitude_prefix(frame)
        vx, vy, vz, px, py, pz = struct.unpack_from("<ffffff", frame, AIR_PREFLIGHT_STATE_LEN)
        return air, AirFlightStateMessage(
            seq=prefix.seq,
            time_ms=prefix.time_ms,
            accel_raw=prefix.accel_raw,
            gyro_raw=prefix.gyro_raw,
            quat_q15=prefix.quat_q15,
            quat=prefix.quat,
            quat_raw_zero=prefix.quat_raw_zero,
            quat_valid=prefix.quat_valid,
            vel_mps=(float(vx), float(vy), float(vz)),
            pos_m=(float(px), float(py), float(pz)),
        )

    if air_type == AirType.PREFLIGHT_STATE:
        if len(frame) != AIR_PREFLIGHT_STATE_LEN:
            raise ValueError(f"bad AIR_PREFLIGHT_STATE length: {len(frame)}")
        prefix = parse_compact_v0_inertial_attitude_prefix(frame)
        return air, AirPreflightStateMessage(
            seq=prefix.seq,
            time_ms=prefix.time_ms,
            accel_raw=prefix.accel_raw,
            gyro_raw=prefix.gyro_raw,
            quat_q15=prefix.quat_q15,
            quat=prefix.quat,
            quat_raw_zero=prefix.quat_raw_zero,
            quat_valid=prefix.quat_valid,
        )

    if air_type == AirType.CAPABILITY:
        if len(frame) != AIR_CAPABILITY_LEN:
            raise ValueError(f"bad AIR_CAPABILITY length: {len(frame)}")
        _type, seq, profile, policy, calibration_mask, alignment_mask, accel_fs, gyro_fs = struct.unpack(
            "<BBBBBBBH", frame
        )
        return air, AirCapabilityMessage(
            seq=int(seq),
            air_profile_id=int(profile),
            command_policy=int(policy),
            calibration_mode_mask=int(calibration_mask),
            alignment_capability_mask=int(alignment_mask),
            accel_full_scale_g=int(accel_fs),
            gyro_full_scale_dps=int(gyro_fs),
        )

    if air_type == AirType.PREFLIGHT_STATUS:
        if len(frame) != AIR_PREFLIGHT_STATUS_LEN:
            raise ValueError(f"bad AIR_PREFLIGHT_STATUS length: {len(frame)}")
        calibration_byte = int(frame[3])
        alignment_byte = int(frame[6])
        flags = int(frame[7])
        calibration_mode_nibble = (calibration_byte >> 4) & 0x0F
        calibration_mode = 0xFF if calibration_mode_nibble == 0x0F else calibration_mode_nibble
        return air, AirPreflightStatusMessage(
            seq=seq,
            lifecycle_state=int(frame[2]),
            calibration_state=calibration_byte & 0x0F,
            calibration_mode=calibration_mode,
            completed_face_mask=int(frame[4]) & 0x3F,
            current_face=int(frame[5]),
            alignment_state=alignment_byte & 0x0F,
            attitude_ready=bool(alignment_byte & (1 << 4)),
            gnss_origin_ready=bool(alignment_byte & (1 << 5)),
            baro_origin_ready=bool(alignment_byte & (1 << 6)),
            system_ready=bool(flags & (1 << 0)),
            start_unlocked=bool(flags & (1 << 1)),
            selftest_passed=bool(flags & (1 << 2)),
            gnss_position_usable=bool(flags & (1 << 3)),
            capability_acked=bool(flags & (1 << 4)),
            calibration_ready=bool(flags & (1 << 5)),
            alignment_ready=bool(flags & (1 << 6)),
            start_block_reason=int(frame[8]),
        )

    if air_type == AirType.STATUS:
        if len(frame) != AIR_STATUS_LEN:
            raise ValueError(f"bad AIR_STATUS length: {len(frame)}")
        _type, seq, status_id, time_ms, arg0, arg1 = struct.unpack("<BBBIBB", frame)
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
        _type, seq, ack_seq, ack_cmd_id, result, time_ms = struct.unpack("<BBBBBI", frame)
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
        _type, seq, cmd_id, token, param0, param1 = struct.unpack("<BBBIBB", frame)
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


__all__ = [
    "AIR_ACK_LEN",
    "AIR_CAPABILITY_LEN",
    "AIR_CMD_LEN",
    "AIR_FLIGHT_STATE_LEN",
    "AIR_MAX_FRAME_LEN",
    "AIR_PREFLIGHT_STATE_LEN",
    "AIR_PREFLIGHT_STATUS_LEN",
    "AIR_PROFILE_COMPACT_V0",
    "AIR_STATUS_LEN",
    "AirAckMessage",
    "AirCapabilityMessage",
    "AirCmdId",
    "AirCmdMessage",
    "AirFlightStateMessage",
    "AirFrame",
    "AirPreflightStateMessage",
    "AirPreflightStatusMessage",
    "AirStatusId",
    "AirStatusMessage",
    "TOKEN_ALIGNMENT",
    "TOKEN_CALIBRATION",
    "TOKEN_LOCK",
    "TOKEN_START_MISSION",
    "TOKEN_UNLOCK",
    "accel_raw_to_mps2",
    "build_air_cmd",
    "gyro_raw_to_radps",
    "normalize_quat",
    "parse_air_frame",
    "parse_compact_v0_inertial_attitude_prefix",
    "q15_to_float",
]
