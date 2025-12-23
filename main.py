from pathlib import Path
import build_video
import argparse


def process_folder(folder_path, args):
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
    print(f"[INFO] Model: {args.model}\n")

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
                language=args.language
            )
        except Exception as e:
            print(f"[ERROR] Failed processing {video.name}")
            print(e)
            continue

    print("\n=== Batch processing complete ===")


def main():
    parser = argparse.ArgumentParser(description="Burn captions into video(s).")

    parser.add_argument(
        "input",
        help="Path to a video file OR a folder of videos"
    )

    parser.add_argument(
        "--endcard",
        default=None,
        help="Optional end card video"
    )

    parser.add_argument(
        "--model",
        default="small",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size (default: small)"
    )

    parser.add_argument(
        "--language",
        default="English",
        help="Language for captions and end card selection"
    )

    args = parser.parse_args()
    input_path = Path(args.input)

    if input_path.is_dir():
        process_folder(input_path, args)
    else:
        build_video.build_video(
            video_path=input_path,
            end_card_path=Path(args.endcard) if args.endcard else None,
            model_name=args.model,
            language=args.language
        )


if __name__ == "__main__":
    main()
