"""
TrueCaptions
Copyright (c) 2025 AJ F. Jex
Licensed under the MIT License
"""

# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import shutil

# Try to ensure stdout/stderr use UTF-8 where possible to avoid UnicodeEncodeError on Windows
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

# === Configuration ===
import pathlib

def resource_path(relative_path):
    """ Get absolute path of resources bundled with PyInstaller """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# support running from a frozen bundle (PyInstaller)
if getattr(sys, 'frozen', False):
    # when frozen, resources are unpacked to sys._MEIPASS
    BASE_DIR = pathlib.Path(sys._MEIPASS)
else:
    BASE_DIR = pathlib.Path(__file__).resolve().parent

SCRIPT_DIR = str(BASE_DIR)
FFMPEG_EXE = resource_path("ffmpeg/ffmpeg.exe")
FFMPEG_DIR = os.path.dirname(FFMPEG_EXE)
# allow overriding output dir via env, otherwise use a transcriptions folder next to the script
TRANSCRIPTIONS_DIR = os.environ.get('AUTOCAPTIONS_OUTDIR', str(pathlib.Path(SCRIPT_DIR) / 'transcriptions'))

# === Step 1: Verify ffmpeg exists ===
if not os.path.isfile(FFMPEG_EXE):
    # if ffmpeg not bundled, rely on PATH ffmpeg if available
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True)
        FFMPEG_EXE = "ffmpeg"
        FFMPEG_DIR = os.path.dirname(shutil.which(FFMPEG_EXE) or "")
    except Exception:
        raise RuntimeError(f"ffmpeg.exe not found at {FFMPEG_EXE} and no ffmpeg on PATH")

os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ["PATH"]

# === Step 2: Import Whisper ===
try:
    import whisper
except ModuleNotFoundError:
    raise ModuleNotFoundError("Whisper is not installed in this Python environment.")

# === Step 3: Helper function to wrap text for line mode ===
def wrap_text_line_mode(text, max_chars):
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        if len(current_line) + (1 if current_line else 0) + len(word) <= max_chars:
            current_line += (" " if current_line else "") + word
        else:
            lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines

def split_words_into_lines(words, max_chars=20):
    """Group whisper word-timestamp dicts into lines of ~max_chars and return
    a list of dicts with text/start/end for each line. If words is empty,
    return an empty list.
    """
    if not words:
        return []

    lines = []
    current_words = []
    current_len = 0

    def flush_current():
        nonlocal current_words, current_len
        if not current_words:
            return
        text = " ".join(w.get("word", "").strip() for w in current_words).strip()
        start = current_words[0].get("start")
        # Use the last word's end timestamp so the caption end is after the last word
        end = current_words[-1].get("end")
        lines.append({"text": text, "start": start, "end": end})
        current_words = []
        current_len = 0

    for w in words:
        word_text = w.get("word", "").strip()
        # treat empty tokens as skipped
        if not word_text:
            continue
        add_len = len(word_text) + (1 if current_len else 0)
        if current_len + add_len <= max_chars:
            current_words.append(w)
            current_len += add_len
        else:
            flush_current()
            current_words.append(w)
            current_len = len(word_text)

    flush_current()
    return lines


def build_caption_segments(result, line_mode=False, max_chars=20):
        """
        Normalize Whisper output into clean caption segments:
        [{ text, start, end }]
        """

        padding = float(os.environ.get('AUTOCAPTIONS_PADDING', '0.08'))
        min_gap = float(os.environ.get('AUTOCAPTIONS_MIN_GAP', '0.01'))
        min_dur = float(os.environ.get('AUTOCAPTIONS_MIN_DUR', '0.05'))

        output = []
        last_end = None

        for seg in result.get("segments", []):
            seg_start = seg.get("start")
            seg_end = seg.get("end")
            seg_text = seg.get("text", "").strip()

            if not seg_text:
                continue

            # ---- Decide how to split ----
            if line_mode:
                words = seg.get("words") or []
                lines = split_words_into_lines(words, max_chars=max_chars)

                if not lines:
                    raw_lines = wrap_text_line_mode(seg_text, max_chars)

                    # ensure valid timing
                    start = seg_start if seg_start is not None else 0.0
                    end = seg_end if seg_end is not None else start + 0.3

                    dur = max(0.001, end - start)
                    step = dur / max(1, len(raw_lines))

                    lines = [{
                        "text": t,
                        "start": start + i * step,
                        "end": start + (i + 1) * step
                    } for i, t in enumerate(raw_lines)]
            else:
                lines = [{
                    "text": seg_text,
                    "start": seg_start,
                    "end": seg_end
                }]

            # ---- Normalize timing ----
            for ln in lines:
                start = ln["start"]
                end = ln["end"]

                if end is not None:
                    end += padding

                if start is not None and end is not None and (end - start) < min_dur:
                    end = start + min_dur

                if last_end is not None and start is not None and start < last_end + min_gap:
                    start = last_end + min_gap
                    if end <= start:
                        end = start + min_dur

                output.append({
                    "text": ln["text"],
                    "start": start,
                    "end": end
                })

                last_end = end

        return output

# === Step 4: Save .ASS ===
# Sets the Timing for the closed captions
def ass_time(seconds: float) -> str:
    cs = int(round(seconds * 100))
    s = cs // 100
    cs = cs % 100
    m = s // 60
    s = s % 60
    h = m // 60
    m = m % 60
    return f"{h}:{m:02}:{s:02}.{cs:02}"

def ass_escape(text: str) -> str:
    return (
        text.replace("\\", r"\\")
            .replace("{", r"\{")
            .replace("}", r"\}")
            .replace("\n", r"\N")
    )

# Generates the Closed captions file with Pre Configured Styling
def save_ass(result, output_path, play_res=(1920, 1080)):
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    lines = []

    # Script Info
    lines.extend([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_res[0]}",
        f"PlayResY: {play_res[1]}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        ""
    ])

    # Styles
    lines.extend([
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
        " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
        " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,80,80,60,1",
        ""
    ])

    # Events
    lines.extend([
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ])

    for seg in result.get("segments", []):
        start = ass_time(seg["start"])
        end = ass_time(seg["end"])
        text = ass_escape(seg["text"].strip())

        lines.append(
            f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"
        )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    print(f"ASS file saved to: {output_path}")
    return output_path

# === Step 5: Transcribe MP4 ===
def mp4_to_srt(mp4_file, line_mode=False):
    import tempfile
    import shutil
    import wave
    import contextlib

    print(f"Transcribing {mp4_file} ... this may take a while")
    model_name = os.environ.get('AUTOCAPTIONS_MODEL', 'small')
    # allow CLI --model
    if '--model' in sys.argv:
        try:
            m_idx = sys.argv.index('--model')
            model_name = sys.argv[m_idx + 1]
        except Exception:
            pass

    # chunking config (seconds)
    chunk_seconds = int(os.environ.get('AUTOCAPTIONS_CHUNK_SECONDS', '30'))

    # decide whether to chunk: use ffmpeg to split into wav segments
    tmpdir = tempfile.mkdtemp(prefix='autocaptions_')
    try:
        segment_pattern = os.path.join(tmpdir, 'seg%05d.wav')
        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', mp4_file,
            '-vn', '-ac', '1', '-ar', '16000',
            '-f', 'segment', '-segment_time', str(chunk_seconds),
            '-reset_timestamps', '1', segment_pattern
        ]
        try:
            subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
        except Exception:
            # if splitting fails, fallback to single-file transcription
            model = whisper.load_model(model_name)
            result = model.transcribe(mp4_file, word_timestamps=True)
            max_chars = int(os.environ.get('AUTOCAPTIONS_MAXCHARS', '20'))
            out_dir = os.environ.get('AUTOCAPTIONS_OUTDIR', TRANSCRIPTIONS_DIR)
            segments = build_caption_segments(
                result,
                line_mode=line_mode,
                max_chars=max_chars
            )

            ass_path = os.path.join(
                out_dir,
                os.path.splitext(os.path.basename(mp4_file))[0] + ".ass"
            )

            return save_ass(segments, ass_path)

        # collect segments
        seg_files = sorted([os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.startswith('seg') and f.endswith('.wav')])
        if len(seg_files) <= 1:
            # single chunk, transcribe normally
            model = whisper.load_model(model_name)
            result = model.transcribe(mp4_file, word_timestamps=True)
            max_chars = int(os.environ.get('AUTOCAPTIONS_MAXCHARS', '20'))
            out_dir = os.environ.get('AUTOCAPTIONS_OUTDIR', TRANSCRIPTIONS_DIR)
            segments = build_caption_segments(
                result,
                line_mode=line_mode,
                max_chars=max_chars
            )

            ass_path = os.path.join(
                out_dir,
                os.path.splitext(os.path.basename(mp4_file))[0] + ".ass"
            )

            return save_ass(segments, ass_path)

        # multi-chunk: transcribe each and stitch results
        model = whisper.load_model(model_name)
        all_segments = []
        total_chunks = len(seg_files)
        elapsed_offsets = []
        # precompute durations to calculate offsets
        durations = []
        for f in seg_files:
            try:
                with contextlib.closing(wave.open(f, 'r')) as wf:
                    dur = wf.getnframes() / float(wf.getframerate())
            except Exception:
                dur = chunk_seconds
            durations.append(dur)

        last_end = 0.0  # keep track of the last segment end time
        # use padding env if provided; fallback to small value
        extra_delay = float(os.environ.get('AUTOCAPTIONS_PADDING', '0.08'))
        cumulative = 0.0  # initialize cumulative offset for stitched chunks

        for i, f in enumerate(seg_files, start=1):
            # transcribe chunk
            chunk_result = model.transcribe(f, word_timestamps=True)

            # adjust timestamps by cumulative offset
            for seg in chunk_result.get('segments', []):
                start = seg.get('start', 0.0) + cumulative
                # handle None end gracefully
                raw_end = seg.get('end', None)
                if raw_end is not None:
                    end = raw_end + cumulative + extra_delay
                else:
                    # fallback to a small duration if end is missing
                    end = start + 0.3

                # prevent overlap with previous segment
                if start < last_end:
                    start = last_end + 0.01  # ensure at least 10ms gap
                if end <= start:
                    end = start + 0.3  # ensure caption is visible for a minimum duration

                # update segment timestamps
                seg['start'] = start
                seg['end'] = end

                # adjust words if present
                if 'words' in seg:
                    for w in seg['words']:
                        if 'start' in w:
                            w['start'] = w.get('start') + cumulative
                        if 'end' in w:
                            w['end'] = w.get('end') + cumulative

                all_segments.append(seg)
                last_end = end  # update for next segment

            # report chunk progress
            try:
                print(f"PROGRESS_CHUNK: {i}/{total_chunks}", flush=True)
            except Exception:
                pass

            cumulative += durations[i-1]

        stitched = {'segments': all_segments}
        max_chars = int(os.environ.get('AUTOCAPTIONS_MAXCHARS', '15'))
        out_dir = os.environ.get('AUTOCAPTIONS_OUTDIR', TRANSCRIPTIONS_DIR)
        segments = build_caption_segments(
            stitched,
            line_mode=line_mode,
            max_chars=max_chars
        )

        ass_path = os.path.join(
            out_dir,
            os.path.splitext(os.path.basename(mp4_file))[0] + ".ass"
        )

        return save_ass(segments, ass_path)
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

# === Step 6: Main ===
def main():
    # Ensure output directory exists
    os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)
    
    if len(sys.argv) < 2:
        raise ValueError("Usage: python AutoCaptions.py <path_to_mp4> [--mode normal|line]")

    mp4_file = sys.argv[1]
    if not os.path.isfile(mp4_file):
        raise FileNotFoundError(f"File not found: {mp4_file}")

    # Default mode is normal (not forced to line)
    line_mode = False

    # First honor environment variable set by GUI: AUTOCAPTIONS_MODE='line'
    if os.environ.get('AUTOCAPTIONS_MODE', '').lower() == 'line':
        line_mode = True

    # simple arg parsing for --mode and optional --max-chars; CLI overrides env
    if '--mode' in sys.argv:
        try:
            mode_idx = sys.argv.index('--mode')
            mode_val = sys.argv[mode_idx + 1].lower()
            if mode_val == 'line':
                line_mode = True
            else:
                line_mode = False
        except Exception:
            pass

    if '--max-chars' in sys.argv:
        try:
            max_idx = sys.argv.index('--max-chars')
            os.environ['AUTOCAPTIONS_MAXCHARS'] = str(int(sys.argv[max_idx + 1]))
        except Exception:
            pass

    mp4_to_srt(mp4_file, line_mode=line_mode)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Print the error and exit with non-zero code when running as script
        print(f"ERROR: {e}")
        sys.exit(1)
