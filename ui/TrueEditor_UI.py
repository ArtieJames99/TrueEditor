'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''
from __future__ import annotations
import sys
import re
import inspect
from pathlib import Path
import datetime
from typing import Callable, Optional, Dict, Any

from PySide6.QtCore import (
    Qt, QSize, QRectF, QPoint, QUrl,
    QObject, Signal, Slot, QRunnable, QThreadPool, QSettings
)
from PySide6.QtGui import (
    QPainter, QBrush, QColor, QPixmap, QPen, QFont, QAction, QDesktopServices, QGuiApplication, QCursor
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QListWidget, QGroupBox,
    QSpinBox, QSlider, QFrame, QRadioButton, QFileDialog,
    QStatusBar, QMessageBox, QProgressBar, QSizePolicy, QSpacerItem, QListWidgetItem, QStyle, QSplitter, QScrollArea,
    QTextEdit, QInputDialog
)


# --- Video utilities (FFmpeg-based) ---
import subprocess
import tempfile
import os
from PySide6.QtGui import QPixmap

def _get_ffmpeg_exes():
    base = Path(__file__).parent.parent / "assets" / "ffmpeg"
    ffmpeg_exe = base / "ffmpeg.exe"
    ffprobe_exe = base / "ffprobe.exe"
    return str(ffmpeg_exe), str(ffprobe_exe)

def get_video_resolution(path: str) -> tuple[int, int]:
    """Return (width, height) using ffprobe."""
    _, ffprobe = _get_ffmpeg_exes()
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)]
    out = subprocess.check_output(cmd, text=True).strip()
    w, h = out.split(",")
    return int(w), int(h)

def grab_first_frame(path: str) -> QPixmap:
    """Extract first frame using ffmpeg and return a QPixmap."""
    ffmpeg, _ = _get_ffmpeg_exes()
    tmp = Path(tempfile.gettempdir()) / f"trueeditor_preview_{os.getpid()}.png"
    # Seek to start and grab single frame
    cmd = [ffmpeg, "-y", "-ss", "00:00:00.000", "-i", str(path), "-frames:v", "1", str(tmp)]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[WARN] ffmpeg failed to extract frame: {result.stderr}")
            return QPixmap()
        pix = QPixmap(str(tmp)) if tmp.exists() else QPixmap()
    except subprocess.CalledProcessError as e:
        print(f"[WARN] ffmpeg failed to extract frame: {e.stderr}")
        pix = QPixmap()
    except Exception as e:
        print(f"[WARN] ffmpeg failed to extract frame: {e}")
        pix = QPixmap()
    try:
        if tmp.exists():
            tmp.unlink()
    except Exception:
        pass
    return pix

def _app_base_dir() -> Path:
    """
    Returns a stable base directory for resources.

    - Frozen (PyInstaller on Windows/macOS):
      - Use the directory of the executable (.exe or the binary inside .app/Contents/MacOS).
      - This ensures resources are found next to the built app.

    - Source run:
      - Use the project root inferred from this file's location:
        <project>/ui/TrueEditor-UI.py  ->  base = <project>
    """
    if getattr(sys, "frozen", False):
        # PyInstaller: sys.executable is:
        #   Windows: .../YourApp.exe
        #   macOS:   .../YourApp.app/Contents/MacOS/YourApp
        exe_dir = Path(sys.executable).resolve().parent

        # If you prefer keeping "final/transcriptions" alongside the .app bundle:
        #   YourApp.app
        #   final/
        #     transcriptions/
        # uncomment the next 4 lines:
        #
        # if sys.platform == "darwin":
        #     # exe_dir = .../YourApp.app/Contents/MacOS
        #     app_dir = exe_dir.parent.parent  # .../YourApp.app
        #     return app_dir.parent            # folder containing YourApp.app

        # Otherwise, default: place resources next to the exe (or inside .app/Contents/MacOS).
        return exe_dir
    else:
        # Source: this file is at <project>/ui/TrueEditor-UI.py
        return Path(__file__).resolve().parent.parent

# -------------------------------
# Custom ToggleSwitch (from your existing UI)
# -------------------------------
class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, width: int = 50, height: int = 28, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._checked = False
        self._circle_radius = height - 4
        self._margin = 2
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            # Background
            bg_color = QColor('#3A8DFF') if self._checked else QColor('#2A2F36')
            painter.setBrush(QBrush(bg_color))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(self.rect(), self.height()/2, self.height()/2)
            # Circle
            circle_x = self.width() - self._circle_radius - self._margin if self._checked else self._margin
            painter.setBrush(QBrush(QColor('#E6E8EB')))
            painter.drawEllipse(circle_x, self._margin, self._circle_radius, self._circle_radius)
        finally:
            painter.end()

    def mousePressEvent(self, event):
        self._checked = not self._checked
        self.toggled.emit(self._checked)
        self.update()
        super().mousePressEvent(event)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool):
        self._checked = value
        self.toggled.emit(self._checked)
        self.update()

# -------------------------------
# CaptionPreview (polished, self-contained)
# -------------------------------

# --- CaptionPreview: auto-fit rounded background around text ---
class CaptionPreview(QFrame):
    """Caption preview with auto-fit background.
    - Drag caption position (center point)
    - Background auto-sizes to the text with padding and rounded corners
    - Word-wrap by max width ratio
    """
    positionChanged = Signal(float, float)  # x, y normalized values

    def __init__(self, video_path: Optional[str] = None, safe_zone_path: Optional[str] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(QSize(400, 300))
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        base_dir = Path(__file__).parent
        self.video_path = Path(video_path) if video_path else base_dir / "preview" / "Example.jpg"
        self.bg_pixmap = QPixmap(str(self.video_path)) if self.video_path.exists() else QPixmap()
        self.safe_zone_path = Path(safe_zone_path) if safe_zone_path else base_dir / "preview"/ "safe_zone.png"
        self.safezone_pixmap = QPixmap(str(self.safe_zone_path)) if self.safe_zone_path.exists() else QPixmap()
        self.show_safezone = False

        # Caption properties
        self._text = "Your captions"
        self._font_family = "Arial"
        self._font_size = 34
        self._bold = False
        self._italic = False
        self._align = Qt.AlignCenter
        # Styling toggles
        self.background_enabled = True
        self.karaoke_enabled = False
        self.drop_shadow_enabled = False

        # Colors
        self._base_color = QColor('#FFFFFF')
        self._karaoke_color = QColor('#FF0000')
        self._background_color = QColor('#000000')
        self._background_opacity = 180  # 0..255

        # Position (normalized center point)
        self._x, self._y = 0.5, 0.70

        # Auto-fit background settings
        self.bg_padding = 12          # px
        self.bg_corner_radius = 12    # px
        self.max_text_width_ratio = 0.85  # of widget width

        # Dragging
        self._dragging = False
        self._drag_start_pos: Optional[QPoint] = None
    
    def _layout_text_lines(self, painter: QPainter, text: str, max_width: float) -> list[str]:
        fm = painter.fontMetrics()
        words = text.split()
        lines = []
        cur = ''
        for w in words:
            cand = w if not cur else cur + ' ' + w
            if fm.horizontalAdvance(cand) <= max_width:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                # If one word exceeds max_width, hard-break by characters
                if fm.horizontalAdvance(w) > max_width:
                    partial = ''
                    for ch in w:
                        if fm.horizontalAdvance(partial + ch) <= max_width:
                            partial += ch
                        else:
                            lines.append(partial)
                            partial = ch
                    cur = partial
                else:
                    cur = w
        if cur:
            lines.append(cur)
        if not lines:
            lines = ['']
        return lines

    def _compute_background_rect(self, painter: QPainter, lines: list[str]) -> QRectF:
        fm = painter.fontMetrics()
        line_h = fm.lineSpacing()
        height = line_h * len(lines) + 2 * self.bg_padding
        widths = [fm.horizontalAdvance(line) for line in lines]
        text_w = max(widths) if widths else 0
        width = text_w + 2 * self.bg_padding

        # Center the rect on (self._x, self._y)
        cx = self.width() * self._x
        cy = self.height() * self._y
        rect_x = cx - width / 2
        rect_y = cy - height / 2
        return QRectF(rect_x, rect_y, width, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            # Background image / fallback fill
            if not self.bg_pixmap.isNull():
                scaled_bg = self.bg_pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                target_rect = QRectF((self.width() - scaled_bg.width())/2, (self.height() - scaled_bg.height())/2,
                                    scaled_bg.width(), scaled_bg.height())
                source_rect = QRectF(0, 0, scaled_bg.width(), scaled_bg.height())
                painter.drawPixmap(target_rect, scaled_bg, source_rect)
            else:
                painter.fillRect(self.rect(), QColor(18, 20, 23))

            # Safe zone overlay
            if self.show_safezone:
                self._draw_scaled_safezone(painter)

            # Font
            font = QFont()
            font.setFamily(self._font_family)
            font.setPointSize(self._font_size)
            font.setBold(self._bold)
            font.setItalic(self._italic)
            painter.setFont(font)
            fm = painter.fontMetrics()

            # Word-wrap & background rect
            max_w = self.width() * self.max_text_width_ratio
            lines = self._layout_text_lines(painter, self._text, max_w)
            bg_rect = self._compute_background_rect(painter, lines)

            # Rounded background
            if self.background_enabled:
                color = QColor(self._background_color)
                color.setAlpha(self._background_opacity)
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(bg_rect, self.bg_corner_radius, self.bg_corner_radius)

            # Optional drop shadow (text)
            if self.drop_shadow_enabled:
                painter.setPen(QColor(0, 0, 0, 160))
                baseline_y = bg_rect.top() + self.bg_padding + fm.ascent()
                for i, line in enumerate(lines):
                    line_w = fm.horizontalAdvance(line)
                    if self._align == Qt.AlignCenter:
                        x = bg_rect.left() + (bg_rect.width() - line_w) / 2
                    elif self._align == Qt.AlignRight:
                        x = bg_rect.right() - self.bg_padding - line_w
                    else:  # left
                        x = bg_rect.left() + self.bg_padding
                    y = baseline_y + i * fm.lineSpacing()
                    painter.drawText(QPoint(int(x+2), int(y+2)), line)  # shadow offset

            # Main text
            painter.setPen(self._karaoke_color if self.karaoke_enabled else self._base_color)
            baseline_y = bg_rect.top() + self.bg_padding + fm.ascent()
            for i, line in enumerate(lines):
                line_w = fm.horizontalAdvance(line)
                if self._align == Qt.AlignCenter:
                    x = bg_rect.left() + (bg_rect.width() - line_w) / 2
                elif self._align == Qt.AlignRight:
                    x = bg_rect.right() - self.bg_padding - line_w
                else:
                    x = bg_rect.left() + self.bg_padding
                y = baseline_y + i * fm.lineSpacing()
                painter.drawText(QPoint(int(x), int(y)), line)

            # For dragging: treat the background rect as the draggable area
            self.caption_rect = bg_rect
        finally:
            painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.caption_rect.contains(event.position().toPoint()):
            self._dragging = True
            self._drag_start_pos = event.position().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = event.position().toPoint() - self._drag_start_pos
            self._x += delta.x() / self.width()
            self._y += delta.y() / self.height()
            self._x = max(0, min(1, self._x))
            self._y = max(0, min(1, self._y))
            self._drag_start_pos = event.position().toPoint()
            self.update()
            
            # Emit signal with current position
            self.positionChanged.emit(self._x, self._y)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def widget_to_image_normalized(self, x_norm: float, y_norm: float) -> tuple[float, float]:
        """
        Convert a position expressed as normalized widget coords (0..1)
        into normalized coordinates relative to the displayed image (0..1),
        accounting for KeepAspectRatio scaling and letterboxing.
        Returns (x_img_norm, y_img_norm) clamped to [0,1].
        """
        if self.bg_pixmap.isNull():
            return max(0.0, min(1.0, x_norm)), max(0.0, min(1.0, y_norm))

        pix_w = self.bg_pixmap.width()
        pix_h = self.bg_pixmap.height()
        if pix_w == 0 or pix_h == 0:
            return max(0.0, min(1.0, x_norm)), max(0.0, min(1.0, y_norm))

        widget_w = self.width()
        widget_h = self.height()
        scale = min(widget_w / pix_w, widget_h / pix_h)
        scaled_w = pix_w * scale
        scaled_h = pix_h * scale

        left = (widget_w - scaled_w) / 2.0
        top = (widget_h - scaled_h) / 2.0

        cx = x_norm * widget_w
        cy = y_norm * widget_h

        # Relative to image area
        rel_x = (cx - left) / scaled_w
        rel_y = (cy - top) / scaled_h

        # Clamp
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))
        return rel_x, rel_y

    def _draw_scaled_safezone(self, painter: QPainter):
        """Scale static safe zone PNG to fit current preview."""
        if self.safezone_pixmap.isNull():
            return
            
        # Scale the safe zone pixmap to match current preview size
        scaled_safe = self.safezone_pixmap.scaled(
            self.size(), 
            Qt.KeepAspectRatio, 
            Qt.SmoothTransformation
        )
        
        # Calculate position to center it
        target_rect = QRectF(
            (self.width() - scaled_safe.width()) / 2,
            (self.height() - scaled_safe.height()) / 2,
            scaled_safe.width(), 
            scaled_safe.height()
        )
        
        painter.drawPixmap(target_rect.topLeft(), scaled_safe)

# -------------------------------
# BrandingPreview
# -------------------------------
class BrandingPreview(QFrame):
    """Branding preview with drag-to-position functionality."""
    positionChanged = Signal(float, float)  # x, y normalized values

    def __init__(self, video_path: Optional[str] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(QSize(800,500))
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        base_dir = Path(__file__).parent
        self.media_type = None  # 'image' | 'video'
        self.video_path = Path(video_path) if video_path else base_dir / "preview" / "Example.jpg"
        self.bg_pixmap = QPixmap(str(self.video_path)) if self.video_path.exists() else QPixmap()
        
        # Branding properties
        self._brand_type = 'End Card'
        self._headline = "Your Brand"
        self._subtext = "Your Subtitle"
        self._logo_pixmap = QPixmap()
        self._video_pixmap = QPixmap()
        
        # Styling
        self._brand_color = QColor('#FFFFFF')
        self._background_color = QColor('#000000')
        self._opacity = 100  # 0..100
        self._width = 200    # px
        
        # Position (normalized center point)
        self._x, self._y = 0.5, 0.70
        
        # Dragging
        self._dragging = False
        self._drag_start_pos: Optional[QPoint] = None

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            # ---------- SAFE BACKGROUND ----------
            if (
                isinstance(self.bg_pixmap, QPixmap)
                and not self.bg_pixmap.isNull()
                and self.bg_pixmap.width() > 0
                and self.bg_pixmap.height() > 0
            ):
                scaled_bg = self.bg_pixmap.scaled(
                    self.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )

                target_rect = QRectF(
                    (self.width() - scaled_bg.width()) / 2,
                    (self.height() - scaled_bg.height()) / 2,
                    scaled_bg.width(),
                    scaled_bg.height()
                )

                painter.drawPixmap(target_rect.toRect(), scaled_bg)
            else:
                painter.fillRect(self.rect(), QColor(18, 20, 23))
                return 

            # ---------- MEDIA-AWARE BRANDING ----------
            if self.media_type == 'image':
                if self._brand_type == 'Watermark':
                    self._draw_watermark(painter)
                elif self._brand_type == 'End Card':
                    self._draw_end_card(painter)
                elif self._brand_type == 'Intro':
                    self._draw_intro(painter)

            elif self.media_type == 'video':
                # Video previews should be minimal & safe
                self._draw_video_overlay(painter)
        finally:
            painter.end()

    def _draw_video_overlay(self, painter):
        painter.setPen(Qt.white)
        painter.setOpacity(0.85)

        painter.drawText(
            self.rect().adjusted(12, 12, -12, -12),
            Qt.AlignBottom | Qt.AlignRight,
            "Video Preview"
        )


    def _draw_watermark(self, painter: QPainter):
        # Draw logo or text watermark
        if not self._logo_pixmap.isNull():
            # Draw logo watermark
            logo_size = min(100, self._width)
            logo_scaled = self._logo_pixmap.scaled(logo_size, logo_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            cx = self.width() * self._x
            cy = self.height() * self._y
            logo_rect = QRectF(cx - logo_size/2, cy - logo_size/2, logo_size, logo_size)
            painter.setOpacity(self._opacity / 100.0)
            painter.drawPixmap(logo_rect.topLeft(), logo_scaled)
            painter.setOpacity(1.0)
        else:
            # Draw text watermark
            font = QFont()
            font.setPointSize(16)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(self._brand_color)
            text = self._headline or "Watermark"
            fm = painter.fontMetrics()
            text_w = fm.horizontalAdvance(text)
            cx = self.width() * self._x
            cy = self.height() * self._y
            text_rect = QRectF(cx - text_w/2, cy - 10, text_w, 20)
            painter.drawText(text_rect, Qt.AlignCenter, text)

    def _draw_end_card(self, painter: QPainter):
        # Draw end card with background
        bg_rect = QRectF(self.width() * 0.1, self.height() * 0.7, self.width() * 0.8, self.height() * 0.25)
        
        # Background
        painter.setOpacity(self._opacity / 100.0)
        painter.setBrush(QBrush(self._background_color))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(bg_rect, 12, 12)
        
        # Content
        font = QFont()
        font.setPointSize(24)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(self._brand_color)
        painter.drawText(bg_rect, Qt.AlignCenter, f"{self._headline}\n{self._subtext}")
        painter.setOpacity(1.0)

    def _draw_intro(self, painter: QPainter):
        # Draw intro screen
        center_rect = QRectF(self.width() * 0.2, self.height() * 0.3, self.width() * 0.6, self.height() * 0.4)
        
        painter.setOpacity(self._opacity / 100.0)
        painter.setBrush(QBrush(self._background_color))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(center_rect, 16, 16)
        
        font = QFont()
        font.setPointSize(32)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(self._brand_color)
        painter.drawText(center_rect, Qt.AlignCenter, f"{self._headline}\n{self._subtext}")
        painter.setOpacity(1.0)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_start_pos = event.position().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = event.position().toPoint() - self._drag_start_pos
            self._x += delta.x() / self.width()
            self._y += delta.y() / self.height()
            self._x = max(0, min(1, self._x))
            self._y = max(0, min(1, self._y))
            self._drag_start_pos = event.position().toPoint()
            self.update()
            
            # Emit signal with current position
            self.positionChanged.emit(self._x, self._y)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def widget_to_image_normalized(self, x_norm: float, y_norm: float) -> tuple[float, float]:
        """Convert widget coords to image coords (same as CaptionPreview)."""
        if self.bg_pixmap.isNull():
            return max(0.0, min(1.0, x_norm)), max(0.0, min(1.0, y_norm))

        pix_w = self.bg_pixmap.width()
        pix_h = self.bg_pixmap.height()
        if pix_w == 0 or pix_h == 0:
            return max(0.0, min(1.0, x_norm)), max(0.0, min(1.0, y_norm))

        widget_w = self.width()
        widget_h = self.height()
        scale = min(widget_w / pix_w, widget_h / pix_h)
        scaled_w = pix_w * scale
        scaled_h = pix_h * scale

        left = (widget_w - scaled_w) / 2.0
        top = (widget_h - scaled_h) / 2.0

        cx = x_norm * widget_w
        cy = y_norm * widget_h

        # Relative to image area
        rel_x = (cx - left) / scaled_w
        rel_y = (cy - top) / scaled_h

        # Clamp
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))
        return rel_x, rel_y


# -------------------------------
# Drop-enabled list for videos
# -------------------------------
class DropListWidget(QListWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p:
                self.addItem(p)
        event.acceptProposedAction()

# -------------------------------
# Generic worker using QRunnable + signals
# -------------------------------
class WorkerSignals(QObject):
    progress = Signal(int)          # 0..100
    log = Signal(str)
    result = Signal(object)         # any
    error = Signal(str)
    finished = Signal()

class Worker(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            # If backend supports a 'report' kwarg, pass a callable for progress/log
            sig = inspect.signature(self.fn)
            if 'report' in sig.parameters:
                def report(percent: Optional[int] = None, message: Optional[str] = None):
                    if percent is not None:
                        self.signals.progress.emit(int(percent))
                    if message:
                        self.signals.log.emit(str(message))
                self.kwargs['report'] = report
            # Execute
            self.signals.log.emit('Job started')
            result = self.fn(*self.args, **self.kwargs)
            self.signals.log.emit('Job finished')
            self.signals.result.emit(result)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()

# -------------------------------
# TrueEditor Main Window
# -------------------------------
class TrueEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('TrueEditor')
        self.resize(1024, 768)

        # Settings & thread pool
        self.settings = QSettings('TrueEditor', 'TrueEditor')
        self.pool = QThreadPool.globalInstance()
        self.backend: Dict[str, Callable] = {} 

        # Central TabWidget
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._home_tab(), 'Home')
        self.tabs.addTab(self._captions_tab(), 'Captions')
        self.tabs.addTab(self._audio_tab(), 'Audio')
        self.tabs.addTab(self._branding_tab(), 'Branding')
        self.tabs.addTab(self._edit_tab(), 'Edit')
        self.tabs.addTab(self._run_tab(), 'Run')

        # Status Bar + Progress
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(200)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.addPermanentWidget(self.progress)
        self.status.showMessage('Ready  •  FFmpeg ✓  Whisper ✓')

        # Set Default output path
        default_output_path = str(Path(__file__).parent.parent / "final"/ "edited_videos")
        self.output_path.setText(default_output_path)

        base = _app_base_dir()
        # Fixed relative: <base>/final/transcriptions
        self.transcriptions_dir = (base / "final" / "transcriptions").resolve()
        self.transcriptions_dir.mkdir(parents=True, exist_ok=True)


        # Menu & toolbar
        self._apply_qss()

    # ---------- QSS Theme ----------
    def _apply_qss(self):
        qss = '''
        /* ===== GLOBAL ===== */
        QWidget {
            background-color: #1A1D21;
            color: #E6E8EB;
            font-family: "Segoe UI", "Inter", sans-serif;
            font-size: 13px;
        }
        QMainWindow { background-color: #121417; }

        /* ===== TABS ===== */
        QTabWidget::pane { border: 1px solid #2A2F36; 
            background: #121417; 
        }

        QTabBar::tab {
            background: #1A1D21; 
            color: #9AA4AF; 
            padding: 8px 14px; 
            border-top-left-radius: 6px; 
            border-top-right-radius: 6px;
        }

        QTabBar::tab:selected { background: #121417; 
            color: #E6E8EB; 
            border-bottom: 2px solid #3A8DFF; 
        }

        QTabBar::tab:hover { color: #E6E8EB; }

        /* ===== GROUP BOXES ===== */
        QGroupBox { 
            background-color: #1A1D21; 
            border: 1px solid #2A2F36; 
            border-radius: 8px; 
            margin-top: 16px; 
            padding: 14px; 
        }

        QGroupBox::title { 
            subcontrol-origin: margin; 
            subcontrol-position: top left; 
            padding: 0 6px; 
            color: #9AA4AF; 
            font-weight: 600; 
            font-size: 12px;
        }

        /* Primary sections (Project / Input) */
        QGroupBox#primary {
            border: 1px solid #2A2F36;
            border-radius: 6px;
        }

        /* Primary controls - visual grouping */
        #HomeTabContainer QGroupBox#primary {
            border: 1px solid #2A2F36;
            border-radius: 6px;
        }

        /* Primary controls - visual grouping */
        #HomeTabContainer QGroupBox#primary > QFormLayout > QWidget,
        #HomeTabContainer QGroupBox#primary > QVBoxLayout > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in primary controls */
        #HomeTabContainer QGroupBox#primary QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in primary controls */
        #HomeTabContainer QGroupBox#primary QLineEdit,
        #HomeTabContainer QGroupBox#primary QComboBox,
        #HomeTabContainer QGroupBox#primary QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in primary controls */
        #HomeTabContainer QGroupBox#primary QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #HomeTabContainer QGroupBox#primary QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in primary controls */
        #HomeTabContainer QGroupBox#primary QSlider {
            background-color: #121417;
            border: none;
        }

        #HomeTabContainer QGroupBox#primary QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        #HomeTabContainer QGroupBox#primary QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Toggle switches in primary controls */
        #HomeTabContainer QGroupBox#primary ToggleSwitch {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 14px;
            padding: 2px;
        }

        #HomeTabContainer QGroupBox#primary ToggleSwitch:hover {
            background-color: #1A1D21;
        }

        /* Secondary container (Quick Start) */
        QGroupBox#secondary {
            background-color: #1A1D21;
            border-radius: 6px;
        }

        /* Quick Start controls - visual grouping */
        #HomeTabContainer QGroupBox#secondary {
            background-color: #1A1D21;
            border-radius: 6px;
        }

        /* Quick Start controls - visual grouping */
        #HomeTabContainer QGroupBox#secondary > QFormLayout > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in quick start controls */
        #HomeTabContainer QGroupBox#secondary QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in quick start controls */
        #HomeTabContainer QGroupBox#secondary QLineEdit,
        #HomeTabContainer QGroupBox#secondary QComboBox,
        #HomeTabContainer QGroupBox#secondary QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in quick start controls */
        #HomeTabContainer QGroupBox#secondary QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #HomeTabContainer QGroupBox#secondary QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in quick start controls */
        #HomeTabContainer QGroupBox#secondary QSlider {
            background-color: #121417;
            border: none;
        }

        #HomeTabContainer QGroupBox#secondary QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        #HomeTabContainer QGroupBox#secondary QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Toggle switches in quick start controls */
        #HomeTabContainer QGroupBox#secondary ToggleSwitch {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 14px;
            padding: 2px;
        }

        #HomeTabContainer QGroupBox#secondary ToggleSwitch:hover {
            background-color: #1A1D21;
        }

        /* Drawer / nested configs */
        QGroupBox#drawer {
            background-color: #121417;
            border: 1px solid #262A31;
            border-radius: 6px;
            margin-top: 6px;
            padding: 10px;
        }

        QGroupBox#drawer::title {
            font-size: 11px;
            color: #7F8893;
        }

        /* Drawer controls - visual grouping */
        QGroupBox#drawer > QFormLayout > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in drawer controls */
        QGroupBox#drawer QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in drawer controls */
        QGroupBox#drawer QLineEdit,
        QGroupBox#drawer QComboBox,
        QGroupBox#drawer QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in drawer controls */
        QGroupBox#drawer QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        QGroupBox#drawer QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in drawer controls */
        QGroupBox#drawer QSlider {
            background-color: #121417;
            border: none;
        }

        QGroupBox#drawer QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        QGroupBox#drawer QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Home tab drawer controls - visual grouping */
        #HomeTabContainer QGroupBox#drawer {
            background-color: #121417;
            border: 1px solid #262A31;
            border-radius: 6px;
            margin-top: 6px;
            padding: 10px;
        }

        #HomeTabContainer QGroupBox#drawer::title {
            font-size: 11px;
            color: #7F8893;
        }

        /* Drawer controls - visual grouping */
        #HomeTabContainer QGroupBox#drawer > QFormLayout > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in drawer controls */
        #HomeTabContainer QGroupBox#drawer QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in drawer controls */
        #HomeTabContainer QGroupBox#drawer QLineEdit,
        #HomeTabContainer QGroupBox#drawer QComboBox,
        #HomeTabContainer QGroupBox#drawer QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in drawer controls */
        #HomeTabContainer QGroupBox#drawer QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #HomeTabContainer QGroupBox#drawer QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in drawer controls */
        #HomeTabContainer QGroupBox#drawer QSlider {
            background-color: #121417;
            border: none;
        }

        #HomeTabContainer QGroupBox#drawer QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        #HomeTabContainer QGroupBox#drawer QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Toggle switches in drawer controls */
        #HomeTabContainer QGroupBox#drawer ToggleSwitch {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 14px;
            padding: 2px;
        }

        #HomeTabContainer QGroupBox#drawer ToggleSwitch:hover {
            background-color: #1A1D21;
        }

        /* Caption controls container */
        #IvCaptionControlsContainer {
            background-color: #121417;
            border: 1px solid #262A31;
            border-radius: 6px;
            padding: 8px;
        }

        /* Control row backgrounds for visual grouping */
        #IvCaptionControlsContainer > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in control rows */
        #IvCaptionControlsContainer QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in control rows */
        #IvCaptionControlsContainer QLineEdit,
        #IvCaptionControlsContainer QComboBox,
        #IvCaptionControlsContainer QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in control rows */
        #IvCaptionControlsContainer QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #IvCaptionControlsContainer QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in control rows */
        #IvCaptionControlsContainer QSlider {
            background-color: #121417;
            border: none;
        }

        #IvCaptionControlsContainer QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        #IvCaptionControlsContainer QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Group boxes inside caption controls */
        #IvCaptionControlsContainer QGroupBox {
            background-color: #14161A;
            border: 1px solid #262A31;
            border-radius: 6px;
            margin-top: 12px;
            padding: 10px;
        }

        #IvCaptionControlsContainer QGroupBox::title {
            color: #9AA4AF;
            font-weight: 600;
            font-size: 11px;
        }

        /* Captions tab container - visual grouping */
        #CaptionsTabContainer {
            background-color: #121417;
            border: 1px solid #262A31;
            border-radius: 6px;
            padding: 8px;
        }

        /* Control row backgrounds for visual grouping */
        #CaptionsTabContainer > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in captions tab controls */
        #CaptionsTabContainer QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in captions tab controls */
        #CaptionsTabContainer QLineEdit,
        #CaptionsTabContainer QComboBox,
        #CaptionsTabContainer QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in captions tab controls */
        #CaptionsTabContainer QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #CaptionsTabContainer QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in captions tab controls */
        #CaptionsTabContainer QSlider {
            background-color: #121417;
            border: none;
        }

        #CaptionsTabContainer QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        #CaptionsTabContainer QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Group boxes inside captions tab controls */
        #CaptionsTabContainer QGroupBox {
            background-color: #14161A;
            border: 1px solid #262A31;
            border-radius: 6px;
            margin-top: 12px;
            padding: 10px;
        }

        #CaptionsTabContainer QGroupBox::title {
            color: #9AA4AF;
            font-weight: 600;
            font-size: 11px;
        }

        /* Captions tab toggle switches - visual grouping */
        #CaptionsTabContainer ToggleSwitch {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 14px;
            padding: 2px;
        }

        #CaptionsTabContainer ToggleSwitch:hover {
            background-color: #1A1D21;
        }

        /* ===== INPUTS ===== */

        QLineEdit, QComboBox, QSpinBox {
            background-color: #121417; 
            border: 1px solid #2A2F36; 
            border-radius: 6px; 
            padding: 6px;
        }

        QLineEdit:focus, QComboBox:focus, QSpinBox:focus { 
            border: 1px solid #3A8DFF; 
        }

        /* Branding controls container */
        #IvBrandingControlsContainer {
            background-color: #121417;
            border: 1px solid #262A31;
            border-radius: 6px;
            padding: 8px;
        }

        /* Control row backgrounds for visual grouping */
        #IvBrandingControlsContainer > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in branding control rows */
        #IvBrandingControlsContainer QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in branding control rows */
        #IvBrandingControlsContainer QLineEdit,
        #IvBrandingControlsContainer QComboBox,
        #IvBrandingControlsContainer QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in branding control rows */
        #IvBrandingControlsContainer QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #IvBrandingControlsContainer QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in branding control rows */
        #IvBrandingControlsContainer QSlider {
            background-color: #121417;
            border: none;
        }

        #IvBrandingControlsContainer QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        #IvBrandingControlsContainer QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Group boxes inside branding controls */
        #IvBrandingControlsContainer QGroupBox {
            background-color: #14161A;
            border: 1px solid #262A31;
            border-radius: 6px;
            margin-top: 12px;
            padding: 10px;
        }

        #IvBrandingControlsContainer QGroupBox::title {
            color: #9AA4AF;
            font-weight: 600;
            font-size: 11px;
        }

        /* Branding tab container - visual grouping */
        #BrandingTabContainer {
            background-color: #121417;
            border: 1px solid #262A31;
            border-radius: 6px;
            padding: 8px;
        }

        /* Control row backgrounds for visual grouping */
        #BrandingTabContainer > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in branding tab controls */
        #BrandingTabContainer QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in branding tab controls */
        #BrandingTabContainer QLineEdit,
        #BrandingTabContainer QComboBox,
        #BrandingTabContainer QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in branding tab controls */
        #BrandingTabContainer QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #BrandingTabContainer QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in branding tab controls */
        #BrandingTabContainer QSlider {
            background-color: #121417;
            border: none;
        }

        #BrandingTabContainer QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        #BrandingTabContainer QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Group boxes inside branding tab controls */
        #BrandingTabContainer QGroupBox {
            background-color: #14161A;
            border: 1px solid #262A31;
            border-radius: 6px;
            margin-top: 12px;
            padding: 10px;
        }

        #BrandingTabContainer QGroupBox::title {
            color: #9AA4AF;
            font-weight: 600;
            font-size: 11px;
        }

        /* Branding tab toggle switches - visual grouping */
        #BrandingTabContainer ToggleSwitch {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 14px;
            padding: 2px;
        }

        #BrandingTabContainer ToggleSwitch:hover {
            background-color: #1A1D21;
        }

        /* Audio controls container */
        #AudioControlsContainer {
            background-color: #121417;
            border: 1px solid #262A31;
            border-radius: 6px;
            padding: 8px;
        }

        /* Control row backgrounds for visual grouping */
        #AudioControlsContainer > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in audio control rows */
        #AudioControlsContainer QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in audio control rows */
        #AudioControlsContainer QLineEdit,
        #AudioControlsContainer QComboBox,
        #AudioControlsContainer QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in audio control rows */
        #AudioControlsContainer QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #AudioControlsContainer QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in audio control rows */
        #AudioControlsContainer QSlider {
            background-color: #121417;
            border: none;
        }

        #AudioControlsContainer QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        #AudioControlsContainer QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Group boxes inside audio controls */
        #AudioControlsContainer QGroupBox {
            background-color: #14161A;
            border: 1px solid #262A31;
            border-radius: 6px;
            margin-top: 12px;
            padding: 10px;
        }

        #AudioControlsContainer QGroupBox::title {
            color: #9AA4AF;
            font-weight: 600;
            font-size: 11px;
        }

        /* Audio tab container - visual grouping */
        #AudioTabContainer {
            background-color: #121417;
            border: 1px solid #262A31;
            border-radius: 6px;
            padding: 8px;
        }

        /* Control row backgrounds for visual grouping */
        #AudioTabContainer > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in audio tab controls */
        #AudioTabContainer QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in audio tab controls */
        #AudioTabContainer QLineEdit,
        #AudioTabContainer QComboBox,
        #AudioTabContainer QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in audio tab controls */
        #AudioTabContainer QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #AudioTabContainer QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in audio tab controls */
        #AudioTabContainer QSlider {
            background-color: #121417;
            border: none;
        }

        #AudioTabContainer QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        #AudioTabContainer QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Group boxes inside audio tab controls */
        #AudioTabContainer QGroupBox {
            background-color: #14161A;
            border: 1px solid #262A31;
            border-radius: 6px;
            margin-top: 12px;
            padding: 10px;
        }

        #AudioTabContainer QGroupBox::title {
            color: #9AA4AF;
            font-weight: 600;
            font-size: 11px;
        }

        /* Audio tab toggle switches - visual grouping */
        #AudioTabContainer ToggleSwitch {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 14px;
            padding: 2px;
        }

        #AudioTabContainer ToggleSwitch:hover {
            background-color: #1A1D21;
        }

        /* Home tab controls - visual grouping */
        #HomeTabContainer {
            background-color: #121417;
            border: 1px solid #262A31;
            border-radius: 6px;
            padding: 8px;
        }

        /* Control row backgrounds for visual grouping */
        #HomeTabContainer > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in home tab controls */
        #HomeTabContainer QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in home tab controls */
        #HomeTabContainer QLineEdit,
        #HomeTabContainer QComboBox,
        #HomeTabContainer QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in home tab controls */
        #HomeTabContainer QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #HomeTabContainer QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Sliders in home tab controls */
        #HomeTabContainer QSlider {
            background-color: #121417;
            border: none;
        }

        #HomeTabContainer QSlider::groove:horizontal {
            background: #2A2F36;
            height: 4px;
            border-radius: 2px;
        }

        #HomeTabContainer QSlider::handle:horizontal {
            background: #3A8DFF;
            width: 14px;
            height: 14px;
            border-radius: 7px;
            margin: -5px 0;
        }

        /* Group boxes inside home tab controls */
        #HomeTabContainer QGroupBox {
            background-color: #14161A;
            border: 1px solid #262A31;
            border-radius: 6px;
            margin-top: 12px;
            padding: 10px;
        }

        #HomeTabContainer QGroupBox::title {
            color: #9AA4AF;
            font-weight: 600;
            font-size: 11px;
        }

        /* Run tab controls - visual grouping */
        #RunTabContainer {
            background-color: #121417;
            border: 1px solid #262A31;
            border-radius: 6px;
            padding: 8px;
        }

        /* Control row backgrounds for visual grouping */
        #RunTabContainer > QWidget {
            background-color: #14161A;
            border-radius: 4px;
            margin: 2px 0;
        }

        /* Labels in run tab controls */
        #RunTabContainer QLabel {
            color: #9AA4AF;
            font-weight: 500;
        }

        /* Input fields in run tab controls */
        #RunTabContainer QLineEdit,
        #RunTabContainer QComboBox,
        #RunTabContainer QSpinBox {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 8px;
        }

        /* Buttons in run tab controls */
        #RunTabContainer QPushButton {
            background-color: #1F2430;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            padding: 4px 12px;
        }

        #RunTabContainer QPushButton:hover {
            background-color: #2A2F36;
        }

        /* Progress bars in run tab controls */
        #RunTabContainer QProgressBar {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
            text-align: center;
        }

        #RunTabContainer QProgressBar::chunk {
            background-color: #3A8DFF;
            border-radius: 3px;
        }

        /* Lists in run tab controls */
        #RunTabContainer QListWidget {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 4px;
        }

        #RunTabContainer QListWidget::item:selected {
            background-color: #3A8DFF;
        }

        /* Group boxes inside run tab controls */
        #RunTabContainer QGroupBox {
            background-color: #14161A;
            border: 1px solid #262A31;
            border-radius: 6px;
            margin-top: 12px;
            padding: 10px;
        }

        #RunTabContainer QGroupBox::title {
            color: #9AA4AF;
            font-weight: 600;
            font-size: 11px;
        }

        /* Run tab toggle switches - visual grouping */
        #RunTabContainer ToggleSwitch {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 14px;
            padding: 2px;
        }

        #RunTabContainer ToggleSwitch:hover {
            background-color: #1A1D21;
        }

        /* ===== BUTTONS ===== */
        QPushButton { 
            background-color: #1F2430; 
            border: 1px solid #2A2F36; 
            border-radius: 6px; 
            padding: 6px 14px; 
        }

        QPushButton:hover { 
            background-color: #2A2F36; 
        }

        QPushButton:pressed { 
            background-color: #3A8DFF; 
            border-color: #3A8DFF; 
        }

        QPushButton#primary { 
            background-color: #3A8DFF; 
            border-color: #3A8DFF; 
            color: white; 
            font-weight: 600; 
        }

        QPushButton#ghost {
            background: transparent;
            border: 1px solid #2A2F36;
            padding: 6px 12px;
        }

        QPushButton#ghost:hover {
            background-color: #1A1D21;
        }

        QPushButton#primary:hover { 
            background-color: #5AA2FF; 
        }

        /* ===== LISTS ===== */
        QListWidget { background-color: #121417; border: 1px solid #2A2F36; border-radius: 6px; }
        QListWidget::item:selected { background-color: #3A8DFF; }

        /* Home tab lists - visual grouping */
        #HomeTabContainer QListWidget {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 6px;
        }

        #HomeTabContainer QListWidget::item:selected {
            background-color: #3A8DFF;
        }
        /* ===== STATUS BAR ===== */
        QStatusBar { background-color: #121417; border-top: 1px solid #2A2F36; color: #9AA4AF; }
        /* ===== RADIO BUTTONS ===== */
        QRadioButton { spacing: 6px; color: #E6E8EB; font-size: 13px; }
        QRadioButton::indicator {
            width: 16px; height: 16px; border: 2px solid #2A2F36; border-radius: 8px; background: #1A1D21;
        }
        QRadioButton::indicator:checked { border-color: #3A8DFF; background: #3A8DFF; }
        QRadioButton::indicator:hover { border-color: #5AA2FF; }
        QRadioButton::indicator:disabled { border-color: #444A52; background: #1A1D21; opacity: 0.5; }

        /* Home tab toggle switches - visual grouping */
        #HomeTabContainer ToggleSwitch {
            background-color: #121417;
            border: 1px solid #2A2F36;
            border-radius: 14px;
            padding: 2px;
        }

        #HomeTabContainer ToggleSwitch:hover {
            background-color: #1A1D21;
        }

        '''
        QApplication.instance().setStyleSheet(qss)

    # -----------------------------
    # Home
    # -----------------------------
    ''' 
    TODO:  
    IMPORTANT: vocal isolation and Music, when using UI do not function correctly.
    - Add Toggel in run tab that enables "Use Previous Caption Files" Else: Remove and make new file. 
    - Easy Out (endcards and Audio cleanup all files if error or quick shut down.)
    - Add Import Folder Option
    - Project Naming Function (make so nests under a folder with project name)
    - Add Save/Load  Preset Functionality
    - 3-4 Common Presets that they can select within, then give the ability to load presets later
    - Make sure it can handle .mov files as well
    '''
    def _toggle_section_header(self, title: str, subtitle: str, toggle: ToggleSwitch) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: 600;")

        subtitle_lbl = QLabel(subtitle)
        subtitle_lbl.setStyleSheet("font-size: 11px; color: #9AA4AF;")

        text_col.addWidget(title_lbl)
        text_col.addWidget(subtitle_lbl)

        layout.addLayout(text_col)
        layout.addStretch()
        layout.addWidget(toggle)

        return header


    def _home_tab(self) -> QWidget:
        root = QWidget()
        root.setObjectName("HomeTabContainer")
        main_layout = QVBoxLayout(root)
        content_layout = QHBoxLayout()
        
        # Project Setup
        project_box = QGroupBox('Project Setup')
        project_box.setObjectName("primary")
        project_form = QFormLayout(project_box)
        self.project_name = QLineEdit()
        self.output_path = QLineEdit()

        # Default Path
        default_output_path = str(Path(__file__).parent.parent / "final"/ "edited_videos")
        self.output_path.setText(default_output_path)
        output_row = QHBoxLayout()

        # Controls
        self.output_browse_btn = QPushButton('Browse')
        self.output_browse_btn.clicked.connect(self._select_output_folder)
        self.open_output_btn = QPushButton('Open Folder')
        self.open_output_btn.clicked.connect(self._open_output_folder)
        output_row.addWidget(self.output_path)
        output_row.addWidget(self.output_browse_btn)
        output_row.addWidget(self.open_output_btn)
        self.platform_preset = QComboBox()
        self.platform_preset.addItems(['Generic', 'YouTube', 'Instagram', 'TikTok'])
        project_form.addRow('Project Name', self.project_name)
        project_form.addRow('Output Folder', output_row)
        project_form.addRow('Platform Preset', self.platform_preset)

        # ---------- Preset Controls -------------
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        project_form.addRow(separator)
        
        quick_start_widget = QWidget()
        quick_start_widget_layout = QVBoxLayout(quick_start_widget)

        quick_start_box = QGroupBox('Quick Start')
        quick_start_box.setObjectName("secondary")
        quick_start_box_layout = QFormLayout(quick_start_box)
        quick_start_widget_layout.addWidget(quick_start_box)

        # Captions Controls - Home
        self.captions_enabled_toggle = ToggleSwitch()

        captions_header = self._toggle_section_header(
            "Captions",
            "Automatically generate subtitles",
            self.captions_enabled_toggle
        )

        self.captions_content_box = QGroupBox("Caption Settings")
        self.captions_content_box.setObjectName("drawer")
        self.captions_content_box.setVisible(False)

        caption_config_layout = QFormLayout(self.captions_content_box)
        caption_config_layout.setContentsMargins(10, 6, 6, 6)
        caption_config_layout.setSpacing(6)

        self.model_size_combo = QComboBox()
        self.model_size_combo.addItems(['Tiny', 'Small', 'Medium', 'Large'])
        self.model_size_combo.setCurrentText('Small')
        caption_config_layout.addRow('Speech Model', self.model_size_combo)

        # Separator in captions settings
        separator_home_caption = QFrame()
        separator_home_caption.setFrameShape(QFrame.HLine)
        separator_home_caption.setFrameShadow(QFrame.Sunken)
        separator_home_caption.setStyleSheet("background-color: #262A31; margin: 4px 0;")
        caption_config_layout.addRow(separator_home_caption)

        self.caption_language_combo = QComboBox()
        self.caption_language_combo.addItems([
            'Auto', 'English', 'Spanish', 'Chinese', 'French', 'German', 'Italian',
            'Tagalog', 'Hindi', 'Arabic', 'Portuguese', 'Russian', 'Japanese',
            'Korean', 'Vietnamese', 'Thai', 'Indonesian', 'Dutch', 'Polish',
            'Turkish', 'Hebrew', 'Swahili', 'Malay', 'Bengali', 'Punjabi',
            'Javanese', 'Tamil', 'Telugu', 'Marathi', 'Urdu', 'Persian',
            'Ukrainian', 'Greek', 'Czech', 'Hungarian', 'Swedish', 'Finnish',
            'Danish', 'Norwegian', 'Romanian', 'Bulgarian', 'Serbian', 'Croatian',
            'Slovak', 'Slovenian', 'Lithuanian', 'Latvian', 'Estonian', 'Filipino'
        ])
        caption_config_layout.addRow('Language', self.caption_language_combo)

        quick_start_box_layout.addRow(captions_header)
        quick_start_box_layout.addRow(self.captions_content_box)

        self.captions_enabled_toggle.toggled.connect(
            self.captions_content_box.setVisible
        )

        # Audio Controls - Home
        self.audio_enabled_toggle = ToggleSwitch()

        audio_header = self._toggle_section_header(
            "Audio",
            "Enhance or replace audio",
            self.audio_enabled_toggle
        )

        self.audio_content_box = QGroupBox("Audio Settings")
        self.audio_content_box.setObjectName("drawer")
        self.audio_content_box.setVisible(False)

        audio_config_layout = QFormLayout(self.audio_content_box)
        audio_config_layout.setContentsMargins(10, 6, 6, 6)
        audio_config_layout.setSpacing(6)

        self.home_voice_isolation_toggle = ToggleSwitch()
        audio_config_layout.addRow('Vocal Isolation', self.home_voice_isolation_toggle)

        # Separator in audio settings
        separator_home_audio = QFrame()
        separator_home_audio.setFrameShape(QFrame.HLine)
        separator_home_audio.setFrameShadow(QFrame.Sunken)
        separator_home_audio.setStyleSheet("background-color: #262A31; margin: 4px 0;")
        audio_config_layout.addRow(separator_home_audio)

        self.home_music_toggle = ToggleSwitch()
        audio_config_layout.addRow('Enable Music', self.home_music_toggle)

        self.home_music_selector = QPushButton('Select Music')
        self.home_music_selector.clicked.connect(self._select_music)
        self.home_music_selector.setObjectName("ghost")
        audio_config_layout.addRow('Music File', self.home_music_selector)

        quick_start_box_layout.addRow(audio_header)
        quick_start_box_layout.addRow(self.audio_content_box)

        self.audio_enabled_toggle.toggled.connect(
            self.audio_content_box.setVisible
        )

        # Branding Controls - Home
        self.branding_enabled_toggle = ToggleSwitch()

        branding_header = self._toggle_section_header(
            "Branding",
            "Add logos or end cards",
            self.branding_enabled_toggle
        )

        self.branding_content_box = QGroupBox("Branding Settings")
        self.branding_content_box.setObjectName("drawer")
        self.branding_content_box.setVisible(False)

        branding_config_layout = QFormLayout(self.branding_content_box)
        branding_config_layout.setContentsMargins(10, 6, 6, 6)
        branding_config_layout.setSpacing(6)

        self.home_end_card_selector = QPushButton('Select End Card')
        self.home_end_card_selector.clicked.connect(self._select_end_card)
        self.home_end_card_selector.setObjectName("ghost")
        branding_config_layout.addRow(self.home_end_card_selector)

        quick_start_box_layout.addRow(branding_header)
        quick_start_box_layout.addRow(self.branding_content_box)

        self.branding_enabled_toggle.toggled.connect(
            self.branding_content_box.setVisible
        )

        # Preset buttons (Save/Load)
        preset_btns_row = QHBoxLayout()
        preset_btns_row.setContentsMargins(0,0,0,0)
        self.save_preset_btn = QPushButton('Save Preset')
        self.load_preset_btn = QPushButton('Load Preset')
        preset_btns_row.addWidget(self.save_preset_btn)
        preset_btns_row.addWidget(self.load_preset_btn)
        quick_start_box_layout.addRow(preset_btns_row)

        project_form.addRow(quick_start_widget)
        
        # Input Videos
        input_box = QGroupBox('Input Videos')
        input_box.setObjectName("primary")
        input_layout = QVBoxLayout(input_box)
        input_layout.setContentsMargins(5,5,5,5)
        input_layout.setSpacing(5)
        self.video_list = DropListWidget()
        self.video_list.itemSelectionChanged.connect(self._update_preview_to_selected)
        add_btn = QPushButton('Add Files')
        add_btn.clicked.connect(self._add_files)
        folder_btn = QPushButton('Select Folder')
        folder_btn.clicked.connect(self._add_folder)
        remove_btn = QPushButton('Remove Selected')
        remove_btn.clicked.connect(self._remove_selected)
        clear_btn = QPushButton('Clear List')
        clear_btn.clicked.connect(lambda: self.video_list.clear())
        input_layout.addWidget(self.video_list)
        input_layout.addWidget(add_btn)
        input_layout.addWidget(folder_btn)
        input_layout.addWidget(remove_btn)
        input_layout.addWidget(clear_btn)

        content_layout.addWidget(project_box, 2)
        content_layout.addWidget(input_box, 1)
        main_layout.addLayout(content_layout)  
        
        # Connect home controls to tabs
        self._connect_home_to_tabs()
        return root
    

    def _captions_tab(self) -> QWidget:
        root = QWidget()
        root.setObjectName("CaptionsTabContainer")
        layout = QHBoxLayout(root)

        # Use a QSplitter to allow resizing of the style panel and preview
        splitter = QSplitter(Qt.Horizontal)

        # -------------------------
        # Style panel (Left side)
        # -------------------------
        style_box = QGroupBox('Caption Style')
        style_layout = QVBoxLayout(style_box)

        # Captions Enabled (ALWAYS visible)
        captions_row = QHBoxLayout()
        captions_row.addWidget(QLabel('Captions Enabled'))

        self.captions_toggle = ToggleSwitch()
        self.captions_toggle.setChecked(False)
        captions_row.addWidget(self.captions_toggle)

        style_layout.addLayout(captions_row)
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel('AI Model'))
        self.ai_model_combo = QComboBox()
        self.ai_model_combo.addItems(['Tiny', 'Small', 'Medium', 'Large'])
        self.ai_model_combo.setCurrentIndex(1)  # Default to small
        model_row.addWidget(self.ai_model_combo)
        style_layout.addLayout(model_row)

        # Invisible Container For caption controlls
        self.iv_caption_controls_container = QWidget()
        self.iv_caption_controls_container.setObjectName("IvCaptionControlsContainer")
        iv_caption_controls_layout = QVBoxLayout(self.iv_caption_controls_container)

        # Set fixed size policy to maintain layout stability
        self.iv_caption_controls_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        iv_caption_controls_layout.setContentsMargins(0, 0, 0, 0)

        # Container for ALL caption controls (hidden when OFF)
        self.caption_controls_container = QWidget()
        caption_controls_layout = QVBoxLayout(self.caption_controls_container)
        caption_controls_layout.setContentsMargins(0, 0, 0, 0)

        # Language
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel('Language'))

        self.language_style_combo = QComboBox()
        self.language_style_combo.addItems([
            'Auto', 'English', 'Spanish', 'Chinese', 'French', 'German', 'Italian',
            'Tagalog', 'Hindi', 'Arabic', 'Portuguese', 'Russian', 'Japanese',
            'Korean', 'Vietnamese', 'Thai', 'Indonesian', 'Dutch', 'Polish',
            'Turkish', 'Hebrew', 'Swahili', 'Malay', 'Bengali', 'Punjabi',
            'Javanese', 'Tamil', 'Telugu', 'Marathi', 'Urdu', 'Persian',
            'Ukrainian', 'Greek', 'Czech', 'Hungarian', 'Swedish', 'Finnish',
            'Danish', 'Norwegian', 'Romanian', 'Bulgarian', 'Serbian', 'Croatian',
            'Slovak', 'Slovenian', 'Lithuanian', 'Latvian', 'Estonian', 'Filipino'
        ])
        lang_row.addWidget(self.language_style_combo)

        caption_controls_layout.addLayout(lang_row)

        # Separator
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.HLine)
        separator1.setFrameShadow(QFrame.Sunken)
        separator1.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        caption_controls_layout.addWidget(separator1)

        # -------------------------
        # Font
        # -------------------------
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel('Font'))

        self.font_combo = QComboBox()
        self._populate_font_combo()
        font_row.addWidget(self.font_combo)
        # Connect the font_combo to update the _font_family property
        self.font_combo.currentTextChanged.connect(self.update_font_family)

        font_row.addWidget(QLabel('Size'))
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(10, 72)
        self.font_size_spin.setValue(34)
        font_row.addWidget(self.font_size_spin)

        caption_controls_layout.addLayout(font_row)

        # Separator
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.HLine)
        separator2.setFrameShadow(QFrame.Sunken)
        separator2.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        caption_controls_layout.addWidget(separator2)

        # -------------------------
        # Font Color
        # -------------------------
        base_color_row = QHBoxLayout()
        base_color_row.addWidget(QLabel('Font Color'))

        self.base_color_btn = QPushButton('Select Font Color')
        base_color_row.addWidget(self.base_color_btn)

        caption_controls_layout.addLayout(base_color_row)

        # Separator
        separator3 = QFrame()
        separator3.setFrameShape(QFrame.HLine)
        separator3.setFrameShadow(QFrame.Sunken)
        separator3.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        caption_controls_layout.addWidget(separator3)

        # -------------------------
        # Text Toggles
        # -------------------------
        toggles_row = QHBoxLayout()

        toggles_row.addWidget(QLabel('Bold'))
        self.bold_toggle = ToggleSwitch()
        toggles_row.addWidget(self.bold_toggle)

        toggles_row.addWidget(QLabel('Italic'))
        self.italic_toggle = ToggleSwitch()
        toggles_row.addWidget(self.italic_toggle)

        toggles_row.addWidget(QLabel('Drop Shadow'))
        self.drop_shadow_toggle = ToggleSwitch()
        toggles_row.addWidget(self.drop_shadow_toggle)

        caption_controls_layout.addLayout(toggles_row)

        # Separator
        separator4 = QFrame()
        separator4.setFrameShape(QFrame.HLine)
        separator4.setFrameShadow(QFrame.Sunken)
        separator4.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        caption_controls_layout.addWidget(separator4)

        # -------------------------
        # Background Group (SINGLE source of truth)
        # -------------------------
        bg_group = QGroupBox('Background')
        bg_form = QFormLayout(bg_group)

        # Enabled and Color inline
        enabled_color_row = QHBoxLayout()
        enabled_color_row.addWidget(QLabel('Enabled'))
        self.background_toggle = ToggleSwitch()
        self.background_toggle.setChecked(False)
        enabled_color_row.addWidget(self.background_toggle)
        
        enabled_color_row.addWidget(QLabel('Color'))
        self.background_color_btn = QPushButton('Background Color')
        enabled_color_row.addWidget(self.background_color_btn)

        # Padding and Corner Radius inline
        padding_radius_row = QHBoxLayout()
        padding_radius_row.addWidget(QLabel('Padding (px)'))
        self.bg_padding_spin = QSpinBox()
        self.bg_padding_spin.setRange(0, 60)
        self.bg_padding_spin.setValue(12)
        padding_radius_row.addWidget(self.bg_padding_spin)

        padding_radius_row.addWidget(QLabel('Corner Radius (px)'))
        self.bg_radius_spin = QSpinBox()
        self.bg_radius_spin.setRange(0, 60)
        self.bg_radius_spin.setValue(12)
        padding_radius_row.addWidget(self.bg_radius_spin)

        self.bg_opacity_slider = QSlider(Qt.Horizontal)
        self.bg_opacity_slider.setRange(0, 255)
        self.bg_opacity_slider.setValue(180)

        self.safezone_toggle = ToggleSwitch()
        self.safezone_toggle.toggled.connect(self._update_preview_style)

        bg_form.addRow(enabled_color_row)
        bg_form.addRow(padding_radius_row)
        bg_form.addRow('Opacity', self.bg_opacity_slider)
        bg_form.addRow('Safe Zone', self.safezone_toggle)

        caption_controls_layout.addWidget(bg_group)

        # Separator
        separator5 = QFrame()
        separator5.setFrameShape(QFrame.HLine)
        separator5.setFrameShadow(QFrame.Sunken)
        separator5.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        caption_controls_layout.addWidget(separator5)

        # -------------------------
        # Karaoke
        # -------------------------
        karaoke_row = QHBoxLayout()
        karaoke_row.addWidget(QLabel('Karaoke'))

        self.karaoke_toggle = ToggleSwitch()
        karaoke_row.addWidget(self.karaoke_toggle)

        self.karaoke_color_btn = QPushButton('Karaoke Color')
        karaoke_row.addWidget(self.karaoke_color_btn)

        caption_controls_layout.addLayout(karaoke_row)

        # Separator
        separator6 = QFrame()
        separator6.setFrameShape(QFrame.HLine)
        separator6.setFrameShadow(QFrame.Sunken)
        separator6.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        caption_controls_layout.addWidget(separator6)

        # -------------------------
        # Caption Length Mode
        # -------------------------
        length_mode_box = QGroupBox('Caption Length Mode')
        length_mode_layout = QVBoxLayout(length_mode_box)

        # Length mode buttons row
        buttons_row = QHBoxLayout()
        self.line_mode_btn = QPushButton('Line Mode')
        self.single_word_btn = QPushButton('Single Word')
        self.movie_mode_btn = QPushButton('Movie Mode')

        for btn in [self.line_mode_btn, self.single_word_btn, self.movie_mode_btn]:
            btn.setCheckable(True)
            btn.setStyleSheet("""
                QPushButton:checked {
                    background-color: #3A8DFF;
                    color: white;
                    font-weight: bold;
                }
                QPushButton {
                    background-color: #1F2430;
                    border: 1px solid #2A2F36;
                    border-radius: 6px;
                    padding: 8px 16px;
                }
                QPushButton:hover {
                    background-color: #2A2F36;
                }
            """)

        self.line_mode_btn.setChecked(True)

        self.line_mode_btn.clicked.connect(lambda: self._on_length_mode_changed('line'))
        self.single_word_btn.clicked.connect(lambda: self._on_length_mode_changed('single_word'))
        self.movie_mode_btn.clicked.connect(lambda: self._on_length_mode_changed('movie'))

        buttons_row.addWidget(self.line_mode_btn)
        buttons_row.addWidget(self.single_word_btn)
        buttons_row.addWidget(self.movie_mode_btn)
        length_mode_layout.addLayout(buttons_row)

        # Alignment dropdown row
        alignment_row = QHBoxLayout()
        alignment_row.addWidget(QLabel('Alignment'))
        self.align_combo = QComboBox()
        self.align_combo.addItems(['Center', 'Left', 'Right'])
        alignment_row.addWidget(self.align_combo)
        length_mode_layout.addLayout(alignment_row)

        caption_controls_layout.addWidget(length_mode_box)

        # Separator
        separator7 = QFrame()
        separator7.setFrameShape(QFrame.HLine)
        separator7.setFrameShadow(QFrame.Sunken)
        separator7.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        caption_controls_layout.addWidget(separator7)

        # -------------------------
        # Caption Coordinates
        coordinates_box = QGroupBox('Caption Position')
        coordinates_layout = QHBoxLayout(coordinates_box)

        # X Coordinate (0-100%)
        x_layout = QVBoxLayout()
        x_layout.addWidget(QLabel('X Position (%)'))
        self.x_position_spin = QSpinBox()
        self.x_position_spin.setRange(0, 100)
        self.x_position_spin.setValue(50)
        self.x_position_spin.setSuffix('%')
        x_layout.addWidget(self.x_position_spin)

        # Y Coordinate (0-100%)
        y_layout = QVBoxLayout()
        y_layout.addWidget(QLabel('Y Position (%)'))
        self.y_position_spin = QSpinBox()
        self.y_position_spin.setRange(0, 100)
        self.y_position_spin.setValue(70)
        self.y_position_spin.setSuffix('%')
        y_layout.addWidget(self.y_position_spin)

        # Add both layouts to the main horizontal layout
        coordinates_layout.addLayout(x_layout)
        coordinates_layout.addLayout(y_layout)

        caption_controls_layout.addWidget(coordinates_box)

        # Add containers to style panel
        style_layout.addWidget(self.caption_controls_container)
        style_layout.addWidget(self.iv_caption_controls_container)

        # Wrap the style panel in a QScrollArea to handle overflow
        style_scroll = QScrollArea()
        style_scroll.setWidget(style_box)
        style_scroll.setWidgetResizable(True)
        style_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        style_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        style_scroll.setMinimumWidth(300)  # Minimum width for the style panel

        # -------------------------
        # Preview (Right side)
        # -------------------------
        self.caption_preview = CaptionPreview()
        self.caption_preview.setMinimumSize(400, 300)  # Minimum size for preview
        self.caption_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Connect the position changed signal to update spinboxes
        self.caption_preview.positionChanged.connect(self._on_preview_position_changed)

        # Add widgets to splitter
        splitter.addWidget(style_scroll)
        splitter.addWidget(self.caption_preview)

        # Set splitter sizes (initial ratio)
        splitter.setSizes([600, 400])

        layout.addWidget(splitter)

        # -------------------------
        # Signal wiring
        # -------------------------
        self.base_color_btn.clicked.connect(self._pick_base_color)
        self.background_color_btn.clicked.connect(self._pick_background_color)
        self.karaoke_color_btn.clicked.connect(self._pick_karaoke_color)

        # ComboBoxes
        self.font_combo.currentTextChanged.connect(self._update_preview_style)
        self.align_combo.currentTextChanged.connect(self._update_preview_style)
        self.language_style_combo.currentTextChanged.connect(self._update_preview_style)

        # SpinBoxes
        self.font_size_spin.valueChanged.connect(self._update_preview_style)
        self.bg_padding_spin.valueChanged.connect(self._update_preview_style)
        self.bg_radius_spin.valueChanged.connect(self._update_preview_style)

        # Sliders
        self.bg_opacity_slider.valueChanged.connect(self._update_preview_style)

        # ToggleSwitches
        self.bold_toggle.toggled.connect(self._update_preview_style)
        self.italic_toggle.toggled.connect(self._update_preview_style)
        self.drop_shadow_toggle.toggled.connect(self._update_preview_style)
        self.background_toggle.toggled.connect(self._update_preview_style)
        self.karaoke_toggle.toggled.connect(self._update_preview_style)

        self.captions_toggle.toggled.connect(self._update_caption_controls_visibility)

        # Coordinate spinboxes (replace the slider connections)
        self.x_position_spin.valueChanged.connect(self._update_preview_position)
        self.y_position_spin.valueChanged.connect(self._update_preview_position)

        # Initial state
        self._update_caption_controls_visibility()

        # Set the initial value of font_combo to match the default _font_family
        self.font_combo.setCurrentText(self.caption_preview._font_family)

        return root

    def _audio_tab(self) -> QWidget:
        root = QWidget()
        root.setObjectName("AudioTabContainer")
        layout = QHBoxLayout(root)

        #----------- Audio Controls -----------
        controls_box = QGroupBox('Audio Controls')
        controls_box.setObjectName("AudioControlsContainer")
        controls_layout = QVBoxLayout(controls_box)

        normalize_row = QHBoxLayout()
        normalize_row.addWidget(QLabel('Normalize Loudness'))
        self.normalize_toggle = ToggleSwitch()
        normalize_row.addWidget(self.normalize_toggle)
        controls_layout.addLayout(normalize_row)

        # Separator
        separator_audio1 = QFrame()
        separator_audio1.setFrameShape(QFrame.HLine)
        separator_audio1.setFrameShadow(QFrame.Sunken)
        separator_audio1.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        controls_layout.addWidget(separator_audio1)

        controls_layout.addWidget(QLabel('Target LUFS'))
        self.lufs_slider = QSlider(Qt.Horizontal)
        self.lufs_slider.setRange(-40, 0)
        self.lufs_slider.setValue(-14)
        self.lufs_slider.setEnabled(False)
        controls_layout.addWidget(self.lufs_slider)
        self.normalize_toggle.toggled.connect(lambda checked: self.lufs_slider.setEnabled(checked))

        # Separator
        separator_audio2 = QFrame()
        separator_audio2.setFrameShape(QFrame.HLine)
        separator_audio2.setFrameShadow(QFrame.Sunken)
        separator_audio2.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        controls_layout.addWidget(separator_audio2)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel('Voice Isolation'))
        self.voice_toggle = ToggleSwitch()
        voice_row.addWidget(self.voice_toggle)
        controls_layout.addLayout(voice_row)

        # Separator
        separator_audio3 = QFrame()
        separator_audio3.setFrameShape(QFrame.HLine)
        separator_audio3.setFrameShadow(QFrame.Sunken)
        separator_audio3.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        controls_layout.addWidget(separator_audio3)

        music_row = QHBoxLayout()
        music_row.addWidget(QLabel('Background Music'))
        self.music_toggle = ToggleSwitch()
        music_row.addWidget(self.music_toggle)
        controls_layout.addLayout(music_row)

        self.select_music_btn = QPushButton('Select Music')
        self.select_music_btn.clicked.connect(self._select_music)
        controls_layout.addWidget(self.select_music_btn)

        # Separator
        separator_audio4 = QFrame()
        separator_audio4.setFrameShape(QFrame.HLine)
        separator_audio4.setFrameShadow(QFrame.Sunken)
        separator_audio4.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        controls_layout.addWidget(separator_audio4)

        # Music Volume Control
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel('Music Volume'))
        self.music_volume_slider = QSlider(Qt.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_slider.setValue(22)  # ~0.22
        self.music_volume_slider.setEnabled(False)
        volume_row.addWidget(self.music_volume_slider)
        self.music_volume_label = QLabel('22%')
        volume_row.addWidget(self.music_volume_label)
        controls_layout.addLayout(volume_row)
        self.music_toggle.toggled.connect(lambda checked: self.music_volume_slider.setEnabled(checked))
        self.music_volume_slider.valueChanged.connect(lambda val: self.music_volume_label.setText(f'{val}%'))

        # Separator
        separator_audio5 = QFrame()
        separator_audio5.setFrameShape(QFrame.HLine)
        separator_audio5.setFrameShadow(QFrame.Sunken)
        separator_audio5.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        controls_layout.addWidget(separator_audio5)

        # Cleanup Level Control
        cleanup_row = QHBoxLayout()
        cleanup_row.addWidget(QLabel('Audio Cleanup'))
        self.cleanup_combo = QComboBox()
        self.cleanup_combo.addItems(['Off', 'Light', 'Full'])
        cleanup_row.addWidget(self.cleanup_combo)
        controls_layout.addLayout(cleanup_row)

        # --------- Preview Area ---------
        preview_box = QGroupBox('Audio Preview')
        preview_layout = QVBoxLayout(preview_box)

        # Video Preview (upper Right)
        self.video_preview = QLabel() 
        self.video_preview.setMinimumSize(320, 180)
        self.video_preview.setMaximumSize(600, 340)
        self.video_preview.setStyleSheet("background-color: #222222; border: 1px solid #444444;")
        preview_layout.addWidget(self.video_preview)

        # Audio Waveform (lower Right)
        self.audio_waveform = QLabel()
        self.audio_waveform.setMinimumSize(320, 90)
        self.audio_waveform.setMaximumSize(600, 160)
        self.audio_waveform.setStyleSheet("background-color: #222222; border: 1px solid #444444;")
        preview_layout.addWidget(self.audio_waveform)

        # add to main layout
        layout.addWidget(controls_box, 3)
        layout.addWidget(preview_box, 2)

        return root

    def _branding_tab(self) -> QWidget:
        root = QWidget()
        root.setObjectName("BrandingTabContainer")
        layout = QHBoxLayout(root)

        # Use a QSplitter to allow resizing of the configuration panel and preview
        splitter = QSplitter(Qt.Horizontal)

        # -------------------------
        # Branding Configuration (Left Column)
        # -------------------------
        branding_box = QGroupBox('Branding Configuration')
        branding_layout = QVBoxLayout(branding_box)

        # Branding Enabled (ALWAYS visible)
        branding_enabled_row = QHBoxLayout()
        branding_enabled_row.addWidget(QLabel('Branding Enabled'))
        
        self.branding_toggle = ToggleSwitch()
        self.branding_toggle.setChecked(False)
        branding_enabled_row.addWidget(self.branding_toggle)
        
        branding_layout.addLayout(branding_enabled_row)

        # Invisible Container For branding controlls
        self.iv_branding_controls_container = QWidget()
        self.iv_branding_controls_container.setObjectName("IvBrandingControlsContainer")
        iv_branding_controls_layout = QVBoxLayout(self.iv_branding_controls_container)
        
        # Set fixed size policy to maintain layout stability
        self.iv_branding_controls_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        iv_branding_controls_layout.setContentsMargins(0, 0, 0, 0)

        # Container for ALL branding controls (hidden when OFF)
        self.branding_controls_container = QWidget()
        branding_controls_layout = QVBoxLayout(self.branding_controls_container)
        branding_controls_layout.setContentsMargins(0, 0, 0, 0)

        # -------------------------
        # Branding Type Selection
        # -------------------------
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel('Branding Type'))
        
        self.brand_type = QComboBox()
        self.brand_type.addItems(['Intro', 'Watermark', 'End Card'])
        self.brand_type.setCurrentIndex(2)  # Default to End Card
        type_row.addWidget(self.brand_type)
        
        branding_controls_layout.addLayout(type_row)

        # Separator
        separator_brand1 = QFrame()
        separator_brand1.setFrameShape(QFrame.HLine)
        separator_brand1.setFrameShadow(QFrame.Sunken)
        separator_brand1.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        branding_controls_layout.addWidget(separator_brand1)

        # -------------------------
        # Media Selection
        # -------------------------
        media_group = QGroupBox('Media Selection')
        media_form = QFormLayout(media_group)
        
        self.brand_logo_btn = QPushButton('Select Logo/Image')
        
        self.brand_video_btn = QPushButton('Select Video')
        
        media_form.addRow('Logo/Image', self.brand_logo_btn)
        media_form.addRow('Video', self.brand_video_btn)
        
        branding_controls_layout.addWidget(media_group)

        # Separator
        separator_brand2 = QFrame()
        separator_brand2.setFrameShape(QFrame.HLine)
        separator_brand2.setFrameShadow(QFrame.Sunken)
        separator_brand2.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        branding_controls_layout.addWidget(separator_brand2)

        # -------------------------
        # Branding Content
        # -------------------------
        '''Commented out for now - used for future branding'''
        #content_group = QGroupBox('Branding Content')
        #content_form = QFormLayout(content_group)
        
        #self.brand_headline = QLineEdit()
        #self.brand_subtext = QLineEdit()
        
        #content_form.addRow('Headline', self.brand_headline)
        #content_form.addRow('Subtext', self.brand_subtext)
        
        # branding_controls_layout.addWidget(content_group)

        # Separator
        separator_brand3 = QFrame()
        separator_brand3.setFrameShape(QFrame.HLine)
        separator_brand3.setFrameShadow(QFrame.Sunken)
        separator_brand3.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        branding_controls_layout.addWidget(separator_brand3)

        # -------------------------
        # Branding Position
        # -------------------------
        position_group = QGroupBox('Branding Position')
        position_layout = QHBoxLayout(position_group)
        
        # X Coordinate (0-100%)
        x_layout = QVBoxLayout()
        x_layout.addWidget(QLabel('X Position (%)'))
        self.brand_x_position_spin = QSpinBox()
        self.brand_x_position_spin.setRange(0, 100)
        self.brand_x_position_spin.setValue(50)
        self.brand_x_position_spin.setSuffix('%')
        x_layout.addWidget(self.brand_x_position_spin)
        
        # Y Coordinate (0-100%)
        y_layout = QVBoxLayout()
        y_layout.addWidget(QLabel('Y Position (%)'))
        self.brand_y_position_spin = QSpinBox()
        self.brand_y_position_spin.setRange(0, 100)
        self.brand_y_position_spin.setValue(70)
        self.brand_y_position_spin.setSuffix('%')
        y_layout.addWidget(self.brand_y_position_spin)
        
        # Add both layouts to the main horizontal layout
        position_layout.addLayout(x_layout)
        position_layout.addLayout(y_layout)
        
        branding_controls_layout.addWidget(position_group)

        # Separator
        separator_brand4 = QFrame()
        separator_brand4.setFrameShape(QFrame.HLine)
        separator_brand4.setFrameShadow(QFrame.Sunken)
        separator_brand4.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        branding_controls_layout.addWidget(separator_brand4)

        # -------------------------
        # Branding Size/Opacity
        # -------------------------
        size_group = QGroupBox('Branding Size & Opacity')
        size_form = QFormLayout(size_group)
        
        # Width and Opacity inline
        width_opacity_row = QHBoxLayout()
        width_opacity_row.addWidget(QLabel('Width (px)'))
        self.brand_width_spin = QSpinBox()
        self.brand_width_spin.setRange(50, 500)
        self.brand_width_spin.setValue(200)
        width_opacity_row.addWidget(self.brand_width_spin)

        width_opacity_row.addWidget(QLabel('Opacity (%)'))
        self.brand_opacity_slider = QSlider(Qt.Horizontal)
        self.brand_opacity_slider.setRange(0, 100)
        self.brand_opacity_slider.setValue(100)
        width_opacity_row.addWidget(self.brand_opacity_slider)

        size_form.addRow(width_opacity_row)
        
        branding_controls_layout.addWidget(size_group)

        # Add containers to branding panel
        branding_layout.addWidget(self.branding_controls_container)
        branding_layout.addWidget(self.iv_branding_controls_container)

        # Wrap the branding panel in a QScrollArea to handle overflow
        branding_scroll = QScrollArea()
        branding_scroll.setWidget(branding_box)
        branding_scroll.setWidgetResizable(True)
        branding_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        branding_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        branding_scroll.setMinimumWidth(300)  # Minimum width for the branding panel

        # -------------------------
        # Branding Preview (Right Column)
        # -------------------------
        self.branding_preview = BrandingPreview()
        self.branding_preview.setMinimumSize(400, 300)  # Minimum size for preview
        self.branding_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # Connect the position changed signal to update spinboxes
        self.branding_preview.positionChanged.connect(self._on_branding_preview_position_changed)

        # Add widgets to splitter
        splitter.addWidget(branding_scroll)
        splitter.addWidget(self.branding_preview)

        # Set splitter sizes (initial ratio)
        splitter.setSizes([600, 400])

        layout.addWidget(splitter)

        # -------------------------
        # Signal wiring
        # -------------------------
        self.brand_logo_btn.clicked.connect(self._select_logo)
        self.brand_video_btn.clicked.connect(self._select_branding_video)
        
        # SpinBoxes
        self.brand_x_position_spin.valueChanged.connect(self._update_branding_preview_position)
        self.brand_y_position_spin.valueChanged.connect(self._update_branding_preview_position)
        self.brand_width_spin.valueChanged.connect(self._update_branding_preview_style)
        self.brand_opacity_slider.valueChanged.connect(self._update_branding_preview_style)
        
        # ComboBox
        self.brand_type.currentTextChanged.connect(self._update_branding_preview_style)
        
        # ToggleSwitch
        self.branding_toggle.toggled.connect(self._update_branding_controls_visibility)

        # Initial state
        self._update_branding_controls_visibility()

        return root


    def _edit_tab(self) -> QWidget:
        root = QWidget()
        root.setObjectName("EditTabContainer")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # === Splitter for controls (left) and preview (right) ===
        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("EditTabSplitter")
        splitter.setHandleWidth(8)

        # ---------------- Left: Controls ----------------
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(8, 8, 8, 8)
        controls_layout.setSpacing(8)

        # Row of action buttons
        btn_row = QHBoxLayout()
        load_video_btn = QPushButton("Load Video")
        load_video_btn.clicked.connect(self._load_edit_video)
        btn_row.addWidget(load_video_btn)

        self.btn_refresh_preview = QPushButton("Refresh Preview")
        self.btn_refresh_preview.clicked.connect(self._refresh_edit_preview)
        self.btn_refresh_preview.setEnabled(False)
        btn_row.addWidget(self.btn_refresh_preview)

        controls_layout.addLayout(btn_row)

        # Caption list
        caption_lbl = QLabel("Captions *Re-Run TrueEditor after edits are complete")
        caption_lbl.setFont(QFont(caption_lbl.font().family(), pointSize=caption_lbl.font().pointSize(), weight=QFont.DemiBold))
        controls_layout.addWidget(caption_lbl)

        self.caption_list = QListWidget()
        self.caption_list.itemDoubleClicked.connect(self._edit_caption_item)
        self.caption_list.currentRowChanged.connect(self._on_caption_row_changed)
        self.caption_list.setMinimumHeight(160)
        self.caption_list.setAlternatingRowColors(True)
        controls_layout.addWidget(self.caption_list, 1)  # give it stretch

        # Raw .ass editor
        raw_lbl = QLabel("Raw Caption Editor")
        raw_lbl.setFont(QFont(raw_lbl.font().family(), pointSize=raw_lbl.font().pointSize(), weight=QFont.DemiBold))
        controls_layout.addWidget(raw_lbl)

        self.edit_ass_editor = QTextEdit()
        self.edit_ass_editor.setPlaceholderText("Raw Captions content...")
        self.edit_ass_editor.textChanged.connect(self._on_ass_text_changed)
        self.edit_ass_editor.setEnabled(False)
        controls_layout.addWidget(self.edit_ass_editor, 2)

        # Save button row
        action_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Captions")
        self.save_btn.clicked.connect(self._save_edit_ass)
        self.save_btn.setEnabled(False)
        action_row.addWidget(self.save_btn)

        # optional: revert button
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self._revert_edit_ass)
        self.revert_btn.setEnabled(False)
        action_row.addWidget(self.revert_btn)

        controls_layout.addLayout(action_row)

        splitter.addWidget(controls)

        # ---------------- Right: Preview ----------------
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(8)

        prev_hdr = QHBoxLayout()
        prev_lbl = QLabel("Preview")
        prev_lbl.setFont(QFont(prev_lbl.font().family(), pointSize=prev_lbl.font().pointSize(), weight=QFont.DemiBold))
        prev_hdr.addWidget(prev_lbl)
        prev_hdr.addStretch(1)
        preview_layout.addLayout(prev_hdr)

        # Your preview surface (replace with your existing QWidget/label/canvas)
        # We'll use a QLabel placeholder called self.edit_preview_view
        self.edit_preview_view = QLabel("No preview")
        self.edit_preview_view.setAlignment(Qt.AlignCenter)
        self.edit_preview_view.setFrameShape(QFrame.StyledPanel)
        self.edit_preview_view.setMinimumSize(QSize(320, 180))
        self.edit_preview_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_layout.addWidget(self.edit_preview_view, 1)

        splitter.addWidget(preview_panel)

        # Initial sizes: Left 2/3, Right 1/3 (you can tweak based on your window width)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([800, 400])

        layout.addWidget(splitter)
        return root

    def _run_tab(self) -> QWidget:
        root = QWidget()
        root.setObjectName("RunTabContainer")
        layout = QVBoxLayout(root)
        
        # Batch Summary Section
        summary_group = QGroupBox('Batch Summary')
        summary_layout = QFormLayout(summary_group)
        
        self.lbl_videos = QLabel('Videos: 0')
        self.lbl_est_time = QLabel('Estimated Time: --')
        self.lbl_current_file = QLabel('Current: --')
        self.lbl_status = QLabel('Status: Ready')
        
        summary_layout.addRow('Videos:', self.lbl_videos)
        summary_layout.addRow('Estimated Time:', self.lbl_est_time)
        summary_layout.addRow('Current File:', self.lbl_current_file)
        summary_layout.addRow('Status:', self.lbl_status)
        
        layout.addWidget(summary_group)

        # Separator
        separator_run1 = QFrame()
        separator_run1.setFrameShape(QFrame.HLine)
        separator_run1.setFrameShadow(QFrame.Sunken)
        separator_run1.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        layout.addWidget(separator_run1)
        
        # Progress Section
        progress_group = QGroupBox('Progress')
        progress_layout = QVBoxLayout(progress_group)
        
        # Overall progress bar
        self.progress_overall = QProgressBar()
        self.progress_overall.setRange(0, 100)
        self.progress_overall.setValue(0)
        self.progress_overall.setTextVisible(True)
        progress_layout.addWidget(QLabel('Overall Progress'))
        progress_layout.addWidget(self.progress_overall)
        
        # Task-specific progress
        self.progress_task = QProgressBar()
        self.progress_task.setRange(0, 100)
        self.progress_task.setValue(0)
        self.progress_task.setTextVisible(True)
        progress_layout.addWidget(QLabel('Current Task Progress'))
        progress_layout.addWidget(self.progress_task)
        
        # Pipeline stages
        stages_layout = QHBoxLayout()
        self.stage_analysis = QLabel('📊 Analysis')
        self.stage_transcription = QLabel('📝 Transcription')
        self.stage_captions = QLabel('🎬 Captions')
        self.stage_audio = QLabel('🎵 Audio')
        self.stage_branding = QLabel('🏷️ Branding')
        
        stages_layout.addWidget(self.stage_analysis)
        stages_layout.addWidget(self.stage_transcription)
        stages_layout.addWidget(self.stage_captions)
        stages_layout.addWidget(self.stage_audio)
        stages_layout.addWidget(self.stage_branding)
        
        progress_layout.addLayout(stages_layout)
        
        layout.addWidget(progress_group)

        # Separator
        separator_run2 = QFrame()
        separator_run2.setFrameShape(QFrame.HLine)
        separator_run2.setFrameShadow(QFrame.Sunken)
        separator_run2.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        layout.addWidget(separator_run2)
        
        # File Progress Section
        file_progress_group = QGroupBox('File Progress')
        file_progress_layout = QVBoxLayout(file_progress_group)
        
        self.file_progress_list = QListWidget()
        self.file_progress_list.setMaximumHeight(140)
        file_progress_layout.addWidget(self.file_progress_list)
        
        layout.addWidget(file_progress_group)

        # Separator
        separator_run3 = QFrame()
        separator_run3.setFrameShape(QFrame.HLine)
        separator_run3.setFrameShadow(QFrame.Sunken)
        separator_run3.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        layout.addWidget(separator_run3)
        
        # Log Section
        log_group = QGroupBox('Processing Log')
        log_layout = QVBoxLayout(log_group)
        
        self.run_log = QListWidget()
        self.run_log.setMaximumHeight(220)
        log_layout.addWidget(self.run_log)
        
        # Log controls
        log_controls = QHBoxLayout()
        self.btn_clear_log = QPushButton('Clear Log')
        log_controls.addWidget(self.btn_clear_log)
        log_layout.addLayout(log_controls)
        
        layout.addWidget(log_group)

        # Separator
        separator_run4 = QFrame()
        separator_run4.setFrameShape(QFrame.HLine)
        separator_run4.setFrameShadow(QFrame.Sunken)
        separator_run4.setStyleSheet("background-color: #262A31; margin: 8px 0;")
        layout.addWidget(separator_run4)
        
        # Control Buttons
        btns = QVBoxLayout()
        control_row = QHBoxLayout()
        self.run_test_btn = QPushButton('Edit First Video')
        self.run_batch_btn = QPushButton('Edit Batch')
        self.btn_stop = QPushButton('Stop')
        self.btn_stop.setEnabled(False)
        user_row = QHBoxLayout()
        self.run_clear_transcriptions_btn = QPushButton('Clear Transcriptions')
        self.run_clear_transcriptions_btn.clicked.connect(self._clear_transcriptions)

        self.open_output_btn = QPushButton('Open output Folder')
        self.open_output_btn.clicked.connect(self._open_output_folder)
        
        control_row.addWidget(self.run_test_btn)
        control_row.addWidget(self.run_batch_btn)
        control_row.addWidget(self.btn_stop)
        user_row.addWidget(self.run_clear_transcriptions_btn)
        user_row.addWidget(self.open_output_btn)
        btns.addLayout(control_row)
        btns.addLayout(user_row)
        layout.addLayout(btns)

        # Connect buttons to backend hooks (pipeline)
        self.run_test_btn.clicked.connect(lambda: self._start_pipeline(test=True))
        self.run_batch_btn.clicked.connect(lambda: self._start_pipeline(test=False))
        self.btn_stop.clicked.connect(self._stop_pipeline)
        self.btn_clear_log.clicked.connect(self._clear_log)
        
        return root

    # ---------- UI Actions ----------
    def _open_output_folder(self):
        path = self.output_path.text().strip()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.information(self, 'Output Folder', 'Set an output folder first.')

    def _select_output_folder(self):
        default_folder = str(Path() / '..' / 'final' / 'edited_videos')
        folder = QFileDialog.getExistingDirectory(self, 'Select Output Folder', default_folder)
        if folder:
            self.output_path.setText(folder)

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, 'Add Video Files', '', 'Video Files (*.mp4 *.mov *.mkv);;All Files (*)')
        for f in files:
            self.video_list.addItem(f)
        self.lbl_videos.setText(f'Videos: {self.video_list.count()}')  
        # Select the last added and auto-update preview    # Select the last added and auto-update preview
        self.video_list.setCurrentRow(self.video_list.count() - 1)
    
    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Add Folder Containing Videos",
            "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if not folder:
            return
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
        existing = {
            self.video_list.item(i).text()
            for i in range(self.video_list.count())
        }
        added = 0
        for path in Path(folder).rglob("*"):
            if path.suffix.lower() in video_exts:
                path_str = str(path)
                if path_str not in existing:
                    self.video_list.addItem(path_str)
                    added += 1
        if added:
            self.lbl_videos.setText(f"Videos: {self.video_list.count()}")
            self.video_list.setCurrentRow(self.video_list.count() - 1)


    def _remove_selected(self):
        for item in self.video_list.selectedItems():
            self.video_list.takeItem(self.video_list.row(item))
        self.lbl_videos.setText(f'Videos: {self.video_list.count()}')

    def _select_music(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select Music', '', 'Audio Files (*.mp3 *.wav);;All Files (*)')
        if path:
            self.select_music_btn.setText(Path(path).name)
            self.select_music_btn.setToolTip(path)

    def _select_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select Logo', '', 'Images (*.png *.jpg *.jpeg);;All Files (*)')
        if path:
            self.brand_logo_btn.setText(Path(path).name)
            self.brand_logo_btn.setToolTip(path)

    def _select_watermark(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Select Watermark', '', 
            'Images (*.png *.jpg *.jpeg *.gif);;Videos (*.mp4 *.mov *.mkv);;All Files (*)'
        )
        if path:
            self.brand_logo_btn.setText(Path(path).name)
            self.brand_logo_btn.setToolTip(path)

    def _select_branding_video(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select Branding Video', '', 'Video Files (*.mp4 *.mov *.mkv);;All Files (*)')
        if path:
            self.brand_video_btn.setText(Path(path).name)
            self.brand_video_btn.setToolTip(path)
            # Update preview with video frame
            self._update_branding_preview_from_video(path)
    

    def _clear_transcriptions(self):
        """Clear all saved transcriptions after confirmation."""
        reply = QMessageBox.question(
            self, 'Clear Transcriptions',
            'Are you sure you want to clear all saved transcriptions? This action cannot be undone.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                # Ensure the transcriptions directory exists
                self._ensure_transcriptions_dir()
                
                # Delete all .ass files in the transcriptions directory
                for ass_file in self.transcriptions_dir.glob('*.ass'):
                    ass_file.unlink()
                
                QMessageBox.information(
                    self, 'Transcriptions Cleared',
                    'All saved transcriptions have been successfully cleared.'
                )
            except Exception as e:
                QMessageBox.warning(
                    self, 'Error',
                    f'Failed to clear transcriptions: {str(e)}'
                )
    

    
    def _select_end_card(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select End Card',
            '',
            'Images (*.mp4 *.mov *.mkv);;Videos (*.png *.jpg *.jpeg)'
        )
        if not path:
            return

        self.home_end_card_selector.setText(Path(path).name)
        self.home_end_card_selector.setToolTip(path)

        self._sync_branding_to_tabs()


    def _update_branding_controls_visibility(self):
        self.branding_controls_container.setVisible(
        self.branding_toggle.isChecked()
    )
    
    def _update_audio_preview(self):
        """Update audio preview when settings change."""
        # Update video preview with first frame
        if self.video_list.count() > 0:
            current_item = self.video_list.currentItem()
            if current_item:
                video_path = current_item.text()
                # Extract first frame and update preview
                def task():
                    return grab_first_frame(video_path)

                def on_result(pix):
                    if isinstance(pix, QPixmap) and not pix.isNull():
                        self.branding_preview.bg_pixmap = pix
                    else:
                        self.run_log.addItem("Branding video preview failed.")
                        self.branding_preview.bg_pixmap = QPixmap()
                    self.branding_preview.update()


                worker = Worker(task)
                worker.signals.result.connect(on_result)
                self.pool.start(worker)

    def _update_branding_preview_position(self):
        """Update branding preview position from spinbox values."""
        x_norm = self.brand_x_position_spin.value() / 100.0
        y_norm = self.brand_y_position_spin.value() / 100.0
        
        self.branding_preview._x = x_norm
        self.branding_preview._y = y_norm
        self.branding_preview.update()

    def _update_branding_preview_style(self):
        """Update branding preview style from controls."""
        self.branding_preview._brand_type = self.brand_type.currentText()
        #self.branding_preview._headline = self.brand_headline.text()
        #self.branding_preview._subtext = self.brand_subtext.text()
        self.branding_preview._width = self.brand_width_spin.value()
        self.branding_preview._opacity = self.brand_opacity_slider.value()
        self.branding_preview.update()
    
    def _update_branding_preview_style(self):
        """Update branding preview style from controls."""
        self.branding_preview._brand_type = self.brand_type.currentText()
        #self.branding_preview._headline = self.brand_headline.text()
        #self.branding_preview._subtext = self.brand_subtext.text()
        self.branding_preview._width = self.brand_width_spin.value()
        self.branding_preview._opacity = self.brand_opacity_slider.value()
        self.branding_preview.update()

    def _update_branding_preview_from_logo(self, path):
        pix = QPixmap(path)
        if pix.isNull():
            return

        self.branding_preview.media_type = 'image'
        self.branding_preview.bg_pixmap = pix
        self.branding_preview.update()

    def _on_branding_preview_position_changed(self, x_norm: float, y_norm: float):
        """Update spinbox values when branding is dragged in preview."""
        x_percent = int(x_norm * 100)
        y_percent = int(y_norm * 100)
        
        self.brand_x_position_spin.blockSignals(True)
        self.brand_y_position_spin.blockSignals(True)
        
        self.brand_x_position_spin.setValue(x_percent)
        self.brand_y_position_spin.setValue(y_percent)
        
        self.brand_x_position_spin.blockSignals(False)
        self.brand_y_position_spin.blockSignals(False)

    def _update_branding_preview_from_video(self, path):
        pix = grab_first_frame(path)

        if not isinstance(pix, QPixmap) or pix.isNull():
            self.run_log.addItem("Failed to load video preview.")
            return

        self.branding_preview.media_type = 'video'
        self.branding_preview.bg_pixmap = pix
        self.branding_preview.update()

    def _update_preview_to_selected(self):
        """Automatically set preview to the first frame of the selected video; fallback if it fails."""
        item = self.video_list.currentItem()
        if not item:
            return
        path = item.text()
        def task(report=None):
            pix = grab_first_frame(path)
            return (pix, path)

        def on_result(result):
            pix, vpath = result
            if isinstance(pix, QPixmap):
                self.caption_preview.bg_pixmap = pix
            else:
                self.caption_preview.bg_pixmap = QPixmap()
            self.caption_preview.update()
            # Cache native resolution (optional)
            try:
                w, h = get_video_resolution(vpath)
                self._last_selected_video_resolution = (w, h)
                self.run_log.addItem(f"Preview set to first frame ({w}x{h}).")
            except Exception as e:
                self.run_log.addItem(f"Resolution read error: {e}")

        def on_error(err):
            # Fallback image
            self.run_log.addItem(f"Preview extraction failed: {err}. Using fallback image.")
            fallback = (Path(__file__).parent / "preview" / "Example.jpg")
            self.caption_preview.bg_pixmap = QPixmap(str(fallback)) if fallback.exists() else QPixmap()
            self.caption_preview.update()

        worker = Worker(task)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        self.pool.start(worker)

    # ---------- Caption style wiring ----------

    def _populate_font_combo(self):
        from PySide6.QtGui import QFontDatabase
        import platform

        db = QFontDatabase()
        installed = set(db.families())

        # Curated native lists
        win_shortlist = [
            "Segoe UI", "Arial", "Tahoma", "Verdana",
            "Georgia", "Times New Roman", "Calibri", "Cambria", "Consolas"
        ]
        mac_shortlist = [
            "Helvetica", "Helvetica Neue", "Arial", "Times",
            "Georgia", "Verdana", "Menlo", "Courier", ".SF NS Text", ".SF NS Display"
        ]

        system = platform.system()
        preferred = win_shortlist if system == "Windows" else mac_shortlist

        # Filter to what's installed; keep order
        available = [f for f in preferred if f in installed]

        # Sensible fallbacks if nothing matched for some reason
        if not available:
            # Try widely available cross-platform standbys
            fallbacks = ["Arial", "Helvetica", "Verdana", "Times New Roman", "Georgia"]
            available = [f for f in fallbacks if f in installed]

        # Last resort: show a compact sorted list of all installed families
        if not available:
            available = sorted(installed)
        self.font_combo.clear()
        self.font_combo.addItems(available)


    def _pick_base_color(self):
        from PySide6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(self.caption_preview._base_color, self, 'Select Base Color')
        if color.isValid():
            self.caption_preview._base_color = color
            self.caption_preview.update()

    def _pick_background_color(self):
        from PySide6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(self.caption_preview._background_color, self, 'Select Background Color')
        if color.isValid():
            self.caption_preview._background_color = color
            self.caption_preview.update()

    def _pick_karaoke_color(self):
        from PySide6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(self.caption_preview._karaoke_color, self, 'Select Karaoke Color')
        if color.isValid():
            self.caption_preview._karaoke_color = color
            self.caption_preview.update()

    def _update_preview_style(self):
        # Text style
        self.caption_preview._font_size = self.font_size_spin.value()
        self.caption_preview._bold = self.bold_toggle.isChecked()
        self.caption_preview._italic = self.italic_toggle.isChecked()

        # Visual effects
        self.caption_preview.drop_shadow_enabled = self.drop_shadow_toggle.isChecked()
        self.caption_preview.background_enabled = self.background_toggle.isChecked()
        self.caption_preview.karaoke_enabled = self.karaoke_toggle.isChecked()
        self.caption_preview.show_safezone = self.safezone_toggle.isChecked()

        # Background geometry + opacity (keep preview and outgoing style identical)
        self.caption_preview.bg_padding = self.bg_padding_spin.value()
        self.caption_preview.bg_corner_radius = self.bg_radius_spin.value()
        self.caption_preview._background_opacity = self.bg_opacity_slider.value()

        # Alignment
        align_text = self.align_combo.currentText()
        if align_text == "Center":
            self.caption_preview._align = Qt.AlignCenter
        elif align_text == "Right":
            self.caption_preview._align = Qt.AlignRight
        else:
            self.caption_preview._align = Qt.AlignLeft

        self.caption_preview.update()

    def update_font_family(self, font_family):
        self.caption_preview._font_family = font_family
        self.caption_preview.update()

    def _on_length_mode_changed(self, mode: str):
        # Reset all buttons
        for btn in [self.line_mode_btn, self.single_word_btn, self.movie_mode_btn]:
            btn.setChecked(False)
        # Check the selected button
        if mode == 'line':
            self.line_mode_btn.setChecked(True)
        elif mode == 'single_word':
            self.single_word_btn.setChecked(True)
        elif mode == 'movie':
            self.movie_mode_btn.setChecked(True)
        # Update preview immediately
        self.caption_preview.update()

    # ---------- Backend connection ----------
    def connect_backend(self, **hooks: Callable):
        '''Register backend hooks.
        Supported keys: analyze, transcribe, captions, audio, branding, pipeline
        Each callable may optionally accept a 'report(percent, message)' kwarg for progress.
        '''
        self.backend.update(hooks)

    def _start_pipeline(self, test: bool):
        fn = self.backend.get('pipeline')
        if not fn:
            QMessageBox.warning(self, 'No backend', 'Connect a pipeline backend via connect_backend(pipeline=...)')
            return
        args = {
            'files': [self.video_list.item(i).text() for i in range(self.video_list.count())],
            'output_folder': self.output_path.text().strip(),
            'language': self.language_style_combo.currentText(),
            'platform': self.platform_preset.currentText(),
            'caption_style': self._collect_caption_style(),
            'audio_settings': self._collect_audio_settings(),
            'branding': self._collect_branding_settings(),
            'test': test,
        }
        self._run_job(fn, **args)

    def _collect_caption_style(self) -> Dict[str, Any]:
        # Convert widget-normalized spinbox values into image-normalized coords
        x_widget = self.x_position_spin.value() / 100.0
        y_widget = self.y_position_spin.value() / 100.0
        try:
            x_img, y_img = self.caption_preview.widget_to_image_normalized(x_widget, y_widget)
        except Exception:
            x_img, y_img = x_widget, y_widget

        return {
            'font': self.font_combo.currentText(),
            'size': self.font_size_spin.value(),
            'bold': self.bold_toggle.isChecked(),
            'italic': self.italic_toggle.isChecked(),
            'drop_shadow': self.drop_shadow_toggle.isChecked(),

            'background': {
                'enabled': self.background_toggle.isChecked(),
                'color': self.caption_preview._background_color.name(),
                'padding': self.bg_padding_spin.value(),
                'corner_radius': self.bg_radius_spin.value(),
                'opacity': self.bg_opacity_slider.value(),
            },

            'karaoke': {
                'enabled': self.karaoke_toggle.isChecked(),
                'color': self.caption_preview._karaoke_color.name(),
            },

            'font_color': self.caption_preview._base_color.name(),

            'position': {
                # position is now normalized to the actual displayed image (0..1)
                'x': x_img,
                'y': y_img,
            },

            'length_mode': self._get_length_mode(),
            'enabled': self.captions_toggle.isChecked(),
            'model_name': self.ai_model_combo.currentText().lower(),
        }

    def _get_length_mode(self) -> str:
        if self.single_word_btn.isChecked():
            return 'single_word'
        elif self.movie_mode_btn.isChecked():
            return 'movie'
        else:
            return 'line'  # default

    def _collect_audio_settings(self) -> Dict[str, Any]:
        return {
            'normalize': self.normalize_toggle.isChecked(),
            'target_lufs': self.lufs_slider.value(),
            'voice_isolation': self.voice_toggle.isChecked(),
            'cleanup_level': self.cleanup_combo.currentText().lower(),
            'music_volume': self.music_volume_slider.value() / 100.0,
            'background_music': {
                'enabled': self.music_toggle.isChecked(),
                'path': self.select_music_btn.toolTip() or '',
            },
        }

    def _collect_branding_settings(self) -> Dict[str, Any]:
        # Convert widget-normalized spinbox values into image-normalized coords
        x_widget = self.brand_x_position_spin.value() / 100.0
        y_widget = self.brand_y_position_spin.value() / 100.0
        try:
            x_img, y_img = self.branding_preview.widget_to_image_normalized(x_widget, y_widget)
        except Exception:
            x_img, y_img = x_widget, y_widget

        return {
            'enabled': self.branding_toggle.isChecked(),
            'type': self.brand_type.currentText(),
            #headline': self.brand_headline.text(),
            #'subtext': self.brand_subtext.text(),
            'logo_path': self.brand_logo_btn.toolTip() or '',
            'video_path': self.brand_video_btn.toolTip() or '',
            'position': {
                'x': x_img,
                'y': y_img,
            },
            'width': self.brand_width_spin.value(),
            'opacity': self.brand_opacity_slider.value() / 100.0,
        }

    def _run_job(self, fn: Callable, **kwargs):
        worker = Worker(fn, **kwargs)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.log.connect(self._on_log)
        worker.signals.result.connect(self._on_result)
        worker.signals.error.connect(self._on_error)
        worker.signals.finished.connect(self._on_finished)
        
        # Enhanced progress tracking - pass a custom report function
        def enhanced_report(percent: Optional[int] = None, message: Optional[str] = None):
            if percent is not None:
                self.progress_task.setValue(int(percent))
            if message:
                # Parse special messages for file and stage tracking
                if message.startswith('FILE_PROGRESS:'):
                    parts = message.split(':', 2)
                    if len(parts) >= 3:
                        file_idx = int(parts[1])
                        status = parts[2]
                        self._update_file_progress(file_idx, status)
                elif message.startswith('STAGE_UPDATE:'):
                    parts = message.split(':', 2)
                    if len(parts) >= 3:
                        stage = parts[1]
                        status = parts[2]
                        self._update_stage_status(stage, status)
                        self.lbl_status.setText(f"Status: {stage.title()} - {status.title()}")
                else:
                    self._on_log(message)
        
        # If backend supports enhanced reporting, pass it
        sig = inspect.signature(fn)
        if 'report' in sig.parameters:
            kwargs['report'] = enhanced_report
            
        self.pool.start(worker)

    # ---------- Worker signal handlers ----------
    def _on_progress(self, percent: int):
        self.progress.setValue(percent)
        self.progress_overall.setValue(percent)
        self.status.showMessage(f'Working… {percent}%')

    def _on_log(self, message: str):
        """Enhanced logging with timestamps, categorization, and progress tracking."""
        timestamp = datetime.datetime.now().strftime('%H:%M:%S')
    
        # Categorize messages for better tracking
        if 'Error' in message or 'Failed' in message or 'Error:' in message:
            formatted_message = f"❌ [{timestamp}] {message}"
            self.run_log.addItem(formatted_message)
        elif 'Starting' in message or 'Processing' in message or 'Job started' in message:
            formatted_message = f"🚀 [{timestamp}] {message}"
            self.run_log.addItem(formatted_message)
        elif 'Finished' in message or 'Completed' in message or 'Job finished' in message:
            formatted_message = f"✅ [{timestamp}] {message}"
            self.run_log.addItem(formatted_message)
        elif 'Warning' in message:
            formatted_message = f"⚠️ [{timestamp}] {message}"
            self.run_log.addItem(formatted_message)
        else:
            formatted_message = f"ℹ️ [{timestamp}] {message}"
            self.run_log.addItem(formatted_message)
    
        # Parse and update task progress if the message contains task progress information
        if 'Task Progress:' in message:
            try:
                task_progress_value = int(message.split('Task Progress:')[1].strip().split('%')[0])
                self.progress_task.setValue(task_progress_value)
            except (ValueError, IndexError):
                pass
    
        # Parse and update overall progress if the message contains overall progress information
        if 'Overall Progress:' in message:
            try:
                overall_progress_value = int(message.split('Overall Progress:')[1].strip().split('%')[0])
                self.progress_overall.setValue(overall_progress_value)
            except (ValueError, IndexError):
                pass
    
        self.run_log.scrollToBottom()
        
    def _on_result(self, result: Any):
        config = {
            'platform': self.platform_preset.currentText(),
            'language': self.language_style_combo.currentText(),
            'output_folder': self.output_path.text().strip(),
        }
        self.settings.setValue('last_run', config)
        self.status.showMessage('Done')
        
        # Update file progress to show completion
        for i in range(self.file_progress_list.count()):
            item = self.file_progress_list.item(i)
            if 'Processing' in item.text() or 'In Progress' in item.text():
                item.setText(item.text().replace('Processing', '✅ Completed'))

    def _on_error(self, err: str):
        self.run_log.addItem(f'❌ Error: {err}')
        QMessageBox.critical(self, 'Error', err)

    def _on_finished(self):
        self.progress.setValue(0)
        self.progress_overall.setValue(0)
        self.progress_task.setValue(0)
        self.status.showMessage('Batch processing completed')
        self.btn_stop.setEnabled(False)

    # ---------- Enhanced Run Tab Methods ----------
    def _start_pipeline(self, test: bool):
        # Initialize enhanced tracking
        self._init_file_progress_tracking()
        self._update_stage_status('analysis', 'active')
        
        # Call original pipeline method
        fn = self.backend.get('pipeline')
        if not fn:
            QMessageBox.warning(self, 'No backend', 'Connect a pipeline backend via connect_backend(pipeline=...)')
            return
            
        # Calculate estimated time based on number of files
        file_count = self.video_list.count()
        estimated_minutes = file_count * 5  # Rough estimate: 5 minutes per file
        self.lbl_est_time.setText(f'Estimated Time: ~{estimated_minutes} minutes')
        
        args = {
            'files': [self.video_list.item(i).text() for i in range(self.video_list.count())],
            'output_folder': self.output_path.text().strip(),
            'language': self.language_style_combo.currentText(),
            'platform': self.platform_preset.currentText(),
            'caption_style': self._collect_caption_style(),
            'audio_settings': self._collect_audio_settings(),
            'branding': self._collect_branding_settings(),
            'test': test,
        }
        self._run_job(fn, **args)
        self.btn_stop.setEnabled(True)

    def _init_file_progress_tracking(self):
        """Initialize file progress tracking with all files in queue."""
        self.file_progress_list.clear()
        for i in range(self.video_list.count()):
            file_path = self.video_list.item(i).text()
            file_name = Path(file_path).name
            item = QListWidgetItem(f"⏳ Queued: {file_name}")
            self.file_progress_list.addItem(item)
        
        # Mark first file as processing if there are files
        if self.file_progress_list.count() > 0:
            first_item = self.file_progress_list.item(0)
            first_item.setText(first_item.text().replace('Queued', '🔄 Processing'))

    def _update_stage_status(self, stage: str, status: str):
        """Update visual status of pipeline stages."""
        stages = {
            'analysis': self.stage_analysis,
            'transcription': self.stage_transcription,
            'captions': self.stage_captions,
            'audio': self.stage_audio,
            'branding': self.stage_branding
        }
        
        if stage in stages:
            label = stages[stage]
            if status == 'active':
                label.setStyleSheet("color: #3A8DFF; font-weight: bold;")
                label.setText(f"🔄 {label.text().split(' ', 1)[1]}")
            elif status == 'completed':
                label.setStyleSheet("color: #4CAF50; font-weight: bold;")
                label.setText(f"✅ {label.text().split(' ', 1)[1]}")
            elif status == 'pending':
                label.setStyleSheet("color: #9AA4AF;")

    def _update_file_progress(self, file_index: int, status: str, message: str = ""):
        """Update progress for a specific file."""
        if file_index < self.file_progress_list.count():
            item = self.file_progress_list.item(file_index)
            file_name = Path(self.video_list.item(file_index).text()).name
            
            if status == 'processing':
                item.setText(f"🔄 Processing: {file_name}")
                self.lbl_current_file.setText(f"Current: {file_name}")
            elif status == 'completed':
                item.setText(f"✅ Completed: {file_name}")
            elif status == 'error':
                item.setText(f"❌ Error: {file_name} - {message}")
            elif status == 'queued':
                item.setText(f"⏳ Queued: {file_name}")

    def _stop_pipeline(self):
        """Stop the current pipeline execution."""
        # This would need to be implemented in the backend
        # For now, just update UI state
        self.status.showMessage('Pipeline stopped by user')
        self.btn_stop.setEnabled(False)
        self.run_log.addItem("🛑 Pipeline stopped by user")

    def _clear_log(self):
        """Clear the processing log."""
        self.run_log.clear()

    def _save_log(self):
        """Save the processing log to a file."""
        import datetime
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"processing_log_{timestamp}.txt"
        
        log_content = []
        for i in range(self.run_log.count()):
            log_content.append(self.run_log.item(i).text())
        
        try:
            with open(filename, 'w') as f:
                f.write('\n'.join(log_content))
            self.run_log.addItem(f"💾 Log saved to: {filename}")
        except Exception as e:
            self.run_log.addItem(f"❌ Failed to save log: {e}")

    def _update_caption_controls_visibility(self):
        self.caption_controls_container.setVisible(
            self.captions_toggle.isChecked()
        )
        
    def _toggle_captions_config(self, checked: bool):
        """Show/hide caption configuration box based on toggle state."""
        self.captions_config_box.setVisible(checked)

    def _toggle_audio_config(self, checked: bool):
        """Show/hide caption configuration box based on toggle state."""
        self.audio_config_box.setVisible(checked)

    def _toggle_branding_config(self, checked: bool):
        """Show/hide branding configuration box based on toggle state."""
        self.branding_config_box.setVisible(checked)

    # ---------- Synchronization Methods ----------
    def _connect_home_to_tabs(self):
        """Connect home tab controls to other tabs."""
        # Connect captions controls
        self.captions_enabled_toggle.toggled.connect(self._sync_captions_to_tabs)
        self.model_size_combo.currentTextChanged.connect(self._sync_captions_to_tabs)
        self.caption_language_combo.currentTextChanged.connect(self._sync_captions_to_tabs)
        
        # Connect audio controls
        self.audio_enabled_toggle.toggled.connect(self._sync_audio_to_tabs)
        self.home_voice_isolation_toggle.toggled.connect(self._sync_audio_to_tabs)
        self.home_music_toggle.toggled.connect(self._sync_audio_to_tabs)
        self.home_music_selector.clicked.connect(self._sync_audio_to_tabs)
        
        # Connect branding controls
        self.branding_enabled_toggle.toggled.connect(self._sync_branding_to_tabs)

    def _sync_captions_to_tabs(self):
        # Enable / disable captions tab
        self.captions_toggle.setChecked(
            self.captions_enabled_toggle.isChecked()
        )

        # Sync model + language
        self.ai_model_combo.setCurrentText(
            self.model_size_combo.currentText()
        )

        self.language_style_combo.setCurrentText(
            self.caption_language_combo.currentText()
        )

        self._update_caption_controls_visibility()
        self._update_preview_style()


    def _sync_audio_to_tabs(self):
        self.voice_toggle.setChecked(
            self.home_voice_isolation_toggle.isChecked()
        )

        self.music_toggle.setChecked(
            self.home_music_toggle.isChecked()
        )

        music_path = self.home_music_selector.toolTip()
        if music_path:
            self.select_music_btn.setText(Path(music_path).name)
            self.select_music_btn.setToolTip(music_path)


    def _sync_branding_to_tabs(self):
        self.branding_toggle.setChecked(
            self.branding_enabled_toggle.isChecked()
        )

        path = self.home_end_card_selector.toolTip()
        if not path:
            return

        ext = Path(path).suffix.lower()

        IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp'}
        VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.webm'}

        if ext in IMAGE_EXTS:
            self.brand_logo_btn.setText(Path(path).name)
            self.brand_logo_btn.setToolTip(path)
            self.brand_type.setCurrentText('End Card')
            self._update_branding_preview_from_logo(path)

        elif ext in VIDEO_EXTS:
            self.brand_video_btn.setText(Path(path).name)
            self.brand_video_btn.setToolTip(path)
            self.brand_type.setCurrentText('End Card')
            self._update_branding_preview_from_video(path)

    def _update_preview_position(self):
        """Update caption preview position from spinbox values."""
        # Convert percentage (0-100) to normalized position (0.0-1.0)
        x_norm = self.x_position_spin.value() / 100.0

        y_norm = self.y_position_spin.value() / 100.0
        
        self.caption_preview._x = x_norm
        self.caption_preview._y = y_norm
        self.caption_preview.update()

    def _on_preview_position_changed(self, x_norm: float, y_norm: float):
        """Update spinbox values when caption is dragged in preview."""
        # Convert normalized position (0.0-1.0) to percentage (0-100)
        x_percent = int(x_norm * 100)
        y_percent = int(y_norm * 100)
        
        # Update spinboxes without triggering their signals (to avoid loops)
        self.x_position_spin.blockSignals(True)
        self.y_position_spin.blockSignals(True)
        
        self.x_position_spin.setValue(x_percent)
        self.y_position_spin.setValue(y_percent)
        
        self.x_position_spin.blockSignals(False)
        self.y_position_spin.blockSignals(False)

    def _on_media_mode_changed(self, mode: str):
        # Reset all buttons
        for btn in [self.media_video_btn, self.media_image_btn]:
            btn.setChecked(False)
        # Check the selected button
        if mode == 'media_video':
            self.media_video_btn.setChecked(True)
        elif mode == 'media_image':
            self.media_image_btn.setChecked(True)
        # Update preview immediately
        self.branding_preview.update()


    def _ensure_transcriptions_dir(self):
        """
        Ensure we have a configured transcriptions directory.
        Defaults to '<cwd>/final/transcriptions' if not already set.
        You can override self.transcriptions_dir externally to match your deployment.
        """
        if not hasattr(self, "transcriptions_dir") or not self.transcriptions_dir:
            # DEFAULT: project-relative folder; adjust if you want a fixed absolute path:
            # e.g., Path(r"C:\TrueEdits-7\final\transcriptions")
            self.transcriptions_dir = Path.cwd() / "final" / "transcriptions"
        self.transcriptions_dir = Path(self.transcriptions_dir)


    def _video_stem_without_edited(self, video_path: Path) -> str:
        return re.sub(r'_Edited$', '', video_path.stem, flags=re.IGNORECASE)

    def _ass_path_for_video(self, video_path: Path) -> Path:
        base_stem = self._video_stem_without_edited(video_path)
        return self.transcriptions_dir / f"{base_stem}.ass"

    def _load_edit_video(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Video", "", "Video Files (*.mp4 *.avi *.mkv)"
        )
        if not file_path:
            return

        video_path = Path(file_path)
        self.current_video_path = str(video_path)

        ass_path = self._ass_path_for_video(video_path)

        if ass_path.exists():
            self._load_ass_for_edit(str(ass_path))
            if hasattr(self, "_refresh_edit_preview"):
                self._refresh_edit_preview()
            return

        # Optional: let user pick manually, but start in fixed directory
        manual_ass, _ = QFileDialog.getOpenFileName(
            self,
            "Select Caption File",
            str(self.transcriptions_dir),
            "ASS Subtitles (*.ass)"
        )
        if manual_ass:
            self._load_ass_for_edit(manual_ass)
            if hasattr(self, "_refresh_edit_preview"):
                self._refresh_edit_preview()
        else:
            QMessageBox.warning(
                self,
                "Caption Not Found",
                (
                    "No .ass file found for this video.\n\n"
                    f"Expected:\n- {ass_path}"
                )
            )
        
    def _load_ass_for_edit(self, ass_path: str):
        try:
            import pysubs2
            subs = pysubs2.load(ass_path)
            self.caption_list.clear()
            self.edit_ass_editor.clear()
            for line in subs:
                item_text = f"{line.start:.3f}s - {line.end:.3f}s: {line.text}"
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, line)  # Store the line object
                self.caption_list.addItem(item)
            # Also load raw content
            with open(ass_path, 'r', encoding='utf-8') as f:
                self.edit_ass_editor.setPlainText(f.read())
            self.current_edit_ass_path = ass_path
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load .ass: {str(e)}")

    def _edit_caption_item(self, item):
        # On double-click, edit the text
        line = item.data(Qt.UserRole)
        new_text, ok = QInputDialog.getText(self, "Edit Caption", "Text:", text=line.text)
        if ok and new_text != line.text:
            line.text = new_text
            item.setText(f"{line.start:.3f}s - {line.end:.3f}s: {new_text}")
            # Update raw editor (rebuild .ass)
            self._rebuild_ass_from_list()

    def _rebuild_ass_from_list(self):
        if not hasattr(self, 'current_edit_ass_path'):
            return
        try:
            import pysubs2
            subs = pysubs2.SSAFile()
            for i in range(self.caption_list.count()):
                item = self.caption_list.item(i)
                line = item.data(Qt.UserRole)
                subs.append(line)
            # Save to raw editor
            output = str(subs)
            self.edit_ass_editor.setPlainText(output)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to rebuild .ass: {str(e)}")

    def _save_edit_ass(self):
        if not hasattr(self, 'current_edit_ass_path'):
            QMessageBox.warning(self, "Error", "No .ass file loaded.")
            return
        try:
            content = self.edit_ass_editor.toPlainText()
            with open(self.current_edit_ass_path, 'w', encoding='utf-8') as f:
                f.write(content)
            self.status.showMessage(f"Saved .ass: {self.current_edit_ass_path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save: {str(e)}")

    def _load_ass_for_edit(self, ass_path: str):
        """Load .ass into editor and list; enable controls and build preview."""
        self.current_ass_path = Path(ass_path)
        try:
            text = self.current_ass_path.read_text(encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read ASS:\n{e}")
            return

        # Track pristine buffer for 'Revert'
        self._ass_original_text = text
        self._ass_dirty = False

        # Populate raw editor
        self.edit_ass_editor.blockSignals(True)
        self.edit_ass_editor.setPlainText(text)
        self.edit_ass_editor.blockSignals(False)
        self.edit_ass_editor.setEnabled(True)

        # Populate caption list from [Events] Dialogue lines (simple parse)
        self._populate_caption_list_from_ass(text)

        # Enable actions
        self.save_btn.setEnabled(True)
        self.revert_btn.setEnabled(True)
        self.btn_refresh_preview.setEnabled(True)

        # Render preview (first frame + ASS)
        self._refresh_edit_preview()

    def _populate_caption_list_from_ass(self, ass_text: str):
        """Very basic ASS events parser: list each 'Dialogue:' line as an item."""
        self.caption_list.clear()
        lines = ass_text.splitlines()
        for ln in lines:
            if ln.lstrip().lower().startswith("dialogue:"):
                # You can parse time/text more precisely if you need
                # Example ASS event format starts with: Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
                # We'll show the text part after the 9th comma as a quick preview
                parts = ln.split(",", 9)
                preview = parts[-1].strip() if len(parts) >= 10 else ln.strip()
                item = QListWidgetItem(preview[:120])  # cap preview
                item.setData(Qt.UserRole, ln)  # keep full line for editing reference
                self.caption_list.addItem(item)

    def _on_caption_row_changed(self, row: int):
        """Optional: when selecting a caption, you could scroll to it in the raw editor."""
        if row < 0:
            return
        item = self.caption_list.item(row)
        if not item:
            return
        line = item.data(Qt.UserRole)
        if not line:
            return
        # Find and select the line in the raw editor
        cursor = self.edit_ass_editor.textCursor()
        doc = self.edit_ass_editor.document()
        it = doc.find(line)
        if it.isNull():
            return
        self.edit_ass_editor.setTextCursor(it)
        self.edit_ass_editor.setFocus()

    def _on_ass_text_changed(self):
        """Mark buffer dirty and (optionally) debounce preview refresh."""
        self._ass_dirty = True
        # You can debounce and auto-refresh preview; for now keep manual Refresh button.

    def _save_edit_ass(self):
        """Write back to the current ASS path."""
        if not hasattr(self, "current_ass_path") or not self.current_ass_path:
            QMessageBox.warning(self, "Warning", "No .ass file loaded.")
            return
        text = self.edit_ass_editor.toPlainText()
        try:
            self.current_ass_path.write_text(text, encoding="utf-8")
            self._ass_original_text = text
            self._ass_dirty = False
            # Rebuild list in case structure changed
            self._populate_caption_list_from_ass(text)
            QMessageBox.information(self, "Saved", f"Saved:\n{self.current_ass_path.name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    def _revert_edit_ass(self):
        if not hasattr(self, "_ass_original_text"):
            return
        self.edit_ass_editor.blockSignals(True)
        self.edit_ass_editor.setPlainText(self._ass_original_text)
        self.edit_ass_editor.blockSignals(False)
        self._ass_dirty = False
        self._populate_caption_list_from_ass(self._ass_original_text)


    def _refresh_edit_preview(self):
        """Refresh the preview to show only the first frame of the current video."""
        if not hasattr(self, "edit_preview_view"):
            return

        video_path = getattr(self, "current_video_path", None)
        if not video_path:
            self.edit_preview_view.setPixmap(QPixmap())
            self.edit_preview_view.setText("No video")
            return

        pix = grab_first_frame(video_path)
        if not pix or pix.isNull():
            self.edit_preview_view.setPixmap(QPixmap())
            self.edit_preview_view.setText("Preview unavailable")
            return

        # Scale to fit the preview label
        scaled = pix.scaled(
            self.edit_preview_view.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self.edit_preview_view.setPixmap(scaled)
        self.edit_preview_view.setText("")

def main():
    app = QApplication(sys.argv)
    win = TrueEditor()
    
    # Import and connect the pipeline backend
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))  # Add parent dir to path
    from Core.pipeline_bridge import pipeline_runner

    win.connect_backend(pipeline=pipeline_runner)
    
    win.show()
    screen_geom = QGuiApplication.screenAt(QCursor.pos()).availableGeometry()

    win.setGeometry(QStyle.alignedRect(
        Qt.LeftToRight,   
        Qt.AlignCenter,   
        win.size(),      
        screen_geom      
    ))

    sys.exit(app.exec())


if __name__ == '__main__':
    main()