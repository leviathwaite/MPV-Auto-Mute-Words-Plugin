# MPV Auto-Mute-Words Plugin

Automatically mutes offensive or restricted words during MPV video playback.
A Python utility transcribes your media with [WhisperX](https://github.com/m-bain/whisperX)
and produces a lightweight sidecar JSON *timetable*; the MPV Lua plugin reads
that timetable and mutes/unmutes in real time while preserving pre-existing
manual mute state.

---

## How it works

```
Your media file
      │
      ▼
tools/build_mute_timetable.py  ←  config/badwords.txt
      │  (WhisperX transcription + word alignment)
      ▼
<media>.mute.json               ← sidecar timetable
      │
      ▼
scripts/active_mute.lua         ← loaded automatically by MPV
      │  (polls playback position, mutes/unmutes)
      ▼
🔇  seamless muted playback
```

Optional desktop flow:

```
tools/build_mute_timetable_ui.py
      │  (file picker + CLI wrapper)
      ▼
tools/build_mute_timetable.py
```

---

## Repository structure

```
MPV-Auto-Mute-Words-Plugin/
├── scripts/
│   └── active_mute.lua          # MPV Lua plugin
├── tools/
│   └── build_mute_timetable.py  # Timetable generator (Python)
│   └── build_mute_timetable_ui.py
│                               # Desktop file-picker wrapper
├── config/
│   └── badwords.txt             # Default bad-words list
├── examples/
│   └── sample.mute.json         # Sample timetable for reference
├── input.conf.example           # Optional MPV key-bindings
├── requirements.txt             # Python dependencies
├── LICENSE                      # MIT
└── README.md
```

---

## Requirements

| Component   | Requirement |
|-------------|-------------|
| MPV         | v0.29+  (Lua 5.1/5.2 runtime) |
| Python      | 3.9+ |
| WhisperX    | 3.1+ |
| PyTorch     | 2.0+ (CPU or CUDA) |
| ffmpeg      | Required by WhisperX for audio extraction |

Install Python dependencies:

```bash
pip install -r requirements.txt
```

> **GPU users:** Install the CUDA-enabled PyTorch wheel *before* running
> `pip install -r requirements.txt`.
> See <https://pytorch.org/get-started/locally/>.

---

## Quick start

### 1 — Generate the mute timetable

```bash
# Using defaults (base model, auto-detect device, config/badwords.txt)
python tools/build_mute_timetable.py /path/to/movie.mkv

# Larger model for better accuracy, GPU
python tools/build_mute_timetable.py /path/to/movie.mkv \
    --model large-v2 --device cuda --compute-type float16

# Custom bad-words list and explicit output path
python tools/build_mute_timetable.py /path/to/movie.mkv \
    --badwords my_list.txt --output /path/to/movie.mute.json
```

This creates `/path/to/movie.mute.json` next to the media file.

Prefer a desktop picker instead of the CLI?

```bash
python tools/build_mute_timetable_ui.py
```

The UI lets you:
- choose the media file with a file picker
- keep the repository `config/badwords.txt` list or browse to a custom list
- adjust core WhisperX options before starting the build
- watch the CLI output in a log panel

Full CLI reference:

```
usage: build_mute_timetable.py [-h] [-b FILE] [-o FILE] [-m MODEL]
                               [-d DEVICE] [-c TYPE] [-l LANG]
                               [--batch-size N] [--merge-gap SEC]
                               MEDIA_FILE

positional arguments:
  MEDIA_FILE            Path to the video or audio file to transcribe

options:
  -b, --badwords FILE   Bad-words list (default: config/badwords.txt)
  -o, --output FILE     Output path (default: <media>.mute.json)
  -m, --model MODEL     Whisper model size (default: base)
                        choices: tiny, base, small, medium, large-v1/v2/v3
  -d, --device DEVICE   cpu | cuda (default: auto-detect)
  -c, --compute-type TYPE
                        int8 | float16 | float32 (default: int8)
  -l, --language LANG   Force language, e.g. 'en' (default: auto-detect)
  --batch-size N        WhisperX batch size (default: 16)
  --merge-gap SEC       Merge intervals closer than SEC seconds (default: 0.1)
```

### 2 — Install the MPV plugin

Copy (or symlink) `scripts/active_mute.lua` into your MPV scripts directory:

```bash
# Linux / macOS
mkdir -p ~/.config/mpv/scripts
cp scripts/active_mute.lua ~/.config/mpv/scripts/

# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:APPDATA\mpv\scripts"
Copy-Item scripts\active_mute.lua "$env:APPDATA\mpv\scripts\"
```

### 3 — Play your media

Open the file in MPV as usual.  The plugin automatically finds the sidecar
JSON and starts muting:

```bash
mpv /path/to/movie.mkv
```

---

## Timetable lookup order

The plugin searches for the sidecar JSON in this order and uses the first
match:

1. `<media_dir>/<basename_no_ext>.mute.json`  ← **preferred**
2. `<media_dir>/<full_filename>.mute.json`
3. `~/.config/mpv/mute-timetables/<basename_no_ext>.mute.json`

Example: playing `~/Videos/Movie.mkv` → looks for
`~/Videos/Movie.mute.json`.

---

## Timetable JSON format

```json
{
  "version": 1,
  "media": "movie.mkv",
  "generated_at": "2024-06-01T12:00:00+00:00",
  "model": "base",
  "entry_count": 2,
  "entries": [
    { "start": 42.10, "end": 42.65 },
    { "start": 97.88, "end": 98.21 }
  ]
}
```

Only `version` and `entries[].start` / `entries[].end` are required.
All timestamps are in **seconds** (floating-point).

See `examples/sample.mute.json` for a complete example.

---

## Plugin options

Options are set via MPV's `--script-opts` flag or in `~/.config/mpv/script-opts/active_mute.conf`:

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `yes` | Enable the plugin on startup |
| `show_osd` | `yes` | Show OSD messages when muting/unmuting |
| `osd_duration` | `2000` | OSD message duration (ms) |
| `padding` | `0.15` | Extra seconds to extend each interval (compensates for decode latency) |

**`script-opts/active_mute.conf` example:**

```ini
enabled=yes
show_osd=yes
osd_duration=1500
padding=0.1
```

**Command-line override:**

```bash
mpv movie.mkv --script-opts=active_mute-padding=0.2,active_mute-show_osd=no
```

---

## Key bindings (optional)

Copy the relevant lines from `input.conf.example` into
`~/.config/mpv/input.conf`:

| Key | Action |
|-----|--------|
| `Ctrl+M` | Toggle auto-mute on/off |
| `Ctrl+R` | Reload timetable for the current file |

---

## Customising the bad-words list

Edit `config/badwords.txt` — one word per line, `#` for comments:

```
# config/badwords.txt
damn
hell
# add your own words below
```

Re-run `build_mute_timetable.py` after any changes.

---

## Troubleshooting

**No intervals muted / plugin silent**
- Check the MPV console (`~`): look for `[active_mute]` log lines.
- Confirm the `.mute.json` file is next to the media file.
- Try `--script-opts=active_mute-show_osd=yes` to see mute notifications.

**Wrong words muted / missed**
- Use a larger Whisper model (`--model large-v2`).
- Force the language with `--language en` to prevent mis-detection.
- Decrease `--merge-gap` to split close intervals.

**Too early / too late muting**
- Adjust `padding` in `script-opts/active_mute.conf` (negative values
  shift mute *earlier*; positive values add a buffer after).

---

## License

[MIT](LICENSE) © leviathwaite
