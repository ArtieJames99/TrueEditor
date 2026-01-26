'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''
# Audio/audio_utils.py

from pathlib import Path
import subprocess

SCRIPT_DIR = Path(__file__).parent
FFMPEG_EXE = (SCRIPT_DIR.parent / "assets" / "ffmpeg" / "ffmpeg.exe").resolve()

def run_ffmpeg(cmd):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
    result = process.communicate()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd, result[1])
    return result

def normalize_audio(
    input_wav: Path,
    output_wav: Path,
    target_lufs: int = -16,
):
    """
    Platform-safe loudness normalization.
    Intended ONLY for non-isolated audio.

    Uses EBU R128 loudnorm with conservative settings.
    """
    cmd = [
        str(FFMPEG_EXE),
        "-y",
        "-i",
        str(input_wav),
        "-af",
        f"loudnorm=I={target_lufs}:LRA=11:TP=-1.5",
        str(output_wav),
    ]
    run_ffmpeg(cmd)