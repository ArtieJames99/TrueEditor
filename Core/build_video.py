'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''
from cProfile import label
import subprocess
from pathlib import Path
import time
import os
import main
from datetime import datetime
from typing import Optional, Dict
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
    output_folder: str = "../final/edited_videos",
    caption_position: Optional[Dict[str, float]] = None,
    watermark_path: Optional[Path] = None,  
    watermark_position: Optional[Dict[str, float]] = None,  
    watermark_opacity: float = 0.5, 
    watermark_size: float = 15.0,  
    target_lufs: float = -14.0
) -> None:
    """
    Full TrueEdits video build pipeline:
        1. Generate ASS captions
        2. Burn captions
        3. Append end card (if any)
        4. Final audio processing over full timeline (voice isolation, normalization, music)
        5. Cleanup temp files
    
    Args:
        video_path: Path to input video
        end_card_path: Optional path to end card video
        model_name: Whisper model size
        language: Language for captions (None for auto-detect)
        cleanup_level: Audio cleanup level ('off', 'light', 'full')
        music_path: Optional path to background music file
        music_volume: Music volume (0.0-1.0)
        platform: Target platform ('instagram', 'youtube', 'tiktok', 'facebook', 'podcast')
        voice_isolation_enabled: Enable voice isolation
        captions_enabled: Enable caption generation and burning
        output_folder: Optional override for output directory (default: final/edited_videos)
        caption_position: Optional dict with 'x' and 'y' keys (0.0-1.0 normalized)
    """

    video_path = Path(video_path).resolve()
    log_message("INFO", f"=== Starting build_video: {video_path.name} ===")

    if not video_path.exists():
        raise FileNotFoundError(video_path)

    # Use provided output folder or default to final/edited_videos
    if output_folder:
        edited_videos_dir = Path(output_folder).resolve()
    else:
        edited_videos_dir = SCRIPT_DIR.parent / "final" / "edited_videos"
    
    edited_videos_dir.mkdir(parents=True, exist_ok=True)

    temp_captioned = edited_videos_dir / f"{video_path.stem}_captioned.mp4"
    temp_timeline = edited_videos_dir / f"{video_path.stem}_timeline.mp4"
    final_output = edited_videos_dir / f"{video_path.stem}_Edited.mp4"

    # --------------------------------------------------
    # Generate captions
    # --------------------------------------------------

    # Generate captions with position data
    ass_path = SCRIPT_DIR.parent / "final" / "transcriptions" / f"{video_path.stem}.ass"
    if not ass_path.exists():
        if captions_enabled:
            log_message("INFO", "Generating captions...")
            # Pass position data to captioner
            captioner.mp4_to_ass(
                video_path, 
                model_name=model_name, 
                language=language,
                position=caption_position  # Pass position data
            )
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
        log_message("INFO", "Captions disabled. - Copying video")
        cmd_copy = [
            str(FFMPEG_EXE),
            "-y",
            "-i", str(video_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(temp_captioned),
        ]
        subprocess.run(cmd_copy, check=True)

    # --------------------------------------------------
    # Add Watermark
    # --------------------------------------------------
    def add_watermark(
        input_video: Path,
        watermark_path: Optional[Path],
        output_video: Path,
        position: Dict[str, float] = {'x': 0.95, 'y': 0.95},  # Bottom-right by default
        opacity: float = 0.5,
        width_percent: float = 15.0,  # 15% of video width
        margin_percent: float = 5.0   # 5% margin from edges
    ) -> None:
        """
        Add watermark to video using FFmpeg.
        
        Args:
            input_video: Input video path
            watermark_path: Watermark image/video path
            output_video: Output video path
            position: Dict with 'x' and 'y' (0.0-1.0 normalized)
            opacity: Watermark opacity (0.0-1.0)
            width_percent: Watermark width as percentage of video width
            margin_percent: Margin from edges as percentage of video width
        """
        if not watermark_path or not watermark_path.exists():
            log_message("INFO", "No watermark provided, skipping watermark step")
            import shutil
            shutil.copy(input_video, output_video)
            return
        
        log_message("INFO", f"Adding watermark: {watermark_path.name}")
        
        # Get video dimensions
        def get_video_dimensions(path: Path) -> tuple[int, int]:
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
        
        video_w, video_h = get_video_dimensions(input_video)
        
        # Calculate watermark position and size
        watermark_w = int(video_w * (width_percent / 100))
        margin = int(video_w * (margin_percent / 100))
        
        # Calculate position (convert normalized 0-1 to pixel coordinates)
        # x=0.95 means 95% from left (bottom-right)
        # y=0.95 means 95% from top (bottom)
        pos_x = int(video_w * position['x']) - margin
        pos_y = int(video_h * position['y']) - margin
        
        # Ensure watermark doesn't go outside video bounds
        pos_x = max(0, min(pos_x, video_w - watermark_w))
        pos_y = max(0, min(pos_y, video_h - int(watermark_w * 0.5)))  # Assume 2:1 aspect ratio
        
        # Build FFmpeg filter
        watermark_filter = f"movie='{watermark_path.as_posix()}'[wm];"
        
        # Scale watermark
        watermark_filter += f"[wm]scale={watermark_w}:-1[wm_scaled];"
        
        # Set opacity
        watermark_filter += f"[wm_scaled]format=rgba,colorchannelmixer=aa={opacity}[wm_opacity];"
        
        # Overlay watermark
        watermark_filter += f"[0:v][wm_opacity]overlay={pos_x}:{pos_y}:enable='gt(t,0)'[v]"
        
        cmd = [
            str(FFMPEG_EXE),
            "-y",
            "-i", str(input_video),
            "-vf", watermark_filter,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-c:a", "copy",
            "-preset", "medium",
            "-crf", "23",
            str(output_video)
        ]
        
        log_message("DEBUG", "Watermark FFmpeg CMD: " + " ".join(cmd))
        
        try:
            subprocess.run(cmd, check=True)
            log_message("INFO", f"Watermark added successfully: {output_video}")
        except subprocess.CalledProcessError as e:
            log_message("ERROR", f"Failed to add watermark: {e}")
            raise

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
        concat_videos(timeline_input, end_card_path, temp_timeline)
        timeline_input = temp_timeline
    else:
        temp_timeline.unlink(missing_ok=True)
        timeline_input.rename(temp_timeline)
        timeline_input = temp_timeline


    timeline_stem = temp_timeline.stem

    # --------------------------------------------------
    # audio processing
    # --------------------------------------------------

    audio_features_enabled = (
        voice_isolation_enabled or
        music_path is not None or 
        normalize_audio is not None or
        cleanup_level in ("light", "full")
    )

    temp_isolated = None
    if audio_features_enabled:
        log_message("INFO", "Processing audio...")

        TEMP_DIR.mkdir(parents=True, exist_ok=True)

        # Step 1: Voice isolation (replace audio with isolated)
        if voice_isolation_enabled:
            log_message("INFO", f"Running voice isolation on {timeline_input.name}...")
            isolated_audio_path = process_voice_isolation(timeline_input, TEMP_DIR)

            if isolated_audio_path and isolated_audio_path.exists():
                log_message("INFO", f"Voice isolation complete: {isolated_audio_path}")

                # Normalize isolated audio
                norm_isolated = TEMP_DIR / f"{timeline_input.stem}_isolated_norm.wav"
                normalize_audio(isolated_audio_path, norm_isolated)
                isolated_audio_path = norm_isolated

                # Process audio to replace with isolated (no music yet)
                temp_isolated = edited_videos_dir / f"{video_path.stem}_isolated.mp4"
                process_audio(
                    video_in=timeline_input,
                    video_out=temp_isolated,
                    music_path=None,
                    music_volume=music_volume,
                    cleanup_level=cleanup_level,
                    platform=platform,
                    isolated_audio=isolated_audio_path,
                    normalize=True,
                    target_lufs=target_lufs
                )
                timeline_input = temp_isolated
                log_message("INFO", f"Isolated video created: {temp_isolated}")
            else:
                log_message("WARN", "Voice isolation failed, proceeding with original audio.")

        # Step 2: Add music (to the now possibly isolated video)
        if music_path:
            log_message("INFO", f"Adding background music to {timeline_input.name}...")
            process_audio(
                video_in=timeline_input,
                video_out=final_output,
                music_path=music_path,
                music_volume=music_volume,
                cleanup_level="off",  # Cleanup already applied if isolated
                platform=platform,
                isolated_audio=None,  # Use audio from video_in
                normalize=True,
                target_lufs=target_lufs
            )
        else:
            # No music, just copy to final output
            if timeline_input != final_output:
                import shutil
                shutil.copy(timeline_input, final_output)

    else:
        # No audio features → skip audio processing entirely
        log_message("INFO", "Skipping audio processing (no audio features enabled).")
        # Just copy the timeline video to final output
        import shutil
        shutil.copy(timeline_input, final_output)

    # --------------------------------------------------
    # CLEANUP TEMP FILES
    # --------------------------------------------------

    log_message("INFO", "Cleaning up temporary files...")
    for stem in [video_path.stem, timeline_stem]:
        for f in TEMP_DIR.glob(f"{stem}*.wav"):
            f.unlink(missing_ok=True)

    temp_captioned.unlink(missing_ok=True)
    temp_timeline.unlink(missing_ok=True)
    if temp_isolated and temp_isolated.exists():
        temp_isolated.unlink(missing_ok=True)

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