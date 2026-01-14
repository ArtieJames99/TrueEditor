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
import whisper

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

TRANSCRIPTIONS_DIR = SCRIPT_DIR / ".." / "final" / "transcriptions"
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

    data = json.loads(subprocess.check_output(cmd, text=True))
    stream = data["streams"][0]

    width = int(stream["width"])
    height = int(stream["height"])

    rotation = 0

    # 1️⃣ Primary source: stream tag
    tags = stream.get("tags", {})
    if "rotate" in tags:
        rotation = int(tags["rotate"])

    # 2️⃣ Fallback: display matrix
    for side in stream.get("side_data_list", []):
        if side.get("side_data_type") == "Display Matrix":
            rotation = int(side.get("rotation", rotation))

    # 3️⃣ Normalize to display orientation
    if rotation in (90, 270, -90):
        width, height = height, width

    # 4️⃣ Enforce vertical sanity (optional but recommended)
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

def save_ass(segments, out_path, video_path, style: AssStyle | None = None, position: dict | None = None):
    """
    Save caption segments to an ASS file.
    Styling is handled by AssStyle (GUI-editable).
    If `position` is provided (dict with 'x' and 'y' normalized 0..1),
    a per-dialogue ASS override `{\pos(px,py)}` will be prepended to each line
    to enforce exact pixel placement in PlayRes coordinates.
    """
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    # Determine video resolution and ensure style exists
    video_w, video_h = get_video_resolution(video_path)

    if style is None:
        # Create an adaptive style sized for this video
        style = AssStyle.adaptive_for_video(video_w, video_h)

    # If the provided style was created for a different PlayRes, scale (existing logic)
    try:
        orig_w = int(style.play_res_x or 1920)
        orig_h = int(style.play_res_y or 1080)
    except Exception:
        orig_w, orig_h = 1920, 1080

    if orig_w > 0 and orig_h > 0:
        scale_x = video_w / orig_w
        scale_y = video_h / orig_h

        new_font = max(8, int(style.font_size * scale_y))
        style.spacing = int(style.spacing * min(scale_x, scale_y))
        max_font = max(8, int(video_h * 0.2))
        style.font_size = min(new_font, max_font)

        style.margin_l = max(0, int(style.margin_l * scale_x))
        style.margin_r = max(0, int(style.margin_r * scale_x))
        style.margin_v = max(0, int(style.margin_v * scale_y))
        style.outline = max(0, int(style.outline * max(scale_x, scale_y)))
        style.shadow = max(0, int(style.shadow * max(scale_x, scale_y)))

    # Ensure header PlayRes matches the actual video resolution and clamp margins
    style.play_res_x = video_w
    style.play_res_y = video_h
    style.clamp_margins(video_w, video_h)

    # Build ASS header from style object
    lines = style.build_header()

    # Events section
    lines += [
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    # If position provided, precompute pixel coords from normalized values
    pos_px = pos_py = None
    if position:
        x_norm = float(position.get('x', 0.5))
        y_norm = float(position.get('y', 0.75))
        # Convert normalized center (0..1) to PlayRes pixels
        pos_px = int(round(x_norm * video_w))
        pos_py = int(round(y_norm * video_h))

    for seg in segments:
        start = ass_time(seg["start"])
        end = ass_time(seg["end"])

        # Escape the dialogue text (this will not escape our manual {\pos(...)} tag)
        escaped_text = ass_escape(seg["text"])

        # If position provided, prepend an override block with exact pixel position.
        # This overrides style margins/alignment for this line and places the
        # text origin according to the style's alignment (`\an`), which will
        # usually be center (5) or the style default. Using \pos gives precise control.
        if pos_px is not None and pos_py is not None:
            # UI provides the caption center point; ensure ASS uses center anchor
            # `\an5` forces the origin to the text center so \pos(x,y) maps to UI coords
            text = f"{{\\an5\\pos({pos_px},{pos_py})}}{escaped_text}"
        else:
            text = escaped_text

        lines.append(
            f"Dialogue: 0,{start},{end},{style.name},,{int(style.margin_l)},{int(style.margin_r)},{int(style.margin_v)},,{text}"
        )

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
def mp4_to_ass(video_path, model_name="small", language=None, style: AssStyle | None = None, position=None):
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
    
    save_ass(segments, ass_path, video_path, style=style, position=position)
    log_message("INFO", f"=== Transcription complete: {ass_path} ===")
    return ass_path


# === Main transcription ===
def main(video_path, language=None):
    video_path = Path(video_path)
    if not video_path.is_file(): raise FileNotFoundError(video_path)

    # Generate ASS captions and return path
    ass_path = mp4_to_ass(video_path, language=language)

    # Merge captions into en.mov (CLI convenience)
    out_mov = video_path.parent / "en.mov"
    ass_path_ffmpeg = ass_path.replace("\\", "/")
    cmd = [FFMPEG_EXE, "-i", str(video_path), "-vf", f"ass='{ass_path_ffmpeg}'", str(out_mov)]
    subprocess.run(cmd, check=True)
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