import math

from PyQt6.QtCore import (
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QEasingCurve,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QWidget

from . import win

RECORDING = "recording"
PROCESSING = "processing"
DONE = "done"
ERROR = "error"
IDLE = "idle"

WIDTH = 420
HEIGHT = 64
RADIUS = 32


class Overlay(QWidget):
    hidden = pyqtSignal()

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setWindowTitle("Голос")
        self.resize(WIDTH, HEIGHT)
        self.setWindowOpacity(0.0)
        self.state = IDLE
        self.text = ""
        self.model_ready = False
        self.level_source = None
        self.level = 0.0
        self.clock = 0.0
        self._backdrop = None
        self._fade_anim = None
        self._move_anim = None
        self._region_timer = QTimer(self)
        self._region_timer.setSingleShot(True)
        self._region_timer.setInterval(40)
        self._region_timer.timeout.connect(self._update_region)
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(33)
        self._render_timer.timeout.connect(self._tick)

    def show_recording(self):
        self.state = RECORDING
        self.text = ""
        self.clock = 0.0
        self.level = 0.0
        self._present()

    def show_processing(self):
        if self.state not in (RECORDING, IDLE, ERROR):
            return
        self.state = PROCESSING
        self.clock = 0.0
        self.update()

    def show_result(self, text):
        self.state = DONE
        self.text = " ".join((text or "").split())
        self.level = 0.0
        self._present()

    def show_error(self, message):
        self.state = ERROR
        self.text = message
        self.level = 0.0
        self._present()

    def hide_panel(self):
        if not self.isVisible():
            return
        self._animate_opacity(0.0, 150, self._finish_hide)

    def set_model_ready(self, ready):
        self.model_ready = ready
        self.update()

    def _finish_hide(self):
        self.hide()
        self._render_timer.stop()
        self.state = IDLE
        self.hidden.emit()

    def _target_geometry(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        width = min(WIDTH, area.width() - 24)
        x = area.x() + (area.width() - width) // 2
        y = area.bottom() - HEIGHT - 48
        if y < area.top() + 12:
            y = area.top() + 12
        return QRect(x, y, width, HEIGHT)

    def _present(self):
        target = self._target_geometry()
        hidden = not self.isVisible() or self.windowOpacity() < 0.02
        if self._fade_anim is not None:
            try:
                self._fade_anim.stop()
            except RuntimeError:
                pass
        if hidden:
            self._capture_backdrop(target)
            self.setGeometry(target.x(), target.y() + 8, target.width(), target.height())
            self.setWindowOpacity(0.0)
            self.show()
            self._animate_opacity(1.0, 160)
            self._animate_move(QPoint(target.x(), target.y()), 200)
        else:
            self.setGeometry(target)
            if self.windowOpacity() < 0.99:
                self._animate_opacity(1.0, 130)
        self._render_timer.start()
        self.update()
        QTimer.singleShot(0, self._after_show)

    def _after_show(self):
        hwnd = int(self.winId())
        win.configure_window(hwnd)
        self._update_region()
        win.show_without_activation(hwnd)

    def _capture_backdrop(self, target):
        self._backdrop = None
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        try:
            full = screen.grabWindow(0)
            if full.isNull():
                return
            area = screen.geometry()
            scale = full.width() / max(1, area.width())
            x = int((target.x() - area.x()) * scale)
            y = int((target.y() - area.y()) * scale)
            width = max(1, int(target.width() * scale))
            height = max(1, int(target.height() * scale))
            crop = full.copy(x, y, width, height)
            if crop.isNull():
                return
            small = crop.scaled(
                max(10, target.width() // 10),
                max(6, target.height() // 10),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._backdrop = small
        except Exception:
            self._backdrop = None

    def _animate_opacity(self, end, duration, finished=None):
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(end)
        anim.setDuration(duration)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if finished:
            anim.finished.connect(finished)
        anim.start()
        self._fade_anim = anim

    def _animate_move(self, end, duration):
        if self._move_anim is not None:
            try:
                self._move_anim.stop()
            except RuntimeError:
                pass
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setStartValue(self.pos())
        anim.setEndValue(end)
        anim.setDuration(duration)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._move_anim = anim

    def _update_region(self):
        if not self.isVisible():
            return
        win.apply_round_region(int(self.winId()), self.width(), self.height(), RADIUS, self.devicePixelRatioF())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible():
            self._region_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._after_show)

    def _tick(self):
        self.clock += 0.033
        target = 0.0
        if self.state == RECORDING and self.level_source:
            try:
                target = float(self.level_source())
            except Exception:
                target = 0.0
        self.level += (target - self.level) * 0.3
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS, RADIUS)
        painter.setClipPath(path)
        if isinstance(self._backdrop, QPixmap):
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(rect, self._backdrop, QRectF(self._backdrop.rect()))
            painter.fillPath(path, QColor(10, 10, 10, 190))
        else:
            painter.fillPath(path, QColor(14, 14, 14, 215))
        painter.setPen(QPen(QColor(255, 255, 255, 42), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        painter.drawLine(QPointF(26, 1.0), QPointF(rect.width() - 26, 1.0))
        self._paint_meter(painter)
        self._paint_text(painter, rect)

    def _paint_meter(self, painter):
        cx = 34.0
        cy = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        if self.state == RECORDING:
            painter.setBrush(QColor(255, 255, 255, 225))
            bar_width = 2.2
            gap = 2.4
            total = 5 * bar_width + 4 * gap
            start = cx - total / 2
            for i in range(5):
                wave = abs(math.sin(self.clock * 7.4 + i * 1.31))
                idle = 1.2 * (0.5 + 0.5 * math.sin(self.clock * 4.2 + i * 0.9))
                height = 2.0 + idle + 19.0 * self.level * (0.32 + 0.68 * wave)
                height = max(2.0, min(22.0, height))
                x = start + i * (bar_width + gap)
                painter.drawRoundedRect(QRectF(x, cy - height / 2, bar_width, height), 1.1, 1.1)
        elif self.state == PROCESSING:
            pen = QPen(QColor(255, 255, 255, 215), 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(QRectF(cx - 7, cy - 7, 14, 14), int(-self.clock * 320 * 16) % 5760, 100 * 16)
        elif self.state == DONE:
            pen = QPen(QColor(255, 255, 255, 220), 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            check = QPainterPath()
            check.moveTo(cx - 7, cy)
            check.lineTo(cx - 1.5, cy + 5.5)
            check.lineTo(cx + 8, cy - 5.5)
            painter.drawPath(check)
        elif self.state == ERROR:
            pen = QPen(QColor(255, 255, 255, 220), 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(QPointF(cx, cy - 7), QPointF(cx, cy + 2))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 220))
            painter.drawEllipse(QRectF(cx - 1.2, cy + 6, 2.4, 2.4))

    def _paint_text(self, painter, rect):
        font = QFont("Segoe UI")
        font.setPixelSize(15)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        if self.state == ERROR:
            text = self.text or "Что-то пошло не так"
            color = QColor(255, 255, 255, 225)
            from_end = False
        elif self.state == DONE:
            text = self.text or "Готово"
            color = QColor(255, 255, 255, 238)
            from_end = False
        elif self.state == PROCESSING:
            text = self.text or "Распознаю…"
            color = QColor(255, 255, 255, 160)
            from_end = True
        else:
            text = self.text or ("Слушаю…" if self.model_ready else "Загружаю модель…")
            color = QColor(255, 255, 255, 230 if self.text else 175)
            from_end = True
        available = rect.width() - 64 - 22
        shown = self._fit_text(text, font, int(available), from_end)
        painter.setPen(color)
        painter.drawText(
            QRectF(64, 0, available, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            shown,
        )

    def _fit_text(self, text, font, width, from_end):
        metrics = QFontMetrics(font)
        if metrics.horizontalAdvance(text) <= width:
            return text
        words = text.split()
        if not words:
            return "…"
        chosen = []
        if from_end:
            for word in reversed(words):
                trial = " ".join([word] + chosen)
                if metrics.horizontalAdvance("… " + trial) > width:
                    break
                chosen.insert(0, word)
            result = "… " + " ".join(chosen)
        else:
            for word in words:
                trial = " ".join(chosen + [word])
                if metrics.horizontalAdvance(trial + " …") > width:
                    break
                chosen.append(word)
            result = " ".join(chosen) + " …"
        if not chosen:
            return metrics.elidedText(text, Qt.TextElideMode.ElideLeft if from_end else Qt.TextElideMode.ElideRight, width)
        return result
