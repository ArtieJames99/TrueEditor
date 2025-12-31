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
FFPROBE_EXE = SCRIPT_DIR.parent / "assets" / "ffmpeg" / "ffprobe.exe"


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
    captions_enabled=False,
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

    edited_videos_dir = SCRIPT_DIR.parent / "final" / "edited_videos"
    edited_videos_dir.mkdir(parents=True, exist_ok=True)

    temp_captioned = edited_videos_dir / f"{video_path.stem}_captioned.mp4"
    temp_timeline = edited_videos_dir / f"{video_path.stem}_timeline.mp4"
    final_output = edited_videos_dir / f"{video_path.stem}_Edited.mp4"

    # --------------------------------------------------
    # Generate captions
    # --------------------------------------------------

    ass_path = SCRIPT_DIR.parent / "final" / "transcriptions" / f"{video_path.stem}.ass"
    if not ass_path.exists():
        if captions_enabled:
            log_message("INFO", "Generating captions...")
            captioner.mp4_to_ass(video_path, model_name=model_name, language=language)
        else:
            log_message("INFO", "Captions disabled. - Skipping Generation")

    # Wait for ASS file
    if captions_enabled:
        log_message("INFO", "Waiting for ASS file...")
        max_wait = 500
        elapsed = 0.0
        while not ass_path.exists() and elapsed < max_wait:
            time.sleep(0.5)
            elapsed += 0.5
        if not ass_path.exists():
            raise FileNotFoundError(f"ASS file not created: {ass_path}")
    else:
        log_message("INFO", "Skipping caption file generation.")

# --------------------------------------------------
# Burn captions
# --------------------------------------------------
    timeline_input = temp_captioned
    if captions_enabled:
        playresx = None
        playresy = None

        try:
            with open(ass_path, "r", encoding="utf-8") as f:
                      lines = f.readlines()
            
            for line in lines:
                if line.startswith("PlayResX:"):
                    playresx = int(line.split(":")[1].strip())
                if line.startswith("PlayResY:"):
                    playresy = int(line.split(":")[1].strip())
        except Exception as e:
            log_message("WARN", f"Failed to read ASS resolution: {e}")

        ass_path_fixed = ass_path.as_posix().replace(":", r"\:")
        ass_filter = f"subtitles='{ass_path_fixed}:original_size={playresx}x{playresy}'"

        # Optional: read PlayResX/Y from ASS
        try:
            with open(ass_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            playresx = playresy = None
            for line in lines:
                if line.startswith("PlayResX:"):
                    playresx = int(line.split(":")[1].strip())
                if line.startswith("PlayResY:"):
                    playresy = int(line.split(":")[1].strip())
            if playresx and playresy:
                ass_filter += f":original_size={playresx}x{playresy}"
            # If PlayResX/Y missing, just skip original_size
        except Exception as e:
            log_message("WARN", f"Failed to read ASS resolution: {e}")

        cmd_burn = [
            str(FFMPEG_EXE),
            "-y",
            "-i",
            str(video_path),
            "-vf", ass_filter,
            "-map", "0:v",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-c:a", "copy",
            str(temp_captioned),
        ]

        log_message("INFO", "Burning captions...")
        log_message("DEBUG", "FFMPEG CMD: " + " ".join(cmd_burn))

        try:
            subprocess.run(cmd_burn, check=True)
        except subprocess.CalledProcessError as e:
            log_message("ERROR", "FFmpeg failed to burn captions.")
            log_message("ERROR", str(e))
    else:
        # Captions disabled → just copy the original video
        log_message("INFO", "Captions disabled. - Skipping Burn")
        import shutil
        shutil.copy(video_path, temp_captioned)

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
    # audio processing
    # --------------------------------------------------

    audio_features_enabled = (
    voice_isolation_enabled or
    music_path is not None
    )

    if audio_features_enabled:
        log_message("INFO", "Processing final audio over full timeline...")

        TEMP_DIR.mkdir(parents=True, exist_ok=True)

        audio_source = None
        is_isolated = False

        # 1. Voice isolation
        if voice_isolation_enabled:
            log_message("INFO", f"Running voice isolation on {temp_timeline.name}...")
            processed_audio = process_voice_isolation(temp_timeline, TEMP_DIR)

            if processed_audio and processed_audio.exists():
                log_message("INFO", f"Voice isolation complete: {processed_audio}")
                audio_source = processed_audio
                is_isolated = True
            else:
                log_message("WARN", "Voice isolation failed, using raw audio.")

        # 2. Extract raw audio if needed
        if not audio_source:
            raw_audio = TEMP_DIR / f"{temp_timeline.stem}_raw.wav"
            log_message("INFO", "Extracting raw timeline audio...")
            extract_audio(temp_timeline, raw_audio)
            audio_source = raw_audio

        # 3. Normalize
        log_message("INFO", "Normalizing audio loudness (LUFS)...")
        norm_audio_tmp = TEMP_DIR / f"{temp_timeline.stem}_norm.wav"
        normalize_audio(audio_source, norm_audio_tmp)
        audio_source = norm_audio_tmp

        # 4. Cleanup level adjustments
        if is_isolated:
            log_message("INFO", "Reducing cleanup level due to voice isolation.")
            cleanup_level = "light"

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

    else:
        # No audio features → skip audio processing entirely
        log_message("INFO", "Skipping audio processing (no audio features enabled).")
        # Just copy the timeline video to final output
        import shutil
        shutil.copy(temp_timeline, final_output)

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

    def get_resolution(path):
        cmd = [
            str(FFPROBE_EXE),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(path)
        ]
        out = subprocess.check_output(cmd, text=True).strip()
        w, h = out.split(",")
        return int(w), int(h)

    # Get main video resolution
    w, h = get_resolution(video1_path)

    log_message("INFO", "Preparing end card for concat...")

    # 1. Ensure end card has audio
    endcard_with_audio = video2_path.parent / "endcard_with_audio.mp4"
    cmd_add_audio = [
        str(FFMPEG_EXE), "-y",
        "-i", str(video2_path),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-shortest",
        "-c:v", "libx264",
        "-c:a", "aac",
        str(endcard_with_audio),
    ]
    subprocess.run(cmd_add_audio, check=True)

    # 2. Scale end card to match main video resolution
    scaled_endcard = video2_path.parent / "endcard_scaled.mp4"
    cmd_scale = [
        str(FFMPEG_EXE), "-y",
        "-i", str(endcard_with_audio),
        "-vf", f"scale={w}:{h}",
        "-c:v", "libx264",
        "-c:a", "aac",
        str(scaled_endcard)
    ]
    subprocess.run(cmd_scale, check=True)

    # 3. Concat timeline
    cmd_concat = [
        str(FFMPEG_EXE), "-y",
        "-i", str(video1_path),
        "-i", str(scaled_endcard),
        "-filter_complex",
        "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]",
        "-map", "[a]",
        "-movflags", "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd_concat, check=True)

    # Cleanup
    video1_path.unlink(missing_ok=True)
    endcard_with_audio.unlink(missing_ok=True)
    scaled_endcard.unlink(missing_ok=True)

    log_message("INFO", f"Saved timeline video: {output_path}")