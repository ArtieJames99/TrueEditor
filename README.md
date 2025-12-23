# TrueEditor
Auto Closed Captioning &amp; end card editing for short form content

TrueEditor is a Windows-first Python tool that automatically **generates, styles, and burns closed captions** into videos using OpenAI Whisper and FFmpeg. It is designed specifically for **vertical (9:16) social media videos** and avoids common platform UI cutoffs on Instagram, Facebook, and similar platforms.

This project supports:

* Single video processing
* Batch folder processing
* Multiple Whisper model sizes
* Language-aware captioning (English / Spanish)
* Caption-safe placement (≈1/3 up from bottom)

---

## Features

* 🎤 **Automatic speech-to-text** via Whisper
* 🎬 **Burn-in captions** using FFmpeg + ASS subtitles
* 📐 Optimized for **9:16 vertical video**
* 🧠 Handles rotated phone footage correctly
* 📂 Batch processing of folders
* 🌎 Language selection (English / Spanish)
* 🪟 Windows-safe paths and execution

---

## Project Structure

```
True/
├── main.py                 # Entry point (single or batch mode)
├── build_video.py          # Handles caption burn + end card
├── autocaptioner.py        # Generates ASS captions using Whisper
├── assets/
│   └── ffmpeg/
│       └── ffmpeg.exe
├── transcriptions/         # Generated .ass subtitle files
├── video_files/            # Input videos
├── .venv/                  # Python virtual environment
└── README.md
```

---

## Requirements

* Windows 10 / 11
* Python **3.10 or newer**
* FFmpeg (bundled locally)

### Python Dependencies

Installed via pip:

```
whisper
ffmpeg-python
numpy
torch
```

> ⚠️ Whisper requires PyTorch. First run may take longer due to model download.

---

## Setup Instructions

### 1. Create & activate virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

(or install manually if no requirements.txt yet)

---

## Usage

### ▶️ Single Video

```powershell
python main.py "video_files\example.mp4"
```

Optional flags:

```powershell
python main.py "video.mp4" --model small --language Spanish
```

| Option       | Description                                                |
| ------------ | ---------------------------------------------------------- |
| `--model`    | Whisper model (`tiny`, `base`, `small`, `medium`, `large`) |
| `--language` | Caption language (English / Spanish)                       |
| `--endcard`  | Optional end card video                                    |

---

### 📂 Batch Folder Processing

```powershell
python main.py "video_files"
```

* Processes all `.mp4` files in the folder
* Skips files already marked `_Edited`
* Outputs burned videos next to originals

---

## Caption Placement (Important)

TrueCaptions uses **ASS subtitle styling** to ensure captions are:

* Bottom-centered
* Raised to ~**1/3 up the screen**
* Safe from Instagram / Facebook UI overlays

### Key Details

* Target format: **9:16 vertical video**
* ASS header uses:

```
PlayResX: 1080
PlayResY: 1920
```

* Captions are anchored using:

```
Alignment: 2   # bottom-center
MarginV: 640   # ~1/3 of 1920
```

> ⚠️ ASS ignores rotation metadata. PlayRes must match the **visual orientation**, not the stored frame size.

---

## Output

* Caption files: `transcriptions/<video_name>.ass`
* Final video: `<video_name>_burned.mp4`

Audio is copied directly (no re-encode).

---

## Common Issues

### Captions stuck in the center

* Your video is rotated via metadata
* ASS ignores rotation flags
* Fix: ensure `PlayResX/PlayResY` match the *visual* orientation

### FFmpeg not found

* Ensure `assets/ffmpeg/ffmpeg.exe` exists
* Script auto-adds it to PATH at runtime

---

## Roadmap

* [ ] Auto-detect vertical vs horizontal
* [ ] Per-platform caption profiles
* [ ] GPU Whisper support
* [ ] GUI frontend

---

## License

MIT License

Copyright (c) 2025 AJ F. Jex

---

## Credits

* OpenAI Whisper
* FFmpeg
* libass subtitle engine

---

Built for fast, reliable social video cap
