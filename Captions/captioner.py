"""
AutoCaptions Single-File Runner
Generates .ASS captions for a video and outputs en.mp4
"""

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

# Max characters per caption
try:
    MAX_CHARS = int(os.environ.get('AUTOCAPTIONS_MAXCHARS', '20'))
except Exception:
    MAX_CHARS = 20

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
    if not FFPROBE_EXE.exists():
        log_message("WARNING", f"FFprobe not found at {FFPROBE_EXE}, cannot get video resolution.")
        return 1920, 1080  # default fallback
    cmd = [
        str(FFPROBE_EXE),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,side_data_list",
        "-of", "json",
        str(video_path)
    ]

    result = subprocess.check_output(cmd, text=True)
    data = json.loads(result)

    stream = data["streams"][0]
    width = stream["width"]
    height = stream["height"]

    # Detect rotation (mobile videos)
    for side in stream.get("side_data_list", []):
        if side.get("side_data_type") == "Display Matrix":
            rotation = int(side.get("rotation", 0))
            if abs(rotation) in (90, 270):
                width, height = height, width

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

def save_ass(segments, out_path, video_path, style: AssStyle | None = None):
    """
    Save caption segments to an ASS file.
    Styling is handled by AssStyle (GUI-editable).
    """
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    video_w, video_h = get_video_resolution(video_path)

    if style is None:
        style = AssStyle.default_for_video(
            width=video_w,
            height=video_h
        )
        style.play_res_x = video_w
        style.play_res_y = video_h


    # Load video resolution
    video_w, video_h = get_video_resolution(video_path)

    # Create default style if none provided
    if style is None:
        style = AssStyle.default_for_video(
            width=video_w,
            height=video_h
        )

    # Build ASS header from style object
    lines = style.build_header()

    # Events section
    lines += [
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    for seg in segments:
        start = ass_time(seg["start"])
        end = ass_time(seg["end"])
        text = ass_escape(seg["text"])
        lines.append(
            f"Dialogue: 0,{start},{end},{style.name},,0,0,0,,{text}"
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



# Public API: transcribe MP4 and save ASS captions
def mp4_to_ass(video_path, model_name="small", language=None, style: AssStyle | None = None):
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
        if language:  # only pass if user specified a language
            transcribe_args["language"] = language.lower()  # Whisper expects lowercase

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
    save_ass(segments, ass_path, video_path, style=style)
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