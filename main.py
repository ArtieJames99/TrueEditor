from pathlib import Path
import argparse
import warnings


warnings.filterwarnings(
    "ignore",
    message=".*AudioMetaData.*",
)

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

if __name__ == "__main__":
    main()