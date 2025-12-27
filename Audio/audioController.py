
from pathlib import Path
from datetime import datetime
import subprocess
import os

SCRIPT_DIR = Path(__file__).parent
FFMPEG_EXE = SCRIPT_DIR.parent / "assets" / "ffmpeg" / "ffmpeg.exe"

if FFMPEG_EXE.exists():
    os.environ["PATH"] = str(FFMPEG_EXE.parent) + os.pathsep + os.environ.get("PATH", "")

LOUDNESS_TARGETS = {
    "instagram": {"i": -14, "tp": -1.0, "lra": 11},
    "facebook":  {"i": -14, "tp": -1.0, "lra": 11},
    "youtube":   {"i": -14, "tp": -1.0, "lra": 11},
    "tiktok":    {"i": -14, "tp": -1.0, "lra": 11},
    "podcast":   {"i": -16, "tp": -1.5, "lra": 9},
}

def log_message(level, msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")

def process_audio(
    video_in: Path,
    video_out: Path,
    music_path: Path | None = None,
    music_volume: float = 0.22,
    cleanup_level: str = "off",
    platform: str = "instagram",
    isolated_audio: Path | None = None
):
    """
    Final audio pipeline:
      - Optional voice cleanup
      - Explicit SR/layout (48k stereo) on voice & music
      - Sidechain ducking
      - Single loudness normalization to platform target
      - Replace original timeline audio (do NOT mix it back in)
    """

    video_in = Path(video_in).resolve()
    video_out = Path(video_out).resolve()

    if not video_in.exists():
        raise FileNotFoundError(video_in)

    if isolated_audio:
        isolated_audio = Path(isolated_audio).resolve()
        if not isolated_audio.exists():
            raise FileNotFoundError(f"Isolated audio not found: {isolated_audio}")

    if music_path:
        music_path = Path(music_path).resolve()
        if not music_path.exists():
            raise FileNotFoundError(f"Music file not found: {music_path}")

    log_message("INFO", f"Audio cleanup: {cleanup_level}")
    log_message("INFO", f"Music enabled: {bool(music_path)}")
    log_message("INFO", f"Platform target: {platform}")

    target = LOUDNESS_TARGETS[platform]

    # --------------------------------------------------
    # Input indexing
    # 0 = video (always)
    # 1 = isolated audio (optional)
    # 2 = music (optional, if isolated exists)
    # 1 = music (if no isolated)
    # --------------------------------------------------
    voice_index = 1 if isolated_audio else 0
    music_index = voice_index + 1

    filters: list[str] = []

    # --------------------------------------------------
    # Voice processing: cleanup -> enforce SR/layout
    # --------------------------------------------------
    if cleanup_level == "full":
        voice_chain = (
            f"[{voice_index}:a]"
            "highpass=80,"
            "lowpass=12000,"
            "afftdn,"
            "dynaudnorm,"
            "aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo"
            "[voice]"
        )
    elif cleanup_level == "light":
        voice_chain = (
            f"[{voice_index}:a]"
            "highpass=80,"
            "lowpass=12000,"
            "aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo"
            "[voice]"
        )
    else:
        voice_chain = (
            f"[{voice_index}:a]"
            "anull,"
            "aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo"
            "[voice]"
        )
    filters.append(voice_chain)

    # --------------------------------------------------
    # Music processing: gain -> enforce SR/layout
    # --------------------------------------------------
    if music_path:
        filters.append(
            f"[{music_index}:a]"
            f"volume={music_volume},"
            "aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo"
            "[music]"
        )

        # Sidechain ducking (music ducks under voice)
        # Use a practical threshold; 0.15 ≈ -16.5 dBFS
        filters.append(
            "[music][voice]"
            "sidechaincompress="
            "threshold=0.15:"
            "ratio=6:"
            "attack=20:"
            "release=400"
            "[ducked]"
        )

        # Mix voice + ducked music
        filters.append("[voice][ducked]amix=inputs=2:normalize=0:duration=longest[mixed]")
    else:
        filters.append("[voice]anull[mixed]")

    # --------------------------------------------------
    # Loudness normalization (single pass at the end)
    # --------------------------------------------------
    filters.append(
        "[mixed]"
        f"loudnorm=I={target['i']}:LRA={target['lra']}:TP={target['tp']}"
        "[aout]"
    )

    # --------------------------------------------------
    # FFmpeg command (replace original audio)
    # --------------------------------------------------
    cmd = [str(FFMPEG_EXE if FFMPEG_EXE.exists() else "ffmpeg"), "-y", "-i", str(video_in)]

    if isolated_audio:
        cmd += ["-i", str(isolated_audio)]
    if music_path:
        cmd += ["-i", str(music_path)]

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        # Intentionally omit -shortest; rely on length-conformed isolated audio
        str(video_out)
    ]

    log_message("DEBUG", " ".join(cmd))
    subprocess.run(cmd, check=True)