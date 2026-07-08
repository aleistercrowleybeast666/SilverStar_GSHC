from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from .flight_plotter import FlightPlotter, PlotterConfig

# Current AIR / GSP-MIN definitions only.
AIR_TYPE_FLIGHT_STATE = 0x10
AIR_TYPE_STATUS = 0x20
AIR_FLIGHT_STATE_LEN = 50
AIR_STATUS_LEN = 9
GSP_TYPE_AIR_RX = 0x02

STATUS_BOOT = 0x01
STATUS_SELFTEST_OK = 0x02
STATUS_MISSION_START = 0x03
STATUS_LAUNCH = 0x04
STATUS_PARACHUTE_DEPLOY = 0x05
STATUS_LANDING = 0x06
STATUS_LOCKED = 0x07
STATUS_UNLOCKED = 0x08

ACCEL_FULL_SCALE_G_DEFAULT = 16.0
GYRO_FULL_SCALE_DPS_DEFAULT = 2000.0
STANDARD_GRAVITY_MPS2 = 9.80665

STATUS_NAME = {
    STATUS_BOOT: "BOOT",
    STATUS_SELFTEST_OK: "SELFTEST_OK",
    STATUS_MISSION_START: "MISSION_START",
    STATUS_LAUNCH: "LAUNCH",
    STATUS_PARACHUTE_DEPLOY: "PARACHUTE_DEPLOY",
    STATUS_LANDING: "LANDING",
    STATUS_LOCKED: "LOCKED",
    STATUS_UNLOCKED: "UNLOCKED",
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
class FlightData:
    mission_start_ms: int
    landing_ms: Optional[int]
    parachute_ms: Optional[int]
    end_ms: int
    source_log: Path
    log_kind: str = "REAL_FLIGHT"
    simulated: bool = False
    simulation_label: str = ""
    status_events: list[StatusEvent] = field(default_factory=list)
    accel: list[TimedVector] = field(default_factory=list)
    gyro: list[TimedVector] = field(default_factory=list)
    quat: list[TimedVector] = field(default_factory=list)
    euler: list[TimedVector] = field(default_factory=list)
    vel: list[TimedVector] = field(default_factory=list)
    pos: list[TimedVector] = field(default_factory=list)
    link: list[LinkSample] = field(default_factory=list)
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


def int16(value: int) -> int:
    value &= 0xFFFF
    return value - 65536 if value >= 32768 else value


def q15_to_float(value: int) -> float:
    return max(-1.0, min(1.0, float(value) / 32767.0))


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


def raw_accel_to_mps2(raw: Iterable[object], full_scale_g: float = ACCEL_FULL_SCALE_G_DEFAULT) -> tuple[float, float, float]:
    vals = tuple(int(v) for v in raw)
    scale = full_scale_g * STANDARD_GRAVITY_MPS2 / 32768.0
    return vals[0] * scale, vals[1] * scale, vals[2] * scale


def raw_gyro_to_radps(raw: Iterable[object], full_scale_dps: float = GYRO_FULL_SCALE_DPS_DEFAULT) -> tuple[float, float, float]:
    vals = tuple(int(v) for v in raw)
    scale = full_scale_dps * math.pi / 180.0 / 32768.0
    return vals[0] * scale, vals[1] * scale, vals[2] * scale


def parse_air_frame(frame: bytes) -> tuple[str, dict] | None:
    """
    Parse current AIR payload only.

    AIR_FLIGHT_STATE length = 50 bytes
    AIR_STATUS length       = 9 bytes
    """
    if len(frame) < 2:
        return None

    air_type = frame[0]
    seq = frame[1]

    if air_type == AIR_TYPE_FLIGHT_STATE:
        if len(frame) != AIR_FLIGHT_STATE_LEN:
            return None

        time_ms = int.from_bytes(frame[2:6], "little")
        accel_raw = tuple(int16(v) for v in struct.unpack("<HHH", frame[6:12]))
        gyro_raw = tuple(int16(v) for v in struct.unpack("<HHH", frame[12:18]))
        quat_q15 = tuple(int16(v) for v in struct.unpack("<HHHH", frame[18:26]))
        vel = struct.unpack("<fff", frame[26:38])
        pos = struct.unpack("<fff", frame[38:50])
        quat = normalize_quat(tuple(q15_to_float(v) for v in quat_q15))  # type: ignore[arg-type]

        return "FLIGHT_STATE", {
            "seq": seq,
            "time_ms": time_ms,
            "accel_raw": accel_raw,
            "gyro_raw": gyro_raw,
            "quat_q15": quat_q15,
            "quat": quat,
            "vel_mps": tuple(float(v) for v in vel),
            "pos_m": tuple(float(v) for v in pos),
        }

    if air_type == AIR_TYPE_STATUS:
        if len(frame) != AIR_STATUS_LEN:
            return None

        return "STATUS", {
            "seq": seq,
            "status_id": frame[2],
            "time_ms": int.from_bytes(frame[3:7], "little"),
            "arg0": frame[7],
            "arg1": frame[8],
        }

    return None


class FlightLogProcessor:
    """
    Offline processor for the current ground-station JSONL log.

    Supported records:
    1. Parsed AIR records:
       {"dir":"RX","layer":"AIR_PARSED","kind":"FLIGHT_STATE"/"STATUS", ...}

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
        total_steps = 2 + 6 + frame_count + 1
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
            if r.get("layer") == "AIR_PARSED" and r.get("kind") in {"FLIGHT_STATE", "STATUS"}:
                return True
            if r.get("layer") == "GSP" and self._is_gsp_air_rx(r):
                return True
        return False

    def _extract_flight_data(self, records: list[dict], log_path: Path) -> FlightData:
        log_kind, simulated, simulation_label = self._detect_log_kind(records)
        if not self._has_any_air_source_record(records):
            raise ValueError("没有找到有效的 AIR_PARSED 或 GSP AIR_RX 飞行数据记录。")
        has_air_parsed = any(r.get("dir") == "RX" and r.get("layer") == "AIR_PARSED" for r in records)

        all_data: dict[str, list[tuple[int, tuple[float, ...]]]] = {
            "accel": [],
            "gyro": [],
            "quat": [],
            "vel": [],
            "pos": [],
        }
        all_status: list[tuple[int, int, int, int]] = []
        all_link: list[tuple[int, float, float]] = []

        for r in records:
            if r.get("dir") != "RX":
                continue

            layer = r.get("layer")

            if layer == "AIR_PARSED":
                self._add_air_parsed_record(r, all_data, all_status)

            elif layer == "GSP" and self._is_gsp_air_rx(r):
                link = self._extract_link_from_gsp(r)
                if link is not None:
                    all_link.append(link)

                # Avoid double counting if app.py already logged AIR_PARSED records.
                if not has_air_parsed:
                    parsed = self._extract_air_from_gsp(r)
                    if parsed is not None:
                        kind, info = parsed
                        if kind == "FLIGHT_STATE":
                            self._add_flight_state(info, all_data)
                        elif kind == "STATUS":
                            self._add_status(info, all_status)

        start_ms = self._first_status_time(all_status, STATUS_MISSION_START)
        if start_ms is None:
            raise ValueError("没有找到 MISSION_START（任务开始）事件，无法确定任务起点。")

        if self._max_data_time(all_data) is None:
            raise ValueError("没有找到有效的 FLIGHT_STATE 遥测数据。")

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
        )

        if landing_ms is None:
            data.warnings.append("LANDING not found; using last data timestamp as end.")
        if parachute_ms is None:
            data.warnings.append("PARACHUTE_DEPLOY not found; no chute marker.")

        data.status_events = [
            StatusEvent((time_ms - start_ms) / 1000.0, sid, STATUS_NAME.get(sid, f"0x{sid:02X}"), time_ms, arg0, arg1)
            for (sid, time_ms, arg0, arg1) in all_status
            if start_ms <= time_ms <= end_ms
        ]

        data.accel = self._convert_samples(all_data["accel"], start_ms, end_ms)
        data.gyro = self._convert_samples(all_data["gyro"], start_ms, end_ms)
        data.quat = self._convert_samples(all_data["quat"], start_ms, end_ms)
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
    ) -> None:
        kind = r.get("kind")
        if kind == "FLIGHT_STATE":
            self._add_flight_state(r, all_data)
        elif kind == "STATUS":
            self._add_status(r, all_status)

    def _add_flight_state(
        self,
        r: dict,
        all_data: dict[str, list[tuple[int, tuple[float, ...]]]],
    ) -> None:
        try:
            time_ms = int(r["time_ms"])
        except (KeyError, TypeError, ValueError):
            return

        accel = safe_float_tuple(r.get("accel_mps2", ()), 3)
        if accel is None and "accel_raw" in r:
            accel_fs = float(r.get("accel_full_scale_g", ACCEL_FULL_SCALE_G_DEFAULT))
            accel = raw_accel_to_mps2(r["accel_raw"], accel_fs)

        gyro = safe_float_tuple(r.get("gyro_radps", ()), 3)
        if gyro is None and "gyro_raw" in r:
            gyro_fs = float(r.get("gyro_full_scale_dps", GYRO_FULL_SCALE_DPS_DEFAULT))
            gyro = raw_gyro_to_radps(r["gyro_raw"], gyro_fs)

        quat = safe_float_tuple(r.get("quat", ()), 4)
        if quat is None and "quat_q15" in r:
            q_raw = safe_int_tuple(r["quat_q15"], 4)
            if q_raw is not None:
                quat = normalize_quat(tuple(q15_to_float(v) for v in q_raw))  # type: ignore[arg-type]

        vel = safe_float_tuple(r.get("vel_mps", r.get("vel", ())), 3)
        pos = safe_float_tuple(r.get("pos_m", r.get("pos", ())), 3)

        if accel is not None:
            all_data["accel"].append((time_ms, accel))
        if gyro is not None:
            all_data["gyro"].append((time_ms, gyro))
        if quat is not None:
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

    def _extract_link_from_gsp(self, r: dict) -> tuple[int, float, float] | None:
        payload = self._gsp_payload_bytes(r)
        if payload is None or len(payload) < 3:
            return None

        rssi = float(int8(payload[0]))
        snr = float(int8(payload[1])) / 4.0
        air_len = payload[2]
        air_frame = payload[3:3 + air_len]

        parsed = parse_air_frame(air_frame) if air_len > 0 else None
        if parsed is not None:
            _kind, info = parsed
            time_ms = int(info["time_ms"])
        elif "time_ms" in r:
            try:
                time_ms = int(r["time_ms"])
            except (TypeError, ValueError):
                return None
        else:
            return None

        return time_ms, rssi, snr

    def _extract_air_from_gsp(self, r: dict) -> tuple[str, dict] | None:
        payload = self._gsp_payload_bytes(r)
        if payload is None or len(payload) < 3:
            return None
        air_len = payload[2]
        air_frame = payload[3:3 + air_len]
        return parse_air_frame(air_frame)

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
            f.write(f"parachute_time_s: {data.parachute_time_s if data.parachute_time_s is not None else 'not_found'}\n")
            for w in data.warnings:
                f.write(f"warning: {w}\n")

            f.write("\n[status_events]\n")
            f.write("time_s\tstatus\targ0\targ1\n")
            for ev in data.status_events:
                f.write(f"{ev.time_s:.6f}\t{ev.name}\t{ev.arg0}\t{ev.arg1}\n")

            write_vec(f, "accel_mps2", data.accel, ("ax", "ay", "az"))
            write_vec(f, "gyro_radps", data.gyro, ("gx", "gy", "gz"))
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
            "protocol": "AIR_GSP_MIN_CURRENT_ONLY",
            "data_kind": data.log_kind,
            "simulated": data.simulated,
            "simulation_label": data.simulation_label,
            "mission_start_ms": data.mission_start_ms,
            "landing_ms": data.landing_ms,
            "parachute_ms": data.parachute_ms,
            "duration_s": data.duration_s,
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
