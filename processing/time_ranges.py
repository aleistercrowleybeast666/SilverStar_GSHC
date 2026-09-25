"""Output windows are presentation choices, never mission duration limits."""
from __future__ import annotations

import math
from dataclasses import dataclass


def Duration_Validate(value: float | None) -> None:
    if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValueError("Duration must be finite and positive, or None for Full")


def Pages_Build(duration_s: float, page_s: float | None = 30.0):
    Duration_Validate(page_s)
    if not math.isfinite(duration_s) or duration_s < 0:
        raise ValueError("Invalid source duration")
    if page_s is None or duration_s == 0:
        yield (0.0, duration_s)
        return
    for index in range(math.ceil(duration_s / page_s)):
        yield (index * page_s, min(duration_s, (index + 1) * page_s))


def PageName_Build(stem: str, suffix: str, page: tuple[float, float]) -> str:
    return f"{stem}_{page[0]:010.3f}-{page[1]:010.3f}_{suffix}.png"


@dataclass(frozen=True)
class GifPlan:
    source_start_s: float
    source_end_s: float
    motion_frames: int
    fps: int = 30
    hold_frames: int = 30

    @property
    def source_duration_s(self) -> float:
        return self.source_end_s - self.source_start_s

    @property
    def speed_factor(self) -> float:
        return max(1.0, self.source_duration_s / 30.0)

    def Times_Get(self) -> list[float]:
        # End is rendered separately for the final hold. Events never change this mapping.
        return [self.source_start_s + i / self.fps * self.speed_factor
                for i in range(self.motion_frames)]

    def Metadata_Get(self) -> dict:
        return {
            "source_range_s": [self.source_start_s, self.source_end_s],
            "source_duration_s": self.source_duration_s,
            "speed_factor": self.speed_factor,
            "fps": self.fps,
            "motion_frames": self.motion_frames,
            "hold_frames": self.hold_frames,
            "motion_duration_s": self.motion_frames / self.fps,
            "hold_duration_s": 1.0,
            "event_slow_motion": False,
            "gif_timing": "centisecond-quantized 30 fps (30/40 ms)",
        }


def GifPlan_Build(duration_s: float, source_s: float | None = None) -> GifPlan:
    Duration_Validate(source_s)
    if not math.isfinite(duration_s) or duration_s < 0:
        raise ValueError("Invalid source duration")
    end = duration_s if source_s is None else min(duration_s, source_s)
    return GifPlan(0.0, end, max(1, math.ceil(min(end, 30.0) * 30)))
