'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''
# audio/apply_audio.py

from pathlib import Path
import subprocess
import main


# Global list to keep track of active subprocesses
_active_subprocesses = []

def apply_audio(
    video_in: Path,
    video_out: Path,
    enable_audio: bool = False,
    music_path: Path | None = None,
    music_volume: float = 0.22,
    platform: str = "instagram",
    cleanup_level: str = "light"
):
    """
    Low-level ffmpeg audio execution.
    """

    video_in = Path(video_in)
    video_out = Path(video_out)

    if not enable_audio:
        # Fastest possible path (no re-encode)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_in),
            "-c", "copy",
            str(video_out)
        ]
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            stdout, stderr = process.communicate(timeout=1)  # short timeout
        except subprocess.TimeoutExpired:
            if main._stop_pipeline:
                process.kill()
                stdout, stderr = process.communicate()
                raise KeyboardInterrupt("Pipeline stopped by user")
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd, output=stdout, stderr=stderr)
        return

    # === Base audio filter ===
    filters = []

    if cleanup_level in ("light", "full"):
        filters.append("highpass=f=80")
        filters.append("lowpass=f=12000")

    if cleanup_level == "full":
        filters.append("afftdn")  # FFT denoise (CPU-friendly)

    audio_filter = ",".join(filters)

    cmd = ["ffmpeg", "-y", "-i", str(video_in)]

    # Optional background music
    if music_path:
        cmd += ["-i", str(music_path)]
        cmd += [
            "-filter_complex",
            f"[0:a]{audio_filter}[voice];"
            f"[1:a]volume={music_volume}[music];"
            f"[voice][music]amix=inputs=2:dropout_transition=3"
        ]
    else:
        cmd += ["-af", audio_filter]

    cmd += [
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        str(video_out)
    ]

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout, stderr = process.communicate(timeout=1)  # short timeout
    except subprocess.TimeoutExpired:
        if main._stop_pipeline:
            process.kill()
            stdout, stderr = process.communicate()
            raise KeyboardInterrupt("Pipeline stopped by user")
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd, output=stdout, stderr=stderr)