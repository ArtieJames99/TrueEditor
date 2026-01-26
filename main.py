'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''
from pathlib import Path
import sys
import atexit
import signal
import argparse
import warnings
import subprocess
from ui.TrueEditor_UI import main as ui_main
from multiprocessing import freeze_support
from Core.logging_utils import setup_logging
from ui.TrueEditor_UI import main as ui_main


warnings.filterwarnings(
    "ignore",
    message=".*AudioMetaData.*",
)

# Global list to keep track of active subprocesses
_active_subprocesses = []
# Global flag to indicate if the pipeline should stop
_stop_pipeline = False
# Flag to prevent multiple cleanup calls
_cleanup_called = False

def cleanup():
    """Cleanup function to kill all subprocesses and perform other cleanup tasks."""
    global _cleanup_called
    if _cleanup_called:
        return  # Already cleaned up
    
    print("Cleaning up...")
    _cleanup_called = True

    # Set the stop flag to True
    global _stop_pipeline
    _stop_pipeline = True

    # Kill all tracked subprocesses with force termination
    global _active_subprocesses
    for process in _active_subprocesses:
        try:
            if process.poll() is None:  # Check if the process is still running
                # First try to terminate gracefully
                process.terminate()
                try:
                    # Wait a short time for graceful termination
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # If it doesn't terminate gracefully, force kill
                    process.kill()
                    print(f"Force killed process: {process.pid}")
                else:
                    print(f"Gracefully terminated process: {process.pid}")
        except Exception as e:
            print(f"Failed to kill process: {process.pid}: {e}")
    
    # Clear the list of active subprocesses
    _active_subprocesses = []


    # Cleanup temporary files and directories
    try:
        # Define the directories to clean up
        temp_dirs = [
            Path(__file__).parent.parent / "TrueEditor" / "temp_audio",
            Path(__file__).parent.parent / "TrueEditor" / "temp_preview",
        ]

        for temp_dir in temp_dirs:
            if temp_dir.exists():
                # Delete all files in the directory
                for file in temp_dir.glob("*"):
                    try:
                        file.unlink()
                    except Exception as e:
                        print(f"Failed to delete file {file}: {e}")

                # Optionally, delete the directory itself
                try:
                    temp_dir.rmdir()
                except Exception as e:
                    print(f"Failed to delete directory {temp_dir}: {e}")

        # Cleanup temporary video files in the edited_videos directory
        edited_videos_dir = Path(__file__).parent.parent / "TrueEditor" / "edited_videos"
        if edited_videos_dir.exists():
            for file in edited_videos_dir.glob("*"):
                if file.is_file() and ("_captioned" in file.name or "_timeline" in file.name):
                    try:
                        file.unlink()
                        print(f"Deleted temporary file: {file}")
                    except Exception as e:
                        print(f"Failed to delete temporary file {file}: {e}")

        print("Temporary files and directories cleaned up.")
    except Exception as e:
        print(f"Error during file cleanup: {e}")

    # Add any other cleanup tasks here
    print("Cleanup complete.")
    
def signal_handler(sig, frame):
    """Handle signals for graceful shutdown."""
    print(f"Received signal {sig}, shutting down gracefully...")
    cleanup()
    sys.exit(0)

def setup_graceful_exit():
    """Setup graceful exit handlers."""
    # Register cleanup function to be called at exit
    atexit.register(cleanup)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

def process_folder(folder_path, args):
    import Core.build_video as build_video
    folder = Path(folder_path).resolve()

    if not folder.exists():
        print(f"[ERROR] Folder not found: {folder}")
        return

    videos = sorted(folder.glob("*.mp4"))

    if not videos:
        print(f"[INFO] No mp4 files found in {folder}")
        return

    print(f"[INFO] Found {len(videos)} video(s)")
    print(f"[INFO] Language: {args.language}")
    print(f"[INFO] Model: {args.model}")
    print(f"[INFO] Audio enabled: {args.audio}\n")

    for idx, video in enumerate(videos, start=1):
        if video.stem.endswith("_Edited"):
            print(f"[SKIP] Already edited: {video.name}")
            continue

        print(f"\n=== [{idx}/{len(videos)}] Processing {video.name} ===")
        
        # Reset cleanup flags for new processing
        global _cleanup_called, _stop_pipeline, _active_subprocesses
        _cleanup_called = False
        _stop_pipeline = False
        _active_subprocesses = []

        try:
            build_video.build_video(
                video_path=video,
                end_card_path=Path(args.endcard) if args.endcard else None,
                model_name=args.model,
                language=args.language,
                music_path=Path(args.music) if args.music else None,
                music_volume=args.music_volume,
                voice_isolation_enabled=args.voice_isolation,
                captions_enabled=args.captions
            )
        except Exception as e:
            print(f"[ERROR] Failed processing {video.name}")
            print(e)
            continue

    print("\n=== Batch processing complete ===")



def main():
    parser = argparse.ArgumentParser(description="Burn captions into video(s).")

    # ---- INPUT ----
    parser.add_argument(
        "input",
        help="Path to a video file OR a folder of videos"
    )

    # ---- CAPTIONS OPTIONS ----
    parser.add_argument(
        "--captions",
        action="store_true",
        help="Enable captions burning"
    )

    # ---- VIDEO OPTIONS ----
    parser.add_argument(
        "--endcard",
        type=str,
        default=None,
        help="Optional end card video file (e.g., EndCard.mp4)"
    )

    parser.add_argument(
        "--model",
        default="small",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size (default: small)"
    )

    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help=(
            "Optional: Language for captions. "
            "Whisper will auto-detect if not specified. "
            "Use this if detection is incorrect."
        )
    )

    # ---- AUDIO OPTIONS ----
    parser.add_argument(
        "--audio",
        action="store_true",
        help="Enable audio enhancement pipeline"
    )

    parser.add_argument(
        "--music",
        default=None,
        help="Optional instrumental music file (mp3/wav)"
    )

    parser.add_argument(
        "--music-volume",
        type=float,
        default=0.5,
        help="Background music volume (default: 0.5)"
    )

    # --- Voice Isolation toggle ---
    parser.add_argument(
        "--voice-isolation",
        action="store_true",
        help="Enable DeepFilterNet voice Isolation"
)
    

    args = parser.parse_args()
    input_path = Path(args.input)

    # Convert endcard path to Path object if provided
    if args.endcard:
        endcard_path = Path(args.endcard).resolve()
        if not endcard_path.exists():
            print(f"[ERROR] End card file not found: {endcard_path}")
            endcard_path = None
    else:
        endcard_path = None

    if input_path.is_dir():
        process_folder(input_path, args)
    else:
        from Core import build_video
        build_video.build_video(
            video_path=input_path,
            end_card_path=endcard_path,
            model_name=args.model,
            language=args.language,
            music_path=Path(args.music) if args.music else None,
            music_volume=args.music_volume,
            voice_isolation_enabled=args.voice_isolation,
            captions_enabled=args.captions,
        )
# comment out when intending to use termnal only commands
def main():
    setup_graceful_exit()
    setup_logging()
    ui_main()

if __name__ == "__main__":
    freeze_support()
    main()