from __future__ import annotations

import math
from collections import deque
from typing import Callable

import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from config import APP_NAME, PLOT_HISTORY, ACCEL_FULL_SCALE_G, GYRO_FULL_SCALE_DPS


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1760, 960)

        self.max_points = PLOT_HISTORY
        self.series = {
            "vel": [deque(maxlen=self.max_points) for _ in range(3)],
            "pos": [deque(maxlen=self.max_points) for _ in range(3)],
        }

        self.latest_quat = (1.0, 0.0, 0.0, 0.0)
        self.latest_euler = (0.0, 0.0, 0.0)

        self.on_refresh_ports: Callable[[], None] | None = None
        self.on_connect_clicked: Callable[[], None] | None = None
        self.on_disconnect_clicked: Callable[[], None] | None = None
        self.on_send_ping: Callable[[], None] | None = None
        self.on_send_lock: Callable[[], None] | None = None
        self.on_send_unlock: Callable[[], None] | None = None
        self.on_send_start: Callable[[], None] | None = None
        self.on_generate_sim_data: Callable[[], None] | None = None
        self.on_process_data: Callable[[], None] | None = None
        self.on_open_log_dir: Callable[[], None] | None = None
        self.on_open_data_dir: Callable[[], None] | None = None

        self._data_tools_busy = False
        self._data_tools_mission_disabled = False
        self._command_state_mission_started = False
        self._dynamic_value_font = QFont("Consolas")
        self._dynamic_value_font.setStyleHint(QFont.StyleHint.Monospace)
        self._dynamic_value_font.setFixedPitch(True)
        self._compact_label_font = QFont()
        self._compact_label_font.setPointSize(9)
        self._compact_value_font = QFont("Consolas")
        self._compact_value_font.setStyleHint(QFont.StyleHint.Monospace)
        self._compact_value_font.setFixedPitch(True)
        self._compact_value_font.setPointSize(9)

        self._build_ui()
        self._build_3d_scene()
        self._build_plots()
        self.reset_runtime_display()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addWidget(self._build_top_bar())

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.gl_view = gl.GLViewWidget()
        self.gl_view.setMinimumSize(360, 360)
        self.gl_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout.addWidget(self.gl_view, 1)

        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        top_info = QWidget()
        top_info_layout = QHBoxLayout(top_info)
        top_info_layout.setContentsMargins(0, 0, 0, 0)
        top_info_layout.setSpacing(8)

        top_info_layout.addWidget(self._build_status_panel(), 1)
        top_info_layout.addWidget(self._build_data_area_panel(), 2)

        right_layout.addWidget(top_info, 0)
        right_layout.addWidget(self._build_plot_panel(), 1)

        splitter.addWidget(right)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([420, 1340])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    def _build_top_bar(self) -> QWidget:
        box = QGroupBox("连接与命令")
        layout = QHBoxLayout(box)

        self.port_combo = QComboBox()
        self.port_combo.setMinimumContentsLength(12)
        self.port_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)

        self.btn_refresh = QPushButton("刷新串口")
        self.btn_connect = QPushButton("连接")
        self.btn_disconnect = QPushButton("断开")

        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(9600, 2000000)
        self.baud_spin.setValue(230400)
        self.baud_spin.setFixedWidth(100)

        self.conn_label = QLabel("未连接")
        self._configure_dynamic_label(self.conn_label, 180, show_tooltip=True)

        self.accel_fs_combo = QComboBox()
        self.accel_fs_combo.addItems(["±2g", "±4g", "±8g", "±16g"])
        self.accel_fs_combo.setCurrentText(f"±{int(ACCEL_FULL_SCALE_G)}g")
        self.accel_fs_combo.setFixedWidth(84)

        self.gyro_fs_combo = QComboBox()
        self.gyro_fs_combo.addItems(["±250dps", "±500dps", "±1000dps", "±2000dps"])
        self.gyro_fs_combo.setCurrentText(f"±{int(GYRO_FULL_SCALE_DPS)}dps")
        self.gyro_fs_combo.setFixedWidth(112)

        self.btn_ping = QPushButton("PING")
        self.btn_lock = QPushButton("LOCK")
        self.btn_unlock = QPushButton("UNLOCK")
        self.btn_start = QPushButton("START")

        self._cmd_button_disabled_style = (
            "QPushButton { padding: 4px 8px; }"
            "QPushButton:disabled {"
            "background-color: #2b2b2b;"
            "color: #8a8a8a;"
            "border: 1px solid #555555;"
            "}"
        )
        for button in (self.btn_ping, self.btn_lock, self.btn_unlock, self.btn_start):
            button.setStyleSheet(self._cmd_button_disabled_style)
            button.setFixedWidth(92)
            button.setMinimumHeight(30)

        layout.addWidget(QLabel("端口"))
        layout.addWidget(self.port_combo)
        layout.addWidget(self.btn_refresh)
        layout.addWidget(QLabel("波特率"))
        layout.addWidget(self.baud_spin)
        layout.addWidget(self.btn_connect)
        layout.addWidget(self.btn_disconnect)
        layout.addWidget(self.conn_label)

        layout.addSpacing(12)
        layout.addWidget(QLabel("加速度量程"))
        layout.addWidget(self.accel_fs_combo)
        layout.addWidget(QLabel("角速度量程"))
        layout.addWidget(self.gyro_fs_combo)

        layout.addSpacing(20)

        for button in (self.btn_ping, self.btn_lock, self.btn_unlock, self.btn_start):
            layout.addWidget(button)
            layout.addSpacing(8)

        layout.addStretch(1)

        self.btn_refresh.clicked.connect(lambda: self.on_refresh_ports and self.on_refresh_ports())
        self.btn_connect.clicked.connect(lambda: self.on_connect_clicked and self.on_connect_clicked())
        self.btn_disconnect.clicked.connect(lambda: self.on_disconnect_clicked and self.on_disconnect_clicked())
        self.btn_ping.clicked.connect(lambda: self.on_send_ping and self.on_send_ping())
        self.btn_lock.clicked.connect(lambda: self.on_send_lock and self.on_send_lock())
        self.btn_unlock.clicked.connect(lambda: self.on_send_unlock and self.on_send_unlock())
        self.btn_start.clicked.connect(lambda: self.on_send_start and self.on_send_start())

        return box

    def _configure_dynamic_label(
        self,
        label: QLabel,
        min_width: int,
        *,
        monospace: bool = True,
        show_tooltip: bool = False,
        compact: bool = False,
    ) -> None:
        label.setMinimumWidth(min_width)
        label.setWordWrap(False)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("showFullTextToolTip", show_tooltip)
        if monospace:
            label.setFont(self._compact_value_font if compact else self._dynamic_value_font)

    def _set_dynamic_label_text(self, label: QLabel, text: str) -> None:
        label.setText(text)
        if label.property("showFullTextToolTip"):
            label.setToolTip("" if text in ("", "—") else text)

    def _add_value_pair(
        self,
        grid: QGridLayout,
        row: int,
        col_group: int,
        name: str,
        value_label: QLabel,
        value_min_width: int = 96,
        value_tooltip: bool = False,
        compact: bool = False,
    ) -> None:
        base_col = col_group * 2

        name_label = QLabel(name)
        name_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if compact:
            name_label.setFont(self._compact_label_font)

        self._configure_dynamic_label(
            value_label,
            value_min_width,
            show_tooltip=value_tooltip,
            compact=compact,
        )

        grid.addWidget(name_label, row, base_col)
        grid.addWidget(value_label, row, base_col + 1)

    def _build_status_panel(self) -> QWidget:
        box = QGroupBox("状态与链路")
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 10, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)

        self.lbl_gs_state = QLabel("—")
        self.lbl_radio_state = QLabel("—")
        self.lbl_tx_cnt = QLabel("0")
        self.lbl_rx_cnt = QLabel("0")
        self.lbl_crc_err = QLabel("0")

        self.lbl_rssi = QLabel("— dBm")
        self.lbl_snr = QLabel("— dB")
        self.lbl_last_status = QLabel("—")
        self.lbl_last_gs_ack = QLabel("—")
        self.lbl_last_air_ack = QLabel("—")

        self._add_value_pair(grid, 0, 0, "地面站状态", self.lbl_gs_state, 260, True, True)
        self._add_value_pair(grid, 1, 0, "无线状态", self.lbl_radio_state, 260, True, True)
        self._add_value_pair(grid, 2, 0, "TX计数", self.lbl_tx_cnt, 260, True, True)
        self._add_value_pair(grid, 3, 0, "RX计数", self.lbl_rx_cnt, 260, True, True)
        self._add_value_pair(grid, 4, 0, "CRC错误", self.lbl_crc_err, 260, True, True)
        self._add_value_pair(grid, 5, 0, "信号强度", self.lbl_rssi, 260, True, True)
        self._add_value_pair(grid, 6, 0, "信噪比", self.lbl_snr, 260, True, True)
        self._add_value_pair(grid, 7, 0, "最近状态事件", self.lbl_last_status, 260, True, True)
        self._add_value_pair(grid, 8, 0, "最近地面站ACK", self.lbl_last_gs_ack, 260, True, True)
        self._add_value_pair(grid, 9, 0, "最近天空端ACK", self.lbl_last_air_ack, 260, True, True)

        grid.setColumnStretch(1, 1)

        return box

    def _build_data_area_panel(self) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(self._build_data_panel(), 1)
        layout.addWidget(self._build_data_tool_panel(), 0)

        return wrapper

    def _build_data_tool_panel(self) -> QWidget:
        box = QGroupBox("数据处理")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(8)

        self.btn_generate_sim = QPushButton("生成模拟\n验证数据")
        self.btn_process_data = QPushButton("处理数据")
        self.btn_open_log_dir = QPushButton("打开 logs")
        self.btn_open_data_dir = QPushButton("打开 data")

        for button in (
            self.btn_generate_sim,
            self.btn_process_data,
            self.btn_open_log_dir,
            self.btn_open_data_dir,
        ):
            button.setMinimumWidth(130)
            button.setMinimumHeight(36)
            button.setStyleSheet(
                "QPushButton { padding: 6px 8px; }"
                "QPushButton:disabled {"
                "background-color: #2b2b2b;"
                "color: #8a8a8a;"
                "border: 1px solid #555555;"
                "}"
            )

        self.btn_generate_sim.setMinimumHeight(44)
        self.btn_process_data.setMinimumHeight(44)

        layout.addWidget(self.btn_generate_sim)
        layout.addWidget(self.btn_process_data)
        layout.addSpacing(8)
        layout.addWidget(self.btn_open_log_dir)
        layout.addWidget(self.btn_open_data_dir)
        layout.addStretch(1)

        self.btn_generate_sim.clicked.connect(lambda: self.on_generate_sim_data and self.on_generate_sim_data())
        self.btn_process_data.clicked.connect(lambda: self.on_process_data and self.on_process_data())
        self.btn_open_log_dir.clicked.connect(lambda: self.on_open_log_dir and self.on_open_log_dir())
        self.btn_open_data_dir.clicked.connect(lambda: self.on_open_data_dir and self.on_open_data_dir())

        return box

    def _build_data_panel(self) -> QWidget:
        box = QGroupBox("实时数据")
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 10, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)

        self.lbl_accel = QLabel("X: —  Y: —  Z: —")
        self.lbl_gyro = QLabel("X: —  Y: —  Z: —")
        self.lbl_quat_raw = QLabel("W: —  X: —  Y: —  Z: —")
        self.lbl_quat = QLabel("W: —  X: —  Y: —  Z: —  valid=—")
        self.lbl_euler = QLabel("R: —  P: —  Y: —")

        self.lbl_vel = QLabel("X: —  Y: —  Z: —")
        self.lbl_pos = QLabel("X: —  Y: —  Z: —")

        self._add_value_pair(grid, 0, 0, "加速度 (m/s²)", self.lbl_accel, 360, False, True)
        self._add_value_pair(grid, 1, 0, "角速度 (rad/s)", self.lbl_gyro, 360, False, True)
        self._add_value_pair(grid, 2, 0, "Q raw", self.lbl_quat_raw, 460, True, True)
        self._add_value_pair(grid, 3, 0, "Q norm", self.lbl_quat, 460, True, True)
        self._add_value_pair(grid, 4, 0, "欧拉角 (rad)", self.lbl_euler, 460, True, True)
        self._add_value_pair(grid, 5, 0, "速度 (m/s)", self.lbl_vel, 360, False, True)
        self._add_value_pair(grid, 6, 0, "位置 (m)", self.lbl_pos, 360, False, True)

        grid.setColumnStretch(1, 1)

        return box

    def _build_plot_panel(self) -> QWidget:
        box = QGroupBox("实时曲线（速度 / 位置）")

        self.plot_layout = QGridLayout(box)
        self.plot_layout.setHorizontalSpacing(8)
        self.plot_layout.setVerticalSpacing(8)

        return box

    def _build_3d_scene(self) -> None:
        self.gl_view.setCameraPosition(distance=11, elevation=18, azimuth=35)

        grid = gl.GLGridItem()
        grid.scale(1, 1, 1)
        self.gl_view.addItem(grid)

        axes = gl.GLAxisItem()
        axes.setSize(2, 2, 2)
        self.gl_view.addItem(axes)

        verts = np.array(
            [
                [-0.35, -0.35, 0.0],
                [0.35, -0.35, 0.0],
                [0.35, 0.35, 0.0],
                [-0.35, 0.35, 0.0],
                [0.00, 0.00, 2.2],
            ],
            dtype=float,
        )

        faces = np.array(
            [
                [0, 1, 4],
                [1, 2, 4],
                [2, 3, 4],
                [3, 0, 4],
                [0, 1, 2],
                [0, 2, 3],
            ],
            dtype=np.uint32,
        )

        colors = np.array(
            [
                [1.00, 0.25, 0.25, 1.00],
                [0.20, 0.80, 0.35, 1.00],
                [0.20, 0.55, 1.00, 1.00],
                [1.00, 0.85, 0.20, 1.00],
                [0.45, 0.45, 0.45, 1.00],
                [0.35, 0.35, 0.35, 1.00],
            ],
            dtype=float,
        )

        self.base_vertices = verts.copy()
        self.base_faces = faces.copy()
        self.base_colors = colors.copy()

        mesh_data = gl.MeshData(vertexes=verts, faces=faces, faceColors=colors)

        self.mesh_item = gl.GLMeshItem(
            meshdata=mesh_data,
            smooth=False,
            drawEdges=True,
            edgeColor=(1, 1, 1, 1),
            shader="shaded",
        )

        self.gl_view.addItem(self.mesh_item)

    def _build_plots(self) -> None:
        pg.setConfigOptions(antialias=True)

        titles = [
            ("速度 X (m/s)", "vel", 0),
            ("速度 Y (m/s)", "vel", 1),
            ("速度 Z (m/s)", "vel", 2),
            ("位置 X (m)", "pos", 0),
            ("位置 Y (m)", "pos", 1),
            ("位置 Z (m)", "pos", 2),
        ]

        self.curves = {}

        for idx, (title, key, axis) in enumerate(titles):
            row, col = divmod(idx, 3)

            plot_widget = pg.PlotWidget(title=title)
            plot_widget.setMinimumSize(240, 190)
            plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            plot_widget.showGrid(x=True, y=True, alpha=0.3)
            plot_widget.setLabel("bottom", "sample")
            plot_widget.setXRange(0, max(self.max_points - 1, 1), padding=0)
            plot_widget.disableAutoRange(axis=pg.ViewBox.XAxis)
            plot_widget.getAxis("left").setWidth(72)
            plot_widget.getAxis("bottom").setHeight(32)

            curve = plot_widget.plot(pen=pg.mkPen(width=2))

            self.plot_layout.addWidget(plot_widget, row, col)
            self.curves[(key, axis)] = curve

    def set_ports(self, ports: list[str]) -> None:
        current = self.port_combo.currentText()

        self.port_combo.clear()
        self.port_combo.addItems(ports)

        if current:
            idx = self.port_combo.findText(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)

    def current_port(self) -> str:
        return self.port_combo.currentText().strip()

    def current_baudrate(self) -> int:
        return int(self.baud_spin.value())

    def current_accel_full_scale_g(self) -> float:
        text = self.accel_fs_combo.currentText().strip().replace("±", "").replace("g", "")
        try:
            return float(text)
        except ValueError:
            return float(ACCEL_FULL_SCALE_G)

    def current_gyro_full_scale_dps(self) -> float:
        text = self.gyro_fs_combo.currentText().strip().replace("±", "").replace("dps", "")
        try:
            return float(text)
        except ValueError:
            return float(GYRO_FULL_SCALE_DPS)

    def set_connection_status(self, text: str) -> None:
        self._set_dynamic_label_text(self.conn_label, text)

    def set_radio_state_hint(self, text: str) -> None:
        self._set_dynamic_label_text(self.lbl_radio_state, text)

    def set_command_buttons_enabled(
        self,
        *,
        ping: bool,
        lock: bool,
        unlock: bool,
        start: bool,
    ) -> None:
        self.btn_ping.setEnabled(ping)
        self.btn_lock.setEnabled(lock)
        self.btn_unlock.setEnabled(unlock)
        self.btn_start.setEnabled(start)

    def _refresh_data_tool_buttons(self) -> None:
        if not hasattr(self, "btn_generate_sim") or not hasattr(self, "btn_process_data"):
            return

        process_enabled = (not self._data_tools_busy) and (not self._data_tools_mission_disabled)
        folder_enabled = not self._data_tools_mission_disabled

        self.btn_generate_sim.setEnabled(process_enabled)
        self.btn_process_data.setEnabled(process_enabled)

        if hasattr(self, "btn_open_log_dir"):
            self.btn_open_log_dir.setEnabled(folder_enabled)
        if hasattr(self, "btn_open_data_dir"):
            self.btn_open_data_dir.setEnabled(folder_enabled)

    def set_data_tools_busy(self, busy: bool) -> None:
        self._data_tools_busy = bool(busy)
        self._refresh_data_tool_buttons()

    def set_data_tools_mission_disabled(self, disabled: bool) -> None:
        self._data_tools_mission_disabled = bool(disabled)
        self._refresh_data_tool_buttons()

    def set_command_state_initial(self) -> None:
        """Initial UI state: air side is assumed LOCKED."""
        self._command_state_mission_started = False
        self.set_data_tools_mission_disabled(False)
        self.set_command_buttons_enabled(
            ping=True,
            lock=False,
            unlock=True,
            start=False,
        )

    def set_command_state_locked(self) -> None:
        if self._command_state_mission_started:
            return

        self.set_data_tools_mission_disabled(False)
        self.set_command_buttons_enabled(
            ping=True,
            lock=False,
            unlock=True,
            start=False,
        )

    def set_command_state_unlocked(self) -> None:
        if self._command_state_mission_started:
            return

        self.set_data_tools_mission_disabled(False)
        self.set_command_buttons_enabled(
            ping=True,
            lock=True,
            unlock=False,
            start=True,
        )

    def set_command_state_mission(self) -> None:
        self._command_state_mission_started = True
        self.set_data_tools_mission_disabled(True)
        self.set_command_buttons_enabled(
            ping=False,
            lock=False,
            unlock=False,
            start=False,
        )

    def reset_runtime_display(self) -> None:
        self._set_dynamic_label_text(self.lbl_gs_state, "—")
        self._set_dynamic_label_text(self.lbl_radio_state, "—")
        self._set_dynamic_label_text(self.lbl_tx_cnt, "0")
        self._set_dynamic_label_text(self.lbl_rx_cnt, "0")
        self._set_dynamic_label_text(self.lbl_crc_err, "0")

        self._set_dynamic_label_text(self.lbl_rssi, "— dBm")
        self._set_dynamic_label_text(self.lbl_snr, "— dB")
        self._set_dynamic_label_text(self.lbl_last_status, "—")
        self._set_dynamic_label_text(self.lbl_last_gs_ack, "—")
        self._set_dynamic_label_text(self.lbl_last_air_ack, "—")

        self._set_dynamic_label_text(self.lbl_accel, "X: —  Y: —  Z: —")
        self._set_dynamic_label_text(self.lbl_gyro, "X: —  Y: —  Z: —")
        self._set_dynamic_label_text(self.lbl_quat_raw, "W: —  X: —  Y: —  Z: —")
        self._set_dynamic_label_text(self.lbl_quat, "W: —  X: —  Y: —  Z: —  valid=—")
        self._set_dynamic_label_text(self.lbl_euler, "R: —  P: —  Y: —")
        self._set_dynamic_label_text(self.lbl_vel, "X: —  Y: —  Z: —")
        self._set_dynamic_label_text(self.lbl_pos, "X: —  Y: —  Z: —")

        for key in self.series:
            for axis in range(3):
                self.series[key][axis].clear()

        self.refresh_plots()

        self.latest_quat = (1.0, 0.0, 0.0, 0.0)
        self.latest_euler = (0.0, 0.0, 0.0)
        self._apply_quat_to_mesh(self.latest_quat)
        self.set_command_state_initial()

    def update_gs_status(
        self,
        gs_state: str,
        radio_state: str,
        tx_cnt: int,
        rx_cnt: int,
        crc_err: int,
    ) -> None:
        self._set_dynamic_label_text(self.lbl_gs_state, gs_state)
        self._set_dynamic_label_text(self.lbl_radio_state, radio_state)
        self._set_dynamic_label_text(self.lbl_tx_cnt, str(tx_cnt))
        self._set_dynamic_label_text(self.lbl_rx_cnt, str(rx_cnt))
        self._set_dynamic_label_text(self.lbl_crc_err, str(crc_err))

    def update_link_quality(self, rssi_dbm: int | None, snr_db: float | None) -> None:
        self._set_dynamic_label_text(self.lbl_rssi, "— dBm" if rssi_dbm is None else f"{rssi_dbm} dBm")
        self._set_dynamic_label_text(self.lbl_snr, "— dB" if snr_db is None else f"{snr_db:.2f} dB")

    def set_last_status(self, text: str) -> None:
        self._set_dynamic_label_text(self.lbl_last_status, text)

    def set_last_gs_ack(self, text: str) -> None:
        self._set_dynamic_label_text(self.lbl_last_gs_ack, text)

    def set_last_air_ack(self, text: str) -> None:
        self._set_dynamic_label_text(self.lbl_last_air_ack, text)

    def update_quat(
        self,
        values: tuple[float, float, float, float],
        raw: tuple[int, int, int, int] | None = None,
        valid: bool = True,
        source: str | None = None,
    ) -> None:
        self.latest_quat = values
        source_prefix = f"{source} " if source else ""
        if raw is None:
            self._set_dynamic_label_text(self.lbl_quat_raw, f"{source_prefix}W: —  X: —  Y: —  Z: —")
        else:
            self._set_dynamic_label_text(
                self.lbl_quat_raw,
                f"{source_prefix}W:{raw[0]}  X:{raw[1]}  Y:{raw[2]}  Z:{raw[3]}",
            )

        valid_suffix = "valid=1" if valid else "valid=0 INVALID/raw=0"
        self._set_dynamic_label_text(
            self.lbl_quat,
            (
                f"{source_prefix}W:{values[0]:.4f}  X:{values[1]:.4f}  "
                f"Y:{values[2]:.4f}  Z:{values[3]:.4f}  {valid_suffix}"
            ),
        )

        euler = self._quat_to_euler_rpy(values)
        self.latest_euler = euler
        euler_suffix = "" if valid else "  INVALID/raw=0"
        self._set_dynamic_label_text(
            self.lbl_euler,
            f"R:{euler[0]:.4f}  P:{euler[1]:.4f}  Y:{euler[2]:.4f}{euler_suffix}",
        )

        self._apply_quat_to_mesh(values)

    def push_vector_sample(self, key: str, values: tuple[float, float, float]) -> None:
        if key in self.series:
            for i in range(3):
                self.series[key][i].append(values[i])
            self.refresh_plots()

        if key == "accel":
            self._set_dynamic_label_text(
                self.lbl_accel,
                f"X:{values[0]:.4f}  Y:{values[1]:.4f}  Z:{values[2]:.4f}",
            )
        elif key == "gyro":
            self._set_dynamic_label_text(
                self.lbl_gyro,
                f"X:{values[0]:.4f}  Y:{values[1]:.4f}  Z:{values[2]:.4f}",
            )
        elif key == "vel":
            self._set_dynamic_label_text(
                self.lbl_vel,
                f"X:{values[0]:.4f}  Y:{values[1]:.4f}  Z:{values[2]:.4f}",
            )
        elif key == "pos":
            self._set_dynamic_label_text(
                self.lbl_pos,
                f"X:{values[0]:.4f}  Y:{values[1]:.4f}  Z:{values[2]:.4f}",
            )

    def refresh_plots(self) -> None:
        for (key, axis), curve in self.curves.items():
            y = list(self.series[key][axis])
            curve.setData(list(range(len(y))), y)

    def _normalize_quat(
        self,
        quat: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float] | None:
        w, x, y, z = quat
        norm = (w * w + x * x + y * y + z * z) ** 0.5

        if norm == 0:
            return None

        return w / norm, x / norm, y / norm, z / norm

    def _quat_to_euler_rpy(
        self,
        quat: tuple[float, float, float, float],
    ) -> tuple[float, float, float]:
        normalized = self._normalize_quat(quat)
        if normalized is None:
            return self.latest_euler

        w, x, y, z = normalized

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

    def _apply_quat_to_mesh(self, quat: tuple[float, float, float, float]) -> None:
        normalized = self._normalize_quat(quat)
        if normalized is None:
            return

        w, x, y, z = normalized

        rot = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ],
            dtype=float,
        )

        rotated = self.base_vertices @ rot.T

        mesh = gl.MeshData(
            vertexes=rotated,
            faces=self.base_faces,
            faceColors=self.base_colors,
        )

        self.mesh_item.setMeshData(meshdata=mesh)
