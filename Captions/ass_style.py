"""
ass_style.py
Centralized ASS style representation for TrueEdits.

This file owns:
- [Script Info]
- [V4+ Styles]
- Margins, alignment, font, colors
- Safe defaults for vertical video

It intentionally does NOT touch dialogue events.
"""

from __future__ import annotations
from pathlib import Path
import json
import re


class AssStyle:
    """
    Represents a single ASS style (usually 'Default').
    Designed to be edited live by a GUI.
    """

    STYLE_FORMAT = (
        "Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding"
    )

    def __init__(
        self,
        name: str = "Default",
        font_name: str = "Roboto",
        font_size: int = 64,
        primary_color: str = "&H00FFFFFF",
        secondary_color: str = "&H000000FF",
        outline_color: str = "&H00000000",
        back_color: str = "&H64000000",
        bold: int = 0,
        italic: int = 0,
        outline: int = 3,
        shadow: int = 2,
        alignment: int = 2,
        margin_l: int = 40,
        margin_r: int = 40,
        margin_v: int = 220,
        spacing: int = 0,
        scale_x: int = 100,
        scale_y: int = 100,
        play_res_x: int = 1920,
        play_res_y: int = 1080,
    ):
        self.name = name
        self.font_name = font_name
        self.font_size = font_size
        self.primary_color = primary_color
        self.secondary_color = secondary_color
        self.outline_color = outline_color
        self.back_color = back_color
        self.bold = bold
        self.italic = italic
        self.underline = 0
        self.strikeout = 0
        self.scale_x = scale_x
        self.scale_y = scale_y
        self.spacing = spacing
        self.angle = 0
        self.border_style = 1
        self.outline = outline
        self.shadow = shadow
        self.alignment = alignment
        self.margin_l = margin_l
        self.margin_r = margin_r
        self.margin_v = margin_v
        self.encoding = 1
        self.play_res_x = play_res_x
        self.play_res_y = play_res_y

    # ------------------------------------------------------------------
    # FACTORIES
    # ------------------------------------------------------------------

    @classmethod
    def default_for_video(cls, width: int, height: int) -> "AssStyle":
        """
        Sensible defaults for vertical short-form content.
        """
        font_size = int(height * 0.072)
        margin_v = int(height * 0.33)
        margin_lr = int(width * 0.037)
        spacing = int(width * 0.0005)

        return cls(
            font_name="Roboto",
            font_size=font_size,
            margin_l=margin_lr,
            margin_r=margin_lr,
            margin_v=margin_v,
            spacing=spacing,
            alignment=2,
            outline=3,
            shadow=2,
        )

    @classmethod
    def from_preset(cls, preset_path: str | Path) -> "AssStyle":
        with open(preset_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def load(cls, ass_path: str | Path) -> "AssStyle":
        """
        Load ONLY the first style from an ASS file.
        Dialogue lines are ignored.
        """
        style_line = None
        with open(ass_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("Style:"):
                    style_line = line.strip()
                    break

        if not style_line:
            raise ValueError("No Style line found in ASS file")

        values = style_line.split(":", 1)[1].split(",")
        values = [v.strip() for v in values]

        return cls(
            name=values[0],
            font_name=values[1],
            font_size=int(values[2]),
            primary_color=values[3],
            secondary_color=values[4],
            outline_color=values[5],
            back_color=values[6],
            bold=int(values[7]),
            italic=int(values[8]),
            scale_x=int(values[11]),
            scale_y=int(values[12]),
            spacing=int(values[13]),
            border_style=int(values[16]),
            outline=int(values[17]),
            shadow=int(values[18]),
            alignment=int(values[19]),
            margin_l=int(values[20]),
            margin_r=int(values[21]),
            margin_v=int(values[22]),
        )

    # ------------------------------------------------------------------
    # SERIALIZATION
    # ------------------------------------------------------------------

    def to_style_line(self) -> str:
        return (
            f"Style: {self.name},{self.font_name},{self.font_size},"
            f"{self.primary_color},{self.secondary_color},"
            f"{self.outline_color},{self.back_color},"
            f"{self.bold},{self.italic},{self.underline},{self.strikeout},"
            f"{self.scale_x},{self.scale_y},{self.spacing},{self.angle},"
            f"{self.border_style},{self.outline},{self.shadow},"
            f"{self.alignment},{self.margin_l},{self.margin_r},"
            f"{self.margin_v},{self.encoding}"
        )

    def build_header(self) -> list[str]:
        """
        Returns ASS header lines up to (but not including) [Events].
        """
        return [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {self.play_res_x}",
            f"PlayResY: {self.play_res_y}",
            "ScaledBorderAndShadow: yes",
            "WrapStyle: 0",
            "",
            "[V4+ Styles]",
            f"Format: {self.STYLE_FORMAT}",
            self.to_style_line(),
        ]

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    def save_preset(self, out_path: str | Path):
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    # ------------------------------------------------------------------
    # GUI HELPERS
    # ------------------------------------------------------------------

    def set_alignment_from_grid(self, row: int, col: int):
        """
        Map a 3x3 grid (row, col) to ASS alignment.
        row: 0=top, 1=middle, 2=bottom
        col: 0=left, 1=center, 2=right
        """
        mapping = {
            (2, 0): 1,
            (2, 1): 2,
            (2, 2): 3,
            (1, 0): 4,
            (1, 1): 5,
            (1, 2): 6,
            (0, 0): 7,
            (0, 1): 8,
            (0, 2): 9,
        }
        self.alignment = mapping[(row, col)]

    def clamp_margins(self, max_width: int, max_height: int):
        self.margin_l = max(0, min(self.margin_l, max_width))
        self.margin_r = max(0, min(self.margin_r, max_width))
        self.margin_v = max(0, min(self.margin_v, max_height))
