from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QSettings

from services.i18n import Language


class Theme(str, Enum):
    LIGHT = "light"
    DARK = "dark"


class ExportLanguage(str, Enum):
    FOLLOW_UI = "follow_ui"
    ZH_CN = Language.ZH_CN.value
    EN_US = Language.EN_US.value


class ExportItem(str, Enum):
    PROCESSED_DATA = "processed_data"
    SUMMARY = "summary"
    CHARTS = "charts"
    ATTITUDE_3D = "attitude_3d"
    SESSION_INFO = "session_info"


ALL_EXPORT_ITEMS = tuple(ExportItem)


@dataclass(frozen=True)
class ResolvedExportOptions:
    language: Language = Language.EN_US
    theme: Theme = Theme.LIGHT
    items: frozenset[ExportItem] = frozenset(ALL_EXPORT_ITEMS)

    @property
    def language_suffix(self) -> str:
        return "ZH" if self.language is Language.ZH_CN else "EN"

    def output_name(self, stem: str, extension: str) -> str:
        normalized_extension = str(extension).lstrip(".")
        return f"{stem}_{self.language_suffix}.{normalized_extension}"


def resolve_export_language(
    export_language: ExportLanguage,
    ui_language: Language,
) -> Language:
    if export_language is ExportLanguage.FOLLOW_UI:
        return ui_language
    return Language(export_language.value)


class AppPreferences:
    SETTINGS_THEME_KEY = "appearance/theme"
    SETTINGS_EXPORT_LANGUAGE_KEY = "export/language"
    SETTINGS_EXPORT_ITEMS_KEY = "export/items"

    def __init__(self, settings: QSettings) -> None:
        self.settings = settings

    def theme(self) -> Theme:
        raw = str(self.settings.value(self.SETTINGS_THEME_KEY, Theme.LIGHT.value))
        try:
            return Theme(raw)
        except ValueError:
            return Theme.LIGHT

    def set_theme(self, theme: Theme | str) -> None:
        selected = theme if isinstance(theme, Theme) else Theme(str(theme))
        self.settings.setValue(self.SETTINGS_THEME_KEY, selected.value)
        self.settings.sync()

    def export_language(self) -> ExportLanguage:
        raw = str(
            self.settings.value(
                self.SETTINGS_EXPORT_LANGUAGE_KEY,
                ExportLanguage.FOLLOW_UI.value,
            )
        )
        try:
            return ExportLanguage(raw)
        except ValueError:
            return ExportLanguage.FOLLOW_UI

    def set_export_language(self, language: ExportLanguage | str) -> None:
        selected = (
            language
            if isinstance(language, ExportLanguage)
            else ExportLanguage(str(language))
        )
        self.settings.setValue(self.SETTINGS_EXPORT_LANGUAGE_KEY, selected.value)
        self.settings.sync()

    def export_items(self) -> frozenset[ExportItem]:
        raw = self.settings.value(self.SETTINGS_EXPORT_ITEMS_KEY, None)
        if raw is None:
            return frozenset(ALL_EXPORT_ITEMS)
        values = [raw] if isinstance(raw, str) else list(raw)
        selected: set[ExportItem] = set()
        for value in values:
            try:
                selected.add(ExportItem(str(value)))
            except ValueError:
                continue
        return frozenset(selected)

    def set_export_items(self, items: frozenset[ExportItem] | set[ExportItem]) -> None:
        ordered = [item.value for item in ALL_EXPORT_ITEMS if item in items]
        self.settings.setValue(self.SETTINGS_EXPORT_ITEMS_KEY, ordered)
        self.settings.sync()


__all__ = [
    "ALL_EXPORT_ITEMS",
    "AppPreferences",
    "ExportItem",
    "ExportLanguage",
    "ResolvedExportOptions",
    "Theme",
    "resolve_export_language",
]
