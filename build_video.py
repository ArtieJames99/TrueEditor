import subprocess
from pathlib import Path
import time
import os
from datetime import datetime
import autocaptioner

# Set working directory to script folder to enable relative paths
SCRIPT_DIR = Path(__file__).parent
os.chdir(SCRIPT_DIR)

# Path to bundled ffmpeg (relative to script)
FFMPEG_EXE = SCRIPT_DIR / "assets" / "ffmpeg" / "ffmpeg.exe"

# Logging helper
def log_message(level, msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")

def build_video(video_path, end_card_path=None, line_mode=True, model_name="small", language="English"):
    log_message("INFO", "=== Starting build_video ===")
    log_message("INFO", f"Working directory: {os.getcwd()}")
    
    video_path = Path(video_path).resolve()
    
    # Create edited_videos directory if it doesn't exist
    edited_videos_dir = SCRIPT_DIR / "edited_videos"
    edited_videos_dir.mkdir(exist_ok=True)
    
    # Temporary output with captions (before end card)
    temp_output = edited_videos_dir / f"{video_path.stem}_temp.mp4"
    # Final output path
    final_output = edited_videos_dir / f"{video_path.stem}_Edited.mp4"
    
    log_message("INFO", f"Input video: {video_path}")
    log_message("INFO", f"Language: {language}")
    log_message("INFO", f"Video exists: {video_path.exists()}")
    
    # Path to ASS subtitles — relative path
    ass_path = Path("transcriptions") / f"{video_path.stem}.ass"
    ass_path = ass_path.resolve()

    log_message("INFO", f"ASS file path: {ass_path}")
    
    # Generate ASS captions if not already present
    if not ass_path.exists():
        log_message("INFO", "ASS file not found, generating captions...")
        autocaptioner.mp4_to_ass(video_path, line_mode=line_mode, model_name=model_name)
    
    # Wait for ASS file to exist and be fully written
    max_wait = 500 # Seconds
    elapsed = 0
    log_message("INFO", f"Waiting for ASS file to be written (max {max_wait}s)...")
    while not ass_path.exists() and elapsed < max_wait:
        time.sleep(0.5)
        elapsed += 0.5
    
    if not ass_path.exists():
        log_message("ERROR", f"ASS file not found after {max_wait}s: {ass_path}")
        raise FileNotFoundError(f"ASS file not found after {max_wait}s: {ass_path}")
    
    file_size = ass_path.stat().st_size
    log_message("INFO", f"ASS file found. File size: {file_size} bytes")
    
    log_message("INFO", "Burning closed captions into video...")
    log_message("INFO", f"Using FFmpeg: {FFMPEG_EXE}")
    log_message("INFO", f"FFmpeg exists: {FFMPEG_EXE.exists()}")
    
    # Use relative path from current working directory for FFmpeg filter
    try:
        ass_path_relative = ass_path.relative_to(SCRIPT_DIR)
    except ValueError:
        ass_path_relative = ass_path
    
    ass_path_for_filter = str(ass_path_relative).replace("\\", "/")
    log_message("DEBUG", f"ASS path for filter: {ass_path_for_filter}")
    
    cmd_burn = [
        str(FFMPEG_EXE),
        "-i", str(video_path),
        "-vf", f"ass={ass_path_for_filter}",
        "-c:a", "copy",
        str(temp_output)
    ]
    
    log_message("DEBUG", f"FFmpeg command: {' '.join(cmd_burn)}")

    try:
        log_message("INFO", "Executing FFmpeg command...")
        subprocess.run(cmd_burn, check=True, capture_output=False)
        log_message("INFO", f"Burned video saved to: {temp_output}")
        log_message("INFO", f"Output file exists: {temp_output.exists()}")
        if temp_output.exists():
            log_message("INFO", f"Output file size: {temp_output.stat().st_size} bytes")
        log_message("INFO", "=== Closed caption burn completed successfully ===")
    except subprocess.CalledProcessError as e:
        log_message("ERROR", f"FFmpeg failed with exit code {e.returncode}")
        log_message("ERROR", f"FFmpeg command: {e.cmd}")
        raise RuntimeError(f"FFmpeg failed to burn captions: {e}") from e
    
    # Handle end card concatenation
    if end_card_path and end_card_path.exists():
        log_message("INFO", "=== Concatenating end card ===")
        log_message("INFO", f"End card: {end_card_path}")
        concat_videos(temp_output, end_card_path, final_output)
        log_message("INFO", f"Final video with end card: {final_output}")
    else:
        # Auto-detect end card based on language if not provided
        default_endcard = SCRIPT_DIR / "endcards" / f"{language}.mp4"
        if default_endcard.exists():
            log_message("INFO", f"=== Auto-detected end card for {language} ===")
            log_message("INFO", f"End card: {default_endcard}")
            concat_videos(temp_output, default_endcard, final_output)
            log_message("INFO", f"Final video with end card: {final_output}")
        else:
            # No end card, just rename temp to final
            log_message("INFO", "No end card provided or found, using captioned video as final output")
            temp_output.rename(final_output)
    
    log_message("INFO", f"=== Video processing complete ===")
    log_message("INFO", f"Final output: {final_output}")
    if final_output.exists():
        log_message("INFO", f"Final file size: {final_output.stat().st_size} bytes")


def concat_videos(video1_path, video2_path, output_path):
    """Safely concatenate main video + end card without speed issues."""

    log_message("INFO", "Concatenating main video with end card (timeline-safe)...")

    # Temporary end card with silent audio
    endcard_with_audio = SCRIPT_DIR / "endcard_with_audio.mp4"

    # 1️⃣ Ensure end card has audio
    cmd_add_audio = [
        str(FFMPEG_EXE),
        "-y",
        "-i", str(video2_path),
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-shortest",
        "-c:v", "libx264",
        "-c:a", "aac",
        str(endcard_with_audio)
    ]

    log_message("DEBUG", f"Add silent audio command: {' '.join(cmd_add_audio)}")
    subprocess.run(cmd_add_audio, check=True)

    # 2️⃣ Timeline-safe concat
    cmd_concat = [
        str(FFMPEG_EXE),
        "-y",
        "-i", str(video1_path),
        "-i", str(endcard_with_audio),
        "-filter_complex",
        "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]",
        "-map", "[a]",
        "-movflags", "+faststart",
        str(output_path)
    ]

    log_message("DEBUG", f"Concat command: {' '.join(cmd_concat)}")
    subprocess.run(cmd_concat, check=True)

    # Cleanup
    if video1_path.exists():
        video1_path.unlink()
    if endcard_with_audio.exists():
        endcard_with_audio.unlink()

    log_message("INFO", f"Final video saved to: {output_path}")
