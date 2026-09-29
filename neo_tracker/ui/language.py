"""Display-only localization. Persisted keys, units and data remain unchanged."""

import os

from neo_tracker.ui.translations_zh import ZH_CN

_language = "en"


def set_language(language: str) -> None:
    global _language
    if language not in {"en", "zh_CN"}:
        raise ValueError("Unsupported interface language")
    _language = language


def tr(source: str, **values: object) -> str:
    translated = ZH_CN.get(source, source) if _language == "zh_CN" else source
    return translated.format(**values) if values else translated


def configure_language(app, language: str | None = None) -> None:
    from PySide6.QtCore import QLibraryInfo, QSettings, QTranslator
    requested = language or os.environ.get("NEOTRACKER_LANGUAGE") or QSettings("ICS", "NeoTracker").value("ui/language", "zh_CN")
    set_language("en" if requested == "en" else "zh_CN")
    if _language == "zh_CN":
        translator = QTranslator(app)
        translator.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
        app.installTranslator(translator)
        app._neo_tracker_translator = translator


def add_language_menu(window) -> None:
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QActionGroup
    from PySide6.QtWidgets import QMessageBox

    menu = window.menuBar().addMenu("语言 / Language")
    group = QActionGroup(menu)
    group.setExclusive(True)
    for label, value in (("简体中文", "zh_CN"), ("English", "en")):
        action = menu.addAction(label)
        group.addAction(action)
        action.setCheckable(True)
        action.setChecked(value == _language)

        def choose(_checked=False, value=value):
            QSettings("ICS", "NeoTracker").setValue("ui/language", value)
            QMessageBox.information(window, "语言 / Language", "下次启动时使用所选语言。\nThe selected language will apply next time you open the app.")

        action.triggered.connect(choose)
