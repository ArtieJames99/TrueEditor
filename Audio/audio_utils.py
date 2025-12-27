# Audio/audio_utils.py

from pathlib import Path
import subprocess

SCRIPT_DIR = Path(__file__).parent
FFMPEG_EXE = (SCRIPT_DIR.parent / "assets" / "ffmpeg" / "ffmpeg.exe").resolve()

def run_ffmpeg(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True)

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
