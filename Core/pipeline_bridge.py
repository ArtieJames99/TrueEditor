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
from Captions.captioner import get_video_resolution, mp4_to_ass, style_from_ui


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
    """Convert UI language names to Whisper language codes."""
    language_map = {
        'auto': None,
        'english': 'en',
        'spanish': 'es',
        'chinese': 'zh',
        'french': 'fr',
        'german': 'de',
        'italian': 'it',
        'tagalog': 'tl',
        'hindi': 'hi',
        'arabic': 'ar',
        'portuguese': 'pt',
        'russian': 'ru',
        'japanese': 'ja',
        'korean': 'ko',
        'vietnamese': 'vi',
        'thai': 'th',
        'indonesian': 'id',
        'dutch': 'nl',
        'polish': 'pl',
        'turkish': 'tr',
        'hebrew': 'he',
        'swahili': 'sw',
        'malay': 'ms',
        'bengali': 'bn',
        'punjabi': 'pa',
        'javanese': 'jv',
        'tamil': 'ta',
        'telugu': 'te',
        'marathi': 'mr',
        'urdu': 'ur',
        'persian': 'fa',
        'ukrainian': 'uk',
        'greek': 'el',
        'czech': 'cs',
        'hungarian': 'hu',
        'swedish': 'sv',
        'finnish': 'fi',
        'danish': 'da',
        'norwegian': 'no',
        'romanian': 'ro',
        'bulgarian': 'bg',
        'serbian': 'sr',
        'croatian': 'hr',
        'slovak': 'sk',
        'slovenian': 'sl',
        'lithuanian': 'lt',
        'latvian': 'lv',
        'estonian': 'et',
        'filipino': 'fil',
    }
    return language_map.get(language_str.lower(), language_str.lower())

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
            
            emit(13, f"INFO: captions_enabled={caption_style.get('enabled', True)} "
                    f"voice_iso={voice_isolation_enabled} "
                    f"music={'yes' if music_path else 'no'} "
                    f"cleanup={cleanup_level}")
            
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
                                emit(int(5 + (idx - 1) / total_files * 90),
                                    f"[{idx}/{total_files}] Using end card: {p.name}")
                            else:
                                emit(int(5 + (idx - 1) / total_files * 90),
                                    f"[{idx}/{total_files}] End card path not found: {video_path_str}")
            except Exception as e:
                emit(int(5 + (idx - 1) / total_files * 90),
                    f"[{idx}/{total_files}] End card processing error: {e}")

            
            ass_path = None

            existing_ass = (Path(output_folder).resolve().parent / "transcriptions" / f"{Path(video_file).stem}.ass")
            caption_style['regenerate'] = False
            use_existing = (caption_style['regenerate'] is False) and existing_ass.exists()


            emit(12, f"INFO: transcriptions_dir={existing_ass.parent}")
            emit(12, f"INFO: use_existing={use_existing}  exists={existing_ass.exists()}  ass={existing_ass.name}")

            if use_existing:
                ass_path = existing_ass
                emit(18, "STAGE_UPDATE:transcription:active")
                emit(19, f"INFO: Using existing captions: {existing_ass.name}")
                emit(38, "STAGE_UPDATE:transcription:completed")
                emit(40, "STAGE_UPDATE:captions:active")
            else:
                vw, vh = get_video_resolution(video_path)
                preview_h = None
                try:
                    preview_h = int(caption_style.get('preview_canvas_height') or 0)
                except Exception:
                    preview_h = 0
                if not preview_h:
                    # Fallback if UI doesn't supply it yet; adjust to your actual preview widget height
                    preview_h = 640

                # Build AssStyle from UI dict and geometry, with Option B scaling
                style_obj = style_from_ui(caption_style, vw, vh, preview_canvas_height=preview_h)
                length_mode= caption_style.get('length_mode', 'line')
                position   = caption_style.get('position', {'x': 0.5, 'y': 0.75})
                model_name = (caption_style.get('model_name') or 'small').lower()

                emit(18, "STAGE_UPDATE:transcription:active")
                ass_path = mp4_to_ass(
                    video_path,
                    model_name=model_name,
                    language=language_code,   # already normalized above
                    style=style_obj,
                    position=position,
                    length_mode=length_mode
                )
                emit(38, "STAGE_UPDATE:transcription:completed")
                emit(40, "STAGE_UPDATE:captions:active")



            
            # Call backend build_video with position data

            build_video(
                video_path=video_path,
                end_card_path=end_card_path_to_use,
                model_name=caption_style.get('model_name', 'small'),
                language=language_code,
                cleanup_level=cleanup_level,
                music_path=music_path,
                music_volume=music_volume,
                platform=platform_code,
                voice_isolation_enabled=voice_isolation_enabled,
                captions_enabled=caption_style.get('enabled', True),
                output_folder=str(output_folder),
                caption_position=caption_style.get('position'), # build_video falls back
                ass_path=ass_path                              
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
