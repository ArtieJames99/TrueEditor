> [!IMPORTANT]
> Copyright (c) 2026 KLJ Enterprises, LLC.
> Licensed under the terms in the LICENSE file in the root of this repository.

# TrueEditor
Auto Closed Captioning & Brand Editing for Short Form Content

TrueEditor is a Windows-first Python tool that automatically **generates, styles, and burns closed captions** into videos using OpenAI-Whisper and FFmpeg. It is designed specifically for **vertical (9:16) social media videos** and avoids common platform UI cutoffs on Instagram, Facebook, and similar platforms.

This project supports:

* Single video processing
* Batch folder processing
* Multiple Whisper model sizes
* Language-aware captioning (English / Spanish)
* Caption-safe placement (≈1/3 up from bottom)
* Optional **end card insertion**
* Optional **background music addition**
* Automatic handling of rotated or vertical phone footage

---

## Features

* 🎤 **Automatic speech-to-text** via Whisper
* 🎬 **Burn-in captions** using FFmpeg + ASS subtitles
* 📐 Optimized for **9:16 vertical video**
* 🧠 Handles rotated phone footage correctly
* 📂 Batch processing of folders
* 🌎 Language selection (English / Spanish)
* 🎵 Optional music addition with volume control
* 🪟 Windows-safe paths and execution
* ⚡ Fast processing with configurable Whisper model sizes (`tiny`, `base`, `small`, `medium`, `large`)

---

## Project Structure
```
True/
├── main.py                 # Entry point (single or batch mode)
├── build_video.py          # Handles caption burn + end card + optional music
├── autocaptioner.py        # Generates ASS captions using Whisper
├── assets/
│   └── ffmpeg/
│       └── ffmpeg.exe
├── transcriptions/         # Generated .ass subtitle files
├── video_files/            # Input videos
├── final/                  # Output videos and temporary files
│   └── temp_audio/         # Temporary audio files for processing
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
scipy
DeepFilterNet

````

> ⚠️ Whisper requires PyTorch. First run may take longer due to model download.

---

## Setup Instructions

### 1. Create & activate virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
````

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

(or install manually if no `requirements.txt` yet)

---

## Usage

### ▶️ Single Video

```powershell
python main.py "video_files\example.mp4"
```
### 📂 Multi Video (Batch Editing)

```powershell
python main.py "path/to/folder"
```

* Processes all `.mp4` files in the folder
* Skips files already marked `_Edited`
* Outputs burned videos next to originals
* Handles optional end card and music for each video


Optional flags:

```powershell
python main.py "video.mp4" --captions --model small --language Spanish --endcard English --music "assets\music\bg.mp4" --music-volume 0.3
```

| Option              | Description                                                |
| ------------------- | ---------------------------------------------------------- |
| `--captions`        | Enables Closed Captions                                    |
| `--model`           | Whisper model (`tiny`, `base`, `small`, `medium`, `large`) |
| `--language`        | Caption language (audo disables auto detection             |
| `--endcard`         | Optional end card video                                    |
| `--music`           | Optional background music video or audio                   |
| `--music-volume`    | Volume multiplier for added music (0.0–1.0 recommended)    |
| `--voice-isolation` | Isloates the Vocals and removes background wind            |

---


## TrueEditor Pipeline

Below is the **processing pipeline**, showing the sequence of events for a single video:

```
Input Video (.mp4)
       │
       ▼
[Audio Extraction]
   - FFmpeg separates audio from video
       │
       ▼
[Speech-to-Text]
   - Whisper transcribes audio
   - Generates timestamps for captions
       │
       ▼
[ASS Caption Generation]
   - autocaptioner.py creates styled .ass file
   - Ensures bottom-centered, safe placement
       │
       ▼
[Optional Audio Processing]
   - Mix in background music
   - Adjust volume
       │
       ▼
[Video Burn-in]
   - FFmpeg burns captions onto video
   - Handles rotation metadata correctly
       │
       ▼
[Optional End Card Insertion]
   - Appends end card video if specified
       │
       ▼
Final Output Video (_burned.mp4)
       │
       ▼
[Temporary Cleanup]
   - Removes temp audio files
   - Leaves transcriptions intact
```

### Notes on Pipeline

1. **Order matters:** Captions must be generated before burn-in.
2. **Audio is copied** unless music is added.
3. **Rotation-safe:** Videos filmed in portrait or landscape are handled correctly.
4. **Batch mode:** The same pipeline is applied iteratively to each `.mp4` file.

---

## Caption Placement (Important)
> Note: In future edits of this Progam there will be a GUI that adds in editing of the location, Size and Positioning of captions

TrueEditor uses **ASS subtitle styling** to ensure captions are:

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

Audio is copied directly (no re-encode). Background music, if added, is mixed at specified volume.

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
* [ ] Enhanced audio handling (noise reduction, wind removal)
* [ ] Automatic end card templating

---

## License

Copyright (c) 2026 KLJ Enterprises, LLC.
Licensed under the terms in the LICENSE file in the root of this repository.

---

## Credits

* OpenAI Whisper
* FFmpeg
* libass subtitle engine

Built for fast, reliable social video captioning, end card branding, and automatic audio integration
