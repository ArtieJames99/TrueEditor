'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''
"""
Pipeline Bridge: Converts UI data format to backend calls with progress reporting.
Bridges the gap between UI (TrueEditor-UI.py) and backend (Core/build_video.py).
"""

from pathlib import Path
from typing import Callable, Optional, Dict, Any, List
from Core.build_video import build_video


def normalize_platform(platform_str: str) -> str:
    """Convert UI platform names to backend format."""
    platform_map = {
        'Generic': 'instagram',
        'YouTube': 'youtube',
        'Instagram': 'instagram',
        'TikTok': 'tiktok',
        'Facebook': 'facebook',
        'Podcast': 'podcast',
    }
    return platform_map.get(platform_str, 'instagram')


def normalize_language(language_str: str) -> Optional[str]:
    """Convert UI language names to backend format."""
    if language_str.lower() == 'auto':
        return None
    return language_str


def cleanup_level_to_string(cleanup_str: str) -> str:
    """Ensure cleanup level is in correct format."""
    level_map = {
        'off': 'off',
        'light': 'light',
        'full': 'full',
    }
    return level_map.get(cleanup_str.lower(), 'off')


def pipeline_runner(
    files: List[str],
    output_folder: str,
    language: str,
    platform: str,
    caption_style: Dict[str, Any],
    audio_settings: Dict[str, Any],
    branding: Dict[str, Any],
    test: bool = False,
    report: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Any]:
    """
    Main pipeline orchestrator: processes video batch with progress reporting.
    
    Args:
        files: List of video file paths
        output_folder: Where to save edited videos
        language: Language for captions (or 'Auto')
        platform: Target platform ('YouTube', 'Instagram', etc.)
        caption_style: Caption styling dict from UI
        audio_settings: Audio settings dict from UI
        branding: Branding settings dict from UI
        test: If True, process only first video for testing
        report: Callback function report(percent: int, message: str)
    
    Returns:
        Dict with 'success': bool, 'processed': int, 'failed': int, 'errors': list
    """
    
    def emit(percent: int, message: str):
        """Safely emit progress reports."""
        if report:
            try:
                report(percent, message)
            except Exception as e:
                print(f"[WARN] Report callback failed: {e}")
    
    # Validation
    output_folder = Path(output_folder).resolve()
    if not output_folder.exists():
        try:
            output_folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            err = f"Failed to create output folder: {e}"
            emit(0, err)
            return {
                'success': False,
                'processed': 0,
                'failed': 0,
                'errors': [err],
            }
    
    if not files:
        err = "No video files provided"
        emit(0, err)
        return {
            'success': False,
            'processed': 0,
            'failed': 0,
            'errors': [err],
        }
    
    # Convert to test mode if requested
    files_to_process = [files[0]] if test else files
    total_files = len(files_to_process)
    
    emit(5, f"Starting pipeline: {total_files} video(s) to process")
    
    # Parse settings
    language_code = normalize_language(language)
    platform_code = normalize_platform(platform)
    cleanup_level = cleanup_level_to_string(audio_settings.get('cleanup_level', 'off'))
    music_volume = audio_settings.get('music_volume', 0.22)
    voice_isolation_enabled = audio_settings.get('voice_isolation', False)
    
    music_path = None
    if audio_settings.get('background_music', {}).get('enabled'):
        music_file = audio_settings['background_music'].get('path', '')
        if music_file and Path(music_file).exists():
            music_path = Path(music_file)
    
    processed = 0
    failed = 0
    errors = []
    
    # Extract position data from caption_style
    caption_position = caption_style.get('position', None)
    
    # Process each video
    for idx, video_file in enumerate(files_to_process, start=1):
        try:
            video_path = Path(video_file).resolve()
            
            if not video_path.exists():
                error_msg = f"Video not found: {video_path}"
                emit(int(5 + (idx / total_files) * 90), f"[{idx}/{total_files}] {error_msg}")
                errors.append(error_msg)
                failed += 1
                continue
            
            emit(int(5 + (idx - 1) / total_files * 90), f"[{idx}/{total_files}] Processing {video_path.name}...")
            
            # Determine end card from UI branding settings (if provided and enabled)
            end_card_path_to_use = None
            try:
                if branding and branding.get('enabled', False):
                    btype = (branding.get('type') or '').strip().lower()
                    # Only treat a provided branding video as an end-card when type is End Card
                    if btype in ('end card', 'endcard', 'end_card'):
                        video_path_str = branding.get('video_path', '') or ''
                        if video_path_str:
                            p = Path(video_path_str)
                            if p.exists():
                                end_card_path_to_use = p
                                emit(int(5 + (idx - 1) / total_files * 90), f"[{idx}/{total_files}] Using end card: {p.name}")
                            else:
                                emit(int(5 + (idx - 1) / total_files * 90), f"[{idx}/{total_files}] End card path not found: {video_path_str}")
            except Exception as e:
                emit(int(5 + (idx - 1) / total_files * 90), f"[{idx}/{total_files}] End card processing error: {e}")
            
            # Call backend build_video with position data
            build_video(
                video_path=video_path,
                end_card_path=end_card_path_to_use,
                model_name="small",  # TODO: make this configurable from UI
                language=language_code,
                cleanup_level=cleanup_level,
                music_path=music_path,
                music_volume=music_volume,
                platform=platform_code,
                voice_isolation_enabled=voice_isolation_enabled,
                captions_enabled=caption_style.get('enabled', True),
                output_folder=str(output_folder),  # Pass the output folder
                caption_position=caption_position,  # Pass position data
            )
            
            processed += 1
            emit(int(5 + (idx / total_files) * 90), f"[{idx}/{total_files}] ✓ {video_path.name} complete")
            
        except Exception as e:
            error_msg = f"Error processing {Path(video_file).name}: {str(e)}"
            emit(int(5 + (idx / total_files) * 90), f"[{idx}/{total_files}] ✗ {error_msg}")
            errors.append(error_msg)
            failed += 1
    
    # Final report
    emit(95, f"Processed: {processed}/{total_files} videos successfully")
    
    if failed > 0:
        emit(100, f"Batch complete with {failed} error(s). Check logs.")
        success = False
    else:
        emit(100, "Batch complete! All videos processed successfully.")
        success = True
    
    return {
        'success': success,
        'processed': processed,
        'failed': failed,
        'errors': errors,
    }


# Alias for UI connection
def pipeline(**kwargs) -> Dict[str, Any]:
    """Wrapper to match UI's expected pipeline signature."""
    return pipeline_runner(**kwargs)
