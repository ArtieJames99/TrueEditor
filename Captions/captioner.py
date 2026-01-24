'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''

import os
import sys
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from .ass_style import AssStyle
import json

# === Configuration ===

# Use bundled FFmpeg from assets folder
SCRIPT_DIR = Path(__file__).parent
FFMPEG_EXE = SCRIPT_DIR.parent / "assets" / "ffmpeg" / "ffmpeg.exe"
FFPROBE_EXE = SCRIPT_DIR.parent / "assets" / "ffmpeg" / "ffprobe.exe"

# Ensure FFmpeg is on PATH for Whisper & subprocesses
ffmpeg_folder = str(FFMPEG_EXE.parent.resolve())
os.environ["PATH"] = ffmpeg_folder + os.pathsep + os.environ.get("PATH", "")

# Verify FFmpeg exists
if not FFMPEG_EXE.exists():
    raise FileNotFoundError(f"FFmpeg not found at {FFMPEG_EXE}")

# Import Whisper after setting PATH


# Max characters per caption  - Add in a way to select what kind of Captions you want (Single word, line mode, Noraml estimates)
def max_chars_for_width(width: int) -> int:
    """
    Estimate max readable characters per line based on video width.
    Assumes ~0.55em average glyph width.
    """
    return max(12, int((width * 0.75) / 30))

try:
    MAX_CHARS = int(os.environ.get('AUTOCAPTIONS_MAXCHARS', '15'))
except Exception:
    MAX_CHARS = 15

TRANSCRIPTIONS_DIR = SCRIPT_DIR / ".." / "TrueEditor" / "transcriptions"
os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)

# === Logging Helper ===
def log_message(level, msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")

# === Helper Functions ===
def transcribe_video(video_path, model_name="small", language=None):
    import whisper

    model = whisper.load_model(model_name)

    try:
        transcribe_args = {
            "verbose": True,
            "word_timestamps": True
        }
        if language:  # only pass if specified
            transcribe_args["language"] = language.lower()

        result = model.transcribe(str(video_path), **transcribe_args)
    except TypeError:
        transcribe_args = {"verbose": True}
        if language:
            transcribe_args["language"] = language.lower()
        result = model.transcribe(str(video_path), **transcribe_args)

    return result


def split_words_into_captions(words, max_chars):
    captions = []
    current_words = []
    current_len = 0

    def flush():
        nonlocal current_words, current_len
        if not current_words:
            return
        captions.append({
            "text": " ".join(w["word"].strip() for w in current_words),
            "start": current_words[0]["start"],
            "end": current_words[-1]["end"]
        })
        current_words = []
        current_len = 0

    for w in words:
        word = w.get("word", "").strip()
        if not word:
            continue

        add_len = len(word) + (1 if current_len else 0)

        if current_len + add_len <= max_chars:
            current_words.append(w)
            current_len += add_len
        else:
            flush()
            current_words.append(w)
            current_len = len(word)

    flush()
    return captions


def css_hex_to_ass(hex_color: str, alpha: int = 0) -> str:
    """
    Convert '#RRGGBB' to ASS '&HAA BB GG RR' (AA=alpha, 00=opaque, FF=transparent).
    """
    hex_color = (hex_color or '').strip()
    if hex_color.startswith('#'):
        hex_color = hex_color[1:]
    if len(hex_color) != 6:
        return "&H00FFFFFF"  # white fallback
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    a = max(0, min(255, alpha))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"

def style_from_ui(caption_style: dict, video_w: int, video_h: int, preview_canvas_height: int | None = None) -> AssStyle:
    """
    Build an AssStyle from the Captions tab dict.
    If preview_canvas_height is provided, scale the UI font size (px) from the
    UI preview canvas space into the ASS PlayRes (video) space:
        ass_size = ui_size * (video_h / preview_canvas_height)
    """
    style = AssStyle.adaptive_for_video(video_w, video_h)

    # --- Font family
    style.font_name = caption_style.get('font', style.font_name)

    fs = int(caption_style.get('size', style.font_size))
    if preview_canvas_height and preview_canvas_height > 0:
        scale_factor = video_h / float(preview_canvas_height)
        fs = int(round(fs * scale_factor))

    style.font_size = max(8, fs)

    # --- Weight/slant
    style.bold   = -1 if caption_style.get('bold') else 0
    style.italic = -1 if caption_style.get('italic') else 0

    # --- Colors
    style.primary_color = css_hex_to_ass(caption_style.get('font_color', '#FFFFFF'), alpha=0)

    # --- Drop shadow
    if caption_style.get('drop_shadow'):
        style.shadow = 5
        style.outline = 0
    else:
        style.shadow = 0 

    # --- Background box
    bg = caption_style.get('background') or {}
    if bg.get('enabled'):
        ass_alpha = 255 - int(bg.get('opacity', 180))  # UI 0..255; ASS 00=opaque
        style.back_color = css_hex_to_ass(bg.get('color', '#000000'), alpha=ass_alpha)
        style.border_style = 3
    else:
        style.border_style = 1

    # --- Alignment (only used if you don't force \anX in overrides)
    align = (caption_style.get('align') or 'Center').lower()
    style.alignment = 2 if align == 'center' else (1 if align == 'left' else 3)

    return style


def build_caption_segments(result, max_chars=20):
    padding = 0.08
    min_gap = 0.01
    min_dur = 0.05

    output = []
    last_end = 0.0

    for seg in result.get("segments", []):
        words = seg.get("words", [])
        if not words:
            continue

        # Split words into SEPARATE CAPTIONS (not wrapped lines)
        captions = split_words_into_captions(words, max_chars)

        for cap in captions:
            start = cap["start"]
            end = cap["end"] + padding

            # Enforce minimum duration
            if end - start < min_dur:
                end = start + min_dur

            # Prevent overlap
            if start < last_end + min_gap:
                start = last_end + min_gap
                end = max(end, start + min_dur)

            output.append({
                "text": cap["text"],   # SINGLE LINE ONLY
                "start": start,
                "end": end
            })

            last_end = end

    return output

# Sets the video resolution for captions to handle the exact video size
def get_video_resolution(video_path):
    """
    Returns the DISPLAY resolution of the video (post-rotation).
    This is the resolution ASS must use.
    """
    if not FFPROBE_EXE.exists():
        log_message("WARNING", "FFprobe missing — using fallback resolution")
        return 1920, 1080

    cmd = [
        str(FFPROBE_EXE),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,side_data_list:stream_tags=rotate",
        "-of", "json",
        str(video_path)
    ]

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    data = json.loads(subprocess.check_output(cmd, text=True,  startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW))
    stream = data["streams"][0]

    width = int(stream["width"])
    height = int(stream["height"])

    rotation = 0

    # 1️ Primary source: stream tag
    tags = stream.get("tags", {})
    if "rotate" in tags:
        rotation = int(tags["rotate"])

    # 2️ Fallback: display matrix
    for side in stream.get("side_data_list", []):
        if side.get("side_data_type") == "Display Matrix":
            rotation = int(side.get("rotation", rotation))

    # 3️ Normalize to display orientation
    if rotation in (90, 270, -90):
        width, height = height, width

    # 4️ Enforce vertical sanity (optional but recommended)
    if height < width:
       width, height = height, width

    log_message(
        "DEBUG",
        f"Video geometry: display={width}x{height}, rotation={rotation}"
    )

    return width, height

def ass_time(sec):
    cs = int(round(sec*100))
    s = cs // 100
    cs = cs % 100
    m = s // 60
    s = s % 60
    h = m // 60
    m = m % 60
    return f"{h}:{m:02}:{s:02}.{cs:02}"

def ass_escape(text):
    return text.replace("\\","\\\\").replace("{","\\{").replace("}","\\}").replace("\n","\\N")


def save_ass(
    segments,
    out_path,
    video_path,
    style: AssStyle | None = None,
    position: dict | None = None,
    karaoke: dict | None = None,
    base_color_hex= "#FFFFFF",
    karaoke_color_hex= "#FF0000",

):
    """
    Save caption segments to an ASS file.
    - If karaoke['enabled'] is True: render single-word pulse using overlays, honoring UI colors.
    - Otherwise: render normal lines in base (font) color.
    """
    import os
    import re
    from Captions.captioner import get_video_resolution, ass_time, ass_escape, log_message

    # ---- Helpers (local to this function) ----
    def hex_to_ass_bbggrr(hex_rgb: str) -> str:
        """
        Convert "#RRGGBB" to ASS override color "&HBBGGRR&".
        Example: "#FF0000" (red) -> "&H0000FF&"
        """
        h = (hex_rgb or "").strip().lstrip("#")
        if len(h) != 6:
            h = "FFFFFF"
        rr, gg, bb = h[0:2], h[2:4], h[4:6]
        return f"&H{bb}{gg}{rr}&"

    # Alpha codes for ASS override
    ALPHA_VISIBLE = "&H00&"  # opaque
    ALPHA_HIDDEN  = "&HFF&"  # fully transparent

    # Preserve spacing: split into tokens (words and spaces separately)
    TOKEN_SPLIT_RE = re.compile(r"\S+|\s+")

    def tokenize_with_spaces(text: str):
        """Return a list of tokens preserving original whitespace."""
        return TOKEN_SPLIT_RE.findall(text)

    def extract_words(tokens):
        """Return list of (word, token_index) for non-space tokens."""
        words = []
        for idx, tok in enumerate(tokens):
            if tok.strip() != "":
                words.append((tok, idx))
        return words

    def core_len(s: str) -> int:
        """Weight for timing—letters/digits only (at least 1)."""
        core = re.sub(r"[^0-9A-Za-z]+", "", s)
        return max(1, len(core))

    # Convert UI colors once
    COLOR_BASE   = hex_to_ass_bbggrr(base_color_hex)       # Font Color
    COLOR_KARAOK = hex_to_ass_bbggrr(karaoke_color_hex)    # Karaoke Color

    # Prepare output directory
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    # Video resolution / style scaling
    video_w, video_h = get_video_resolution(video_path)

    if style is None:
        style = AssStyle.adaptive_for_video(video_w, video_h)

    orig_w = int(style.play_res_x or 1920)
    orig_h = int(style.play_res_y or 1080)
    if orig_w != video_w or orig_h != video_h:
        scale_x = video_w / orig_w
        scale_y = video_h / orig_h
        style.font_size = max(8, min(int(style.font_size * scale_y), int(video_h * 0.2)))
        style.spacing   = int(style.spacing * min(scale_x, scale_y))
        style.margin_l  = max(0, int(style.margin_l * scale_x))
        style.margin_r  = max(0, int(style.margin_r * scale_x))
        style.margin_v  = max(0, int(style.margin_v * scale_y))
        style.outline   = max(0, int(style.outline  * max(scale_x, scale_y)))
        style.shadow    = max(0, int(style.shadow   * max(scale_x, scale_y)))

    style.play_res_x = video_w
    style.play_res_y = video_h
    style.clamp_margins(video_w, video_h)

    # Header
    lines = style.build_header()
    lines += [
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # Compute position override once (applied to all events)
    an = int(position.get("anchor", 5)) if position else 5
    pos_px = int(round(position.get("x", 0.5) * video_w)) if position else None
    pos_py = int(round(position.get("y", 0.75) * video_h)) if position else None
    pos_tag = f"\\an{an}\\pos({pos_px},{pos_py})" if pos_px is not None else ""

    karaoke_enabled = karaoke.get("enabled", False) if karaoke else False

    # ---- MAIN LOOP: build events per segment (bug fix: keep everything inside the loop) ----
    MIN_S = 0.03  # minimum seconds per word (helps fast speech)

    for seg in segments:
        seg_start = seg["start"]
        seg_end   = seg["end"]
        start = ass_time(seg_start)
        end   = ass_time(seg_end)

        raw_text = (seg.get("text") or "").strip()
        escaped_full = ass_escape(raw_text)

        if karaoke_enabled and raw_text:
            # 1) Base line (Layer 0): entire sentence in base (font) color
            base_text = f"{{{pos_tag}\\alpha{ALPHA_VISIBLE}\\c{COLOR_BASE}}}{escaped_full}"
            lines.append(
                f"Dialogue: 0,{start},{end},{style.name},,"
                f"{int(style.margin_l)},{int(style.margin_r)},{int(style.margin_v)},,{base_text}"
            )

            # 2) Overlays (Layer 1): one per word, only current word visible in Karaoke Color
            tokens    = tokenize_with_spaces(raw_text)
            word_list = extract_words(tokens)  # [(word, token_index), ...]

            if not word_list:
                # Edge case: all spaces (unlikely, but safe)
                continue

            total_duration = max(0.01, seg_end - seg_start)
            lengths = [core_len(w) for (w, _) in word_list]
            total_len = sum(lengths) or len(word_list)

            durations = [(total_duration * L / total_len) for L in lengths]
            durations = [max(MIN_S, d) for d in durations]
            drift = total_duration - sum(durations)
            if abs(drift) > 1e-6:
                idx_longest = max(range(len(durations)), key=lambda i: durations[i])
                durations[idx_longest] = max(MIN_S, durations[idx_longest] + drift)

            cur_t = seg_start
            for word_idx, d in enumerate(durations):
                parts = [f"{{{pos_tag}}}"]  # include position/anchor
                cur_word_counter = -1
                for t in tokens:
                    if t.strip() == "":
                        # space token: hide in overlay (base line provides visible spacing)
                        parts.append(f"{{\\alpha{ALPHA_HIDDEN}}}{ass_escape(t)}")
                    else:
                        cur_word_counter += 1
                        if cur_word_counter == word_idx:
                            # current word visible in Karaoke color
                            parts.append(f"{{\\alpha{ALPHA_VISIBLE}\\c{COLOR_KARAOK}}}{ass_escape(t)}")
                        else:
                            # other words hidden
                            parts.append(f"{{\\alpha{ALPHA_HIDDEN}}}{ass_escape(t)}")

                overlay_text = "".join(parts)
                lines.append(
                    f"Dialogue: 1,{ass_time(cur_t)},{ass_time(cur_t + d)},{style.name},,"
                    f"{int(style.margin_l)},{int(style.margin_r)},{int(style.margin_v)},,{overlay_text}"
                )
                cur_t += d

        else:
            # Non-karaoke: one line in base (font) color
            text = f"{{{pos_tag}\\alpha{ALPHA_VISIBLE}\\c{COLOR_BASE}}}{escaped_full}"
            lines.append(
                f"Dialogue: 0,{start},{end},{style.name},,"
                f"{int(style.margin_l)},{int(style.margin_r)},{int(style.margin_v)},,{text}"
            )

    # Write file
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log_message("INFO", f"ASS saved to: {out_path}")
    return out_path

def wrap_ass_text_max_2_lines(text, max_chars):
    """
    Wrap text into at most 2 lines using ASS line breaks (\\N)
    """
    words = text.split()
    lines = []
    current = ""

    for word in words:
        if len(current) + len(word) + (1 if current else 0) <= max_chars:
            current += (" " if current else "") + word
        else:
            lines.append(current)
            current = word
            if len(lines) == 2:
                break

    if current and len(lines) < 2:
        lines.append(current)

    return r"\N".join(lines)

def format_captions_by_mode(captions: list, mode: str) -> list:
    """Format captions based on selected length mode."""
    
    if mode == 'single_word':
        # Split each caption into individual words
        result = []
        for caption in captions:
            words = caption['text'].split()
            for word in words:
                result.append({
                    'start': caption['start'],
                    'end': caption['end'],
                    'text': word.strip()
                })
        return result
    
    elif mode == 'movie':
        # Combine captions into longer blocks (2-3 sentences)
        result = []
        current_block = []
        current_text = ""
        
        for caption in captions:
            if len(current_block) < 3 and len(current_text + " " + caption['text']) < 120:
                current_block.append(caption)
                current_text += " " + caption['text']
            else:
                if current_block:
                    result.append({
                        'start': current_block[0]['start'],
                        'end': current_block[-1]['end'],
                        'text': current_text.strip()
                    })
                current_block = [caption]
                current_text = caption['text']
        
        # Add remaining block
        if current_block:
            result.append({
                'start': current_block[0]['start'],
                'end': current_block[-1]['end'],
                'text': current_text.strip()
            })
        
        return result
    
    else:  # line mode (default)
        # Return captions as-is, just clean up formatting
        return [{
            'start': c['start'],
            'end': c['end'],
            'text': c['text'].strip()
        } for c in captions]


# Public API: transcribe MP4 and save ASS captions
def mp4_to_ass(video_path, model_name="small", language=None, style: AssStyle | None = None, position=None, length_mode: str = 'line', karaoke: dict | None = None, base_color_hex: str = "#FFFFFF", karaoke_color_hex: str = "#FF0000"):
    import whisper
    """Transcribe a video file and write an .ass captions file.
    Returns the path to the generated .ass file.
    """
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(video_path)

    log_message("INFO", f"=== Starting transcription ===")
    log_message("INFO", f"Input file: {video_path}")
    log_message("INFO", f"Model: {model_name}")
    
    log_message("INFO", "Loading Whisper model...")
    model = whisper.load_model(model_name)
    log_message("INFO", "Model loaded successfully")
    
    log_message("INFO", "Starting audio transcription (this may take a while)...")
    try:
        transcribe_args = {
            "verbose": True,
            "word_timestamps": True
        }
        if language:  # only pass if specified
            transcribe_args["language"] = language.lower()

        result = model.transcribe(str(video_path), **transcribe_args)
    except TypeError:
        # fallback for older Whisper versions without word_timestamps
        transcribe_args = {"verbose": True}
        if language:
            transcribe_args["language"] = language.lower()
        result = model.transcribe(str(video_path), **transcribe_args)
        

    log_message("INFO", "Transcription complete")
    log_message("INFO", "Building caption segments...")
    segments = build_caption_segments(result, max_chars=MAX_CHARS)

    # NEW: apply the selected length mode from UI
    segments = format_captions_by_mode(segments, mode=length_mode)

    log_message("INFO", f"Generated {len(segments)} caption segments")
    log_message("INFO", "Saving ASS file...")
    ass_path = os.path.join(TRANSCRIPTIONS_DIR, f"{video_path.stem}.ass")

    
    # Apply custom position if provided (always ensure a style exists)
    if position:
        x_norm = float(position.get('x', 0.5))
        y_norm = float(position.get('y', 0.75))

        # Determine video resolution (needed to compute margins)
        video_w, video_h = get_video_resolution(video_path)

        # Ensure we have a style to modify
        if style is None:
            style = AssStyle.adaptive_for_video(video_w, video_h)

        # ASS alignment mapping (1-9 grid)
        # 7 8 9
        # 4 5 6
        # 1 2 3

        # Horizontal part
        if x_norm < 0.33:
            horiz = 1  # Left column
        elif x_norm > 0.67:
            horiz = 3  # Right column
        else:
            horiz = 2  # Center column

        # Vertical part
        if y_norm < 0.33:
            vert = 3  # Top row in ASS mapping uses +6 later 
        elif y_norm > 0.67:
            vert = 1  # Bottom row in ASS mapping
        else:
            vert = 2  # Middle row

        # Convert to ASS alignment value
        # ASS numbering: bottom-left=1, bottom-center=2, bottom-right=3,
        # middle-left=4, middle-center=5, middle-right=6,
        # top-left=7, top-center=8, top-right=9
        # Map (vert,horiz) to the above
        mapping = {
            (1, 1): 1, (1, 2): 2, (1, 3): 3,
            (2, 1): 4, (2, 2): 5, (2, 3): 6,
            (3, 1): 7, (3, 2): 8, (3, 3): 9,
        }
        style.alignment = mapping[(vert, horiz)]

        # Calculate horizontal margins: larger distance from center → increase margin.
        # Use a simple heuristic that keeps captions inside safe area.
        # Clamp to reasonable values.
        margin_lr = int(video_w * max(0.02, min(0.45, abs(0.5 - x_norm) * 2.0)))
        style.margin_l = margin_lr
        style.margin_r = margin_lr

        # Vertical margin: convert normalized center Y into ASS bottom margin.
        # ASS margin_v is distance from bottom, so invert Y.
        margin_v = int(video_h * max(0.02, min(0.45, (1.0 - y_norm) * 0.6)))
        style.margin_v = margin_v
    
    save_ass(segments, ass_path, video_path, style=style, position=position, karaoke=karaoke, base_color_hex=base_color_hex, karaoke_color_hex=karaoke_color_hex)
    log_message("INFO", f"=== Transcription complete: {ass_path} ===")
    return ass_path


# === Main transcription ===
def main(video_path, language=None):
    video_path = Path(video_path)
    if not video_path.is_file(): raise FileNotFoundError(video_path)

    # Generate ASS captions and return path
    ass_path = mp4_to_ass(video_path, language=language)

    # Merge captions into en.mov (CLI convenience)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    out_mov = video_path.parent / "en.mov"
    ass_path_ffmpeg = ass_path.replace("\\", "/")
    cmd = [FFMPEG_EXE, "-i", str(video_path), "-vf", f"ass='{ass_path_ffmpeg}'", str(out_mov)]
    subprocess.run(cmd, check=True, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
    print(f"Video with captions saved to: {out_mov}")

if __name__ == "__main__":
    if len(sys.argv)<2:
        print("Usage: python AutoCaptionsSingle.py <video.mp4> [--language <lang>]")
        sys.exit(1)
    video_path = sys.argv[1]
    language = None
    if "--language" in sys.argv:
        try:
            lang_idx = sys.argv.index("--language")
            language = sys.argv[lang_idx + 1]
        except IndexError:
            print("Error: --language requires a language argument")
            sys.exit(1)
    main(video_path, language=language)