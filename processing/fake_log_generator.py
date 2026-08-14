from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Callable

from protocol.common import (
    AirSensorDetailCode,
    AirSensorStatusFlag,
    air_sensor_canonical_name,
    enum_name,
)

AIR_TYPE_FLIGHT_STATE = 0x10
AIR_TYPE_PREFLIGHT_STATE = 0x11
AIR_TYPE_CAPABILITY = 0x12
AIR_TYPE_PREFLIGHT_STATUS = 0x13
AIR_TYPE_SENSOR_STATUS = 0x14
AIR_TYPE_STATUS = 0x20

STATUS_MISSION_START = 0x03
STATUS_PARACHUTE_DEPLOY = 0x05
STATUS_LANDING = 0x06
STATUS_ALIGNMENT = 0x0A
STATUS_CALIBRATION = 0x0B
STATUS_CALIBRATION_FACE = 0x0C

ACCEL_FULL_SCALE_G = 16.0
GYRO_FULL_SCALE_DPS = 2000.0
STANDARD_GRAVITY_MPS2 = 9.80665
SIMULATION_LABEL = "SIMULATION_VALIDATION"
SimulationProgressCallback = Callable[[int, int], None]
SimulationCancelCallback = Callable[[], bool]


class SimulationCancelledError(RuntimeError):
    """Raised when synthetic-log generation is cancelled by the user."""


def clamp_i16(value: int) -> int:
    return max(-32768, min(32767, int(value)))


def euler_to_quat(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    cr = math.cos(roll / 2.0)
    sr = math.sin(roll / 2.0)
    cp = math.cos(pitch / 2.0)
    sp = math.sin(pitch / 2.0)
    cy = math.cos(yaw / 2.0)
    sy = math.sin(yaw / 2.0)

    w = cr * cp * cy + sr * sp * sy
    x = sr * cp * cy - cr * sp * sy
    y = cr * sp * cy + sr * cp * sy
    z = cr * cp * sy

    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm <= 0.0:
        return 1.0, 0.0, 0.0, 0.0
    return w / norm, x / norm, y / norm, z / norm


def quat_to_q15(quat: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    return tuple(clamp_i16(round(max(-1.0, min(1.0, q)) * 32768.0)) for q in quat)  # type: ignore[return-value]


def accel_to_raw(accel_mps2: tuple[float, float, float]) -> tuple[int, int, int]:
    scale = 32768.0 / (ACCEL_FULL_SCALE_G * STANDARD_GRAVITY_MPS2)
    return tuple(clamp_i16(round(v * scale)) for v in accel_mps2)  # type: ignore[return-value]


def gyro_to_raw(gyro_radps: tuple[float, float, float]) -> tuple[int, int, int]:
    scale = 32768.0 / (GYRO_FULL_SCALE_DPS * math.pi / 180.0)
    return tuple(clamp_i16(round(v * scale)) for v in gyro_radps)  # type: ignore[return-value]


def add_status(
    records: list[dict],
    host_ts: float,
    seq: int,
    status_id: int,
    time_ms: int,
    arg0: int = 0,
    arg1: int = 0,
) -> None:
    records.append(
        {
            "ts": host_ts,
            "dir": "RX",
            "layer": "AIR_PARSED",
            "kind": "STATUS",
            "simulated": True,
            "simulation_label": SIMULATION_LABEL,
            "seq": seq & 0xFF,
            "status_id": status_id,
            "time_ms": time_ms,
            "arg0": int(arg0),
            "arg1": int(arg1),
        }
    )


def add_capability(records: list[dict], host_ts: float, seq: int) -> None:
    records.append(
        {
            "ts": host_ts,
            "dir": "RX",
            "layer": "AIR_PARSED",
            "kind": "CAPABILITY",
            "simulated": True,
            "simulation_label": SIMULATION_LABEL,
            "seq": seq & 0xFF,
            "air_profile_id": 0,
            "profile_supported": True,
            "command_policy": 1,
            "calibration_mode_mask": 0x07,
            "sensor_summary_flags": 0x0F,
            "accel_full_scale_g": ACCEL_FULL_SCALE_G,
            "gyro_full_scale_dps": GYRO_FULL_SCALE_DPS,
        }
    )


def add_preflight_status(
    records: list[dict],
    host_ts: float,
    seq: int,
    *,
    lifecycle_state: int,
    calibration_state: int,
    calibration_mode: int,
    completed_face_mask: int,
    alignment_state: int,
    alignment_ready: bool,
) -> None:
    records.append(
        {
            "ts": host_ts,
            "dir": "RX",
            "layer": "AIR_PARSED",
            "kind": "PREFLIGHT_STATUS",
            "simulated": True,
            "simulation_label": SIMULATION_LABEL,
            "seq": seq & 0xFF,
            "lifecycle_state": lifecycle_state,
            "calibration_state": calibration_state,
            "calibration_mode": calibration_mode,
            "completed_face_mask": completed_face_mask,
            "current_face": 0xFF,
            "alignment_state": alignment_state,
            "system_ready": alignment_ready,
            "start_unlocked": alignment_ready,
            "selftest_passed": True,
            "gnss_position_usable": True,
            "capability_acked": True,
            "calibration_ready": calibration_state == 4,
            "alignment_ready": alignment_ready,
            "start_block_reason": 0 if alignment_ready else 0x0C,
        }
    )


def add_sensor_status(
    records: list[dict],
    host_ts: float,
    seq: int,
    *,
    snapshot_id: int,
    sensor_id: int,
    instance_id: int,
    status_flags: int,
    detail_code: int,
    index: int,
    total: int,
) -> None:
    records.append(
        {
            "ts": host_ts,
            "dir": "RX",
            "layer": "AIR_PARSED",
            "kind": "SENSOR_STATUS",
            "simulated": True,
            "simulation_label": SIMULATION_LABEL,
            "seq": seq & 0xFF,
            "snapshot_id": snapshot_id & 0xFF,
            "sensor_id": sensor_id & 0xFF,
            "sensor_name": air_sensor_canonical_name(sensor_id),
            "instance_id": instance_id & 0xFF,
            "status_flags": status_flags & 0xFF,
            "raw_flags": status_flags & 0xFF,
            "status_flag_names": [
                flag.name
                for flag in AirSensorStatusFlag
                if status_flags & int(flag)
            ],
            "detail_code": detail_code & 0xFF,
            "detail_name": enum_name(
                AirSensorDetailCode,
                detail_code,
                prefix="UNKNOWN_DETAIL",
            ),
            "index": index,
            "total": total,
        }
    )


def add_preflight_state(
    records: list[dict],
    host_ts: float,
    seq: int,
    time_ms: int,
    accel_mps2: tuple[float, float, float],
    gyro_radps: tuple[float, float, float],
    quat: tuple[float, float, float, float],
) -> None:
    accel_raw = accel_to_raw(accel_mps2)
    gyro_raw = gyro_to_raw(gyro_radps)
    quat_q15 = quat_to_q15(quat)
    records.append(
        {
            "ts": host_ts,
            "dir": "RX",
            "layer": "AIR_PARSED",
            "kind": "PREFLIGHT_STATE",
            "simulated": True,
            "simulation_label": SIMULATION_LABEL,
            "seq": seq & 0xFF,
            "time_ms": time_ms,
            "accel_raw": list(accel_raw),
            "gyro_raw": list(gyro_raw),
            "quat_q15": list(quat_q15),
            "quat_raw_zero": False,
            "quat_valid": True,
            "physical_conversion_valid": True,
            "air_profile_id": 0,
            "accel_full_scale_g": ACCEL_FULL_SCALE_G,
            "gyro_full_scale_dps": GYRO_FULL_SCALE_DPS,
            "accel_mps2": list(accel_mps2),
            "gyro_radps": list(gyro_radps),
            "quat": list(quat),
        }
    )


def add_flight_state(
    records: list[dict],
    host_ts: float,
    seq: int,
    time_ms: int,
    accel_mps2: tuple[float, float, float],
    gyro_radps: tuple[float, float, float],
    quat: tuple[float, float, float, float],
    vel_mps: tuple[float, float, float],
    pos_m: tuple[float, float, float],
) -> None:
    accel_raw = accel_to_raw(accel_mps2)
    gyro_raw = gyro_to_raw(gyro_radps)
    quat_q15 = quat_to_q15(quat)
    quat_raw_zero = all(q == 0 for q in quat_q15)

    records.append(
        {
            "ts": host_ts,
            "dir": "RX",
            "layer": "AIR_PARSED",
            "kind": "FLIGHT_STATE",
            "simulated": True,
            "simulation_label": SIMULATION_LABEL,
            "seq": seq & 0xFF,
            "time_ms": time_ms,
            "accel_raw": list(accel_raw),
            "gyro_raw": list(gyro_raw),
            "quat_q15": list(quat_q15),
            "quat_raw_zero": quat_raw_zero,
            "quat_valid": not quat_raw_zero,
            "physical_conversion_valid": True,
            "air_profile_id": 0,
            "accel_full_scale_g": ACCEL_FULL_SCALE_G,
            "gyro_full_scale_dps": GYRO_FULL_SCALE_DPS,
            "accel_mps2": [float(v) for v in accel_mps2],
            "gyro_radps": [float(v) for v in gyro_radps],
            "quat": [float(v) for v in quat],
            "vel_mps": [float(v) for v in vel_mps],
            "pos_m": [float(v) for v in pos_m],
        }
    )


def add_link(records: list[dict], host_ts: float, time_ms: int, rssi: int, snr_db: float) -> None:
    snr_q4 = max(-128, min(127, int(round(snr_db * 4.0))))
    payload = bytes([(rssi & 0xFF), (snr_q4 & 0xFF), 0])
    records.append(
        {
            "ts": host_ts,
            "dir": "RX",
            "layer": "GSP",
            "simulated": True,
            "simulation_label": SIMULATION_LABEL,
            "msg_type": 2,
            "gsp_type": 2,
            "time_ms": time_ms,
            "payload_hex": payload.hex(),
        }
    )


def boost_accel_z(t: float, g: float = STANDARD_GRAVITY_MPS2) -> float:
    """Launch acceleration: 0 -> about +10 g -> -1 g in about 1.1 s."""
    if t < 0.0:
        return 0.0
    if t < 0.18:
        return (10.0 * g) * (t / 0.18)
    if t < 0.75:
        return 10.0 * g
    if t < 1.10:
        alpha = (t - 0.75) / 0.35
        return (10.0 * g) * (1.0 - alpha) + (-1.0 * g) * alpha
    return -1.0 * g


def impact_profile(tau: float, g: float = STANDARD_GRAVITY_MPS2) -> tuple[float, tuple[float, float, float]]:
    """
    Landing transient lasting about 0.45 s.
    Returns (az, gyro_xyz). Peaks appear before the final zero state.
    """
    if tau < 0.0:
        return 0.0, (0.0, 0.0, 0.0)
    if tau < 0.08:
        az = 5.5 * g * math.sin(math.pi * tau / 0.08)
        gyro = (
            7.0 * math.sin(math.pi * tau / 0.08),
            5.5 * math.sin(math.pi * tau / 0.08),
            3.5 * math.sin(math.pi * tau / 0.08),
        )
        return az, gyro
    if tau < 0.18:
        u = (tau - 0.08) / 0.10
        az = -2.2 * g * math.sin(math.pi * u)
        gyro = (
            -4.0 * math.sin(math.pi * u),
            3.0 * math.sin(math.pi * u),
            -2.0 * math.sin(math.pi * u),
        )
        return az, gyro
    if tau < 0.45:
        decay = math.exp(-(tau - 0.18) / 0.10)
        az = 0.6 * g * math.sin(2.0 * math.pi * 4.0 * (tau - 0.18)) * decay
        gyro = (
            1.2 * math.sin(2.0 * math.pi * 3.0 * (tau - 0.18)) * decay,
            1.0 * math.cos(2.0 * math.pi * 2.6 * (tau - 0.18)) * decay,
            0.8 * math.sin(2.0 * math.pi * 2.0 * (tau - 0.18)) * decay,
        )
        return az, gyro
    return 0.0, (0.0, 0.0, 0.0)


def simulate(
    duration_s: float = 90.0,
    seed: int = 42,
    *,
    progress: SimulationProgressCallback | None = None,
    cancel_requested: SimulationCancelCallback | None = None,
) -> list[dict]:
    """
    Generate a 5 Hz synthetic JSONL log matching the current upper-computer log format.

    Key behavior:
    - Always tries to generate through landing.
    - Landing is represented as:
      pre-landing impact peaks -> acceleration/gyro become zero.
    - The last telemetry samples therefore represent a landed state.
    """
    def check_cancel() -> None:
        if cancel_requested is not None and cancel_requested():
            raise SimulationCancelledError("Simulation-data generation was cancelled.")

    check_cancel()
    random.seed(seed)

    records: list[dict] = []
    mission_start_ms = 100000
    host_t0 = 1777000000.0

    sim_dt = 0.01
    log_dt = 0.20
    next_log_t = 0.0

    seq = 0
    preflight_start_ms = mission_start_ms - 5000
    add_capability(records, host_t0 - 5.0, seq)
    seq += 1
    add_preflight_status(
        records,
        host_t0 - 4.8,
        seq,
        lifecycle_state=2,
        calibration_state=2,
        calibration_mode=2,
        completed_face_mask=0,
        alignment_state=0,
        alignment_ready=False,
    )
    seq += 1
    for sample_index in range(5):
        sample_time_ms = preflight_start_ms + sample_index * 200
        add_preflight_state(
            records,
            host_t0 - 4.6 + sample_index * 0.2,
            seq,
            sample_time_ms,
            (0.0, 0.0, STANDARD_GRAVITY_MPS2),
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0, 0.0),
        )
        seq += 1

    for face in range(6):
        add_status(
            records,
            host_t0 - 3.4 + face * 0.1,
            seq,
            STATUS_CALIBRATION_FACE,
            preflight_start_ms + 1500 + face * 100,
            face,
            1,
        )
        seq += 1
    add_status(
        records,
        host_t0 - 2.7,
        seq,
        STATUS_CALIBRATION,
        preflight_start_ms + 2300,
        4,
        2,
    )
    seq += 1
    add_preflight_status(
        records,
        host_t0 - 2.5,
        seq,
        lifecycle_state=2,
        calibration_state=4,
        calibration_mode=2,
        completed_face_mask=0x3F,
        alignment_state=1,
        alignment_ready=False,
    )
    seq += 1
    alignment_snapshot_id = 1
    alignment_sensors = (
        (0x01, 0, 0xFF, 0x00),
        (0x02, 0, 0xDF, 0x00),
        (0x03, 0, 0xCF, 0x00),
        (0x04, 0, 0x4F, 0x00),
    )
    for index, (sensor_id, instance_id, status_flags, detail_code) in enumerate(
        alignment_sensors
    ):
        add_sensor_status(
            records,
            host_t0 - 1.6 + index * 0.08,
            seq,
            snapshot_id=alignment_snapshot_id,
            sensor_id=sensor_id,
            instance_id=instance_id,
            status_flags=status_flags,
            detail_code=detail_code,
            index=index,
            total=len(alignment_sensors),
        )
        seq += 1
    add_status(
        records,
        host_t0 - 1.2,
        seq,
        STATUS_ALIGNMENT,
        mission_start_ms - 1200,
        3,
        alignment_snapshot_id,
    )
    seq += 1
    add_preflight_status(
        records,
        host_t0 - 1.0,
        seq,
        lifecycle_state=3,
        calibration_state=4,
        calibration_mode=2,
        completed_face_mask=0x3F,
        alignment_state=3,
        alignment_ready=True,
    )
    seq += 1
    add_status(records, host_t0, seq, STATUS_MISSION_START, mission_start_ms)
    seq += 1

    x = y = z = 0.0
    vx = vy = vz = 0.0
    ax_lpf = ay_lpf = 0.0
    wind_ax_bias = random.uniform(-1.2, 1.2)
    wind_ay_bias = random.uniform(-1.2, 1.2)

    t = 0.0
    max_sim_s = max(duration_s, 120.0)
    total_iterations = max(1, int(math.ceil(max_sim_s / sim_dt)) + 1)

    t_apogee: float | None = None
    t_ground_contact: float | None = None
    parachute_sent = False
    landing_sent = False

    last_roll = last_pitch = last_yaw = 0.0

    while t <= max_sim_s:
        iteration = min(total_iterations, int(round(t / sim_dt)))
        if iteration % 100 == 0:
            check_cancel()
            if progress is not None:
                progress(iteration, total_iterations)
        time_ms = mission_start_ms + int(round(t * 1000.0))
        host_ts = host_t0 + t

        if t_ground_contact is None:
            if t_apogee is None:
                az = boost_accel_z(t)
                vz += az * sim_dt
                z = max(0.0, z + vz * sim_dt)
                if t > 1.2 and vz <= 0.0:
                    t_apogee = t
            else:
                tau = t - t_apogee
                if not parachute_sent:
                    add_status(records, host_ts, seq, STATUS_PARACHUTE_DEPLOY, time_ms)
                    seq += 1
                    parachute_sent = True

                target_vz = -8.0
                old_vz = vz
                vz += (target_vz - vz) * (sim_dt / 1.8)
                az = (vz - old_vz) / sim_dt
                z_next = z + vz * sim_dt
                if z_next <= 0.0 and t > (t_apogee or 0.0) + 2.0:
                    z = 0.0
                    vz = -1.2
                    t_ground_contact = t
                else:
                    z = max(0.0, z_next)

            ax_lpf = 0.985 * ax_lpf + 0.015 * random.gauss(0.0, 5.0)
            ay_lpf = 0.985 * ay_lpf + 0.015 * random.gauss(0.0, 5.0)
            ax = wind_ax_bias + ax_lpf + random.gauss(0.0, 1.8)
            ay = wind_ay_bias + ay_lpf + random.gauss(0.0, 1.8)

            if parachute_sent:
                # After chute deployment, horizontal speed should slowly approach
                # about 2~3 m/s instead of continuing a near-ballistic drift.
                # The final x/y position is the integrated landing offset and
                # must NOT be forced back to zero.
                vxy = math.hypot(vx, vy)
                if vxy > 1e-6:
                    ux, uy = vx / vxy, vy / vxy
                else:
                    angle = random.uniform(0.0, 2.0 * math.pi)
                    ux, uy = math.cos(angle), math.sin(angle)
                target_vxy = 2.5
                target_vx = ux * target_vxy
                target_vy = uy * target_vxy
                vx += (target_vx - vx) * (sim_dt / 4.5)
                vy += (target_vy - vy) * (sim_dt / 4.5)
                ax = (target_vx - vx) / 4.5 + random.gauss(0.0, 0.12)
                ay = (target_vy - vy) / 4.5 + random.gauss(0.0, 0.12)
            else:
                damp = 0.015
                vx += (ax - damp * vx) * sim_dt
                vy += (ay - damp * vy) * sim_dt

            x += vx * sim_dt
            y += vy * sim_dt

            if t_apogee is None:
                roll = 0.04 * math.sin(2.0 * math.pi * 1.6 * t) + random.gauss(0.0, 0.01)
                pitch = 0.05 * math.sin(2.0 * math.pi * 1.3 * t + 0.7) + random.gauss(0.0, 0.01)
                yaw = 0.25 * math.sin(2.0 * math.pi * 0.30 * t)
                gyro = (
                    0.45 * math.cos(2.0 * math.pi * 1.6 * t),
                    0.38 * math.cos(2.0 * math.pi * 1.3 * t + 0.7),
                    0.10 * math.cos(2.0 * math.pi * 0.30 * t),
                )
            else:
                tau = t - t_apogee
                amp = 1.8 * math.exp(-tau / 7.0)
                roll = amp * math.sin(2.0 * math.pi * 0.85 * tau)
                pitch = amp * math.cos(2.0 * math.pi * 0.72 * tau)
                yaw = 1.2 * math.sin(2.0 * math.pi * 0.25 * tau)
                gyro = (
                    2.4 * math.exp(-tau / 6.0) * math.cos(2.0 * math.pi * 0.85 * tau),
                    2.0 * math.exp(-tau / 6.0) * math.sin(2.0 * math.pi * 0.72 * tau),
                    0.6 * math.exp(-tau / 8.0),
                )

            last_roll, last_pitch, last_yaw = roll, pitch, yaw

        else:
            tau = t - t_ground_contact
            az, gyro = impact_profile(tau)
            ax = 0.2 * math.sin(2.0 * math.pi * 5.0 * tau) * math.exp(-tau / 0.16)
            ay = -0.15 * math.cos(2.0 * math.pi * 4.0 * tau) * math.exp(-tau / 0.16)

            vx *= math.exp(-sim_dt / 0.10)
            vy *= math.exp(-sim_dt / 0.10)
            vz *= math.exp(-sim_dt / 0.06)

            # Keep the integrated landing offset in x/y. Only z returns to the
            # ground plane. Do not force x/y back to zero in the last frames.
            z = 0.0

            if tau < 0.45:
                roll = last_roll + 0.35 * math.sin(2.0 * math.pi * 4.0 * tau) * math.exp(-tau / 0.18)
                pitch = last_pitch + 0.28 * math.cos(2.0 * math.pi * 3.2 * tau) * math.exp(-tau / 0.18)
                yaw = last_yaw + 0.22 * math.sin(2.0 * math.pi * 2.5 * tau) * math.exp(-tau / 0.22)
            else:
                # Landed: acceleration and angular velocity are both zero.
                ax = 0.0
                ay = 0.0
                az = 0.0
                gyro = (0.0, 0.0, 0.0)
                vx *= math.exp(-sim_dt / 0.12)
                vy *= math.exp(-sim_dt / 0.12)
                vz *= math.exp(-sim_dt / 0.08)
                if abs(vx) < 0.02:
                    vx = 0.0
                if abs(vy) < 0.02:
                    vy = 0.0
                if abs(vz) < 0.02:
                    vz = 0.0
                roll = last_roll * math.exp(-sim_dt / 0.20)
                pitch = last_pitch * math.exp(-sim_dt / 0.20)
                yaw = last_yaw * math.exp(-sim_dt / 0.25)
                if abs(roll) < 0.01:
                    roll = 0.0
                if abs(pitch) < 0.01:
                    pitch = 0.0
                if abs(yaw) < 0.01:
                    yaw = 0.0

            last_roll, last_pitch, last_yaw = roll, pitch, yaw

        if t + 1e-9 >= next_log_t:
            quat = euler_to_quat(last_roll, last_pitch, last_yaw)
            add_flight_state(
                records,
                host_ts,
                seq,
                time_ms,
                (ax, ay, az),
                gyro,
                quat,
                (vx, vy, vz),
                (x, y, z),
            )
            seq += 1

            rssi = int(-58 - 0.45 * t + random.gauss(0.0, 1.5))
            if parachute_sent:
                rssi += 4
            snr = 9.0 - 0.06 * t + random.gauss(0.0, 0.5)
            add_link(records, host_ts + 0.01, time_ms, rssi, snr)

            next_log_t += log_dt

            if t_ground_contact is not None and (t - t_ground_contact) >= 0.80 and not landing_sent:
                add_status(records, host_ts, seq, STATUS_LANDING, time_ms)
                landing_sent = True
                break

        t += sim_dt

    check_cancel()
    if not landing_sent:
        # Fallback: force a final landed sample and landing status.
        t = min(max_sim_s, t)
        host_ts = host_t0 + t
        time_ms = mission_start_ms + int(round(t * 1000.0))
        quat = euler_to_quat(0.0, 0.0, 0.0)
        add_flight_state(records, host_ts, seq, time_ms, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), quat, (vx, vy, vz), (x, y, max(0.0, z)))
        seq += 1
        add_status(records, host_ts, seq, STATUS_LANDING, time_ms)

    gap_start = host_t0 + 24.0
    gap_end = host_t0 + 28.0
    records = [
        r for r in records
        if not (gap_start < float(r.get("ts", 0.0)) < gap_end and r.get("kind") != "STATUS")
    ]

    records.sort(key=lambda r: float(r.get("ts", 0.0)))
    check_cancel()
    if progress is not None:
        progress(total_iterations, total_iterations)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a 5 Hz fake flight JSONL log.")
    parser.add_argument("--output", type=Path, default=Path("logs/fake_flight_log.jsonl"))
    parser.add_argument(
        "--duration",
        type=float,
        default=90.0,
        help="Minimum simulation time budget. The generator still tries to reach landing.",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    records = simulate(duration_s=args.duration, seed=args.seed)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "ts": 0.0,
        "dir": "META",
        "layer": "SIMULATION",
        "kind": SIMULATION_LABEL,
        "simulated": True,
        "simulation_label": SIMULATION_LABEL,
        "note": "Generated by processing.fake_log_generator for validation, not real flight data.",
    }

    with args.output.open("w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"generated: {args.output}")
    print(f"records: {len(records)}")


if __name__ == "__main__":
    main()
