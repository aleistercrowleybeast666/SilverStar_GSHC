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
from matplotlib.text import Text
import numpy as np
from PIL import Image

from services.i18n import Language
from services.preferences import Theme


@dataclass
class PlotterConfig:
    gif_fps: int = 5
    gap_threshold_s: Optional[float] = None
    pos_z_is_height: bool = True
    language: Language = Language.EN_US
    theme: Theme = Theme.LIGHT
    filename_suffix: str = "EN"


@dataclass(frozen=True)
class PlotThemeColors:
    figure: str
    axes: str
    pane: str
    text: str
    grid: str
    spine: str
    accent: str
    series: tuple[str, str, str]
    ground: str
    mesh_edge: str


LIGHT_PLOT_COLORS = PlotThemeColors(
    figure="#f4f6f8",
    axes="#ffffff",
    pane="#eef2f6",
    text="#1f2933",
    grid="#8b98a7",
    spine="#66717f",
    accent="#1769aa",
    series=("#1769aa", "#087f46", "#c62828"),
    ground="#78bce8",
    mesh_edge="#263238",
)


DARK_PLOT_COLORS = PlotThemeColors(
    figure="#181b20",
    axes="#20242b",
    pane="#252b33",
    text="#e6e9ef",
    grid="#6f7a88",
    spine="#9ca6b3",
    accent="#65b5ff",
    series=("#65b5ff", "#4bd58b", "#ff7777"),
    ground="#397da8",
    mesh_edge="#f2f5f8",
)


PLOT_TEXT: dict[str, tuple[str, str]] = {
    "acceleration": ("加速度", "Acceleration"),
    "angular_rate": ("角速度", "Angular Rate"),
    "euler_angle": ("四元数解算欧拉角", "Euler Angles from Quaternion"),
    "velocity": ("速度", "Velocity"),
    "position": ("位置", "Position"),
    "roll": ("横滚", "Roll"),
    "pitch": ("俯仰", "Pitch"),
    "yaw": ("偏航", "Yaw"),
    "east": ("东向", "East"),
    "north": ("北向", "North"),
    "up": ("天向", "Up"),
    "time_axis": ("任务时间 / s", "Mission Time / s"),
    "no_data": ("无数据", "NO DATA"),
    "parachute": ("开伞", "PARACHUTE"),
    "link_quality": ("链路质量", "Link Quality"),
    "packet_loss": ("FLIGHT_STATE 每秒丢包数", "FLIGHT_STATE Packet Loss per Second"),
    "packet_loss_axis": ("该秒丢包数", "Packets Lost in Second"),
    "attitude": ("姿态", "Attitude"),
    "trajectory": ("三维轨迹", "3D Trajectory"),
    "rising": ("上升段", "Rising"),
    "falling": ("开伞后", "After Parachute"),
    "time_value": ("时间 = {time:.2f} s", "Time = {time:.2f} s"),
    "axis_acceleration": ("加速度 / m/s²", "Acceleration / m/s²"),
    "axis_angular_rate": ("角速度 / rad/s", "Angular Rate / rad/s"),
    "axis_angle": ("角度 / rad", "Angle / rad"),
    "axis_velocity": ("速度 / m/s", "Velocity / m/s"),
    "axis_position": ("位置 / m", "Position / m"),
}


class FlightPlotter:
    def __init__(self, config: PlotterConfig | None = None) -> None:
        self.config = config or PlotterConfig()
        self.config.gif_fps = max(1, min(30, int(self.config.gif_fps)))
        self.colors = (
            DARK_PLOT_COLORS
            if self.config.theme is Theme.DARK
            else LIGHT_PLOT_COLORS
        )
        self._font = (
            self._find_cjk_font()
            if self.config.language is Language.ZH_CN
            else None
        )

    def text(self, key: str, **params: object) -> str:
        values = PLOT_TEXT.get(key)
        if values is None:
            return key
        template = values[0] if self.config.language is Language.ZH_CN else values[1]
        return template.format(**params)

    def _style_figure(self, fig) -> None:
        colors = self.colors
        fig.patch.set_facecolor(colors.figure)
        for axis in fig.axes:
            axis.set_facecolor(colors.axes)
            axis.tick_params(colors=colors.text)
            axis.xaxis.label.set_color(colors.text)
            axis.yaxis.label.set_color(colors.text)
            axis.title.set_color(colors.text)
            for spine in axis.spines.values():
                spine.set_color(colors.spine)
            if hasattr(axis, "zaxis"):
                axis.zaxis.label.set_color(colors.text)
                for coordinate_axis in (axis.xaxis, axis.yaxis, axis.zaxis):
                    coordinate_axis.pane.set_facecolor(colors.pane)
                    coordinate_axis.pane.set_edgecolor(colors.spine)
                    coordinate_axis._axinfo["grid"]["color"] = colors.grid
            legend = axis.get_legend()
            if legend is not None:
                legend.get_frame().set_facecolor(colors.axes)
                legend.get_frame().set_edgecolor(colors.spine)
                for label in legend.get_texts():
                    label.set_color(colors.text)
        for text in fig.findobj(match=Text):
            text.set_color(colors.text)
            if self._font is not None:
                text.set_fontproperties(self._font)

    def _save_figure(self, fig, output_path: Path, *, dpi: int | None = None) -> None:
        self._style_figure(fig)
        fig.tight_layout()
        fig.savefig(
            output_path,
            dpi=dpi,
            facecolor=fig.get_facecolor(),
            edgecolor="none",
        )
        plt.close(fig)

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
                ax.set_xlabel(self.text("time_axis"))
                ax.set_ylabel(ylabel)
                ax.grid(True, color=self.colors.grid, alpha=0.35)
                ax.text(
                    0.5,
                    0.5,
                    self.text("no_data"),
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                )
            self._save_figure(fig, output_path, dpi=160)
            return

        t = np.array([s.time_s for s in samples], dtype=float)
        y = np.array([s.values[:3] for s in samples], dtype=float)

        for i, (ax, label) in enumerate(zip(axes, labels)):
            tt, yy = self._insert_nan_gaps(t, y[:, i])
            ax.plot(tt, yy, linewidth=1.5, color=self.colors.series[i])
            if parachute_time_s is not None:
                ax.axvline(
                    parachute_time_s,
                    linestyle="--",
                    linewidth=1.0,
                    color="#f39c12",
                )
                ax.text(
                    parachute_time_s,
                    0.98,
                    self.text("parachute"),
                    transform=ax.get_xaxis_transform(),
                    ha="right",
                    va="top",
                )
            ax.set_title(label)
            ax.set_xlabel(self.text("time_axis"))
            ax.set_ylabel(ylabel)
            ax.grid(True, color=self.colors.grid, alpha=0.35)

        self._save_figure(fig, output_path, dpi=160)

    def plot_link_quality(self, output_path: Path, link_samples, parachute_time_s: float | None = None) -> None:
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharex=False)
        fig.suptitle(self.text("link_quality"))

        if not link_samples:
            for ax, title, ylabel in [
                (axes[0], "RSSI", "RSSI / dBm"),
                (axes[1], "SNR", "SNR / dB"),
            ]:
                ax.set_title(title)
                ax.set_xlabel(self.text("time_axis"))
                ax.set_ylabel(ylabel)
                ax.grid(True, color=self.colors.grid, alpha=0.35)
                ax.text(
                    0.5,
                    0.5,
                    self.text("no_data"),
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                )
            self._save_figure(fig, output_path, dpi=160)
            return

        t = np.array([s.time_s for s in link_samples], dtype=float)
        rssi = np.array([s.rssi_dbm for s in link_samples], dtype=float)
        snr = np.array([s.snr_db for s in link_samples], dtype=float)

        for index, (ax, values, title, ylabel) in enumerate([
            (axes[0], rssi, "RSSI", "RSSI / dBm"),
            (axes[1], snr, "SNR", "SNR / dB"),
        ]):
            tt, yy = self._insert_nan_gaps(t, values)
            ax.plot(tt, yy, linewidth=1.5, color=self.colors.series[index])
            if parachute_time_s is not None:
                ax.axvline(
                    parachute_time_s,
                    linestyle="--",
                    linewidth=1.0,
                    color="#f39c12",
                )
                ax.text(
                    parachute_time_s,
                    0.98,
                    self.text("parachute"),
                    transform=ax.get_xaxis_transform(),
                    ha="right",
                    va="top",
                )
            ax.set_title(title)
            ax.set_xlabel(self.text("time_axis"))
            ax.set_ylabel(ylabel)
            ax.grid(True, color=self.colors.grid, alpha=0.35)

        self._save_figure(fig, output_path, dpi=160)

    def plot_packet_loss_per_second(self, output_path: Path, loss_per_second: list[tuple[int, int]]) -> None:
        fig, ax = plt.subplots(figsize=(10, 4.5))
        seconds = [second for second, _lost in loss_per_second]
        lost_counts = [lost for _second, lost in loss_per_second]

        ax.bar(seconds, lost_counts, width=0.8, color=self.colors.accent)
        ax.set_title(self.text("packet_loss"))
        ax.set_xlabel(self.text("time_axis"))
        ax.set_ylabel(self.text("packet_loss_axis"))
        ax.set_ylim(0, 5)
        ax.set_yticks(range(6))
        if seconds:
            ax.set_xlim(min(seconds) - 0.5, max(seconds) + 0.5)
        ax.grid(True, axis="y", color=self.colors.grid, alpha=0.35)

        self._save_figure(fig, output_path, dpi=160)

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

            path = frames_dir / (
                f"frame_{idx:04d}_{self.config.filename_suffix}.png"
            )
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
            dst = frames_dir / (
                f"frame_{start_idx + i:04d}_{self.config.filename_suffix}.png"
            )
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
        ax_att.set_title(f"{self.text('attitude')} - {phase}")
        ax_att.text2D(
            0.50,
            0.95,
            self.text("time_value", time=t),
            transform=ax_att.transAxes,
            ha="center",
            va="top",
            fontsize=11,
        )

        self._draw_motion(ax_pos, data, t, pos, motion_limits)
        ax_pos.set_title(f"{self.text('trajectory')} - {phase}")

        self._save_figure(fig, path)

    def _phase_text(self, data, t: float) -> str:
        return self.text(
            "falling"
            if data.parachute_time_s is not None and t >= data.parachute_time_s
            else "rising"
        )

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
        coll = Poly3DCollection(
            polys,
            facecolors=face_colors,
            edgecolors=self.colors.mesh_edge,
            linewidths=0.6,
            alpha=0.95,
        )
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
        plane_coll = Poly3DCollection(
            plane,
            facecolors=self.colors.ground,
            edgecolors="none",
            alpha=0.25,
        )
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
            ax.text(cx, cy, cz, f" {self.text('parachute')}", fontsize=9)

        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_zlim(*zlim)
        ax.set_xlabel(f"{self.text('east')} / m")
        ax.set_ylabel(f"{self.text('north')} / m")
        ax.set_zlabel(f"{self.text('up')} / m")
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
