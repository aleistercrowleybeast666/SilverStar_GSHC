from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
from PIL import Image


@dataclass
class PlotterConfig:
    gif_fps: int = 5
    gap_threshold_s: Optional[float] = None
    pos_z_is_height: bool = True


class FlightPlotter:
    def __init__(self, config: PlotterConfig | None = None) -> None:
        self.config = config or PlotterConfig()
        self.config.gif_fps = max(1, min(30, int(self.config.gif_fps)))

    def estimate_gif_frame_count(self, data) -> int:
        frame_times, _mode = self._build_gif_frame_times(data)
        return max(1, len(frame_times) + self._final_hold_extra_frames())

    def plot_vector_figure(
        self,
        output_path: Path,
        title: str,
        samples,
        labels: tuple[str, str, str],
        ylabel: str,
        parachute_time_s: float | None = None,
    ) -> None:
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), sharex=False)
        fig.suptitle(title)

        if not samples:
            for ax, label in zip(axes, labels):
                ax.set_title(label)
                ax.set_xlabel("t / s")
                ax.set_ylabel(ylabel)
                ax.grid(True)
                ax.text(0.5, 0.5, "NO DATA", ha="center", va="center", transform=ax.transAxes)
            fig.tight_layout()
            fig.savefig(output_path, dpi=160)
            plt.close(fig)
            return

        t = np.array([s.time_s for s in samples], dtype=float)
        y = np.array([s.values[:3] for s in samples], dtype=float)

        for i, (ax, label) in enumerate(zip(axes, labels)):
            tt, yy = self._insert_nan_gaps(t, y[:, i])
            ax.plot(tt, yy, linewidth=1.5)
            if parachute_time_s is not None:
                ax.axvline(parachute_time_s, linestyle="--", linewidth=1.0)
                ax.text(parachute_time_s, 0.98, "CHUTE", transform=ax.get_xaxis_transform(), ha="right", va="top")
            ax.set_title(label)
            ax.set_xlabel("t / s")
            ax.set_ylabel(ylabel)
            ax.grid(True)

        fig.tight_layout()
        fig.savefig(output_path, dpi=160)
        plt.close(fig)

    def plot_link_quality(self, output_path: Path, link_samples, parachute_time_s: float | None = None) -> None:
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharex=False)
        fig.suptitle("LINK QUALITY")

        if not link_samples:
            for ax, title, ylabel in [
                (axes[0], "RSSI", "RSSI / dBm"),
                (axes[1], "SNR", "SNR / dB"),
            ]:
                ax.set_title(title)
                ax.set_xlabel("t / s")
                ax.set_ylabel(ylabel)
                ax.grid(True)
                ax.text(0.5, 0.5, "NO DATA", ha="center", va="center", transform=ax.transAxes)
            fig.tight_layout()
            fig.savefig(output_path, dpi=160)
            plt.close(fig)
            return

        t = np.array([s.time_s for s in link_samples], dtype=float)
        rssi = np.array([s.rssi_dbm for s in link_samples], dtype=float)
        snr = np.array([s.snr_db for s in link_samples], dtype=float)

        for ax, values, title, ylabel in [
            (axes[0], rssi, "RSSI", "RSSI / dBm"),
            (axes[1], snr, "SNR", "SNR / dB"),
        ]:
            tt, yy = self._insert_nan_gaps(t, values)
            ax.plot(tt, yy, linewidth=1.5)
            if parachute_time_s is not None:
                ax.axvline(parachute_time_s, linestyle="--", linewidth=1.0)
                ax.text(parachute_time_s, 0.98, "CHUTE", transform=ax.get_xaxis_transform(), ha="right", va="top")
            ax.set_title(title)
            ax.set_xlabel("t / s")
            ax.set_ylabel(ylabel)
            ax.grid(True)

        fig.tight_layout()
        fig.savefig(output_path, dpi=160)
        plt.close(fig)

    def plot_packet_loss_per_second(self, output_path: Path, loss_per_second: list[tuple[int, int]]) -> None:
        fig, ax = plt.subplots(figsize=(10, 4.5))
        seconds = [second for second, _lost in loss_per_second]
        lost_counts = [lost for _second, lost in loss_per_second]
        cjk_font = self._find_cjk_font()

        ax.bar(seconds, lost_counts, width=0.8)
        ax.set_title("FLIGHT_STATE Packet Loss per Second")
        ax.set_xlabel("任务时间 / s", fontproperties=cjk_font)
        ax.set_ylabel("该秒丢包数", fontproperties=cjk_font)
        ax.set_ylim(0, 5)
        ax.set_yticks(range(6))
        if seconds:
            ax.set_xlim(min(seconds) - 0.5, max(seconds) + 0.5)
        ax.grid(True, axis="y", alpha=0.3)

        fig.tight_layout()
        fig.savefig(output_path, dpi=160)
        plt.close(fig)

    def _find_cjk_font(self) -> font_manager.FontProperties | None:
        for family in ("Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", "SimHei", "SimSun"):
            try:
                path = font_manager.findfont(family, fallback_to_default=False)
            except ValueError:
                continue
            return font_manager.FontProperties(fname=path)
        return None

    def generate_attitude_motion_gif(self, data, gif_path: Path, frames_dir: Path) -> Generator[Path, None, None]:
        frames_dir.mkdir(parents=True, exist_ok=True)
        frame_times, mode = self._build_gif_frame_times(data)

        frame_paths: list[Path] = []
        last_quat = (1.0, 0.0, 0.0, 0.0)
        last_pos = (0.0, 0.0, 0.0)
        motion_limits = self._trajectory_limits(data)

        for idx, t in enumerate(frame_times):
            if mode == "interpolate":
                q = self._sample_interpolated(data.quat, float(t), last_quat, is_quat=True)
                p = self._sample_interpolated(data.pos, float(t), last_pos, is_quat=False)
            else:
                q = self._sample_nearest(data.quat, float(t), last_quat)
                p = self._sample_nearest(data.pos, float(t), last_pos)

            last_quat = tuple(float(v) for v in q[:4])
            last_pos = tuple(float(v) for v in p[:3])

            path = frames_dir / f"frame_{idx:04d}.png"
            self._draw_gif_frame(path, data, float(t), last_quat, last_pos, motion_limits)
            frame_paths.append(path)
            yield path

        if frame_paths:
            extra = self._append_final_hold_frames(frame_paths, frames_dir)
            for extra_path in extra:
                yield extra_path
            self._save_gif_with_real_timing(gif_path, frame_paths + extra, frame_times)

    def _final_hold_extra_frames(self) -> int:
        # Hold about 1 s at the end; at 5 fps this gives 5 extra frames,
        # so the final state is shown for about 1 additional second.
        return max(1, int(round(self.config.gif_fps * 1.0)))

    def _append_final_hold_frames(self, frame_paths: list[Path], frames_dir: Path) -> list[Path]:
        if not frame_paths:
            return []
        extra_count = self._final_hold_extra_frames()
        src = frame_paths[-1]
        extras: list[Path] = []
        start_idx = len(frame_paths)
        for i in range(extra_count):
            dst = frames_dir / f"frame_{start_idx + i:04d}.png"
            shutil.copy2(src, dst)
            extras.append(dst)
        return extras

    def _build_gif_frame_times(self, data) -> tuple[np.ndarray, str]:
        if data.quat and data.pos:
            times = sorted({round(float(s.time_s), 6) for s in data.quat} | {round(float(s.time_s), 6) for s in data.pos})
        elif data.pos:
            times = sorted({round(float(s.time_s), 6) for s in data.pos})
        elif data.quat:
            times = sorted({round(float(s.time_s), 6) for s in data.quat})
        else:
            return np.array([0.0]), "original"

        times = [t for t in times if 0.0 <= t <= data.duration_s]
        if len(times) <= 1:
            return np.array(times or [0.0]), "original"

        positive_dt = np.diff(np.array(times, dtype=float))
        positive_dt = positive_dt[positive_dt > 1e-9]
        if len(positive_dt) == 0:
            return np.array(times, dtype=float), "original"

        median_dt = float(np.median(positive_dt))
        target_dt = 1.0 / float(self.config.gif_fps)
        if median_dt > target_dt * 1.5:
            count = max(2, int(math.ceil(data.duration_s * self.config.gif_fps)) + 1)
            return np.linspace(0.0, data.duration_s, count), "interpolate"

        return np.array(times, dtype=float), "original"

    def _save_gif_with_real_timing(self, gif_path: Path, frame_paths: list[Path], frame_times: np.ndarray) -> None:
        if not frame_paths:
            return

        base_dt_ms = max(20, int(round(1000.0 / float(self.config.gif_fps))))
        durations: list[int] = []
        original_count = len(frame_times)
        for i in range(len(frame_paths)):
            if i < original_count - 1:
                dt = float(frame_times[i + 1] - frame_times[i])
                durations.append(max(20, int(round(dt * 1000.0))))
            elif i == original_count - 1:
                durations.append(base_dt_ms)
            else:
                durations.append(base_dt_ms)

        images = [Image.open(path).convert("P", palette=Image.Palette.ADAPTIVE) for path in frame_paths]
        images[0].save(
            gif_path,
            save_all=True,
            append_images=images[1:],
            duration=durations,
            loop=0,
            optimize=False,
        )
        for img in images:
            img.close()

    def _draw_gif_frame(self, path: Path, data, t: float, quat: tuple[float, float, float, float], pos: tuple[float, float, float], motion_limits) -> None:
        fig = plt.figure(figsize=(10, 5), dpi=120)

        ax_att = fig.add_subplot(1, 2, 1, projection="3d")
        ax_pos = fig.add_subplot(1, 2, 2, projection="3d")

        phase = self._phase_text(data, t)

        self._draw_rocket_attitude(ax_att, quat)
        ax_att.set_title(f"Posture - {phase}")
        ax_att.text2D(0.50, 0.95, f"t = {t:.2f} s", transform=ax_att.transAxes, ha="center", va="top", fontsize=11)

        self._draw_motion(ax_pos, data, t, pos, motion_limits)
        ax_pos.set_title(f"Trajectory - {phase}")

        fig.tight_layout()
        fig.savefig(path)
        plt.close(fig)

    def _phase_text(self, data, t: float) -> str:
        return "FALLING" if data.parachute_time_s is not None and t >= data.parachute_time_s else "RISING"

    def _draw_rocket_attitude(self, ax, quat: tuple[float, float, float, float]) -> None:
        verts = np.array(
            [
                [-0.35, -0.35, 0.0],
                [0.35, -0.35, 0.0],
                [0.35, 0.35, 0.0],
                [-0.35, 0.35, 0.0],
                [0.0, 0.0, 2.2],
            ],
            dtype=float,
        )
        faces = [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4], [0, 1, 2], [0, 2, 3]]
        face_colors = ["#ff4040", "#33cc59", "#338cff", "#ffd633", "#777777", "#555555"]

        rot = self._quat_to_rot(quat)
        rotated = verts @ rot.T

        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        polys = [[rotated[i] for i in face] for face in faces]
        coll = Poly3DCollection(polys, facecolors=face_colors, edgecolors="black", linewidths=0.6, alpha=0.95)
        ax.add_collection3d(coll)

        lim = 2.0
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_zlim(-0.5, 2.8)
        ax.set_xlabel("X")
        ax.set_ylabel("Y")
        ax.set_zlabel("Z")
        ax.view_init(elev=18, azim=35)

    def _draw_motion(self, ax, data, t: float, pos: tuple[float, float, float], motion_limits) -> None:
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        history = [(s.time_s, tuple(s.values[:3])) for s in data.pos if s.time_s <= t]
        if history:
            if history[-1][0] < t:
                history.append((t, pos))
        else:
            history = [(t, pos)]

        parachute_t = data.parachute_time_s
        before_points = [p for tt, p in history if parachute_t is None or tt < parachute_t]
        after_points = [p for tt, p in history if parachute_t is not None and tt >= parachute_t]

        xlim, ylim, zlim = motion_limits

        # Ground plane z = 0
        plane = [[
            (xlim[0], ylim[0], 0.0),
            (xlim[1], ylim[0], 0.0),
            (xlim[1], ylim[1], 0.0),
            (xlim[0], ylim[1], 0.0),
        ]]
        plane_coll = Poly3DCollection(plane, facecolors="#87cefa", edgecolors="none", alpha=0.20)
        ax.add_collection3d(plane_coll)

        if before_points:
            arr_before = np.array(before_points, dtype=float)
            ax.plot(arr_before[:, 0], arr_before[:, 1], arr_before[:, 2], linewidth=1.5, color="red")

        if after_points:
            arr_after = np.array(after_points, dtype=float)
            if before_points:
                bridge = np.array([before_points[-1], after_points[0]], dtype=float)
                ax.plot(bridge[:, 0], bridge[:, 1], bridge[:, 2], linewidth=1.2, color="blue")
            ax.plot(arr_after[:, 0], arr_after[:, 1], arr_after[:, 2], linewidth=1.5, color="blue")

        point_color = "blue" if parachute_t is not None and t >= parachute_t else "red"
        ax.scatter([pos[0]], [pos[1]], [pos[2]], s=35, color=point_color)

        chute_point = self._get_chute_point(data)
        if chute_point is not None and parachute_t is not None and t >= parachute_t:
            cx, cy, cz = chute_point
            ax.scatter([cx], [cy], [cz], s=45, color="orange", marker="^")
            ax.text(cx, cy, cz, " CHUTE", fontsize=9)

        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_zlim(*zlim)
        ax.set_xlabel("X / m")
        ax.set_ylabel("Y / m")
        ax.set_zlabel("Z / m")
        ax.view_init(elev=20, azim=35)

    def _get_chute_point(self, data):
        t = getattr(data, "parachute_time_s", None)
        if t is None or not data.pos:
            return None
        nearest = min(data.pos, key=lambda s: abs(float(s.time_s) - float(t)))
        return tuple(float(v) for v in nearest.values[:3])

    def _trajectory_limits(self, data):
        if not data.pos:
            pts = np.zeros((1, 3), dtype=float)
        else:
            pts = np.array([s.values[:3] for s in data.pos], dtype=float)
            pts = np.vstack([pts, np.array([[pts[:, 0].mean(), pts[:, 1].mean(), 0.0]])])
        return self._equal_limits(pts)

    def _equal_limits(self, pts: np.ndarray):
        mins = pts.min(axis=0)
        maxs = pts.max(axis=0)
        centers = (mins + maxs) / 2.0
        span = max(float((maxs - mins).max()), 1.0)
        half = span / 2.0 + max(0.5, span * 0.05)
        return (
            (centers[0] - half, centers[0] + half),
            (centers[1] - half, centers[1] + half),
            (centers[2] - half, centers[2] + half),
        )

    def _quat_to_rot(self, quat: tuple[float, float, float, float]) -> np.ndarray:
        w, x, y, z = quat
        norm = math.sqrt(w * w + x * x + y * y + z * z)
        if norm <= 0.0:
            return np.eye(3)
        w, x, y, z = w / norm, x / norm, y / norm, z / norm
        return np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ],
            dtype=float,
        )

    def _sample_nearest(self, samples, t: float, fallback):
        if not samples:
            return fallback
        best = min(samples, key=lambda s: abs(float(s.time_s) - t))
        return best.values

    def _sample_interpolated(self, samples, t: float, fallback, is_quat: bool):
        if not samples:
            return fallback

        if t <= samples[0].time_s:
            return samples[0].values

        threshold = self._gap_threshold_for_samples(samples)
        prev = samples[0]
        for nxt in samples[1:]:
            if t <= nxt.time_s:
                dt = nxt.time_s - prev.time_s
                if dt <= 1e-9:
                    return prev.values
                if dt > threshold:
                    return prev.values
                alpha = (t - prev.time_s) / dt
                if is_quat:
                    return self._nlerp_quat(prev.values, nxt.values, alpha)
                return tuple(float(prev.values[i]) * (1.0 - alpha) + float(nxt.values[i]) * alpha for i in range(len(prev.values)))
            prev = nxt

        return samples[-1].values

    def _gap_threshold_for_samples(self, samples) -> float:
        if self.config.gap_threshold_s is not None:
            return float(self.config.gap_threshold_s)
        if len(samples) <= 2:
            return 2.0
        t = np.array([s.time_s for s in samples], dtype=float)
        dt = np.diff(t)
        positive = dt[dt > 1e-9]
        if len(positive) == 0:
            return 2.0
        return max(2.0, float(np.median(positive)) * 5.0)

    def _nlerp_quat(self, q0, q1, alpha: float):
        a = np.array(q0[:4], dtype=float)
        b = np.array(q1[:4], dtype=float)
        if float(np.dot(a, b)) < 0.0:
            b = -b
        q = a * (1.0 - alpha) + b * alpha
        norm = float(np.linalg.norm(q))
        if norm <= 0.0:
            return tuple(q0[:4])
        q /= norm
        return tuple(float(v) for v in q)

    def _insert_nan_gaps(self, t: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if len(t) <= 1:
            return t, y

        threshold = self.config.gap_threshold_s
        if threshold is None:
            dt = np.diff(t)
            positive = dt[dt > 1e-9]
            if len(positive) == 0:
                threshold = 2.0
            else:
                threshold = max(2.0, float(np.median(positive)) * 5.0)

        out_t = [float(t[0])]
        out_y = [float(y[0])]
        for i in range(1, len(t)):
            if float(t[i] - t[i - 1]) > threshold:
                out_t.append(float("nan"))
                out_y.append(float("nan"))
            out_t.append(float(t[i]))
            out_y.append(float(y[i]))

        return np.array(out_t), np.array(out_y)
