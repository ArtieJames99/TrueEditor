from pathlib import Path
from Audio.voice_isolation import (
    extract_audio,
    prenormalize_audio,
    isolate_voice,
    apply_noise_gate,
)
import sys

def main(video_path: Path):
    if not video_path.exists():
        print(f"[ERROR] File not found: {video_path}")
        sys.exit(1)

    out_dir = Path("tests/output")
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_wav   = out_dir / "raw.wav"
    pre_wav   = out_dir / "prenorm.wav"
    df_wav    = out_dir / "df.wav"
    gated_wav = out_dir / "df_gated.wav"

    print("\n=== TEST: VOICE ISOLATION PIPELINE ===\n")

    print("[1] Extracting audio...")
    extract_audio(video_path, raw_wav)
    print(f"    → {raw_wav}")

    print("[2] Pre-normalizing (-20 LUFS)...")
    prenormalize_audio(raw_wav, pre_wav)
    print(f"    → {pre_wav}")

    print("[3] Running DeepFilterNet...")
    isolated = isolate_voice(pre_wav, df_wav)
    if not isolated:
        print("[FAIL] DeepFilterNet returned None")
        sys.exit(1)
    print(f"    → {df_wav}")

    print("[4] Applying noise gate...")
    apply_noise_gate(df_wav, gated_wav)
    print(f"    → {gated_wav}")

    print("\n✅ TEST COMPLETE")
    print("Compare these files:")
    print(f"  RAW:        {raw_wav}")
    print(f"  DF ONLY:    {df_wav}")
    print(f"  DF + GATE:  {gated_wav}")
    print("\nListen especially to SILENCE sections.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_voice_isolation.py <video_file>")
        sys.exit(1)

    main(Path(sys.argv[1]))
