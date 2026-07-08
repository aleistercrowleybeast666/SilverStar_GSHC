from __future__ import annotations

from dataclasses import dataclass

from .common import GspType, crc16_ccitt_false

GSP_SOF1 = 0xA5
GSP_SOF2 = 0x5A
GSP_MAX_PAYLOAD = 255


@dataclass(frozen=True)
class GspFrame:
    msg_type: int
    payload: bytes


@dataclass(frozen=True)
class GsStatus:
    gs_state: int
    radio_state: int
    tx_cnt: int
    rx_cnt: int
    crc_err_cnt: int


@dataclass(frozen=True)
class GsToPcAirFrame:
    rssi_dbm: int
    snr_db: float
    air_frame: bytes


@dataclass(frozen=True)
class GspAck:
    ack_gsp_type: int
    result: int
    detail: int


class GspParser:
    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[GspFrame]:
        self._buf.extend(data)
        frames: list[GspFrame] = []

        while True:
            if len(self._buf) < 6:
                break

            sof = self._find_sof()
            if sof < 0:
                self._buf.clear()
                break
            if sof > 0:
                del self._buf[:sof]

            if len(self._buf) < 6:
                break

            payload_len = self._buf[3]
            if payload_len > GSP_MAX_PAYLOAD:
                del self._buf[0]
                continue

            total = 6 + payload_len
            if len(self._buf) < total:
                break

            raw = bytes(self._buf[:total])
            calc = crc16_ccitt_false(raw[2:-2])
            recv = int.from_bytes(raw[-2:], "little")
            if calc != recv:
                del self._buf[0]
                continue

            frames.append(
                GspFrame(
                    msg_type=raw[2],
                    payload=raw[4:-2],
                )
            )
            del self._buf[:total]

        return frames

    def _find_sof(self) -> int:
        for i in range(max(0, len(self._buf) - 1)):
            if self._buf[i] == GSP_SOF1 and self._buf[i + 1] == GSP_SOF2:
                return i
        return -1


def parse_gsp_frame(frame: GspFrame):
    p = frame.payload

    if frame.msg_type == GspType.GS_STATUS:
        if len(p) != 12:
            return None
        return GsStatus(
            gs_state=p[0],
            radio_state=p[1],
            tx_cnt=int.from_bytes(p[2:6], "little"),
            rx_cnt=int.from_bytes(p[6:10], "little"),
            crc_err_cnt=int.from_bytes(p[10:12], "little"),
        )

    if frame.msg_type == GspType.AIR_RX:
        if len(p) < 3:
            return None
        rssi = int.from_bytes(bytes([p[0]]), "little", signed=True)
        snr_q4 = int.from_bytes(bytes([p[1]]), "little", signed=True)
        air_len = p[2]
        if len(p) != 3 + air_len:
            return None
        return GsToPcAirFrame(
            rssi_dbm=rssi,
            snr_db=snr_q4 / 4.0,
            air_frame=bytes(p[3:3 + air_len]),
        )

    if frame.msg_type == GspType.ACK:
        if len(p) != 3:
            return None
        return GspAck(
            ack_gsp_type=p[0],
            result=p[1],
            detail=p[2],
        )

    return None


def build_gsp_frame(msg_type: int, payload: bytes) -> bytes:
    if len(payload) > GSP_MAX_PAYLOAD:
        raise ValueError("GSP payload too long")
    head = bytes([msg_type & 0xFF, len(payload) & 0xFF])
    crc = crc16_ccitt_false(head + payload)
    return bytes([GSP_SOF1, GSP_SOF2]) + head + payload + crc.to_bytes(2, "little")


def build_pc_to_gs_air_frame(air_frame: bytes) -> bytes:
    if len(air_frame) > 255:
        raise ValueError("AIR frame too long")
    payload = bytes([len(air_frame)]) + air_frame
    return build_gsp_frame(int(GspType.AIR_TX), payload)
