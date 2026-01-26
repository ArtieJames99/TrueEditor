'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''
import subprocess
from pathlib import Path
import time
import shutil
import sys
from datetime import datetime
from typing import Optional, Dict, Any

from Core import pipeline_state
from Audio.audio_utils import normalize_audio
from Audio.voice_isolation import process_voice_isolation, TEMP_DIR
from Audio.audioController import process_audio
from Captions import captioner
from Core.path_utils import app_base_path

# -----------------------------
# Global setup
# -----------------------------
SCRIPT_DIR = app_base_path()
FFMPEG_EXE = SCRIPT_DIR / "assets" / "ffmpeg" / "ffmpeg.exe"
FFPROBE_EXE = SCRIPT_DIR / "assets" / "ffmpeg" / "ffprobe.exe"

_active_subprocesses = []

# -----------------------------
# Logging
# -----------------------------
def log_message(level, msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")

# -----------------------------
# Subprocess helpers
# -----------------------------
def create_tracked_subprocess(cmd, name="subprocess", timeout=None):
    if pipeline_state._stop_pipeline:
        log_message("INFO", f"Pipeline stopped, skipping {name}")
        raise KeyboardInterrupt(f"Pipeline stopped before {name}")

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW
        )
        _active_subprocesses.append(process)
        log_message("INFO", f"Started {name} (PID: {process.pid})")
        return process
    except Exception as e:
        log_message("ERROR", f"Failed to start {name}: {e}")
        raise

def force_terminate_all():
    if pipeline_state._stop_pipeline:
        log_message("INFO", "Force terminating all subprocesses...")
        for p in _active_subprocesses.copy():
            if p.poll() is None:
                p.kill()
        _active_subprocesses.clear()

def check_stop_condition():
    if pipeline_state._stop_pipeline:
        log_message("INFO", "Pipeline stop requested...")
        force_terminate_all()
        raise KeyboardInterrupt("Pipeline stopped by user")

def wait_for_process_or_stop(process, name="subprocess"):
    try:
        while True:
            retcode = process.poll()
            if retcode is not None:
                # Finished
                stdout, stderr = process.communicate()
                return stdout, stderr
            if pipeline_state._stop_pipeline:
                log_message("INFO", f"Stopping {name}...")
                process.kill()
                stdout, stderr = process.communicate()
                raise KeyboardInterrupt(f"{name} stopped by user")
            time.sleep(0.1)
    except Exception:
        if process.poll() is None:
            process.kill()
        raise

# -----------------------------
# Build video
# -----------------------------
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
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    log_message("INFO", f"=== Starting build_video: {video_path.name} ===")
    edited_videos_dir = Path(output_folder).resolve()
    edited_videos_dir.mkdir(parents=True, exist_ok=True)

    temp_captioned = edited_videos_dir / f"{video_path.stem}_captioned.mp4"
    temp_timeline  = edited_videos_dir / f"{video_path.stem}_timeline.mp4"
    final_output   = edited_videos_dir / f"{video_path.stem}_Edited.mp4"

    # -----------------------------
    # Generate captions
    # -----------------------------
    if ass_path:
        ass_path = Path(ass_path)
        if not ass_path.exists():
            raise FileNotFoundError(f"ASS file not found: {ass_path}")
    else:
        ass_path = (SCRIPT_DIR.parent / "TrueEditor" / "transcriptions" / f"{video_path.stem}.ass").resolve()
        if not ass_path.exists() and captions_enabled:
            log_message("INFO", "Generating captions...")
            caption_position = caption_position or {'x': 0.5, 'y': 0.75, 'anchor': 5}
            karaoke_settings = (caption_style or {}).get('karaoke', {})
            ass_path = captioner.mp4_to_ass(video_path, model_name=model_name, language=language,
                                           position=caption_position, karaoke=karaoke_settings)
            # wait for ASS creation
            for _ in range(60):
                if pipeline_state._stop_pipeline:
                    log_message("INFO", "Pipeline stopped by user during ASS wait")
                    raise KeyboardInterrupt()
                if ass_path.exists(): break
                time.sleep(0.5)
            if not ass_path.exists():
                raise FileNotFoundError(f"ASS file not created: {ass_path}")

    # -----------------------------
    # Burn captions
    # -----------------------------
    if captions_enabled:
        playresx = playresy = None
        try:
            with open(ass_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PlayResX:"): playresx = int(line.split(":")[1].strip())
                    if line.startswith("PlayResY:"): playresy = int(line.split(":")[1].strip())
        except Exception:
            pass
        escaped_ass_path = ass_path.as_posix().replace(":", r"\:")
        ass_filter = f"subtitles='{escaped_ass_path}'"
        if playresx and playresy:
            ass_filter += f":original_size={playresx}x{playresy}"

        cmd_burn = [
            str(FFMPEG_EXE), "-y", "-i", str(video_path),
            "-vf", ass_filter, "-map", "0:v", "-map", "0:a?",
            "-c:v", "libx264", "-c:a", "copy", str(temp_captioned)
        ]
        process = create_tracked_subprocess(cmd_burn, "burn_captions")
        stdout, stderr = wait_for_process_or_stop(process, "burn_captions")
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd_burn, stderr)
    else:
        shutil.copy(video_path, temp_captioned)

    # -----------------------------
    # Append end card if exists
    # -----------------------------
    timeline_input = temp_captioned
    if end_card_path and Path(end_card_path).exists():
        concat_videos(timeline_input, Path(end_card_path).resolve(), temp_timeline)
        timeline_input = temp_timeline
    else:
        shutil.copy(timeline_input, temp_timeline)
        timeline_input = temp_timeline

    # -----------------------------
    # Audio processing
    # -----------------------------
    audio_features_enabled = voice_isolation_enabled or music_path or cleanup_level in ("light","full")
    temp_isolated = None
    if audio_features_enabled:
        check_stop_condition()
        if voice_isolation_enabled:
            try:
                isolated_audio = process_voice_isolation(timeline_input, TEMP_DIR)
                if isolated_audio.exists():
                    norm_isolated = TEMP_DIR / f"{timeline_input.stem}_isolated_norm.wav"
                    normalize_audio(isolated_audio, norm_isolated)
                    temp_isolated = edited_videos_dir / f"{video_path.stem}_isolated.mp4"
                    process_audio(video_in=timeline_input, video_out=temp_isolated,
                                  music_path=None, music_volume=music_volume,
                                  cleanup_level=cleanup_level, platform=platform,
                                  isolated_audio=norm_isolated, normalize=True, target_lufs=target_lufs)
                    timeline_input = temp_isolated
            except KeyboardInterrupt:
                log_message("INFO", "Voice isolation stopped by user")
                return

        if music_path:
            check_stop_condition()
            process_audio(video_in=timeline_input, video_out=final_output,
                          music_path=music_path, music_volume=music_volume,
                          cleanup_level="off", platform=platform,
                          isolated_audio=None, normalize=True, target_lufs=target_lufs)
        else:
            shutil.copy(timeline_input, final_output)
    else:
        shutil.copy(timeline_input, final_output)

    # -----------------------------
    # Cleanup
    # -----------------------------
    for f in [temp_captioned, temp_timeline, temp_isolated]:
        if f and f.exists(): f.unlink(missing_ok=True)

    log_message("INFO", f"=== Build complete: {final_output} ===")

# -----------------------------
# Video + End card concatenation
# -----------------------------
def concat_videos(timeline_path: Path, end_card_path: Path, output_path: Path):
    timeline_path, end_card_path, output_path = map(Path.resolve, [timeline_path, end_card_path, output_path])
    if not timeline_path.exists() or not end_card_path.exists():
        raise FileNotFoundError("Timeline or end card missing")

    # Probe timeline
    cmd_probe_video = [str(FFPROBE_EXE), "-v", "error", "-select_streams", "v:0",
                       "-show_entries", "stream=width,height,pix_fmt,r_frame_rate,codec_name",
                       "-of", "default=noprint_wrappers=1", str(timeline_path)]
    proc = create_tracked_subprocess(cmd_probe_video, "probe_video")
    stdout, _ = proc.communicate()
    info = dict(line.strip().split("=",1) for line in stdout.decode().splitlines() if "=" in line)
    w, h = int(info["width"]), int(info["height"])
    pix_fmt = info["pix_fmt"]
    vcodec = info["codec_name"]
    fps_str = info["r_frame_rate"]
    fps = float(fps_str.split("/")[0])/float(fps_str.split("/")[1]) if "/" in fps_str else float(fps_str)

    # Probe audio
    cmd_probe_audio = [str(FFPROBE_EXE), "-v", "error", "-select_streams", "a:0",
                       "-show_entries", "stream=codec_name,channels,sample_rate",
                       "-of", "default=noprint_wrappers=1", str(timeline_path)]
    proc = create_tracked_subprocess(cmd_probe_audio, "probe_audio")
    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        acodec, channels, ar = "aac", 2, 48000
    else:
        audio_info = dict(line.strip().split("=",1) for line in stdout.decode().splitlines() if "=" in line)
        acodec = audio_info.get("codec_name","aac")
        channels = int(audio_info.get("channels","2"))
        ar = int(audio_info.get("sample_rate","48000"))

    # Ensure end card has audio
    temp_end_audio = end_card_path.parent / "endcard_with_audio.mp4"
    cmd_add_audio = [str(FFMPEG_EXE), "-y", "-i", str(end_card_path),
                     "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                     "-shortest", "-c:v", "libx264", "-c:a", "aac", str(temp_end_audio)]
    process = create_tracked_subprocess(cmd_add_audio, "add_audio_endcard")
    process.communicate()

    # Scale end card
    temp_scaled = end_card_path.parent / "endcard_scaled.mp4"
    cmd_scale = [str(FFMPEG_EXE), "-y", "-i", str(temp_end_audio),
                 "-vf", f"scale={w}:{h},format={pix_fmt}", "-r", str(int(fps)),
                 "-c:v", vcodec, "-c:a", acodec, "-ar", str(ar), "-ac", str(channels),
                 str(temp_scaled)]
    process = create_tracked_subprocess(cmd_scale, "scale_endcard")
    process.communicate()

    # Concat
    cmd_concat = [str(FFMPEG_EXE), "-y", "-i", str(timeline_path), "-i", str(temp_scaled),
                  "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
                  "-map", "[v]", "-map", "[a]", "-movflags", "+faststart", str(output_path)]
    process = create_tracked_subprocess(cmd_concat, "concat_videos")
    process.communicate()

    # Cleanup
    temp_end_audio.unlink(missing_ok=True)
    temp_scaled.unlink(missing_ok=True)

    log_message("INFO", f"Saved concatenated video: {output_path}")
