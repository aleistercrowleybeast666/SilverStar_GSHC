"""Read-only preparation steps and independently expiring navigation health."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from protocol.common import AirAckResult, enum_name
from services.i18n import I18n
from services.navigation_state import (
    FusionGroup,
    NavigationStartResult,
    PreparationStep,
)
from services.preferences import Theme
from services.state_model import FlightControllerState
from ui.theme import theme_colors
from ui.touch_scroll import TouchScroll_Enable


class NavigationPanel(QGroupBox):
    def __init__(self, i18n: I18n, *, preflight: bool) -> None:
        super().__init__()
        self.i18n = i18n
        self.preflight = preflight
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        self.steps: dict[str, QLabel] = {}
        if preflight:
            grid = QGridLayout()
            for index, key in enumerate(("link", *(step.name.lower() for step in PreparationStep), "start")):
                label = QLabel()
                label.setWordWrap(True)
                self.steps[key] = label
                grid.addWidget(label, index // 3, index % 3)
            layout.addLayout(grid)
        else:
            self.toggle = QPushButton()
            self.toggle.setCheckable(True)
            self.toggle.setMinimumHeight(30)
            layout.addWidget(self.toggle)
            self.details = QScrollArea()
            self.details.setWidgetResizable(True)
            self.details.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.details.setMinimumHeight(180)
            self.details.setMaximumHeight(240)
            self.detail_content = QWidget()
            self.details.setWidget(self.detail_content)
            TouchScroll_Enable(self.details)
            details_layout = QVBoxLayout(self.detail_content)
            details_layout.setSizeConstraint(QLayout.SetMinimumSize)
            details_layout.setAlignment(Qt.AlignTop)
            self.group = QComboBox()
            TouchScroll_Enable(self.group.view())
            for group in FusionGroup:
                self.group.addItem(group.name, int(group))
            details_layout.addWidget(self.group)
            self.health = QLabel()
            self.health.setWordWrap(True)
            self.health.setAlignment(Qt.AlignTop)
            self.health.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            details_layout.addWidget(self.health)
            self.unavailable = QLabel()
            self.unavailable.setWordWrap(True)
            self.unavailable.setAlignment(Qt.AlignTop)
            self.unavailable.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            details_layout.addWidget(self.unavailable)
            layout.addWidget(self.details)
            self.details.hide()
            self.toggle.toggled.connect(self.details.setVisible)
        self._state: FlightControllerState | None = None
        if not preflight:
            self.group.currentIndexChanged.connect(self.Navigation_Refresh)

    def Navigation_Refresh(self) -> None:
        if self._state is not None:
            self.Navigation_Render(self._state)

    def Navigation_Render(self, state: FlightControllerState) -> None:
        self._state = state
        tr = self.i18n.tr
        navigation = state.navigation
        check = navigation.Navigation_StartCheck()
        self.setTitle(tr("navigation.preparation" if self.preflight else "navigation.health"))
        algorithm = {0: "Pure INS", 1: "KF_6", 2: "ESKF_15"}.get(navigation.algorithm_id, tr("common.unknown"))
        self.summary.setText(tr("navigation.summary", algorithm=algorithm, state=tr(f"navigation.{check.value}"),
                                session=navigation.session if navigation.session is not None else "—",
                                generation=navigation.generation if navigation.generation is not None else "—"))
        colors = theme_colors(getattr(self.window(), "theme", Theme.LIGHT))
        summary_color = colors.success if check is NavigationStartResult.ALLOWED else colors.warning
        self.summary.setStyleSheet(f"color: {summary_color};")
        if self.preflight:
            snapshot = navigation.preparation
            fresh = check not in {NavigationStartResult.UNSUPPORTED, NavigationStartResult.WAITING, NavigationStartResult.STALE}
            for key, label in self.steps.items():
                status = tr(f"navigation.{check.value}")
                if key == "link":
                    status = tr("navigation.step_ready" if state.connected and state.capability_acked else "navigation.step_waiting")
                elif key == "start":
                    if fresh and snapshot is not None and snapshot.reason:
                        status = self.i18n.enum("ack_result", enum_name(AirAckResult, snapshot.reason))
                    elif check is NavigationStartResult.ALLOWED:
                        status = tr("navigation.step_ready" if state.start_ready() else "navigation.step_waiting")
                elif fresh and snapshot is not None:
                    bit = int(PreparationStep[key.upper()])
                    status = tr("navigation.not_required" if not snapshot.required_mask & bit else
                                "navigation.step_ready" if snapshot.ready_mask & bit else "navigation.step_waiting")
                label.setText(tr(f"navigation.step.{key}") + " · " + status)
        else:
            statuses = [navigation.Navigation_ReadMetric(group, 0) for group in range(5)
                        if navigation.group_mask & (1 << group)]
            results = [value & 15 for value in statuses if value is not None]
            worst = max(results, default=0)
            health_text = tr("navigation.WAITING")
            complete = bool(statuses) and len(results) == len(statuses) and all(value in range(7) for value in results)
            if complete and 0 not in results:
                health_text = tr(f"navigation.result.{worst}")
            elif results:
                health_text = tr("navigation.partial")
            self.summary.setText(tr("navigation.summary", algorithm=algorithm, state=health_text,
                                   session=navigation.session or "—", generation=navigation.generation or 0))
            nominal = complete and all(value == 0x11 for value in statuses)
            color = colors.error if worst >= 3 else colors.success if nominal else colors.warning
            self.summary.setStyleSheet(f"color: {color};")
            self.toggle.setText(tr("navigation.group_details"))
            self.unavailable.setText(self.Navigation_SystemText(state))
            group = int(self.group.currentData())
            status = navigation.Navigation_ReadMetric(group, 0)
            age = navigation.Navigation_GroupAge(group)
            r_scale = navigation.Navigation_ReadMetric(group, 2)
            nis = navigation.Navigation_ReadMetric(group, 3)
            if status is None:
                status_text = tr("navigation.STALE" if navigation.metrics else "navigation.WAITING")
            else:
                result = status & 15
                quality = (status >> 4) & 15
                status_text = (tr(f"navigation.result.{result}") if result <= 6 else tr("navigation.unknown_code", code=result))
                status_text += " · " + (tr(f"navigation.quality.{quality}") if quality <= 3 else tr("navigation.unknown_code", code=quality))
                status_text += " · " + tr("navigation.reason", code=status >> 8)
            age_text = tr("common.unknown") if age is None else f"{age / 1000:.1f} s"
            self.health.setText(tr("navigation.metrics", state=status_text, age=age_text,
                                   r_scale="—" if r_scale in (None, 65535) else f"{r_scale / 256:.2f}",
                                   nis="—" if nis in (None, 65535) else f"{nis / 256:.2f}"))
            ages = [navigation.Navigation_MetricAge(group, metric) for metric in range(4, 8)]
            age_texts = [tr("common.unknown") if value is None else f"{value / 1000:.1f}s" for value in ages]
            recoveries = navigation.Navigation_ReadMetric(group, 8)
            self.health.setText(self.health.text() + "\n" + tr("navigation.history",
                receive=age_texts[0], valid=age_texts[1], attempt=age_texts[2], recovery=age_texts[3],
                count=tr("common.unknown") if recoveries in (None, 65535) else ("≥65534" if recoveries == 65534 else str(recoveries))))

    def Navigation_SystemText(self, state: FlightControllerState) -> str:
        navigation = state.navigation
        tr = self.i18n.tr

        def number(metric: int, scale: int = 1) -> str:
            value = navigation.Navigation_ReadMetric(7, metric)
            if value in (None, 65535):
                return tr("common.unknown")
            prefix = "≥" if value == 65534 else ""
            return prefix + (str(value) if scale == 1 else f"{value / scale:.2f}")

        def age(metric: int) -> str:
            value = navigation.Navigation_MetricAge(7, metric)
            return tr("common.unknown") if value is None else f"{value / 1000:.1f}s"

        flags = navigation.Navigation_ReadMetric(7, 6)
        quality = tr("common.unknown")
        if flags not in (None, 65535):
            quality = ", ".join(tr(f"navigation.imu_flag.{bit}") for bit in range(7) if flags & (1 << bit)) or tr("navigation.no_flags")
            if flags & ~127:
                quality += " · " + tr("navigation.unknown_code", code=flags & ~127)
        fix = navigation.Navigation_ReadMetric(7, 5)
        fix_text = tr("common.unknown") if fix in (None, 65535) else tr("navigation.fix",
            fix=fix & 255, online=int(bool(fix & 256)), fix_ok=int(bool(fix & 512)), usable=int(bool(fix & 1024)))
        return "\n".join((
            tr("navigation.gnss_detail", satellites=number(0), hacc=number(1, 100), vacc=number(2, 100), sacc=number(3, 100), age=age(4), fix=fix_text),
            tr("navigation.imu_detail", flags=quality, age=age(7), generation=number(8), accel=number(16, 100), gyro=number(9)),
            tr("navigation.logger_detail", overflow=number(10), normal=number(11), estimator=number(12), suppressed=number(13), state=number(14), capacity=number(15)),
            tr("navigation.detail_freshness"),
        ))
