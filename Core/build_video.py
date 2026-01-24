'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''
from cProfile import label
import subprocess
from pathlib import Path
import time
import os
import sys
import main
import shutil
from datetime import datetime
from typing import Optional, Dict, Any

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

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
from Core.path_utils import app_base_path
import numpy as np
from scipy.io import wavfile

# --------------------------------------------------
# Setup
# --------------------------------------------------
SCRIPT_DIR = app_base_path()

FFMPEG_EXE = SCRIPT_DIR / "assets" / "ffmpeg" / "ffmpeg.exe"
FFPROBE_EXE = SCRIPT_DIR / "assets" / "ffmpeg" / "ffprobe.exe"

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
    video_path: Path,
    end_card_path: Optional[Path] = None,
    model_name: str = "small",
    language: str = "auto",
    cleanup_level: str = "off",
    music_path: Optional[Path] = None,
    music_volume: float = 0.22,
    platform: str = "generic",
    voice_isolation_enabled: bool = False,
    captions_enabled: bool = True,
    output_folder: str = "../TrueEditor/edited_videos",
    caption_position: Optional[Dict[str, float]] = None,
    watermark_path: Optional[Path] = None,
    watermark_position: Optional[Dict[str, float]] = None,
    watermark_opacity: float = 0.5,
    watermark_size: float = 15.0,
    target_lufs: float = -14.0,
    ass_path: Optional[Path] = None,
    caption_style: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Full TrueEdits video build pipeline with proper caption & karaoke handling.
    """

    video_path = Path(video_path).resolve()
    log_message("INFO", f"=== Starting build_video: {video_path.name} ===")
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    edited_videos_dir = Path(output_folder).resolve()
    edited_videos_dir.mkdir(parents=True, exist_ok=True)

    temp_captioned = edited_videos_dir / f"{video_path.stem}_captioned.mp4"
    temp_timeline  = edited_videos_dir / f"{video_path.stem}_timeline.mp4"
    final_output   = edited_videos_dir / f"{video_path.stem}_Edited.mp4"

    # --------------------------------------------------
    # Generate captions
    # --------------------------------------------------
    if ass_path:
        ass_path = Path(ass_path)
        if not ass_path.exists():
            raise FileNotFoundError(f"Provided ASS not found: {ass_path}")
        log_message("INFO", f"Using provided ASS: {ass_path.name}")
    else:
        ass_path = (SCRIPT_DIR.parent / "TrueEditor" / "transcriptions" / f"{video_path.stem}.ass").resolve()
        if not ass_path.exists() and captions_enabled:
            log_message("INFO", "Generating captions...")
            # Ensure caption_position is defined
            caption_position = caption_position or {'x': 0.5, 'y': 0.75, 'anchor': 5}
            karaoke_settings = (caption_style or {}).get('karaoke', {})

            ass_path = captioner.mp4_to_ass(
                video_path,
                model_name=model_name,
                language=language,
                position=caption_position,
                karaoke=karaoke_settings
            )

            # Wait until ASS is actually created
            max_wait, elapsed = 30.0, 0.0
            while not ass_path.exists() and elapsed < max_wait:
                time.sleep(0.5)
                elapsed += 0.5
            if not ass_path.exists():
                raise FileNotFoundError(f"ASS file not created: {ass_path}")

    log_message("INFO", f"Burning captions with file: {ass_path}")

    # --------------------------------------------------
    # Burn captions
    # --------------------------------------------------
    if captions_enabled:
        playresx = playresy = None
        try:
            with open(ass_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PlayResX:"): playresx = int(line.split(":")[1].strip())
                    if line.startswith("PlayResY:"): playresy = int(line.split(":")[1].strip())
        except Exception as e:
            log_message("WARN", f"Failed to read ASS resolution: {e}")

        ass_path_fixed = ass_path.as_posix().replace(":", r"\:")
        ass_filter = f"subtitles='{ass_path_fixed}'"
        if playresx and playresy:
            ass_filter += f":original_size={playresx}x{playresy}"

        cmd_burn = [
            str(FFMPEG_EXE), "-y", "-i", str(video_path),
            "-vf", ass_filter,
            "-map", "0:v", "-map", "0:a?",
            "-c:v", "libx264", "-c:a", "copy",
            str(temp_captioned)
        ]
        log_message("DEBUG", "FFmpeg burn captions CMD: " + " ".join(cmd_burn))
        subprocess.run(cmd_burn, check=True)
    else:
        log_message("INFO", "Captions disabled. Copying video without burn-in.")
        shutil.copy(video_path, temp_captioned)

    # --------------------------------------------------
    # Append end card if exists
    # --------------------------------------------------
    timeline_input = temp_captioned
    if end_card_path:
        end_card_path = Path(end_card_path).resolve()
    if end_card_path and end_card_path.exists():
        concat_videos(timeline_input, end_card_path, temp_timeline)
        timeline_input = temp_timeline
    else:
        temp_timeline.unlink(missing_ok=True)
        timeline_input.rename(temp_timeline)
        timeline_input = temp_timeline

    # --------------------------------------------------
    # Audio processing (voice isolation, music, normalization)
    # --------------------------------------------------
    audio_features_enabled = voice_isolation_enabled or music_path or cleanup_level in ("light", "full")
    temp_isolated = None
    if audio_features_enabled:
        if voice_isolation_enabled:
            isolated_audio_path = process_voice_isolation(timeline_input, TEMP_DIR)
            if isolated_audio_path and isolated_audio_path.exists():
                norm_isolated = TEMP_DIR / f"{timeline_input.stem}_isolated_norm.wav"
                normalize_audio(isolated_audio_path, norm_isolated)
                temp_isolated = edited_videos_dir / f"{video_path.stem}_isolated.mp4"
                process_audio(
                    video_in=timeline_input,
                    video_out=temp_isolated,
                    music_path=None,
                    music_volume=music_volume,
                    cleanup_level=cleanup_level,
                    platform=platform,
                    isolated_audio=norm_isolated,
                    normalize=True,
                    target_lufs=target_lufs
                )
                timeline_input = temp_isolated

        if music_path:
            process_audio(
                video_in=timeline_input,
                video_out=final_output,
                music_path=music_path,
                music_volume=music_volume,
                cleanup_level="off",
                platform=platform,
                isolated_audio=None,
                normalize=True,
                target_lufs=target_lufs
            )
        else:
            shutil.copy(timeline_input, final_output)
    else:
        shutil.copy(timeline_input, final_output)

    # --------------------------------------------------
    # Watermark (optional)
    # --------------------------------------------------
    #if watermark_path and watermark_path.exists():
    #    watermark_position = watermark_position or {'x': 0.95, 'y': 0.95}
    #    add_watermark(timeline_input, watermark_path, final_output, position=watermark_position,
    #                  opacity=watermark_opacity, width_percent=watermark_size)

    # --------------------------------------------------
    # Cleanup
    # --------------------------------------------------
    for f in [temp_captioned, temp_timeline, temp_isolated]:
        if f and f.exists(): f.unlink(missing_ok=True)

    log_message("INFO", f"=== Build complete: {final_output} ===")


# --------------------------------------------------
# End card concat function
# --------------------------------------------------

def concat_videos(timeline_path: Path, end_card_path: Path, output_path: Path):
    """
    Concatenates a timeline video with an end card, making the end card
    match the timeline's codec, resolution, frame rate, pixel format, and audio.
    """

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE

    # 1️⃣ Probe timeline video
    cmd_probe_video = [
        str(FFPROBE_EXE),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,pix_fmt,r_frame_rate,codec_name",
        "-of", "default=noprint_wrappers=1",
        str(timeline_path)
    ]
    result = subprocess.run(cmd_probe_video, capture_output=True, text=True, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        log_message("ERROR", f"Failed to probe video: {result.stderr}")
        raise ValueError(f"Failed to probe video: {result.stderr}")

    out = result.stdout.strip().splitlines()

    # Parse the output by matching keys and values
    timeline_w = None
    timeline_h = None
    timeline_pix_fmt = None
    timeline_fps_str = None
    timeline_vcodec = None

    for line in out:
        if line.startswith("width="):
            timeline_w = int(line.split("=")[1])
        elif line.startswith("height="):
            timeline_h = int(line.split("=")[1])
        elif line.startswith("pix_fmt="):
            timeline_pix_fmt = line.split("=")[1]
        elif line.startswith("r_frame_rate="):
            timeline_fps_str = line.split("=")[1]
        elif line.startswith("codec_name="):
            timeline_vcodec = line.split("=")[1]

    if timeline_w is None or timeline_h is None or timeline_pix_fmt is None or timeline_fps_str is None or timeline_vcodec is None:
        log_message("ERROR", f"Failed to parse video stream information. Output: {out}")
        raise ValueError("Failed to parse video stream information from ffprobe output")

    # Convert r_frame_rate fraction to float
    if '/' in timeline_fps_str:
        num, den = timeline_fps_str.split('/')
        timeline_fps = float(num) / float(den)
    else:
        timeline_fps = float(timeline_fps_str)

    # 2️⃣ Probe timeline audio
    cmd_probe_audio = [
        str(FFPROBE_EXE),
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,channels,sample_rate",
        "-of", "default=noprint_wrappers=1",
        str(timeline_path)
    ]
    try:
        result_audio = subprocess.run(cmd_probe_audio, capture_output=True, text=True, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
        if result_audio.returncode != 0:
            log_message("WARN", f"No audio stream found or failed to probe audio: {result_audio.stderr}")
            timeline_acodec = "aac"
            timeline_channels = "2"
            timeline_ar = "48000"
        else:
            out_audio = result_audio.stdout.strip().splitlines()
            timeline_acodec = out_audio[0].split("=")[1] if out_audio and out_audio[0].startswith("codec_name=") else "aac"
            timeline_channels = out_audio[1].split("=")[1] if len(out_audio) > 1 and out_audio[1].startswith("channels=") else "2"
            timeline_ar = out_audio[2].split("=")[1] if len(out_audio) > 2 and out_audio[2].startswith("sample_rate=") else "48000"
    except Exception as e:
        log_message("WARN", f"Error probing audio: {e}")
        timeline_acodec = "aac"
        timeline_channels = "2"
        timeline_ar = "48000"

    # 3️⃣ Prepare end card with audio if missing
    end_card_audio_path = end_card_path.parent / "endcard_with_audio.mp4"
    cmd_add_audio = [
        str(FFMPEG_EXE), "-y",
        "-i", str(end_card_path),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-shortest",
        "-c:v", "libx264",
        "-c:a", "aac",
        str(end_card_audio_path),
    ]
    subprocess.run(cmd_add_audio, check=True, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)

    # 4️⃣ Scale & match codec to timeline
    scaled_endcard = end_card_path.parent / "endcard_scaled.mp4"
    cmd_scale = [
        str(FFMPEG_EXE), "-y",
        "-i", str(end_card_audio_path),
        "-vf", f"scale={timeline_w}:{timeline_h},format={timeline_pix_fmt}",
        "-r", str(int(timeline_fps)),  # convert float fps to int for FFmpeg
        "-c:v", timeline_vcodec,
        "-c:a", timeline_acodec,
        "-ar", str(timeline_ar),
        "-ac", str(timeline_channels),
        str(scaled_endcard)
    ]
    subprocess.run(cmd_scale, check=True, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)

    # 5️⃣ Concat timeline + scaled end card
    cmd_concat = [
        str(FFMPEG_EXE), "-y",
        "-i", str(timeline_path),
        "-i", str(scaled_endcard),
        "-filter_complex",
        "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]",
        "-map", "[a]",
        "-movflags", "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd_concat, check=True, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)

    # 6️⃣ Cleanup temp files
    end_card_audio_path.unlink(missing_ok=True)
    scaled_endcard.unlink(missing_ok=True)

    log_message("INFO", f"Saved timeline video: {output_path}")