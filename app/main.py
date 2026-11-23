import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from app import win
from app.audio import Recorder
from app.hotkey import HotkeyListener
from app.overlay import DONE, ERROR, IDLE, PROCESSING, RECORDING, Overlay
from app.settings import Settings
from app.speech import SpeechEngine, to_16k

VERSION = "1.1"


def setup_logging(folder):
    handler = RotatingFileHandler(folder / "golos.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    logging.info("Golos %s started", VERSION)


class Golos(QObject):
    hotkey_pressed = pyqtSignal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = Settings()
        self.recorder = Recorder()
        self.speech = SpeechEngine(self.settings)
        self.overlay = Overlay()
        self.overlay.level_source = lambda: self.recorder.level
        self.state = IDLE
        self.session = 0
        self.models_ready = False

        self.hotkey = HotkeyListener(lambda: self.hotkey_pressed.emit())
        self.hotkey_pressed.connect(self.toggle)
        self.capture_timer = QTimer(self)
        self.capture_timer.setSingleShot(True)
        self.capture_timer.timeout.connect(self._finish_recording)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.overlay.hide_panel)

        self.tray = QSystemTrayIcon(QIcon(str(win.asset_path("icon.png"))), self)
        self.enabled_action = None
        self.autostart_action = None
        self.punctuation_action = None
        self.language_actions = {}
        self._build_menu()
        self.tray.show()

        self.overlay.hidden.connect(self._overlay_hidden)
        self.speech.final_ready.connect(self._show_result)
        self.speech.failed.connect(self._speech_failed)
        self.speech.loaded.connect(self._models_loaded)
        self.speech.load_failed.connect(self._models_failed)

        autostart = win.autostart_enabled()
        if autostart:
            win.set_autostart(True)
        self.settings.set("autostart", autostart)
        self.autostart_action.blockSignals(True)
        self.autostart_action.setChecked(autostart)
        self.autostart_action.blockSignals(False)
        self._update_icon()
        self.overlay.set_model_ready(False)

        if self.settings.first_run:
            self.settings.first_run = False
            self.settings.save()
            QTimer.singleShot(1000, self._first_run)

    def _build_menu(self):
        menu = QMenu()
        title = QAction("Голос - диктовка", menu)
        title.setEnabled(False)
        menu.addAction(title)
        menu.addSeparator()

        self.enabled_action = QAction("Диктовка", menu)
        self.enabled_action.setCheckable(True)
        self.enabled_action.setChecked(bool(self.settings.get("enabled", True)))
        self.enabled_action.toggled.connect(self._enabled_changed)
        menu.addAction(self.enabled_action)

        self.autostart_action = QAction("Запускать с Windows", menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(bool(self.settings.get("autostart", False)))
        self.autostart_action.toggled.connect(self._autostart_changed)
        menu.addAction(self.autostart_action)

        self.punctuation_action = QAction("Пунктуация", menu)
        self.punctuation_action.setCheckable(True)
        self.punctuation_action.setChecked(bool(self.settings.get("punctuation", True)))
        self.punctuation_action.toggled.connect(self._punctuation_changed)
        menu.addAction(self.punctuation_action)

        language_menu = menu.addMenu("Язык")
        group = QActionGroup(language_menu)
        group.setExclusive(True)
        for code, text in (("ru", "Русский"), ("en", "English - перевод")):
            action = QAction(text, language_menu)
            action.setCheckable(True)
            action.setData(code)
            action.setChecked(self.settings.get("language", "ru") == code)
            action.triggered.connect(self._language_changed)
            group.addAction(action)
            language_menu.addAction(action)
            self.language_actions[code] = action

        menu.addSeparator()
        folder_action = QAction("Открыть папку программы", menu)
        folder_action.triggered.connect(lambda: win.open_path(win.project_dir()))
        menu.addAction(folder_action)
        about_action = QAction("О программе", menu)
        about_action.triggered.connect(self._about)
        menu.addAction(about_action)
        menu.addSeparator()
        exit_action = QAction("Выход", menu)
        exit_action.triggered.connect(self.quit)
        menu.addAction(exit_action)

        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Голос - диктовка\nЛевый Ctrl + правый Alt")

    def start(self):
        self.speech.preload()
        if not self.hotkey.start():
            logging.error("hotkey hook failed")
            self._notify("Не удалось включить горячую клавишу")
        logging.info("ready, models=%s", win.models_dir())

    def toggle(self):
        if not self.settings.get("enabled", True):
            return
        if self.state == RECORDING:
            self._stop_recording()
        elif self.state == PROCESSING:
            return
        else:
            self._start_recording()

    def _start_recording(self):
        if self.state == RECORDING:
            return
        self.session += 1
        self.hide_timer.stop()
        self.capture_timer.stop()
        try:
            self.recorder.start()
        except Exception:
            logging.exception("mic start failed")
            self.state = ERROR
            self.overlay.show_error("Микрофон не найден")
            self._schedule_hide(2600)
            return
        self.state = RECORDING
        self.overlay.set_model_ready(self.models_ready)
        self.overlay.show_recording()
        logging.info("recording")

    def _stop_recording(self):
        if self.state != RECORDING:
            return
        self.state = PROCESSING
        self.overlay.show_processing()
        # Даём дописать хвост фразы, иначе последнее слово часто срезается.
        self.capture_timer.start(220)

    def _finish_recording(self):
        if self.state != PROCESSING:
            return
        audio, rate = self.recorder.stop()
        duration = audio.size / rate if rate else 0.0
        rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
        logging.info("recorded %.2fs, rms %.4f", duration, rms)
        if duration < 0.3:
            self._fail("Слишком коротко")
            return
        if rms < 0.002:
            self._fail("Ничего не расслышал")
            return
        self.speech.submit_final(self.session, to_16k(audio, rate))

    def _show_result(self, session, text):
        if session != self.session or self.state != PROCESSING:
            return
        if not text:
            self._fail("Ничего не расслышал")
            return
        self.state = DONE
        self.overlay.show_result(text)
        QTimer.singleShot(180, lambda: self._insert(text, session))
        self._schedule_hide(1900)
        logging.info("result: %s", text)

    def _insert(self, text, session, attempt=0):
        if session != self.session or self.state != DONE:
            return
        if win.modifiers_down() and attempt < 25:
            QTimer.singleShot(100, lambda: self._insert(text, session, attempt + 1))
            return
        clipboard = QApplication.clipboard()
        old = clipboard.text()
        clipboard.setText(text)
        QTimer.singleShot(80, lambda: self._paste(session, text, old))

    def _paste(self, session, text, old):
        if session != self.session:
            return
        try:
            win.send_paste()
        except Exception:
            logging.exception("paste failed")
            self._notify("Не удалось вставить текст. Он остался в буфере.")
            return
        QTimer.singleShot(1100, lambda: self._restore(old, text))

    def _restore(self, old, inserted):
        clipboard = QApplication.clipboard()
        if clipboard.text() != inserted:
            return
        if old:
            clipboard.setText(old)
        else:
            clipboard.clear()

    def _speech_failed(self, session, message):
        if session == self.session and self.state in (PROCESSING, RECORDING):
            self._fail(message)

    def _fail(self, message):
        self.state = ERROR
        self.overlay.show_error(message)
        self._schedule_hide(2200)
        logging.info("failed: %s", message)

    def _schedule_hide(self, delay):
        self.hide_timer.start(delay)

    def _overlay_hidden(self):
        if self.state != RECORDING:
            self.state = IDLE

    def _models_loaded(self):
        self.models_ready = True
        self.overlay.set_model_ready(True)
        logging.info("models ready")

    def _models_failed(self, message):
        self.models_ready = False
        logging.error("models failed: %s", message)
        self._notify("Не удалось загрузить модель. Подробности в golos.log.")

    def _enabled_changed(self, checked):
        self.settings.set("enabled", checked)
        self._update_icon()
        if not checked:
            self._cancel()

    def _autostart_changed(self, checked):
        if not win.set_autostart(checked):
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(not checked)
            self.autostart_action.blockSignals(False)
            self._notify("Не удалось изменить автозагрузку")
            return
        self.settings.set("autostart", checked)

    def _punctuation_changed(self, checked):
        self.settings.set("punctuation", checked)

    def _language_changed(self):
        action = self.sender()
        if action:
            self.settings.set("language", action.data())
            logging.info("language %s", action.data())

    def _cancel(self):
        self.session += 1
        self.hide_timer.stop()
        self.capture_timer.stop()
        try:
            self.recorder.cancel()
        except Exception:
            pass
        self.state = IDLE
        self.overlay.hide_panel()

    def _update_icon(self):
        enabled = bool(self.settings.get("enabled", True))
        self.tray.setIcon(QIcon(str(win.asset_path("icon.png" if enabled else "icon_off.png"))))
        self.tray.setToolTip("Голос - диктовка" if enabled else "Голос - выключено")

    def _first_run(self):
        self._notify("Левый Ctrl + правый Alt - начать и закончить")

    def _notify(self, text):
        try:
            self.tray.showMessage("Голос", text, QSystemTrayIcon.MessageIcon.Information, 4000)
        except Exception:
            pass

    def _about(self):
        box = QMessageBox()
        box.setWindowTitle("О программе")
        box.setText(f"Голос {VERSION}\n\nЛевый Ctrl + правый Alt.\nПапка: {self.settings.folder}")
        box.setWindowIcon(QIcon(str(win.asset_path("icon.png"))))
        box.exec()

    def quit(self):
        logging.info("exit")
        self.hide_timer.stop()
        self.capture_timer.stop()
        try:
            if self.hotkey:
                self.hotkey.stop()
        except Exception:
            pass
        try:
            self.recorder.cancel()
        except Exception:
            pass
        self.overlay.close()
        self.tray.hide()
        self.app.quit()


def main():
    if not win.single_instance():
        return 0
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Golos")
    app.setApplicationDisplayName("Голос")
    app.setWindowIcon(QIcon(str(win.asset_path("icon.png"))))
    settings = Settings()
    setup_logging(settings.folder)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Голос", "Трей недоступен")
        return 1
    golos = Golos(app)
    golos.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
