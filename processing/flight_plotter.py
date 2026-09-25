from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.text import Text
import numpy as np
from PIL import GifImagePlugin, Image

from .time_ranges import GifPlan_Build

from services.i18n import Language
from services.preferences import Theme


@dataclass
class PlotterConfig:
    gif_fps: int = 30
    gap_threshold_s: Optional[float] = None
    pos_z_is_height: bool = True
    language: Language = Language.EN_US
    theme: Theme = Theme.LIGHT
    filename_suffix: str = "EN"
    gif_source_duration_s: float | None = None


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
        self.config.gif_fps = 30
        self.page_range: tuple[float, float] | None = None
        self.cancel_check = lambda: None
        self.encoding_progress = lambda: None
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
        if self.page_range is not None:
            start, end = self.page_range
            for axis in fig.axes:
                if not hasattr(axis, "zaxis"):
                    axis.set_xlim(start, end if end > start else start + 0.001)
            fig.suptitle(f"{fig._suptitle.get_text() if fig._suptitle else ''} [{start:g}–{end:g} s]")
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
        if self.page_range is not None:
            samples = [s for s in samples if self.page_range[0] <= s.time_s <= self.page_range[1]]
            if parachute_time_s is not None and not self.page_range[0] <= parachute_time_s <= self.page_range[1]:
                parachute_time_s = None
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
        if self.page_range is not None:
            link_samples = [s for s in link_samples if self.page_range[0] <= s.time_s <= self.page_range[1]]
            if parachute_time_s is not None and not self.page_range[0] <= parachute_time_s <= self.page_range[1]:
                parachute_time_s = None
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
        if self.page_range is not None:
            loss_per_second = [(t, n) for t, n in loss_per_second if self.page_range[0] <= t <= self.page_range[1]]
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

    def generate_attitude_motion_gif(
        self, data, gif_path: Path, frames_dir: Path | None = None
    ) -> Generator[Path, None, None]:
        """Render and encode one palette frame at a time; frames_dir is obsolete."""
        plan = GifPlan_Build(data.duration_s, self.config.gif_source_duration_s)
        frame_times = np.asarray(plan.Times_Get())
        self.page_range = None
        renderer = self._GifRenderer_Create(data)
        last_frame = None
        try:
            with gif_path.open("wb") as output:
                for index, t in enumerate([*frame_times, plan.source_end_s]):
                    self.cancel_check()
                    frame = self._GifFrame_Render(renderer, data, float(t))
                    try:
                        self._GifFrame_Write(output, frame, index)
                        if index == len(frame_times):
                            last_frame = frame.copy()
                    finally:
                        frame.close()
                    self.encoding_progress()
                    yield gif_path
                assert last_frame is not None
                try:
                    for index in range(len(frame_times) + 1, len(frame_times) + plan.hold_frames):
                        self.cancel_check()
                        self._GifFrame_Write(output, last_frame, index)
                        self.encoding_progress()
                        yield gif_path
                finally:
                    last_frame.close()
                output.write(b";")
        finally:
            plt.close(renderer["figure"])
        self.cancel_check()
        gif_path.with_suffix(".json").write_text(
            json.dumps(plan.Metadata_Get(), indent=2), encoding="utf-8"
        )

    def _final_hold_extra_frames(self) -> int:
        return 30

    def _build_gif_frame_times(self, data) -> tuple[np.ndarray, str]:
        plan = GifPlan_Build(data.duration_s, self.config.gif_source_duration_s)
        return np.asarray(plan.Times_Get()), "interpolate"

    def _GifRenderer_Create(self, data) -> dict:
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        figure = plt.figure(figsize=(10, 5), dpi=120)
        attitude = figure.add_subplot(1, 2, 1, projection="3d")
        trajectory = figure.add_subplot(1, 2, 2, projection="3d")
        vertices = np.array(
            [[-0.35, -0.35, 0.0], [0.35, -0.35, 0.0],
             [0.35, 0.35, 0.0], [-0.35, 0.35, 0.0], [0.0, 0.0, 2.2]],
            dtype=float,
        )
        faces = ((0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4),
                 (0, 1, 2), (0, 2, 3))
        rocket = Poly3DCollection(
            [[vertices[i] for i in face] for face in faces],
            facecolors=["#ff4040", "#33cc59", "#338cff", "#ffd633", "#777777", "#555555"],
            edgecolors=self.colors.mesh_edge,
            linewidths=0.6,
            alpha=0.95,
        )
        attitude.add_collection3d(rocket)
        attitude.set(xlim=(-2, 2), ylim=(-2, 2), zlim=(-0.5, 2.8),
                     xlabel="X", ylabel="Y", zlabel="Z")
        attitude.view_init(elev=18, azim=35)
        clock = attitude.text2D(
            0.50, 0.95, "", transform=attitude.transAxes,
            ha="center", va="top", fontsize=11,
        )
        limits = self._trajectory_limits(data)
        xlim, ylim, zlim = limits
        ground = Poly3DCollection(
            [[(xlim[0], ylim[0], 0.0), (xlim[1], ylim[0], 0.0),
              (xlim[1], ylim[1], 0.0), (xlim[0], ylim[1], 0.0)]],
            facecolors=self.colors.ground, edgecolors="none", alpha=0.25,
        )
        trajectory.add_collection3d(ground)
        before, = trajectory.plot([], [], [], linewidth=1.5, color="red")
        after, = trajectory.plot([], [], [], linewidth=1.5, color="blue")
        current, = trajectory.plot([], [], [], marker="o", markersize=5, color="red")
        chute_point = self._get_chute_point(data)
        chute = None
        chute_label = None
        if chute_point is not None:
            chute, = trajectory.plot(
                [chute_point[0]], [chute_point[1]], [chute_point[2]],
                marker="^", linestyle="", markersize=7, color="orange",
            )
            chute_label = trajectory.text(
                *chute_point, f" {self.text('parachute')}", fontsize=9,
            )
            chute.set_visible(False)
            chute_label.set_visible(False)
        trajectory.set(xlim=xlim, ylim=ylim, zlim=zlim,
                       xlabel=f"{self.text('east')} / m",
                       ylabel=f"{self.text('north')} / m",
                       zlabel=f"{self.text('up')} / m")
        trajectory.view_init(elev=20, azim=35)
        self._style_figure(figure)
        figure.tight_layout()
        times = np.asarray([sample.time_s for sample in data.pos], dtype=float)
        points = np.asarray([sample.values[:3] for sample in data.pos], dtype=float).reshape(-1, 3)
        return dict(
            figure=figure, attitude=attitude, trajectory=trajectory,
            vertices=vertices, faces=faces, rocket=rocket, clock=clock,
            before=before, after=after, current=current,
            chute=chute, chute_label=chute_label,
            times=times, points=points,
        )

    @staticmethod
    def _GifLine_Set(line, points: np.ndarray) -> None:
        if points.size:
            line.set_data(points[:, 0], points[:, 1])
            line.set_3d_properties(points[:, 2])
        else:
            line.set_data([], [])
            line.set_3d_properties([])

    def _GifFrame_Render(self, renderer: dict, data, t: float) -> Image.Image:
        quaternion = self._sample_interpolated(
            data.quat, t, (1, 0, 0, 0), is_quat=True
        )
        position = np.asarray(self._sample_interpolated(
            data.pos, t, (0, 0, 0), is_quat=False
        )[:3], dtype=float)
        vertices = renderer["vertices"] @ self._quat_to_rot(tuple(quaternion[:4])).T
        renderer["rocket"].set_verts(
            [[vertices[i] for i in face] for face in renderer["faces"]]
        )
        phase = self._phase_text(data, t)
        renderer["attitude"].set_title(f"{self.text('attitude')} - {phase}")
        renderer["trajectory"].set_title(f"{self.text('trajectory')} - {phase}")
        renderer["clock"].set_text(self.text("time_value", time=t))
        times = renderer["times"]
        points = renderer["points"]
        end = int(np.searchsorted(times, t, side="right"))
        history = points[:end]
        if end == 0 or times[end - 1] < t:
            history = np.vstack((history, position))
            history_times = np.append(times[:end], t)
        else:
            history_times = times[:end]
        deploy = data.parachute_time_s
        if deploy is None:
            before_points = history
            after_points = np.empty((0, 3))
        else:
            split = int(np.searchsorted(history_times, deploy, side="left"))
            before_points = history[:split]
            after_points = history[max(0, split - 1):] if split < len(history) else np.empty((0, 3))
            if split >= len(history):
                after_points = np.empty((0, 3))
        self._GifLine_Set(renderer["before"], before_points)
        self._GifLine_Set(renderer["after"], after_points)
        self._GifLine_Set(renderer["current"], position.reshape(1, 3))
        after_deploy = deploy is not None and t >= deploy
        renderer["current"].set_color("blue" if after_deploy else "red")
        if renderer["chute"] is not None:
            renderer["chute"].set_visible(after_deploy)
            renderer["chute_label"].set_visible(after_deploy)
        renderer["figure"].canvas.draw()
        rgba = renderer["figure"].canvas.buffer_rgba()
        width, height = renderer["figure"].canvas.get_width_height()
        return Image.frombytes("RGBA", (width, height), bytes(rgba)).convert("RGB").quantize(colors=256)

    def _GifFrame_Write(self, output, frame: Image.Image, index: int) -> None:
        if index == 0:
            blocks, _ = GifImagePlugin.getheader(frame, info={"loop": 0})
            for block in blocks:
                output.write(block)
        duration = (round((index + 1) * 100 / 30) - round(index * 100 / 30)) * 10
        for block in GifImagePlugin.getdata(
            frame, duration=duration, disposal=1, include_color_table=True
        ):
            output.write(block)

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
