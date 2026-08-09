from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from protocol.air import (
    AIR_PROFILE_COMPACT_V0,
    AirCapabilityMessage,
    AirFlightStateMessage,
    AirPreflightStateMessage,
    AirPreflightStatusMessage,
    AirStatusMessage,
    accel_raw_to_mps2,
    gyro_raw_to_radps,
    parse_air_frame as parse_wire_air_frame,
)
from protocol.common import AirStatusId

from .flight_plotter import FlightPlotter, PlotterConfig

GSP_TYPE_AIR_RX = 0x02

STATUS_BOOT = int(AirStatusId.BOOT)
STATUS_SELFTEST_OK = int(AirStatusId.SELFTEST_COMPLETE)
STATUS_MISSION_START = int(AirStatusId.MISSION_START)
STATUS_LAUNCH = int(AirStatusId.LAUNCH)
STATUS_PARACHUTE_DEPLOY = int(AirStatusId.PARACHUTE_DEPLOY)
STATUS_LANDING = int(AirStatusId.LANDING)
STATUS_LOCKED = int(AirStatusId.LOCKED)
STATUS_UNLOCKED = int(AirStatusId.UNLOCKED)
STATUS_GNSS_POSITION = int(AirStatusId.GNSS_POSITION)
STATUS_ALIGNMENT = int(AirStatusId.ALIGNMENT)
STATUS_CALIBRATION = int(AirStatusId.CALIBRATION)
STATUS_CALIBRATION_FACE = int(AirStatusId.CALIBRATION_FACE)

FLIGHT_TELEMETRY_PERIOD_MS = 200

STATUS_NAME = {
    STATUS_BOOT: "BOOT",
    STATUS_SELFTEST_OK: "SELFTEST_OK",
    STATUS_MISSION_START: "MISSION_START",
    STATUS_LAUNCH: "LAUNCH",
    STATUS_PARACHUTE_DEPLOY: "PARACHUTE_DEPLOY",
    STATUS_LANDING: "LANDING",
    STATUS_LOCKED: "LOCKED",
    STATUS_UNLOCKED: "UNLOCKED",
    STATUS_GNSS_POSITION: "GNSS_POSITION",
    STATUS_ALIGNMENT: "ALIGNMENT",
    STATUS_CALIBRATION: "CALIBRATION",
    STATUS_CALIBRATION_FACE: "CALIBRATION_FACE",
}

ProgressCallback = Callable[[int, int, str], None]


@dataclass
class TimedVector:
    time_s: float
    values: tuple[float, ...]
    source_time_ms: int


@dataclass
class StatusEvent:
    time_s: float
    status_id: int
    name: str
    source_time_ms: int
    arg0: int = 0
    arg1: int = 0


@dataclass
class LinkSample:
    time_s: float
    rssi_dbm: float
    snr_db: float


@dataclass
class PacketLossStats:
    expected_period_ms: int = FLIGHT_TELEMETRY_PERIOD_MS
    expected_rate_hz: int = 5
    received_packets: int = 0
    expected_packets: int = 0
    lost_packets: int = 0
    packet_loss_rate: float = 0.0
    loss_window_end_basis: str = "last_received_flight_state"
    loss_per_second: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class FlightData:
    mission_start_ms: int
    landing_ms: Optional[int]
    parachute_ms: Optional[int]
    end_ms: int
    source_log: Path
    log_kind: str = "REAL_FLIGHT"
    simulated: bool = False
    simulation_label: str = ""
    air_profile_id: int | None = None
    command_policy: int | None = None
    accel_full_scale_g: float | None = None
    gyro_full_scale_dps: float | None = None
    final_preflight_lifecycle: int | None = None
    calibration_mode: int | None = None
    calibration_final_state: int | None = None
    alignment_final_state: int | None = None
    gnss_position_usable_before_start: bool | None = None
    status_events: list[StatusEvent] = field(default_factory=list)
    accel: list[TimedVector] = field(default_factory=list)
    gyro: list[TimedVector] = field(default_factory=list)
    quat: list[TimedVector] = field(default_factory=list)
    quat_valid: list[TimedVector] = field(default_factory=list)
    euler: list[TimedVector] = field(default_factory=list)
    vel: list[TimedVector] = field(default_factory=list)
    pos: list[TimedVector] = field(default_factory=list)
    link: list[LinkSample] = field(default_factory=list)
    packet_loss: PacketLossStats = field(default_factory=PacketLossStats)
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.end_ms - self.mission_start_ms) / 1000.0)

    @property
    def parachute_time_s(self) -> Optional[float]:
        if self.parachute_ms is None:
            return None
        return (self.parachute_ms - self.mission_start_ms) / 1000.0


def int8(value: int) -> int:
    value &= 0xFF
    return value - 256 if value >= 128 else value


def q15_to_float(value: int) -> float:
    return max(-1.0, min(1.0, float(value) / 32768.0))


def normalize_quat(quat: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    w, x, y, z = quat
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm <= 0.0:
        return 1.0, 0.0, 0.0, 0.0
    return w / norm, x / norm, y / norm, z / norm


def quat_to_euler_rpy(quat: tuple[float, float, float, float]) -> tuple[float, float, float]:
    w, x, y, z = normalize_quat(quat)

    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    sinp = 2.0 * (w * y - z * x)
    if sinp >= 1.0:
        pitch = math.pi / 2.0
    elif sinp <= -1.0:
        pitch = -math.pi / 2.0
    else:
        pitch = math.asin(sinp)

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return roll, pitch, yaw


def safe_float_tuple(values: Iterable[object], expected_len: int) -> tuple[float, ...] | None:
    try:
        out = tuple(float(v) for v in values)
    except (TypeError, ValueError):
        return None
    if len(out) != expected_len:
        return None
    return out


def safe_int_tuple(values: Iterable[object], expected_len: int) -> tuple[int, ...] | None:
    try:
        out = tuple(int(v) for v in values)
    except (TypeError, ValueError):
        return None
    if len(out) != expected_len:
        return None
    return out


def raw_accel_to_mps2(
    raw: Iterable[object],
    full_scale_g: float,
) -> tuple[float, float, float]:
    values = tuple(int(value) for value in raw)
    return accel_raw_to_mps2(values, full_scale_g)  # type: ignore[arg-type]


def raw_gyro_to_radps(
    raw: Iterable[object],
    full_scale_dps: float,
) -> tuple[float, float, float]:
    values = tuple(int(value) for value in raw)
    return gyro_raw_to_radps(values, full_scale_dps)  # type: ignore[arg-type]


def calculate_packet_loss_stats(
    flight_time_ms: Iterable[int],
    landing_ms: int | None,
    period_ms: int = FLIGHT_TELEMETRY_PERIOD_MS,
) -> PacketLossStats:
    if period_ms <= 0:
        raise ValueError("period_ms must be positive")

    valid_times = sorted({int(time_ms) for time_ms in flight_time_ms if int(time_ms) >= 0})
    end_basis = "landing" if landing_ms is not None else "last_received_flight_state"
    end_ms = int(landing_ms) if landing_ms is not None else (valid_times[-1] if valid_times else None)
    expected_rate_hz = int(round(1000 / period_ms))

    if end_ms is None or end_ms < 0:
        return PacketLossStats(
            expected_period_ms=period_ms,
            expected_rate_hz=expected_rate_hz,
            loss_window_end_basis=end_basis,
        )

    last_tick = end_ms // period_ms
    expected_ticks = set(range(last_tick + 1))
    received_ticks: set[int] = set()
    for time_ms in valid_times:
        tick_index = round(time_ms / period_ms)
        if tick_index in expected_ticks:
            received_ticks.add(tick_index)
    lost_ticks = expected_ticks - received_ticks

    last_second = (last_tick * period_ms) // 1000
    loss_counts = [0] * (last_second + 1)
    for tick_index in lost_ticks:
        expected_time_ms = tick_index * period_ms
        loss_counts[expected_time_ms // 1000] += 1

    expected_packets = len(expected_ticks)
    received_packets = len(received_ticks)
    lost_packets = max(0, expected_packets - received_packets)
    packet_loss_rate = lost_packets / expected_packets if expected_packets > 0 else 0.0
    return PacketLossStats(
        expected_period_ms=period_ms,
        expected_rate_hz=expected_rate_hz,
        received_packets=received_packets,
        expected_packets=expected_packets,
        lost_packets=lost_packets,
        packet_loss_rate=packet_loss_rate,
        loss_window_end_basis=end_basis,
        loss_per_second=list(enumerate(loss_counts)),
    )


def parse_air_frame(
    frame: bytes,
    capability: AirCapabilityMessage | None = None,
) -> tuple[str, dict] | None:
    """Raw-log fallback parser for SilverStar 0.0.8 / Profile 0 only."""
    try:
        _air, message = parse_wire_air_frame(frame)
    except ValueError:
        return None

    if isinstance(message, AirCapabilityMessage):
        return "CAPABILITY", {
            "seq": message.seq,
            "air_profile_id": message.air_profile_id,
            "command_policy": message.command_policy,
            "calibration_mode_mask": message.calibration_mode_mask,
            "alignment_capability_mask": message.alignment_capability_mask,
            "accel_full_scale_g": message.accel_full_scale_g,
            "gyro_full_scale_dps": message.gyro_full_scale_dps,
            "_message": message,
        }

    if isinstance(message, AirPreflightStatusMessage):
        return "PREFLIGHT_STATUS", dict(message.__dict__)

    if isinstance(message, AirStatusMessage):
        return "STATUS", dict(message.__dict__)

    if isinstance(message, (AirPreflightStateMessage, AirFlightStateMessage)):
        info = {
            "seq": message.seq,
            "time_ms": message.time_ms,
            "accel_raw": message.accel_raw,
            "gyro_raw": message.gyro_raw,
            "quat_q15": message.quat_q15,
            "quat": message.quat,
            "quat_raw_zero": message.quat_raw_zero,
            "quat_valid": message.quat_valid,
        }
        if (
            capability is not None
            and capability.profile_supported
            and capability.accel_full_scale_g > 0
            and capability.gyro_full_scale_dps > 0
        ):
            info["accel_full_scale_g"] = capability.accel_full_scale_g
            info["gyro_full_scale_dps"] = capability.gyro_full_scale_dps
            info["accel_mps2"] = accel_raw_to_mps2(
                message.accel_raw,
                capability.accel_full_scale_g,
            )
            info["gyro_radps"] = gyro_raw_to_radps(
                message.gyro_raw,
                capability.gyro_full_scale_dps,
            )
        if isinstance(message, AirFlightStateMessage):
            info["vel_mps"] = message.vel_mps
            info["pos_m"] = message.pos_m
            return "FLIGHT_STATE", info
        return "PREFLIGHT_STATE", info

    return None


class FlightLogProcessor:
    """
    Offline processor for SilverStar 0.0.8 / AIR_PROFILE_COMPACT_V0 logs.

    Supported records:
    1. Parsed AIR records:
       CAPABILITY/PREFLIGHT_STATUS/PREFLIGHT_STATE/STATUS/FLIGHT_STATE.

    2. Current GSP AIR_RX records:
       {"dir":"RX","layer":"GSP","msg_type":2 or "gsp_type":2,"payload_hex":"..."}
       payload = RSSI_DBM, SNR_Q4, AIR_LEN, AIR_FRAME
    """

    def __init__(
        self,
        output_root: Path | str = "data",
        gif_fps: int = 5,
        gap_threshold_s: Optional[float] = None,
        pos_z_is_height: bool = True,
    ) -> None:
        self.output_root = Path(output_root)
        self.gif_fps = max(1, min(30, int(gif_fps)))
        self.gap_threshold_s = gap_threshold_s
        self.pos_z_is_height = pos_z_is_height

    def process_file(self, log_path: Path | str, progress: ProgressCallback | None = None) -> Path:
        log_path = Path(log_path)
        output_dir = self._make_output_dir()

        raw_records = self._read_jsonl(log_path)
        data = self._extract_flight_data(raw_records, log_path)

        plotter = FlightPlotter(
            PlotterConfig(
                gif_fps=self.gif_fps,
                gap_threshold_s=self.gap_threshold_s,
                pos_z_is_height=self.pos_z_is_height,
            )
        )

        frame_count = plotter.estimate_gif_frame_count(data)
        total_steps = 2 + 7 + frame_count + 1
        done = 0

        def step(msg: str) -> None:
            nonlocal done
            done += 1
            if progress is not None:
                progress(done, total_steps, msg)

        self._write_processed_data(data, output_dir / "processed_data.txt")
        step("processed_data.txt")

        self._write_summary(data, output_dir / "summary.txt")
        step("summary.txt")

        plotter.plot_vector_figure(output_dir / "accel.png", "ACC", data.accel, ("ax", "ay", "az"), "ACC / m/s^2", data.parachute_time_s)
        step("accel.png")

        plotter.plot_vector_figure(output_dir / "gyro.png", "GYRO", data.gyro, ("gx", "gy", "gz"), "GYRO / rad/s", data.parachute_time_s)
        step("gyro.png")

        plotter.plot_vector_figure(output_dir / "euler.png", "EULER FROM QUAT", data.euler, ("roll", "pitch", "yaw"), "ANGLE / rad", data.parachute_time_s)
        step("euler.png")

        plotter.plot_vector_figure(output_dir / "velocity.png", "VEL", data.vel, ("vx", "vy", "vz"), "VEL / m/s", data.parachute_time_s)
        step("velocity.png")

        plotter.plot_vector_figure(output_dir / "position.png", "POS", data.pos, ("x", "y", "z"), "POS / m", data.parachute_time_s)
        step("position.png")

        plotter.plot_link_quality(output_dir / "link_quality.png", data.link, data.parachute_time_s)
        step("link_quality.png")

        plotter.plot_packet_loss_per_second(
            output_dir / "packet_loss_per_second.png",
            data.packet_loss.loss_per_second,
        )
        step("packet_loss_per_second.png")

        frames_dir = output_dir / "gif_frames"
        frames_dir.mkdir(exist_ok=True)
        gif_path = output_dir / "attitude_motion.gif"

        for _ in plotter.generate_attitude_motion_gif(data, gif_path, frames_dir):
            step("gif frame")

        step("attitude_motion.gif")

        self._write_manifest(data, output_dir / "manifest.json", log_path)
        return output_dir

    def _read_jsonl(self, log_path: Path) -> list[dict]:
        if log_path.suffix.lower() != ".jsonl":
            raise ValueError("请选择 .jsonl 日志文件。")
        if not log_path.exists() or not log_path.is_file():
            raise ValueError("文件不存在或不是普通文件。")
        if log_path.stat().st_size <= 0:
            raise ValueError("文件为空。")

        records: list[dict] = []
        non_empty_lines = 0

        try:
            with log_path.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    non_empty_lines += 1

                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"第 {line_no} 行不是合法 JSONL/JSON 格式：{exc.msg}") from exc

                    if not isinstance(obj, dict):
                        raise ValueError(f"第 {line_no} 行不是 JSON 对象，日志格式不正确。")

                    obj["_line_no"] = line_no
                    records.append(obj)
        except UnicodeDecodeError as exc:
            raise ValueError("文件不是 UTF-8 文本日志，无法解析。") from exc

        if non_empty_lines == 0:
            raise ValueError("文件为空或只有空行。")
        if not records:
            raise ValueError("没有找到有效 JSON 记录。")

        return records

    def _detect_log_kind(self, records: list[dict]) -> tuple[str, bool, str]:
        for r in records:
            if r.get("simulated") is True:
                label = str(r.get("simulation_label") or "SIMULATION_VALIDATION")
                return label, True, label
            if r.get("kind") == "SIMULATION_VALIDATION":
                label = str(r.get("simulation_label") or "SIMULATION_VALIDATION")
                return label, True, label
            if r.get("layer") == "SIMULATION":
                label = str(r.get("simulation_label") or "SIMULATION_VALIDATION")
                return label, True, label
        return "REAL_FLIGHT", False, ""

    def _has_any_air_source_record(self, records: list[dict]) -> bool:
        for r in records:
            if r.get("dir") != "RX":
                continue
            if r.get("layer") == "AIR_PARSED" and r.get("kind") in {
                "CAPABILITY",
                "PREFLIGHT_STATUS",
                "PREFLIGHT_STATE",
                "FLIGHT_STATE",
                "STATUS",
            }:
                return True
            if r.get("layer") == "GSP" and self._is_gsp_air_rx(r):
                return True
        return False

    def _extract_flight_data(self, records: list[dict], log_path: Path) -> FlightData:
        log_kind, simulated, simulation_label = self._detect_log_kind(records)
        if not self._has_any_air_source_record(records):
            raise ValueError("没有找到有效的 AIR_PARSED 或 GSP AIR_RX 飞行数据记录。")
        has_parsed_flight = any(
            r.get("dir") == "RX"
            and r.get("layer") == "AIR_PARSED"
            and r.get("kind") == "FLIGHT_STATE"
            for r in records
        )
        has_parsed_status = any(
            r.get("dir") == "RX"
            and r.get("layer") == "AIR_PARSED"
            and r.get("kind") == "STATUS"
            for r in records
        )
        has_parsed_link = any(
            r.get("dir") == "RX"
            and r.get("layer") == "AIR_PARSED"
            and r.get("kind") == "FLIGHT_STATE"
            and r.get("rssi_dbm") is not None
            and r.get("snr_db") is not None
            for r in records
        )

        all_data: dict[str, list[tuple[int, tuple[float, ...]]]] = {
            "accel": [],
            "gyro": [],
            "quat": [],
            "quat_valid": [],
            "vel": [],
            "pos": [],
        }
        all_status: list[tuple[int, int, int, int]] = []
        all_link: list[tuple[int, float, float]] = []
        all_flight_time_ms: list[int] = []
        capability_info: dict | None = None
        final_preflight_status: dict | None = None
        raw_capability: AirCapabilityMessage | None = None

        for r in records:
            if r.get("dir") != "RX":
                continue

            layer = r.get("layer")

            if layer == "AIR_PARSED":
                self._add_air_parsed_record(r, all_data, all_status, all_flight_time_ms)
                if r.get("kind") == "CAPABILITY" and int(r.get("air_profile_id", -1)) == AIR_PROFILE_COMPACT_V0:
                    capability_info = r
                elif r.get("kind") == "PREFLIGHT_STATUS":
                    final_preflight_status = r
                elif (
                    r.get("kind") == "FLIGHT_STATE"
                    and r.get("rssi_dbm") is not None
                    and r.get("snr_db") is not None
                ):
                    try:
                        all_link.append(
                            (int(r["time_ms"]), float(r["rssi_dbm"]), float(r["snr_db"]))
                        )
                    except (KeyError, TypeError, ValueError):
                        pass

            elif layer == "GSP" and self._is_gsp_air_rx(r):
                link = self._extract_link_from_gsp(r, raw_capability)
                if link is not None and not has_parsed_link:
                    all_link.append(link)

                # Always inspect the raw AIR frame for session metadata.  A
                # partially upgraded log may already contain parsed FLIGHT and
                # STATUS records while Capability/PStatus only exist in GSP.
                # Telemetry/status samples themselves still prefer AIR_PARSED
                # records so the paired raw record is never counted twice.
                parsed = self._extract_air_from_gsp(r, raw_capability)
                if parsed is not None:
                    kind, info = parsed
                    if kind == "CAPABILITY":
                        candidate = info.get("_message")
                        if isinstance(candidate, AirCapabilityMessage) and candidate.profile_supported:
                            raw_capability = candidate
                            capability_info = info
                    elif kind == "PREFLIGHT_STATUS":
                        final_preflight_status = info
                    elif kind == "FLIGHT_STATE" and not has_parsed_flight:
                        self._add_flight_state(info, all_data, all_flight_time_ms)
                    elif kind == "STATUS" and not has_parsed_status:
                        self._add_status(info, all_status)

        start_ms = self._first_status_time(all_status, STATUS_MISSION_START)
        if start_ms is None:
            start_ms = min(all_flight_time_ms) if all_flight_time_ms else None

        if self._max_data_time(all_data) is None:
            raise ValueError("没有找到有效的 FLIGHT_STATE 遥测数据。")
        if start_ms is None:
            raise ValueError("没有 MISSION_START 或 FLIGHT_STATE，无法确定任务起点。")

        landing_ms = self._first_status_time(all_status, STATUS_LANDING, after_ms=start_ms)
        parachute_ms = self._first_status_time(all_status, STATUS_PARACHUTE_DEPLOY, after_ms=start_ms)

        last_data_ms = self._max_data_time(all_data)
        end_ms = landing_ms if landing_ms is not None else (last_data_ms if last_data_ms is not None else start_ms)
        if end_ms < start_ms:
            end_ms = start_ms

        data = FlightData(
            mission_start_ms=start_ms,
            landing_ms=landing_ms,
            parachute_ms=parachute_ms,
            end_ms=end_ms,
            source_log=log_path,
            log_kind=log_kind,
            simulated=simulated,
            simulation_label=simulation_label,
            air_profile_id=self._optional_int(capability_info, "air_profile_id"),
            command_policy=self._optional_int(capability_info, "command_policy"),
            accel_full_scale_g=self._optional_float(capability_info, "accel_full_scale_g"),
            gyro_full_scale_dps=self._optional_float(capability_info, "gyro_full_scale_dps"),
            final_preflight_lifecycle=self._optional_int(final_preflight_status, "lifecycle_state"),
            calibration_mode=self._optional_int(final_preflight_status, "calibration_mode"),
            calibration_final_state=self._optional_int(final_preflight_status, "calibration_state"),
            alignment_final_state=self._optional_int(final_preflight_status, "alignment_state"),
            gnss_position_usable_before_start=self._optional_bool(
                final_preflight_status,
                "gnss_position_usable",
            ),
        )
        task_flight_time_ms = [time_ms - start_ms for time_ms in all_flight_time_ms]
        task_landing_ms = landing_ms - start_ms if landing_ms is not None else None
        data.packet_loss = calculate_packet_loss_stats(task_flight_time_ms, task_landing_ms)

        if landing_ms is None:
            data.warnings.append("LANDING not found; using last data timestamp as end.")
        if parachute_ms is None:
            data.warnings.append("PARACHUTE_DEPLOY not found; no chute marker.")
        if self._first_status_time(all_status, STATUS_MISSION_START) is None:
            data.warnings.append("MISSION_START not found; first FLIGHT_STATE defines mission start.")
        if capability_info is None:
            data.warnings.append(
                "CAPABILITY not found; raw IMU counts are not converted by guessing a full scale."
            )

        data.status_events = [
            StatusEvent((time_ms - start_ms) / 1000.0, sid, STATUS_NAME.get(sid, f"0x{sid:02X}"), time_ms, arg0, arg1)
            for (sid, time_ms, arg0, arg1) in all_status
            if start_ms <= time_ms <= end_ms
        ]

        data.accel = self._convert_samples(all_data["accel"], start_ms, end_ms)
        data.gyro = self._convert_samples(all_data["gyro"], start_ms, end_ms)
        data.quat = self._convert_samples(all_data["quat"], start_ms, end_ms)
        data.quat_valid = self._convert_samples(all_data["quat_valid"], start_ms, end_ms)
        data.vel = self._convert_samples(all_data["vel"], start_ms, end_ms)
        data.pos = self._convert_samples(all_data["pos"], start_ms, end_ms)
        data.euler = [
            TimedVector(q.time_s, quat_to_euler_rpy(q.values), q.source_time_ms)  # type: ignore[arg-type]
            for q in data.quat
            if len(q.values) == 4
        ]
        data.link = [
            LinkSample((time_ms - start_ms) / 1000.0, rssi, snr)
            for (time_ms, rssi, snr) in all_link
            if start_ms <= time_ms <= end_ms
        ]

        invalid_quat_count = sum(1 for s in data.quat_valid if s.values and s.values[0] < 0.5)
        if invalid_quat_count > 0:
            data.warnings.append(
                f"{invalid_quat_count} FLIGHT_STATE quaternion samples have quat_q15 raw all zero; "
                "check IMU 0x59 Quaternion Pack output."
            )
        if invalid_quat_count > 0 and not data.quat:
            data.warnings.append("No valid quaternion samples; attitude plots/GIF use unit quaternion fallback.")

        return data

    def _is_gsp_air_rx(self, r: dict) -> bool:
        try:
            return int(r.get("gsp_type", r.get("msg_type", -1))) == GSP_TYPE_AIR_RX
        except (TypeError, ValueError):
            return False

    def _add_air_parsed_record(
        self,
        r: dict,
        all_data: dict[str, list[tuple[int, tuple[float, ...]]]],
        all_status: list[tuple[int, int, int, int]],
        all_flight_time_ms: list[int],
    ) -> None:
        kind = r.get("kind")
        if kind == "FLIGHT_STATE":
            self._add_flight_state(r, all_data, all_flight_time_ms)
        elif kind == "STATUS":
            self._add_status(r, all_status)

    def _add_flight_state(
        self,
        r: dict,
        all_data: dict[str, list[tuple[int, tuple[float, ...]]]],
        all_flight_time_ms: list[int],
    ) -> None:
        try:
            time_ms = int(r["time_ms"])
        except (KeyError, TypeError, ValueError):
            return

        all_flight_time_ms.append(time_ms)

        accel = safe_float_tuple(r.get("accel_mps2", ()), 3)
        if accel is None and "accel_raw" in r:
            accel_fs_value = r.get("accel_full_scale_g")
            if accel_fs_value is not None:
                try:
                    accel = raw_accel_to_mps2(r["accel_raw"], float(accel_fs_value))
                except (TypeError, ValueError):
                    accel = None

        gyro = safe_float_tuple(r.get("gyro_radps", ()), 3)
        if gyro is None and "gyro_raw" in r:
            gyro_fs_value = r.get("gyro_full_scale_dps")
            if gyro_fs_value is not None:
                try:
                    gyro = raw_gyro_to_radps(r["gyro_raw"], float(gyro_fs_value))
                except (TypeError, ValueError):
                    gyro = None

        q_raw = safe_int_tuple(r.get("quat_q15", ()), 4) if "quat_q15" in r else None
        quat_raw_zero = False
        if "quat_raw_zero" in r:
            quat_raw_zero = bool(r.get("quat_raw_zero"))
        elif q_raw is not None:
            quat_raw_zero = all(v == 0 for v in q_raw)

        if "quat_valid" in r:
            quat_valid = bool(r.get("quat_valid"))
        elif q_raw is not None:
            quat_valid = not quat_raw_zero
        else:
            quat_valid = True

        quat = safe_float_tuple(r.get("quat", ()), 4)
        if quat is None and q_raw is not None:
            quat = normalize_quat(tuple(q15_to_float(v) for v in q_raw))  # type: ignore[arg-type]

        vel = safe_float_tuple(r.get("vel_mps", r.get("vel", ())), 3)
        pos = safe_float_tuple(r.get("pos_m", r.get("pos", ())), 3)

        if accel is not None:
            all_data["accel"].append((time_ms, accel))
        if gyro is not None:
            all_data["gyro"].append((time_ms, gyro))
        if quat is not None:
            all_data["quat_valid"].append((time_ms, (1.0 if quat_valid else 0.0,)))
            if quat_valid:
                all_data["quat"].append((time_ms, normalize_quat(quat)))  # type: ignore[arg-type]
        if vel is not None:
            all_data["vel"].append((time_ms, vel))
        if pos is not None:
            all_data["pos"].append((time_ms, pos))

    def _add_status(self, r: dict, all_status: list[tuple[int, int, int, int]]) -> None:
        try:
            sid = int(r["status_id"])
            time_ms = int(r["time_ms"])
            arg0 = int(r.get("arg0", 0))
            arg1 = int(r.get("arg1", 0))
        except (KeyError, TypeError, ValueError):
            return
        all_status.append((sid, time_ms, arg0, arg1))

    def _extract_link_from_gsp(
        self,
        r: dict,
        capability: AirCapabilityMessage | None = None,
    ) -> tuple[int, float, float] | None:
        payload = self._gsp_payload_bytes(r)
        if payload is None or len(payload) < 3:
            return None

        rssi = float(int8(payload[0]))
        snr = float(int8(payload[1])) / 4.0
        air_len = payload[2]
        air_frame = payload[3:3 + air_len]

        parsed = parse_air_frame(air_frame, capability) if air_len > 0 else None
        if parsed is not None:
            _kind, info = parsed
            if "time_ms" not in info:
                return None
            time_ms = int(info["time_ms"])
        elif "time_ms" in r:
            try:
                time_ms = int(r["time_ms"])
            except (TypeError, ValueError):
                return None
        else:
            return None

        return time_ms, rssi, snr

    def _extract_air_from_gsp(
        self,
        r: dict,
        capability: AirCapabilityMessage | None = None,
    ) -> tuple[str, dict] | None:
        payload = self._gsp_payload_bytes(r)
        if payload is None or len(payload) < 3:
            return None
        air_len = payload[2]
        air_frame = payload[3:3 + air_len]
        return parse_air_frame(air_frame, capability)

    def _gsp_payload_bytes(self, r: dict) -> bytes | None:
        payload_hex = r.get("payload_hex")
        if isinstance(payload_hex, str):
            try:
                return bytes.fromhex(payload_hex)
            except ValueError:
                return None

        payload = r.get("payload")
        if isinstance(payload, list):
            try:
                return bytes(int(x) & 0xFF for x in payload)
            except (TypeError, ValueError):
                return None

        return None

    @staticmethod
    def _optional_int(record: dict | None, key: str) -> int | None:
        if not record or record.get(key) is None:
            return None
        try:
            return int(record[key])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(record: dict | None, key: str) -> float | None:
        if not record or record.get(key) is None:
            return None
        try:
            return float(record[key])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_bool(record: dict | None, key: str) -> bool | None:
        if not record or record.get(key) is None:
            return None
        return bool(record[key])

    def _first_status_time(self, status: list[tuple[int, int, int, int]], status_id: int, after_ms: int | None = None) -> int | None:
        times = [time_ms for (sid, time_ms, _arg0, _arg1) in status if sid == status_id and (after_ms is None or time_ms >= after_ms)]
        return min(times) if times else None

    def _max_data_time(self, all_data: dict[str, list[tuple[int, tuple[float, ...]]]]) -> int | None:
        values: list[int] = []
        for samples in all_data.values():
            values.extend(time_ms for time_ms, _v in samples)
        return max(values) if values else None

    def _convert_samples(self, samples: list[tuple[int, tuple[float, ...]]], start_ms: int, end_ms: int) -> list[TimedVector]:
        samples = sorted(samples, key=lambda item: item[0])
        return [TimedVector((time_ms - start_ms) / 1000.0, values, time_ms) for (time_ms, values) in samples if start_ms <= time_ms <= end_ms]

    def _make_output_dir(self) -> Path:
        self.output_root.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        idx = 1
        while True:
            path = self.output_root / f"{today}-{idx}"
            if not path.exists():
                path.mkdir(parents=True)
                return path
            idx += 1

    def _write_processed_data(self, data: FlightData, path: Path) -> None:
        def write_vec(f, title: str, samples: list[TimedVector], labels: tuple[str, ...]) -> None:
            f.write(f"\n[{title}]\n")
            f.write("time_s\t" + "\t".join(labels) + "\n")
            for s in samples:
                f.write(f"{s.time_s:.6f}\t" + "\t".join(f"{v:.9g}" for v in s.values) + "\n")

        with path.open("w", encoding="utf-8") as f:
            f.write("Flight offline processed data\n")
            f.write(f"data_kind: {data.log_kind}\n")
            f.write(f"simulated: {str(data.simulated).lower()}\n")
            if data.simulation_label:
                f.write(f"simulation_label: {data.simulation_label}\n")
            f.write(f"source_log: {data.source_log}\n")
            f.write(f"mission_start_ms: {data.mission_start_ms}\n")
            f.write(f"mission_end_ms: {data.end_ms}\n")
            f.write(f"duration_s: {data.duration_s:.3f}\n")
            f.write(f"air_profile_id: {data.air_profile_id}\n")
            f.write(f"command_policy: {data.command_policy}\n")
            f.write(f"accel_full_scale_g: {data.accel_full_scale_g}\n")
            f.write(f"gyro_full_scale_dps: {data.gyro_full_scale_dps}\n")
            f.write(f"parachute_time_s: {data.parachute_time_s if data.parachute_time_s is not None else 'not_found'}\n")
            for w in data.warnings:
                f.write(f"warning: {w}\n")

            f.write("\n[status_events]\n")
            f.write("time_s\tstatus\targ0\targ1\n")
            for ev in data.status_events:
                f.write(f"{ev.time_s:.6f}\t{ev.name}\t{ev.arg0}\t{ev.arg1}\n")

            write_vec(f, "accel_mps2", data.accel, ("ax", "ay", "az"))
            write_vec(f, "gyro_radps", data.gyro, ("gx", "gy", "gz"))
            write_vec(f, "quat_valid", data.quat_valid, ("valid",))
            write_vec(f, "quat_wxyz", data.quat, ("qw", "qx", "qy", "qz"))
            write_vec(f, "euler_rad_from_quat", data.euler, ("roll", "pitch", "yaw"))
            write_vec(f, "velocity_mps", data.vel, ("vx", "vy", "vz"))
            write_vec(f, "position_m", data.pos, ("x", "y", "z"))

            f.write("\n[link_quality]\n")
            f.write("time_s\trssi_dbm\tsnr_db\n")
            for s in data.link:
                f.write(f"{s.time_s:.6f}\t{s.rssi_dbm:.3f}\t{s.snr_db:.3f}\n")

    def _write_summary(self, data: FlightData, path: Path) -> None:
        max_accel = self._max_norm(data.accel)
        max_vel = self._max_norm(data.vel)
        max_height = self._max_z(data.pos)

        with path.open("w", encoding="utf-8") as f:
            f.write("Flight summary\n")
            f.write(f"data_kind: {data.log_kind}\n")
            f.write(f"simulated: {str(data.simulated).lower()}\n")
            if data.simulation_label:
                f.write(f"simulation_label: {data.simulation_label}\n")
            f.write(f"source_log: {data.source_log}\n")
            f.write(f"duration_s: {data.duration_s:.3f}\n")
            f.write(f"air_profile_id: {data.air_profile_id}\n")
            f.write(f"command_policy: {data.command_policy}\n")
            f.write(f"accel_full_scale_g: {data.accel_full_scale_g}\n")
            f.write(f"gyro_full_scale_dps: {data.gyro_full_scale_dps}\n")
            f.write(f"final_preflight_lifecycle: {data.final_preflight_lifecycle}\n")
            f.write(f"calibration_mode: {data.calibration_mode}\n")
            f.write(f"calibration_final_state: {data.calibration_final_state}\n")
            f.write(f"alignment_final_state: {data.alignment_final_state}\n")
            f.write(
                "gnss_position_usable_before_start: "
                f"{data.gnss_position_usable_before_start}\n"
            )
            f.write(f"parachute_time_s: {data.parachute_time_s if data.parachute_time_s is not None else 'not_found'}\n")
            for w in data.warnings:
                f.write(f"warning: {w}\n")

            f.write("\nmax_accel:\n")
            if max_accel is None:
                f.write("no accel data\n")
            else:
                t, values, norm = max_accel
                f.write(f"time_s: {t:.6f}\n")
                f.write(f"ax_mps2: {values[0]:.6g}\n")
                f.write(f"ay_mps2: {values[1]:.6g}\n")
                f.write(f"az_mps2: {values[2]:.6g}\n")
                f.write(f"norm_mps2: {norm:.6g}\n")

            f.write("\nmax_velocity:\n")
            if max_vel is None:
                f.write("no velocity data\n")
            else:
                t, values, norm = max_vel
                f.write(f"time_s: {t:.6f}\n")
                f.write(f"vx_mps: {values[0]:.6g}\n")
                f.write(f"vy_mps: {values[1]:.6g}\n")
                f.write(f"vz_mps: {values[2]:.6g}\n")
                f.write(f"norm_mps: {norm:.6g}\n")

            f.write("\nmax_height:\n")
            if max_height is None:
                f.write("no position data\n")
            else:
                t, z = max_height
                f.write(f"time_s: {t:.6f}\n")
                f.write(f"z_m: {z:.6g}\n")

            packet_loss = data.packet_loss
            f.write("\npacket_loss:\n")
            f.write(f"expected_period_ms: {packet_loss.expected_period_ms}\n")
            f.write(f"expected_rate_hz: {packet_loss.expected_rate_hz}\n")
            f.write(f"received_packets: {packet_loss.received_packets}\n")
            f.write(f"expected_packets: {packet_loss.expected_packets}\n")
            f.write(f"lost_packets: {packet_loss.lost_packets}\n")
            f.write(f"packet_loss_rate: {packet_loss.packet_loss_rate:.9g}\n")
            f.write(f"packet_loss_percent: {packet_loss.packet_loss_rate * 100.0:.6g}%\n")
            f.write(f"loss_window_end_basis: {packet_loss.loss_window_end_basis}\n")

    def _max_norm(self, samples: list[TimedVector]) -> tuple[float, tuple[float, ...], float] | None:
        if not samples:
            return None
        best = None
        for s in samples:
            if len(s.values) < 3:
                continue
            norm = math.sqrt(s.values[0] ** 2 + s.values[1] ** 2 + s.values[2] ** 2)
            if best is None or norm > best[2]:
                best = (s.time_s, s.values, norm)
        return best

    def _max_z(self, samples: list[TimedVector]) -> tuple[float, float] | None:
        if not samples:
            return None
        best = None
        for s in samples:
            if len(s.values) < 3:
                continue
            z = s.values[2]
            if best is None or z > best[1]:
                best = (s.time_s, z)
        return best

    def _write_manifest(self, data: FlightData, path: Path, log_path: Path) -> None:
        manifest = {
            "source_log": str(log_path),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "protocol": "SilverStar_0.0.8_AIR_PROFILE_COMPACT_V0",
            "data_kind": data.log_kind,
            "simulated": data.simulated,
            "simulation_label": data.simulation_label,
            "mission_start_ms": data.mission_start_ms,
            "landing_ms": data.landing_ms,
            "parachute_ms": data.parachute_ms,
            "duration_s": data.duration_s,
            "capability": {
                "air_profile_id": data.air_profile_id,
                "command_policy": data.command_policy,
                "accel_full_scale_g": data.accel_full_scale_g,
                "gyro_full_scale_dps": data.gyro_full_scale_dps,
            },
            "preflight": {
                "final_lifecycle": data.final_preflight_lifecycle,
                "calibration_mode": data.calibration_mode,
                "calibration_final_state": data.calibration_final_state,
                "alignment_final_state": data.alignment_final_state,
                "gnss_position_usable_before_start": data.gnss_position_usable_before_start,
            },
            "packet_loss": {
                "expected_period_ms": data.packet_loss.expected_period_ms,
                "expected_rate_hz": data.packet_loss.expected_rate_hz,
                "received_packets": data.packet_loss.received_packets,
                "expected_packets": data.packet_loss.expected_packets,
                "lost_packets": data.packet_loss.lost_packets,
                "packet_loss_rate": data.packet_loss.packet_loss_rate,
                "packet_loss_percent": data.packet_loss.packet_loss_rate * 100.0,
                "loss_window_end_basis": data.packet_loss.loss_window_end_basis,
            },
            "warnings": data.warnings,
        }
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Process current AIR/GSP-MIN flight JSONL log.")
    parser.add_argument("log", type=Path, help="Path to flight JSONL log")
    parser.add_argument("--output-root", type=Path, default=Path("data"))
    parser.add_argument("--gif-fps", type=int, default=5, help="Target GIF fps when interpolation is needed. Default 5.")
    args = parser.parse_args()

    processor = FlightLogProcessor(output_root=args.output_root, gif_fps=args.gif_fps)

    def progress(done: int, total: int, msg: str) -> None:
        print(f"[{done}/{total}] {msg}")

    out = processor.process_file(args.log, progress=progress)
    print(f"done: {out}")


if __name__ == "__main__":
    main()
