'''
Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.
'''

import os
import torch
from scipy.io import wavfile
from df import enhance, init_df
from pathlib import Path
import numpy as np
import subprocess

# --------------------------------------------------
# Paths
# --------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
FFMPEG_EXE = (SCRIPT_DIR.parent / "assets" / "ffmpeg" / "ffmpeg.exe").resolve()
os.environ["PATH"] = str(FFMPEG_EXE.parent) + os.pathsep + os.environ.get("PATH", "")
TEMP_DIR = (SCRIPT_DIR.parent / "final" / "temp_audio").resolve()

os.environ["PATH"] = str(FFMPEG_EXE.parent) + os.pathsep + os.environ.get("PATH", "")

# --------------------------------------------------
# DeepFilterNet initialization
# --------------------------------------------------
DF_MODEL = None
DF_STATE = None

def get_df_model():
    global DF_MODEL, DF_STATE
    if DF_MODEL is None:
        DF_MODEL, DF_STATE, _ = init_df()
        print("[DEBUG] DeepFilterNet initialized successfully.")
    return DF_MODEL, DF_STATE
    

# --------------------------------------------------
# Helper: run FFmpeg
# --------------------------------------------------
def run_ffmpeg(cmd):
    cmd = [str(c) for c in cmd]
    print(f"[DEBUG] Running FFmpeg: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[ERROR] FFmpeg failed:")
        print(result.stderr)
        raise RuntimeError(f"FFmpeg failed with code {result.returncode}")

# --------------------------------------------------
# Extract audio
# --------------------------------------------------
def extract_audio(video_path: Path, output_wav: Path):
    cmd = [
        str(FFMPEG_EXE), "-y",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "48000",
        str(output_wav),
    ]
    run_ffmpeg(cmd)



from pathlib import Path
import subprocess
import numpy as np
from scipy.io import wavfile

# --------------------------------------------------
# Conform isolated audio to video specs
def conform_isolated_to_video(
    ffmpeg_exe: Path,
    isolated_wav: Path,
    reference_video: Path,
    out_wav: Path,
    target_sr: int = 48000,
    target_channels: int = 2,
):
    """
    Resample & upmix isolated_wav to target_sr/target_channels,
    then trim/pad to exactly match the reference video's audio length.
    """

    out_wav.parent.mkdir(parents=True, exist_ok=True)

    # 1) Extract reference audio (uniform SR/ch layout)
    ref_wav = out_wav.parent / f"{reference_video.stem}_ref.wav"
    subprocess.run([
        str(ffmpeg_exe), "-y",
        "-i", str(reference_video),
        "-vn",
        "-ac", str(target_channels),
        "-ar", str(target_sr),
        "-c:a", "pcm_s16le",
        str(ref_wav)
    ], check=True)

    # 2) Resample/upmix isolated to target
    iso_resamp = out_wav.parent / f"{out_wav.stem}_resamp.wav"
    subprocess.run([
        str(ffmpeg_exe), "-y",
        "-i", str(isolated_wav),
        "-ac", str(target_channels),
        "-ar", str(target_sr),
        "-c:a", "pcm_s16le",
        str(iso_resamp)
    ], check=True)

    # 3) Length match (pad/truncate)
    sr_ref, ref_data = wavfile.read(str(ref_wav))       # (N, 2)
    sr_iso, iso_data = wavfile.read(str(iso_resamp))    # (M, 2 or 1)

    assert sr_ref == target_sr and sr_iso == target_sr, "SR mismatch after resample"

    # Ensure 2D
    if iso_data.ndim == 1:
        iso_data = np.stack([iso_data, iso_data], axis=1)
    elif iso_data.shape[1] == 1 and target_channels == 2:
        iso_data = np.repeat(iso_data, 2, axis=1)

    N = ref_data.shape[0]
    M = iso_data.shape[0]
    if M < N:
        pad = np.zeros((N - M, iso_data.shape[1]), dtype=iso_data.dtype)
        iso_data = np.concatenate([iso_data, pad], axis=0)
    elif M > N:
        iso_data = iso_data[:N, :]

    wavfile.write(str(out_wav), target_sr, iso_data)

    # Cleanup temporaries
    try:
        Path(ref_wav).unlink(missing_ok=True)
        Path(iso_resamp).unlink(missing_ok=True)
    except Exception:
        pass


# --------------------------------------------------
# Gentle pre-normalization (OPTIONAL, SAFE)
# --------------------------------------------------
def prenormalize_audio(input_wav: Path, output_wav: Path, target_lufs=-20):
    """
    Gentle LUFS normalization BEFORE DeepFilterNet.
    Prevents DF from under-driving quiet speech.
    """
    cmd = [
        str(FFMPEG_EXE), "-y",
        "-i", str(input_wav),
        "-af", "highpass=f=100,loudnorm=I=-16:TP=-1.5:LRA=11",
        str(output_wav),
    ]
    run_ffmpeg(cmd)

# --------------------------------------------------
# Post-DF noise gate (CHANGE #3)
# --------------------------------------------------
def apply_noise_gate(input_wav: Path, output_wav: Path):
    """
    Gentle gate to remove residual room tone after DF.
    """
    cmd = [
        str(FFMPEG_EXE), "-y",
        "-i", str(input_wav),
        "-af", "agate=threshold=-45dB:ratio=1.5:knee=6:attack=50:release=300",
        str(output_wav),
    ]
    run_ffmpeg(cmd)

# --------------------------------------------------
# DeepFilterNet isolation (FLOAT OUTPUT — CHANGE #4)
# --------------------------------------------------
def isolate_voice(input_wav: Path, output_wav: Path):
    DF_MODEL, DF_STATE = get_df_model()
    if DF_MODEL is None or DF_STATE is None:
        print("[WARN] DeepFilterNet unavailable.")
        return None

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    temp_wav = output_wav.parent / f"{output_wav.stem}_flt.wav"

    run_ffmpeg([
        str(FFMPEG_EXE), "-y",
        "-i", str(input_wav),
        "-ac", "1",              # mono
        "-ar", "48000",          # 48 kHz (DF training domain)
        "-c:a", "pcm_f32le",     # float32 for torch

        str(temp_wav)
    ])

    sr, audio = wavfile.read(temp_wav)
    audio = audio.astype(np.float32)
    waveform = torch.from_numpy(audio).unsqueeze(0)

    try:
        enhanced = enhance(DF_MODEL, DF_STATE, waveform, sr, atten_lim_db=-30)
        enhanced_np = np.clip(enhanced.squeeze(0).numpy(), -1.0, 1.0)
        wavfile.write(str(output_wav), sr, enhanced_np.astype(np.float32))
    except Exception as e:
        print(f"[WARN] DF enhancement failed: {e}")
        return None
    finally:
        temp_wav.unlink(missing_ok=True)

    return output_wav if output_wav.exists() else None

# --------------------------------------------------
# Full pipeline: extract → prenAorm → DF → gate
# --------------------------------------------------

def process_voice_isolation(video_path: Path, temp_dir: Path = None):
    if temp_dir is None:
        temp_dir = TEMP_DIR
    temp_dir.mkdir(parents=True, exist_ok=True)

    raw_wav   = temp_dir / f"{video_path.stem}_raw.wav"
    pre_wav   = temp_dir / f"{video_path.stem}_prenorm.wav"
    voice_wav = temp_dir / f"{video_path.stem}_voice.wav"
    gated_wav = temp_dir / f"{video_path.stem}_voice_gated.wav"
    conform_wav = temp_dir / f"{video_path.stem}_voice_conformed.wav"

    # 1. Extract
    extract_audio(video_path, raw_wav)

    # 2. Gentle pre-normalization
    prenormalize_audio(raw_wav, pre_wav)

    # 3. DeepFilterNet
    isolated = isolate_voice(pre_wav, voice_wav)
    if not isolated:
        print("[WARN] Voice isolation failed, using raw audio.")
        return raw_wav

    # 4. Noise gate
    apply_noise_gate(isolated, gated_wav)
    

    # Conform: SR=48k, stereo, exact length
    conform_wav = temp_dir / f"{video_path.stem}_voice_conformed.wav"
    conform_isolated_to_video(
        ffmpeg_exe=FFMPEG_EXE,
        isolated_wav=gated_wav,
        reference_video=video_path,
        out_wav=conform_wav,
        target_sr=48000,
        target_channels=2,
    )

    print(f"[DEBUG] Voice isolation complete: {conform_wav}")
    return conform_wav
   