from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from services.state_model import quat_to_euler_rpy

from .air import (
    AIR_PROFILE_COMPACT_V0,
    AirAckMessage,
    AirCapabilityMessage,
    AirCmdMessage,
    AirFlightStateMessage,
    AirFrame,
    AirPreflightStateMessage,
    AirPreflightStatusMessage,
    AirSensorStatusMessage,
    AirStatusMessage,
    accel_raw_to_mps2,
    gyro_raw_to_radps,
    parse_air_frame,
)
from .common import (
    AirAckResult,
    AirAlignmentState,
    AirCalibrationMode,
    AirCalibrationState,
    AirCmdId,
    AirCommandPolicy,
    AirLifecycleState,
    AirSensorDetailCode,
    AirSensorStatusFlag,
    AirStatusId,
    AirType,
    GspType,
    air_sensor_canonical_name,
    enum_name,
)
from .gsp_min import GsStatus, GsToPcAirFrame, GspAck, GspFrame, GspParser, parse_gsp_frame

FLIGHT_TELEMETRY_PERIOD_MS = 200


@dataclass(frozen=True)
class ProtocolEvent:
    host_rx_monotonic_ns: int
    host_processed_monotonic_ns: int
    gsp_frame: GspFrame
    parsed_gsp: Any | None
    air_frame: AirFrame | None = None
    air_message: Any | None = None
    air_error: str = ""
    rssi_dbm: int | None = None
    snr_db: float | None = None
    accel_mps2: tuple[float, float, float] | None = None
    gyro_radps: tuple[float, float, float] | None = None
    capability_context: AirCapabilityMessage | None = None
    packet_stats: dict[str, int | float | bool] | None = None


@dataclass(frozen=True)
class ReceivePipelineDiagnostics:
    gsp_frames: int
    gsp_parse_errors: int
    gsp_crc_errors: int
    gsp_resyncs: int
    parser_buffer_size: int
    air_frames: int
    air_parse_errors: int


class ReceivePipeline:
    """Pure protocol core used by the background worker and stress tests."""

    def __init__(self) -> None:
        self.gsp_parser = GspParser()
        self.gsp_parse_errors = 0
        self.air_frames = 0
        self.air_parse_errors = 0
        self.capability_context: AirCapabilityMessage | None = None
        self.unsupported_profile_id: int | None = None
        self._mission_tracking = False
        self._last_flight_time_ms: int | None = None
        self._received_flight_packets = 0
        self._estimated_lost_packets = 0

    def feed(self, data: bytes, host_rx_monotonic_ns: int | None = None) -> list[ProtocolEvent]:
        if not data:
            return []
        rx_ns = int(host_rx_monotonic_ns or time.monotonic_ns())
        events: list[ProtocolEvent] = []

        for frame in self.gsp_parser.feed(data):
            parsed_gsp = parse_gsp_frame(frame)
            if parsed_gsp is None and frame.msg_type in {
                int(GspType.GS_STATUS),
                int(GspType.AIR_RX),
                int(GspType.ACK),
            }:
                self.gsp_parse_errors += 1

            air_frame: AirFrame | None = None
            air_message: Any | None = None
            air_error = ""
            rssi_dbm: int | None = None
            snr_db: float | None = None
            accel: tuple[float, float, float] | None = None
            gyro: tuple[float, float, float] | None = None
            packet_stats: dict[str, int | float | bool] | None = None

            if isinstance(parsed_gsp, GsToPcAirFrame):
                self.air_frames += 1
                rssi_dbm = parsed_gsp.rssi_dbm
                snr_db = parsed_gsp.snr_db
                if (
                    self.unsupported_profile_id is not None
                    and parsed_gsp.air_frame
                    and parsed_gsp.air_frame[0] != int(AirType.CAPABILITY)
                ):
                    self.air_parse_errors += 1
                    air_error = (
                        "AIR profile unsupported; frame was retained raw but not interpreted: "
                        f"profile={self.unsupported_profile_id}"
                    )
                else:
                    try:
                        air_frame, air_message = parse_air_frame(parsed_gsp.air_frame)
                    except Exception as exc:
                        self.air_parse_errors += 1
                        air_error = str(exc)

            if isinstance(air_message, AirCapabilityMessage):
                if air_message.profile_supported:
                    self.capability_context = air_message
                    self.unsupported_profile_id = None
                else:
                    self.capability_context = None
                    self.unsupported_profile_id = air_message.air_profile_id

            if (
                isinstance(air_message, AirStatusMessage)
                and air_message.status_id == int(AirStatusId.MISSION_START)
                and not self._mission_tracking
            ):
                self._mission_reset(active=True)

            if isinstance(air_message, AirFlightStateMessage):
                if not self._mission_tracking:
                    self._mission_reset(active=True)
                packet_stats = self._flight_packet_track(air_message.time_ms)

            context = self.capability_context
            if isinstance(air_message, (AirPreflightStateMessage, AirFlightStateMessage)):
                if context is not None and context.air_profile_id == AIR_PROFILE_COMPACT_V0:
                    try:
                        accel = accel_raw_to_mps2(
                            air_message.accel_raw,
                            float(context.accel_full_scale_g),
                        )
                        gyro = gyro_raw_to_radps(
                            air_message.gyro_raw,
                            float(context.gyro_full_scale_dps),
                        )
                    except ValueError:
                        accel = None
                        gyro = None

            processed_ns = time.monotonic_ns()
            event = ProtocolEvent(
                host_rx_monotonic_ns=rx_ns,
                host_processed_monotonic_ns=processed_ns,
                gsp_frame=frame,
                parsed_gsp=parsed_gsp,
                air_frame=air_frame,
                air_message=air_message,
                air_error=air_error,
                rssi_dbm=rssi_dbm,
                snr_db=snr_db,
                accel_mps2=accel,
                gyro_radps=gyro,
                capability_context=context,
                packet_stats=packet_stats,
            )
            events.append(event)

        return events

    def _mission_reset(self, *, active: bool = False) -> None:
        self._mission_tracking = bool(active)
        self._last_flight_time_ms = None
        self._received_flight_packets = 0
        self._estimated_lost_packets = 0

    def _flight_packet_track(self, time_ms: int) -> dict[str, int | float | bool]:
        lost_since_previous = 0
        time_non_monotonic = False
        if self._last_flight_time_ms is None:
            self._last_flight_time_ms = int(time_ms)
        else:
            delta_ms = int(time_ms) - self._last_flight_time_ms
            if delta_ms > 0:
                expected_steps = max(1, round(delta_ms / FLIGHT_TELEMETRY_PERIOD_MS))
                lost_since_previous = max(0, expected_steps - 1)
                self._last_flight_time_ms = int(time_ms)
            else:
                time_non_monotonic = True

        self._received_flight_packets += 1
        self._estimated_lost_packets += lost_since_previous
        expected = self._received_flight_packets + self._estimated_lost_packets
        loss_rate = self._estimated_lost_packets / expected if expected else 0.0
        return {
            "lost_since_previous": lost_since_previous,
            "received_flight_packets": self._received_flight_packets,
            "estimated_lost_packets": self._estimated_lost_packets,
            "expected_flight_packets": expected,
            "packet_loss_rate": loss_rate,
            "time_non_monotonic": time_non_monotonic,
        }

    def diagnostics(self) -> ReceivePipelineDiagnostics:
        gsp = self.gsp_parser.diagnostics()
        return ReceivePipelineDiagnostics(
            gsp_frames=gsp.frames,
            gsp_parse_errors=self.gsp_parse_errors,
            gsp_crc_errors=gsp.crc_errors,
            gsp_resyncs=gsp.resyncs,
            parser_buffer_size=gsp.buffer_size,
            air_frames=self.air_frames,
            air_parse_errors=self.air_parse_errors,
        )


def _base_record(event: ProtocolEvent) -> dict[str, Any]:
    return {
        "ts": time.time(),
        "host_rx_monotonic_ns": event.host_rx_monotonic_ns,
        "host_processed_monotonic_ns": event.host_processed_monotonic_ns,
        "processing_lag_ms": (
            event.host_processed_monotonic_ns - event.host_rx_monotonic_ns
        )
        / 1_000_000.0,
        "dir": "RX",
    }


def _capability_fields(capability: AirCapabilityMessage | None) -> dict[str, Any]:
    if (
        capability is None
        or capability.accel_full_scale_g <= 0
        or capability.gyro_full_scale_dps <= 0
    ):
        return {
            "physical_conversion_valid": False,
            "air_profile_id": capability.air_profile_id if capability is not None else None,
            "accel_full_scale_g": (
                capability.accel_full_scale_g if capability is not None else None
            ),
            "gyro_full_scale_dps": (
                capability.gyro_full_scale_dps if capability is not None else None
            ),
        }
    return {
        "physical_conversion_valid": True,
        "air_profile_id": capability.air_profile_id,
        "accel_full_scale_g": capability.accel_full_scale_g,
        "gyro_full_scale_dps": capability.gyro_full_scale_dps,
    }


def protocol_event_log_records(event: ProtocolEvent) -> list[dict[str, Any]]:
    """Build complete raw and parsed records without touching Qt or disk."""
    base = _base_record(event)
    frame = event.gsp_frame
    records: list[dict[str, Any]] = [
        {
            **base,
            "layer": "GSP",
            "kind": "GSP_FRAME",
            "msg_type": int(frame.msg_type),
            "raw_hex": frame.raw.hex(),
            "payload_hex": frame.payload.hex(),
        }
    ]

    if event.parsed_gsp is None:
        if frame.msg_type in {
            int(GspType.GS_STATUS),
            int(GspType.AIR_RX),
            int(GspType.ACK),
        }:
            records.append(
                {
                    **base,
                    "layer": "GSP_PARSED",
                    "kind": "GSP_PARSE_ERROR",
                    "msg_type": int(frame.msg_type),
                }
            )
        return records

    if isinstance(event.parsed_gsp, GsStatus):
        records.append(
            {
                **base,
                "layer": "GSP_PARSED",
                "kind": "GS_STATUS",
                "gs_state": event.parsed_gsp.gs_state,
                "radio_state": event.parsed_gsp.radio_state,
                "tx_cnt": event.parsed_gsp.tx_cnt,
                "rx_cnt": event.parsed_gsp.rx_cnt,
                "crc_err_cnt": event.parsed_gsp.crc_err_cnt,
            }
        )
        return records

    if isinstance(event.parsed_gsp, GspAck):
        records.append(
            {
                **base,
                "layer": "GSP_PARSED",
                "kind": "GSP_ACK",
                "ack_gsp_type": event.parsed_gsp.ack_gsp_type,
                "result": event.parsed_gsp.result,
                "detail": event.parsed_gsp.detail,
            }
        )
        return records

    if not isinstance(event.parsed_gsp, GsToPcAirFrame):
        return records

    air_bytes = event.parsed_gsp.air_frame
    records.append(
        {
            **base,
            "layer": "AIR",
            "kind": "AIR_RAW",
            "air_hex": air_bytes.hex(),
            "air_type": int(air_bytes[0]) if air_bytes else None,
            "air_len": len(air_bytes),
            "rssi_dbm": event.rssi_dbm,
            "snr_db": event.snr_db,
        }
    )

    if event.air_error:
        records.append(
            {
                **base,
                "layer": "AIR_PARSED",
                "kind": "AIR_PARSE_ERROR",
                "error": event.air_error,
                "air_hex": air_bytes.hex(),
                "rssi_dbm": event.rssi_dbm,
                "snr_db": event.snr_db,
            }
        )
        return records

    message = event.air_message
    common = {
        **base,
        "layer": "AIR_PARSED",
        "rssi_dbm": event.rssi_dbm,
        "snr_db": event.snr_db,
    }

    if isinstance(message, AirCapabilityMessage):
        records.append(
            {
                **common,
                "kind": "CAPABILITY",
                "seq": message.seq,
                "air_profile_id": message.air_profile_id,
                "profile_supported": message.profile_supported,
                "command_policy": message.command_policy,
                "command_policy_name": enum_name(
                    AirCommandPolicy, message.command_policy
                ),
                "calibration_mode_mask": message.calibration_mode_mask,
                "sensor_summary_flags": message.sensor_summary_flags,
                "accel_full_scale_g": message.accel_full_scale_g,
                "gyro_full_scale_dps": message.gyro_full_scale_dps,
            }
        )

    elif isinstance(message, AirPreflightStatusMessage):
        records.append(
            {
                **common,
                "kind": "PREFLIGHT_STATUS",
                "seq": message.seq,
                "lifecycle_state": message.lifecycle_state,
                "lifecycle_name": enum_name(AirLifecycleState, message.lifecycle_state),
                "calibration_state": message.calibration_state,
                "calibration_state_name": enum_name(AirCalibrationState, message.calibration_state),
                "calibration_mode": message.calibration_mode,
                "calibration_mode_name": enum_name(AirCalibrationMode, message.calibration_mode),
                "completed_face_mask": message.completed_face_mask,
                "current_face": message.current_face,
                "alignment_state": message.alignment_state,
                "alignment_state_name": enum_name(AirAlignmentState, message.alignment_state),
                "system_ready": message.system_ready,
                "start_unlocked": message.start_unlocked,
                "selftest_passed": message.selftest_passed,
                "gnss_position_usable": message.gnss_position_usable,
                "capability_acked": message.capability_acked,
                "calibration_ready": message.calibration_ready,
                "alignment_ready": message.alignment_ready,
                "start_block_reason": message.start_block_reason,
                "start_block_reason_name": enum_name(AirAckResult, message.start_block_reason),
            }
        )

    elif isinstance(message, AirSensorStatusMessage):
        records.append(
            {
                **common,
                "kind": "SENSOR_STATUS",
                "seq": message.seq,
                "snapshot_id": message.snapshot_id,
                "sensor_id": message.sensor_id,
                "sensor_name": air_sensor_canonical_name(message.sensor_id),
                "instance_id": message.instance_id,
                "status_flags": message.status_flags,
                "raw_flags": message.status_flags,
                "status_flag_names": [
                    flag.name
                    for flag in AirSensorStatusFlag
                    if message.status_flags & int(flag)
                ],
                "detail_code": message.detail_code,
                "detail_name": enum_name(
                    AirSensorDetailCode,
                    message.detail_code,
                    prefix="UNKNOWN_DETAIL",
                ),
                "index": message.index,
                "total": message.total,
            }
        )

    elif isinstance(message, (AirPreflightStateMessage, AirFlightStateMessage)):
        kind = "PREFLIGHT_STATE" if isinstance(message, AirPreflightStateMessage) else "FLIGHT_STATE"
        converted = _capability_fields(event.capability_context)
        record: dict[str, Any] = {
            **common,
            "kind": kind,
            "seq": message.seq,
            "time_ms": message.time_ms,
            "accel_raw": list(message.accel_raw),
            "gyro_raw": list(message.gyro_raw),
            "quat_q15": list(message.quat_q15),
            "quat": list(message.quat),
            "quat_raw_zero": message.quat_raw_zero,
            "quat_valid": message.quat_valid,
            "euler_rpy": list(quat_to_euler_rpy(message.quat)),
            "accel_mps2": list(event.accel_mps2) if event.accel_mps2 is not None else None,
            "gyro_radps": list(event.gyro_radps) if event.gyro_radps is not None else None,
            **converted,
        }
        if isinstance(message, AirFlightStateMessage):
            record["vel_mps"] = list(message.vel_mps)
            record["pos_m"] = list(message.pos_m)
            if event.packet_stats is not None:
                record.update(event.packet_stats)
        records.append(record)

    elif isinstance(message, AirStatusMessage):
        records.append(
            {
                **common,
                "kind": "STATUS",
                "seq": message.seq,
                "status_id": message.status_id,
                "status_name": enum_name(AirStatusId, message.status_id),
                "time_ms": message.time_ms,
                "arg0": message.arg0,
                "arg1": message.arg1,
            }
        )

    elif isinstance(message, AirAckMessage):
        records.append(
            {
                **common,
                "kind": "ACK",
                "seq": message.seq,
                "ack_seq": message.ack_seq,
                "ack_cmd_id": message.ack_cmd_id,
                "ack_cmd_name": enum_name(AirCmdId, message.ack_cmd_id),
                "result": message.result,
                "result_name": enum_name(AirAckResult, message.result),
                "time_ms": message.time_ms,
            }
        )

    elif isinstance(message, AirCmdMessage):
        records.append(
            {
                **common,
                "kind": "CMD",
                "seq": message.seq,
                "cmd_id": message.cmd_id,
                "cmd_name": enum_name(AirCmdId, message.cmd_id),
                "token": message.token,
                "param0": message.param0,
                "param1": message.param1,
            }
        )

    return records


__all__ = [
    "ProtocolEvent",
    "ReceivePipeline",
    "ReceivePipelineDiagnostics",
    "protocol_event_log_records",
]
