from cProfile import label
import subprocess
from pathlib import Path
import time
import os
import main
from datetime import datetime
from Audio.audio_utils import normalize_audio
from Audio import voice_isolation
from Audio.voice_isolation import (
    extract_audio,
    isolate_voice,
    process_voice_isolation,
    TEMP_DIR,
)
from Captions import captioner
from Audio.audioController import process_audio
import numpy as np
from scipy.io import wavfile

# --------------------------------------------------
# Setup
# --------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
os.chdir(SCRIPT_DIR)

FFMPEG_EXE = SCRIPT_DIR.parent / "assets" / "ffmpeg" / "ffmpeg.exe"


# --------------------------------------------------
# Logging
# --------------------------------------------------

def log_message(level, msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


# --------------------------------------------------
# Main build function
# --------------------------------------------------

def build_video(
    video_path,
    end_card_path=None,
    model_name="small",
    language="English",
    cleanup_level="off",  # off | light | full
    music_path=None,
    music_volume=0.22,
    platform="instagram",
    voice_isolation_enabled=False,
):
    """
    Full TrueEdits video build pipeline:
        1. Generate ASS captions
        2. Burn captions
        3. Append end card (if any)
        4. Final audio processing over full timeline (voice isolation, normalization, music)
        5. Cleanup temp files
    """

    video_path = Path(video_path).resolve()
    log_message("INFO", f"=== Starting build_video: {video_path.name} ===")

    if not video_path.exists():
        raise FileNotFoundError(video_path)

    edited_videos_dir = SCRIPT_DIR / ".." / "final" / "edited_videos"
    edited_videos_dir.mkdir(parents=True, exist_ok=True)

    temp_captioned = edited_videos_dir / f"{video_path.stem}_captioned.mp4"
    temp_timeline = edited_videos_dir / f"{video_path.stem}_timeline.mp4"
    final_output = edited_videos_dir / f"{video_path.stem}_Edited.mp4"

    # --------------------------------------------------
    # Generate captions
    # --------------------------------------------------

    ass_path = SCRIPT_DIR / ".." / "final" / "transcriptions" / f"{video_path.stem}.ass"
    if not ass_path.exists():
        log_message("INFO", "Generating captions...")
        captioner.mp4_to_ass(video_path, model_name=model_name, language=language)

    # Wait for ASS file
    log_message("INFO", "Waiting for ASS file...")
    max_wait = 500
    elapsed = 0.0
    while not ass_path.exists() and elapsed < max_wait:
        time.sleep(0.5)
        elapsed += 0.5
    if not ass_path.exists():
        raise FileNotFoundError(f"ASS file not created: {ass_path}")

    # --------------------------------------------------
    # Burn captions
    # --------------------------------------------------

    try:
        ass_relative = ass_path.relative_to(SCRIPT_DIR)
    except ValueError:
        ass_relative = ass_path
    ass_filter = str(ass_relative).replace("\\", "/")

    cmd_burn = [
        str(FFMPEG_EXE),
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"ass={ass_filter}",
        "-map",
        "0:v",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-c:a",
        "copy",
        str(temp_captioned),
    ]
    log_message("INFO", "Burning captions...")
    subprocess.run(cmd_burn, check=True)

    # --------------------------------------------------
    # Append end card
    # --------------------------------------------------

    timeline_input = temp_captioned
    if end_card_path:
        end_card_path = Path(end_card_path).resolve()

    if not end_card_path or not end_card_path.exists():
        auto_endcard = SCRIPT_DIR / ".." / "assets" / "endcards" / f"{language}.mp4"
        if auto_endcard.exists():
            end_card_path = auto_endcard

    if end_card_path and end_card_path.exists():
        log_message("INFO", "Concatenating end card...")
        concat_videos(timeline_input, end_card_path, temp_timeline)
    else:
        timeline_input.rename(temp_timeline)

    timeline_stem = temp_timeline.stem

    # --------------------------------------------------
    # FINAL audio processing
    # --------------------------------------------------

    log_message("INFO", "Processing final audio over full timeline...")

    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    audio_source = None
    is_isolated = False  # ✅ isolation flag

    # 1. Voice isolation
    if voice_isolation_enabled:
        log_message("INFO", f"Running voice isolation on {temp_timeline.name}...")
        processed_audio = process_voice_isolation(temp_timeline, TEMP_DIR)

        if processed_audio and processed_audio.exists():
            log_message("INFO", f"Voice isolation complete: {processed_audio}")
            audio_source = processed_audio
            is_isolated = True  # ✅ set flag to skip normalization
        else:
            log_message("WARN", "Voice isolation failed, using raw audio.")

    # 2. Fallback to raw audio
    if not audio_source:
        raw_audio = TEMP_DIR / f"{temp_timeline.stem}_raw.wav"
        log_message("INFO", "Extracting raw timeline audio...")
        extract_audio(temp_timeline, raw_audio)
        audio_source = raw_audio


    # Always normalize loudness for platform consistency
    log_message("INFO", "Normalizing audio loudness (LUFS)...")
    norm_audio_tmp = TEMP_DIR / f"{temp_timeline.stem}_norm.wav"
    normalize_audio(audio_source, norm_audio_tmp)  # ensure this is LUFS-based, not peak-only
    audio_source = norm_audio_tmp

    # Reduce cleanup intensity due to isolation (skip heavy denoise/EQ)
    if is_isolated:
        log_message("INFO", "Reducing cleanup level due to voice isolation.")

    # 4. Reduce cleanup intensity if isolated
    if is_isolated:
        log_message("INFO", "Reducing cleanup level due to voice isolation.")
        cleanup_level = "light"

    
    log_message("INFO", f"process_audio() input:")
    log_message("INFO", f"  video_in={temp_timeline}")
    log_message("INFO", f"  final_out={final_output}")
    log_message("INFO", f"  isolated_audio={audio_source if audio_source else 'None'}")
    log_message("INFO", f"  cleanup_level={cleanup_level}, music_path={music_path}, music_volume={music_volume}")


    # 5. Final audio processing
    process_audio(
        video_in=temp_timeline,
        video_out=final_output,
        cleanup_level=cleanup_level,
        music_path=music_path,
        music_volume=music_volume,
        platform=platform,
        isolated_audio=audio_source,
    )

    # --------------------------------------------------
    # CLEANUP TEMP FILES
    # --------------------------------------------------

    log_message("INFO", "Cleaning up temporary files...")
    for stem in [video_path.stem, timeline_stem]:
        for f in TEMP_DIR.glob(f"{stem}*.wav"):
            f.unlink(missing_ok=True)

    temp_captioned.unlink(missing_ok=True)
    temp_timeline.unlink(missing_ok=True)

    log_message("INFO", "=== Build complete ===")
    log_message("INFO", f"Final output: {final_output}")


# --------------------------------------------------
# End card concat function
# --------------------------------------------------

def concat_videos(video1_path, video2_path, output_path):
    """
    Timeline-safe concat with guaranteed audio streams.
    """
    log_message("INFO", "Preparing end card for concat...")

    endcard_with_audio = video2_path.parent / "endcard_with_audio.mp4"

    # Ensure end card has audio
    cmd_add_audio = [
        str(FFMPEG_EXE),
        "-y",
        "-i",
        str(video2_path),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-shortest",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(endcard_with_audio),
    ]
    subprocess.run(cmd_add_audio, check=True)

    # Concat timeline
    cmd_concat = [
        str(FFMPEG_EXE),
        "-y",
        "-i",
        str(video1_path),
        "-i",
        str(endcard_with_audio),
        "-filter_complex",
        "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd_concat, check=True)

    video1_path.unlink(missing_ok=True)
    endcard_with_audio.unlink(missing_ok=True)
    log_message("INFO", f"Saved timeline video: {output_path}")