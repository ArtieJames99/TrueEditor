'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''
from __future__ import annotations
import sys
import inspect
from pathlib import Path
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
    QStatusBar, QMessageBox, QProgressBar, QSizePolicy, QSpacerItem, QListWidgetItem, QStyle
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
        self._font_size = 34
        self._bold = False
        self._italic = False
        self._align = Qt.AlignCenter
        # Styling toggles
        self.background_enabled = True
        self.karioke_enabled = False
        self.drop_shadow_enabled = False

        # Colors
        self._base_color = QColor('#FFFFFF')
        self._karioke_color = QColor('#FF0000')
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

    # ---- word-wrap helper ----
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
            painter.setPen(self._karioke_color if self.karioke_enabled else self._base_color)
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

        # Menu & toolbar
        self._apply_qss()

    # ---------- QSS Theme ----------
    def _apply_qss(self):
        qss = '''
        /* ===== GLOBAL ===== */
        QWidget {
            background-color: #121417;
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
            border: 1px solid #3A8DFF;
        }

        /* Secondary container (Quick Start) */
        QGroupBox#secondary {
            background-color: #181B1F;
        }

        /* Drawer / nested configs */
        QGroupBox#drawer {
            background-color: #14161A;
            border: 1px solid #262A31;
            margin-top: 6px;
            padding: 10px;
        }

        QGroupBox#drawer::title {
            font-size: 11px;
            color: #7F8893;
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
        /* ===== STATUS BAR ===== */
        QStatusBar { background-color: #1A1D21; border-top: 1px solid #2A2F36; color: #9AA4AF; }
        /* ===== RADIO BUTTONS ===== */
        QRadioButton { spacing: 6px; color: #E6E8EB; font-size: 13px; }
        QRadioButton::indicator {
            width: 16px; height: 16px; border: 2px solid #2A2F36; border-radius: 8px; background: #1A1D21;
        }
        QRadioButton::indicator:checked { border-color: #3A8DFF; background: #3A8DFF; }
        QRadioButton::indicator:hover { border-color: #5AA2FF; }
        QRadioButton::indicator:disabled { border-color: #444A52; background: #1A1D21; opacity: 0.5; }
        '''
        QApplication.instance().setStyleSheet(qss)

    # -----------------------------
    # Home
    # -----------------------------
    ''' 
    TODO:  
    - Update model selection for Vocal Isolation
    - Update language selection to match openai-whisper functionality eg:tagalog = tl
    - Add Save/Load  Preset Functionality
    - Update Captions Tab so it doesn't get so big when it is toggeled on. 
    - 3-4 Common Presets that they can select within, then give the ability to load presets later
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

        self.caption_language_combo = QComboBox()
        self.caption_language_combo.addItems(
            ['Auto', 'English', 'Spanish', 'Chinese', 'French', 'German', 'Italian']
        )
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
        remove_btn = QPushButton('Remove Selected')
        remove_btn.clicked.connect(self._remove_selected)
        clear_btn = QPushButton('Clear List')
        clear_btn.clicked.connect(lambda: self.video_list.clear())
        input_layout.addWidget(self.video_list)
        input_layout.addWidget(add_btn)
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
        layout = QHBoxLayout(root)

        # -------------------------
        # Style panel
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
        model_row.addWidget (QLabel('AI Model'))
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
        self.language_style_combo.addItems(['Auto', 'English', 'Spanish', 'Chinese'])
        lang_row.addWidget(self.language_style_combo)

        caption_controls_layout.addLayout(lang_row)

        # -------------------------
        # Font
        # -------------------------
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel('Font'))

        self.font_combo = QComboBox()
        self.font_combo.addItems(['Arial', 'Roboto', 'Inter', 'Times New Roman'])
        font_row.addWidget(self.font_combo)

        font_row.addWidget(QLabel('Size'))
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(10, 72)
        self.font_size_spin.setValue(34)
        font_row.addWidget(self.font_size_spin)

        caption_controls_layout.addLayout(font_row)

        # -------------------------
        # Font Color
        # -------------------------
        base_color_row = QHBoxLayout()
        base_color_row.addWidget(QLabel('Font Color'))

        self.base_color_btn = QPushButton('Select Font Color')
        base_color_row.addWidget(self.base_color_btn)

        caption_controls_layout.addLayout(base_color_row)

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

        # -------------------------
        # Background Group (SINGLE source of truth)
        # -------------------------
        bg_group = QGroupBox('Background')
        bg_form = QFormLayout(bg_group)

        self.background_toggle = ToggleSwitch()
        self.background_toggle.setChecked(False)

        self.background_color_btn = QPushButton('Background Color')

        self.bg_padding_spin = QSpinBox()
        self.bg_padding_spin.setRange(0, 60)
        self.bg_padding_spin.setValue(12)

        self.bg_radius_spin = QSpinBox()
        self.bg_radius_spin.setRange(0, 60)
        self.bg_radius_spin.setValue(12)

        self.bg_opacity_slider = QSlider(Qt.Horizontal)
        self.bg_opacity_slider.setRange(0, 255)
        self.bg_opacity_slider.setValue(180)

        self.bg_maxwidth_slider = QSlider(Qt.Horizontal)
        self.bg_maxwidth_slider.setRange(40, 100)
        self.bg_maxwidth_slider.setValue(85)

        self.align_combo = QComboBox()
        self.align_combo.addItems(['Center', 'Left', 'Right'])

        self.safezone_toggle = ToggleSwitch()
        self.safezone_toggle.toggled.connect(self._update_preview_style)

        bg_form.addRow('Enabled', self.background_toggle)
        bg_form.addRow('Color', self.background_color_btn)
        bg_form.addRow('Padding (px)', self.bg_padding_spin)
        bg_form.addRow('Corner Radius (px)', self.bg_radius_spin)
        bg_form.addRow('Opacity', self.bg_opacity_slider)
        bg_form.addRow('Max Width (%)', self.bg_maxwidth_slider)
        bg_form.addRow('Alignment', self.align_combo)
        bg_form.addRow('Safe Zone', self.safezone_toggle)

        caption_controls_layout.addWidget(bg_group)

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

        # -------------------------
        # Caption Length Mode
        # -------------------------
        length_mode_box = QGroupBox('Caption Length Mode')
        length_mode_layout = QHBoxLayout(length_mode_box)

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

        length_mode_layout.addWidget(self.line_mode_btn)
        length_mode_layout.addWidget(self.single_word_btn)
        length_mode_layout.addWidget(self.movie_mode_btn)

        caption_controls_layout.addWidget(length_mode_box)

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

        # Preview (ALWAYS visible)
        self.caption_preview = CaptionPreview()

        # Connect the position changed signal to update spinboxes
        self.caption_preview.positionChanged.connect(self._on_preview_position_changed)

        layout.addWidget(style_box, 2)
        layout.addWidget(self.caption_preview, 3)

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
        self.bg_maxwidth_slider.valueChanged.connect(self._update_preview_style)

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

        return root

    def _audio_tab(self) -> QWidget:
        root = QWidget()
        layout = QHBoxLayout(root)

        #----------- Audio Controls -----------
        controls_box = QGroupBox('Audio Controls')
        controls_layout = QVBoxLayout(controls_box)

        normalize_row = QHBoxLayout()
        normalize_row.addWidget(QLabel('Normalize Loudness'))
        self.normalize_toggle = ToggleSwitch()
        normalize_row.addWidget(self.normalize_toggle)
        controls_layout.addLayout(normalize_row)

        controls_layout.addWidget(QLabel('Target LUFS'))
        self.lufs_slider = QSlider(Qt.Horizontal)
        self.lufs_slider.setRange(-40, 0)
        self.lufs_slider.setValue(-14)
        self.lufs_slider.setEnabled(False)
        controls_layout.addWidget(self.lufs_slider)
        self.normalize_toggle.toggled.connect(lambda checked: self.lufs_slider.setEnabled(checked))

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel('Voice Isolation'))
        self.voice_toggle = ToggleSwitch()
        voice_row.addWidget(self.voice_toggle)
        controls_layout.addLayout(voice_row)

        music_row = QHBoxLayout()
        music_row.addWidget(QLabel('Background Music'))
        self.music_toggle = ToggleSwitch()
        music_row.addWidget(self.music_toggle)
        controls_layout.addLayout(music_row)

        self.select_music_btn = QPushButton('Select Music')
        self.select_music_btn.clicked.connect(self._select_music)
        controls_layout.addWidget(self.select_music_btn)

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
        layout = QHBoxLayout(root)

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

        # -------------------------
        # Branding Content
        # -------------------------
        content_group = QGroupBox('Branding Content')
        content_form = QFormLayout(content_group)
        
        self.brand_headline = QLineEdit()
        self.brand_subtext = QLineEdit()
        
        content_form.addRow('Headline', self.brand_headline)
        content_form.addRow('Subtext', self.brand_subtext)
        
        branding_controls_layout.addWidget(content_group)

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

        # -------------------------
        # Branding Size/Opacity
        # -------------------------
        size_group = QGroupBox('Branding Size & Opacity')
        size_form = QFormLayout(size_group)
        
        self.brand_width_spin = QSpinBox()
        self.brand_width_spin.setRange(50, 500)
        self.brand_width_spin.setValue(200)
        self.brand_width_spin.setSuffix('px')
        
        self.brand_opacity_slider = QSlider(Qt.Horizontal)
        self.brand_opacity_slider.setRange(0, 100)
        self.brand_opacity_slider.setValue(100)
        
        size_form.addRow('Width', self.brand_width_spin)
        size_form.addRow('Opacity', self.brand_opacity_slider)
        
        branding_controls_layout.addWidget(size_group)

        # Add containers to branding panel
        branding_layout.addWidget(self.branding_controls_container)
        branding_layout.addWidget(self.iv_branding_controls_container)

        # -------------------------
        # Branding Preview (Right Column)
        # -------------------------
        self.branding_preview = BrandingPreview()
        
        # Connect the position changed signal to update spinboxes
        self.branding_preview.positionChanged.connect(self._on_branding_preview_position_changed)

        layout.addWidget(branding_box, 2)
        layout.addWidget(self.branding_preview, 3)

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

    def _run_tab(self) -> QWidget:
        root = QWidget()
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
        
        # File Progress Section
        file_progress_group = QGroupBox('File Progress')
        file_progress_layout = QVBoxLayout(file_progress_group)
        
        self.file_progress_list = QListWidget()
        self.file_progress_list.setMaximumHeight(140)
        file_progress_layout.addWidget(self.file_progress_list)
        
        layout.addWidget(file_progress_group)
        
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
        
        # Control Buttons
        btns = QVBoxLayout()
        control_row = QHBoxLayout()
        self.run_test_btn = QPushButton('Run Test')
        self.run_batch_btn = QPushButton('Run Batch')
        self.btn_stop = QPushButton('Stop')
        self.btn_stop.setEnabled(False)
        self.open_output_btn = QPushButton('Open output Folder')
        self.open_output_btn.clicked.connect(self._open_output_folder)
        
        
        control_row.addWidget(self.run_test_btn)
        control_row.addWidget(self.run_batch_btn)
        control_row.addWidget(self.btn_stop)
        btns.addLayout(control_row)
        btns.addWidget(self.open_output_btn)
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
    
    def _select_end_card(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select End Card',
            '',
            'Images (*.png *.jpg *.jpeg);;Videos (*.mp4 *.mov *.mkv)'
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
        self.branding_preview._headline = self.brand_headline.text()
        self.branding_preview._subtext = self.brand_subtext.text()
        self.branding_preview._width = self.brand_width_spin.value()
        self.branding_preview._opacity = self.brand_opacity_slider.value()
        self.branding_preview.update()
    
    def _update_branding_preview_style(self):
        """Update branding preview style from controls."""
        self.branding_preview._brand_type = self.brand_type.currentText()
        self.branding_preview._headline = self.brand_headline.text()
        self.branding_preview._subtext = self.brand_subtext.text()
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
        color = QColorDialog.getColor(self.caption_preview._karioke_color, self, 'Select Karaoke Color')
        if color.isValid():
            self.caption_preview._karioke_color = color
            self.caption_preview.update()

    def _update_preview_style(self):
        self.caption_preview._font_size = self.font_size_spin.value()
        self.caption_preview._bold = self.bold_toggle.isChecked()
        self.caption_preview._italic = self.italic_toggle.isChecked()
        self.caption_preview.drop_shadow_enabled = self.drop_shadow_toggle.isChecked()
        self.caption_preview.background_enabled = self.background_toggle.isChecked()
        self.caption_preview.karioke_enabled = self.karaoke_toggle.isChecked()
        self.caption_preview.show_safezone = self.safezone_toggle.isChecked()
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
                'padding': self.caption_preview.bg_padding,
                'corner_radius': self.caption_preview.bg_corner_radius,
                'opacity': self.caption_preview._background_opacity,
            },
            'karaoke': {
                'enabled': self.karaoke_toggle.isChecked(),
                'color': self.caption_preview._karioke_color.name(),
            },
            'font_color': self.caption_preview._base_color.name(),
            'position': {
                # position is now normalized to the actual displayed image (0..1)
                'x': x_img,
                'y': y_img,
            },
            'length_mode': self._get_length_mode(),
            'enabled': self.captions_toggle.isChecked(),
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
            'headline': self.brand_headline.text(),
            'subtext': self.brand_subtext.text(),
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
        # Enhanced log formatting with timestamps and categorization
        import datetime
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
