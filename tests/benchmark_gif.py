"""Optional 30-second synthetic GIF benchmark; run from the repository root."""
from __future__ import annotations

import json
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np

from processing.flight_log_processor import FlightData, TimedVector
from processing.flight_plotter import FlightPlotter, PlotterConfig


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    target.mkdir(parents=True, exist_ok=True)
    data = FlightData(0, None, 15000, 30000, target / "input.jsonl")
    data.pos = [
        TimedVector(float(t), (float(t), 0.2 * float(t), float(t)), int(t * 1000))
        for t in np.arange(0, 30.01, 0.2)
    ]
    data.quat = [
        TimedVector(0.0, (1, 0, 0, 0), 0),
        TimedVector(30.0, (1, 0, 0, 0), 30000),
    ]
    plotter = FlightPlotter(PlotterConfig(gif_source_duration_s=None))
    elapsed = {"render_s": 0.0, "encode_s": 0.0}
    for name, key in (("_GifFrame_Render", "render_s"), ("_GifFrame_Write", "encode_s")):
        original = getattr(plotter, name, None)
        if original is None:
            continue

        def wrapped(*args, _original=original, _key=key, **kwargs):
            begin = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                elapsed[_key] += time.perf_counter() - begin

        setattr(plotter, name, wrapped)
    tracemalloc.start()
    begin = time.perf_counter()
    frames = sum(1 for _ in plotter.generate_attitude_motion_gif(data, target / "flight.gif", target / "frames"))
    elapsed["total_s"] = time.perf_counter() - begin
    elapsed["peak_python_bytes"] = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    pngs = list(target.rglob("*.png"))
    elapsed.update(frame_count=frames, temp_png_count=len(pngs), temp_png_bytes=sum(p.stat().st_size for p in pngs))
    print(json.dumps(elapsed, indent=2), flush=True)


if __name__ == "__main__":
    main()
