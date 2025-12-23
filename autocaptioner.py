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

# === Configuration ===

# Use bundled ffmpeg from assets folder
SCRIPT_DIR = Path(__file__).parent
FFMPEG_EXE = SCRIPT_DIR / "assets" / "ffmpeg" / "ffmpeg.exe"
os.environ["PATH"] = str(FFMPEG_EXE.parent) + os.pathsep + os.environ.get("PATH", "")
import whisper  
try:
    MAX_CHARS = int(os.environ.get('AUTOCAPTIONS_MAXCHARS', '20'))
except Exception:
    MAX_CHARS = 20

LINE_MODE = True  # default to line-mode
TRANSCRIPTIONS_DIR = "transcriptions"
os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)

# === Logging Helper ===
def log_message(level, msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")

# === Helper Functions ===
def wrap_text_line_mode(text, max_chars):
    words = text.split()
    lines, current = [], ""
    for w in words:
        if len(current) + (1 if current else 0) + len(w) <= max_chars:
            current += (" " if current else "") + w
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines

def split_words_into_lines(words, max_chars=20):
    if not words: return []
    lines, current_words, current_len = [], [], 0
    def flush():
        nonlocal current_words, current_len
        if not current_words: return
        lines.append({
            "text": " ".join(w["word"].strip() for w in current_words),
            "start": current_words[0]["start"],
            "end": current_words[-1]["end"]
        })
        current_words, current_len = [], 0
    for w in words:
        word_text = w.get("word","").strip()
        if not word_text: continue
        add_len = len(word_text) + (1 if current_len else 0)
        if current_len + add_len <= max_chars:
            current_words.append(w)
            current_len += add_len
        else:
            flush()
            current_words.append(w)
            current_len = len(word_text)
    flush()
    return lines

def build_caption_segments(result, line_mode=True, max_chars=20):
    padding = 0.08
    min_gap = 0.01
    min_dur = 0.05
    output = []
    last_end = 0
    for seg in result.get("segments", []):
        seg_start = seg.get("start",0)
        seg_end = seg.get("end",seg_start+0.3)
        seg_text = seg.get("text","").strip()
        if not seg_text: continue
        if line_mode:
            words = seg.get("words",[])
            lines = split_words_into_lines(words, max_chars)
            if not lines:
                raw_lines = wrap_text_line_mode(seg_text, max_chars)
                dur = max(0.001, seg_end-seg_start)
                lines = [{"text": t, "start": seg_start + i*dur/len(raw_lines),
                          "end": seg_start + (i+1)*dur/len(raw_lines)} for i,t in enumerate(raw_lines)]
        else:
            lines = [{"text": seg_text, "start": seg_start, "end": seg_end}]
        for ln in lines:
            start = ln["start"]
            end = ln["end"] + padding
            if end - start < min_dur: end = start + min_dur
            if start < last_end + min_gap: start = last_end + min_gap; end = max(end, start+min_dur)
            output.append({"text": ln["text"], "start": start, "end": end})
            last_end = end
    return output

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

def save_ass(segments, out_path):
    # create directory for output path; if no dirname, use current dir
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)
    lines = ["[Script Info]",
             "ScriptType: v4.00+",
             "PlayResX: 1920",
             "PlayResY: 1080",
             "ScaledBorderAndShadow: yes",
             "WrapStyle: 2","",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
             " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
             " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             "Style: Default,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,2,80,80,360,1","",             "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for seg in segments:
        start = ass_time(seg["start"])
        end = ass_time(seg["end"])
        text = ass_escape(seg["text"])
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    with open(out_path,"w",encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"ASS saved to: {out_path}")
    return out_path

# Public API: transcribe MP4 and save ASS captions
def mp4_to_ass(video_path, line_mode=LINE_MODE, model_name="small"):
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
        result = model.transcribe(str(video_path), word_timestamps=True, verbose=True)
    except TypeError:
        result = model.transcribe(str(video_path), verbose=True)
    
    log_message("INFO", "Transcription complete")
    
    log_message("INFO", "Building caption segments...")
    segments = build_caption_segments(result, line_mode=line_mode, max_chars=MAX_CHARS)
    log_message("INFO", f"Generated {len(segments)} caption segments")
    
    log_message("INFO", "Saving ASS file...")
    ass_path = os.path.join(TRANSCRIPTIONS_DIR, f"{video_path.stem}.ass")
    save_ass(segments, ass_path)
    log_message("INFO", f"=== Transcription complete: {ass_path} ===")
    return ass_path

# === Main transcription ===
def main(video_path):
    video_path = Path(video_path)
    if not video_path.is_file(): raise FileNotFoundError(video_path)

    # Generate ASS captions and return path
    ass_path = mp4_to_ass(video_path, line_mode=LINE_MODE)

    # Merge captions into en.mov (CLI convenience)
    out_mov = video_path.parent / "en.mov"
    cmd = [FFMPEG_EXE, "-i", str(video_path), "-vf", f"ass={ass_path}", str(out_mov)]
    subprocess.run(cmd, check=True)
    print(f"Video with captions saved to: {out_mov}")

if __name__ == "__main__":
    if len(sys.argv)<2:
        print("Usage: python AutoCaptionsSingle.py <video.mp4>")
        sys.exit(1)
    main(sys.argv[1])
