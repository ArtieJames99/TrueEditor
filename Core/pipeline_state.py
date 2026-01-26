'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''
import subprocess
from pathlib import Path

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
    for process in _active_subprocesses.copy():
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)  # ensure OS releases handles
                    print(f"Force killed process: {process.pid}")
                else:
                    print(f"Gracefully terminated process: {process.pid}")
        except Exception as e:
            print(f"Failed to kill process {getattr(process, 'pid', 'unknown')}: {e}")
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

    # Reset the _stop_pipeline flag to False after cleanup
    _stop_pipeline = False
    _cleanup_called = False

    # Add any other cleanup tasks here
    print("Cleanup complete.")

