from __future__ import annotations

import unittest

from protocol.air import AirSensorStatusMessage
from protocol.common import AirAlignmentState, AirSensorDetailCode, AirSensorId
from services.state_model import AlignmentSensorSnapshotCache, FlightControllerState


def sensor_frame(
    snapshot_id: int,
    index: int,
    total: int,
    sensor_id: int,
    *,
    instance_id: int = 0,
    detail_code: int = int(AirSensorDetailCode.NONE),
) -> AirSensorStatusMessage:
    return AirSensorStatusMessage(
        seq=index,
        snapshot_id=snapshot_id,
        sensor_id=sensor_id,
        instance_id=instance_id,
        status_flags=0xFF,
        detail_code=detail_code,
        index=index,
        total=total,
    )


class AlignmentSensorSnapshotTests(unittest.TestCase):
    def test_out_of_order_duplicate_latest_wins_and_ready_completes(self) -> None:
        cache = AlignmentSensorSnapshotCache()
        cache.receive(sensor_frame(7, 2, 3, 0x35))
        cache.receive(sensor_frame(7, 0, 3, int(AirSensorId.GNSS)))
        cache.receive(sensor_frame(7, 1, 3, int(AirSensorId.IMU)))
        result = cache.receive(
            sensor_frame(
                7,
                1,
                3,
                int(AirSensorId.IMU),
                detail_code=int(AirSensorDetailCode.UNHEALTHY),
            )
        )

        self.assertTrue(result.duplicate_index)
        self.assertEqual(cache.duplicate_frames, 1)
        snapshot = cache.terminate(7, int(AirAlignmentState.READY))
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertTrue(snapshot.complete)
        self.assertFalse(snapshot.incomplete)
        self.assertEqual(
            snapshot.frames_by_index[1].detail_code,
            int(AirSensorDetailCode.UNHEALTHY),
        )
        self.assertEqual(
            [frame.sensor_id for frame in snapshot.ordered_frames()],
            [int(AirSensorId.IMU), int(AirSensorId.GNSS), 0x35],
        )

    def test_missing_frame_failed_terminal_is_incomplete_but_visible(self) -> None:
        cache = AlignmentSensorSnapshotCache()
        cache.receive(sensor_frame(8, 0, 3, int(AirSensorId.IMU)))
        cache.receive(sensor_frame(8, 2, 3, int(AirSensorId.BAROMETER)))

        snapshot = cache.terminate(8, int(AirAlignmentState.FAILED))

        self.assertIs(cache.latest_terminal_snapshot, snapshot)
        assert snapshot is not None
        self.assertFalse(snapshot.complete)
        self.assertTrue(snapshot.incomplete)
        self.assertEqual(len(snapshot.frames_by_index), 2)

    def test_stale_ff_keeps_last_terminal_snapshot(self) -> None:
        cache = AlignmentSensorSnapshotCache()
        cache.receive(sensor_frame(9, 0, 1, int(AirSensorId.IMU)))
        previous = cache.terminate(9, int(AirAlignmentState.READY))

        stale = cache.terminate(0xFF, int(AirAlignmentState.STALE))

        self.assertIsNone(stale)
        self.assertIs(cache.latest_terminal_snapshot, previous)
        self.assertNotIn(0xFF, cache.accumulators)

    def test_new_terminal_snapshot_replaces_previous(self) -> None:
        cache = AlignmentSensorSnapshotCache()
        cache.receive(sensor_frame(1, 0, 1, int(AirSensorId.IMU)))
        first = cache.terminate(1, int(AirAlignmentState.READY))
        cache.receive(sensor_frame(2, 0, 1, int(AirSensorId.GNSS)))
        second = cache.terminate(2, int(AirAlignmentState.FAILED))

        self.assertIsNot(first, second)
        self.assertIs(cache.latest_terminal_snapshot, second)
        assert second is not None
        self.assertEqual(second.snapshot_id, 2)
        self.assertEqual(
            second.terminal_alignment_state, int(AirAlignmentState.FAILED)
        )

    def test_total_mismatch_is_diagnostic_and_not_merged(self) -> None:
        cache = AlignmentSensorSnapshotCache()
        cache.receive(sensor_frame(4, 0, 2, int(AirSensorId.IMU)))
        result = cache.receive(sensor_frame(4, 1, 3, int(AirSensorId.GNSS)))

        self.assertTrue(result.total_mismatch)
        self.assertEqual(cache.total_mismatches, 1)
        self.assertNotIn(1, cache.accumulators[4].frames_by_index)

    def test_new_session_state_does_not_reuse_previous_snapshot(self) -> None:
        previous = FlightControllerState(session_generation=1)
        previous.alignment_sensor_snapshots.receive(
            sensor_frame(5, 0, 1, int(AirSensorId.IMU))
        )
        previous.alignment_sensor_snapshots.terminate(
            5, int(AirAlignmentState.READY)
        )

        current = FlightControllerState(session_generation=2)

        self.assertIsNot(
            current.alignment_sensor_snapshots,
            previous.alignment_sensor_snapshots,
        )
        self.assertIsNone(current.alignment_sensor_snapshots.latest_terminal_snapshot)
        self.assertFalse(current.alignment_sensor_snapshots.accumulators)


if __name__ == "__main__":
    unittest.main()
