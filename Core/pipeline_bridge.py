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
import sys
from pathlib import Path
import logging

# Add the project root to sys.path to allow absolute imports
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

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
    base_color_hex="#FFFFFF",
    karaoke_color_hex="#FF0000",
    report: Optional[Callable[[int, str], None]] = None,
    stop_pipeline: bool = False,
) -> Dict[str, Any]:
    """Main pipeline orchestrator: processes video batch with progress reporting.
    Ensures caption positions and karaoke tags are consistently applied."""

    def emit(percent: int | None, message: str | None):
        if report:
            try:
                report(percent, message)
                return
            except Exception as e:
                logging.getLogger("trueeditor.pipeline").warning(f"Report callback failed: {e}")
        # last resort (do not print in GUI exe)
        if message:
            logging.getLogger("trueeditor.pipeline").info(message)

    # Check if the pipeline should stop
    if stop_pipeline:
        emit(0, "Pipeline stopped by user.")
        return {'success': False, 'processed': 0, 'failed': 0, 'errors': ["Pipeline stopped by user."]}


    output_folder = Path(output_folder).resolve()
    if not output_folder.exists():
        try:
            output_folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            err = f"Failed to create output folder: {e}"
            emit(0, err)
            return {'success': False, 'processed': 0, 'failed': 0, 'errors': [err]}

    if not files:
        err = "No video files provided"
        emit(0, err)
        return {'success': False, 'processed': 0, 'failed': 0, 'errors': [err]}

    files_to_process = [files[0]] if test else files
    total_files = len(files_to_process)
    emit(5, f"Starting pipeline: {total_files} video(s) to process")

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

    processed, failed = 0, 0
    errors = []

    # Extract caption position and fallback defaults
    caption_position = caption_style.get('position') or {'x': 0.5, 'y': 0.75, 'anchor': 5}

    for idx, video_file in enumerate(files_to_process, start=1):
        try:
            video_path = Path(video_file).resolve()
            if not video_path.exists():
                msg = f"Video not found: {video_path}"
                emit(int(5 + (idx / total_files) * 90), f"[{idx}/{total_files}] {msg}")
                errors.append(msg)
                failed += 1
                continue

            # Determine end card
            end_card_path_to_use = None
            if branding.get('enabled') and (branding.get('type') or '').lower() in ('end card', 'endcard', 'end_card'):
                b_video = Path(branding.get('video_path', '') or '').resolve()
                if b_video.exists():
                    end_card_path_to_use = b_video
                if b_video.exists():
                    end_card_path_to_use = b_video
                    emit(int(5 + (idx-1)/total_files*90), f"[{idx}/{total_files}] Using end card: {b_video.name}")

            # Check if an existing ASS file can be reused
            trans_dir = Path(output_folder).parent / "transcriptions"
            trans_dir.mkdir(parents=True, exist_ok=True)
            ass_file = trans_dir / f"{video_path.stem}.ass"
            use_existing = not caption_style.get('regenerate', False) and ass_file.exists()

            if use_existing:
                ass_path = ass_file
                emit(18, "STAGE_UPDATE:transcription:active")
                emit(19, f"Using existing captions: {ass_file.name}")
                emit(38, "STAGE_UPDATE:transcription:completed")
            else:
                vw, vh = get_video_resolution(video_path)
                preview_h = int(caption_style.get('preview_canvas_height') or 640)

                style_obj = style_from_ui(caption_style, vw, vh, preview_canvas_height=preview_h)
                length_mode = caption_style.get('length_mode', 'line')
                model_name = (caption_style.get('model_name') or 'small').lower()
                karaoke_settings = caption_style.get('karaoke', {})

                emit(18, "STAGE_UPDATE:transcription:active")
                ass_path = mp4_to_ass(
                    video_path,
                    model_name=model_name,
                    language=language_code,
                    style=style_obj,
                    position=caption_position,   # Ensures proper \pos and \an tags
                    length_mode=length_mode,
                    karaoke=karaoke_settings,
                    base_color_hex=base_color_hex,
                    karaoke_color_hex=karaoke_color_hex
                )
                ass_path = Path(ass_path).resolve()
                emit(38, "STAGE_UPDATE:transcription:completed")

            if caption_style.get('enabled', True) and (ass_path is None or not ass_path.exists()):
                raise RuntimeError(f"Caption generation failed: ASS file not created ({ass_path})")

            # Build final video
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
                caption_position=caption_position,
                ass_path=ass_path,
                caption_style=caption_style
            )

            processed += 1
            emit(int(5 + (idx / total_files) * 90), f"[{idx}/{total_files}] ✓ {video_path.name} complete")

        except Exception as e:
            msg = f"Error processing {video_path.name}: {e}"
            emit(int(5 + (idx / total_files) * 90), f"[{idx}/{total_files}] ✗ {msg}")
            errors.append(msg)
            failed += 1

    # Final report
    emit(95, f"Processed: {processed}/{total_files} videos successfully")
    success = failed == 0
    emit(100, f"Batch complete{' with errors' if not success else '!'}")

    return {'success': success, 'processed': processed, 'failed': failed, 'errors': errors}


# Alias for UI connection
def pipeline(**kwargs) -> Dict[str, Any]:
    """Wrapper to match UI's expected pipeline signature."""
    return pipeline_runner(**kwargs)
