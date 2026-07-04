#!/usr/bin/env python3
"""
build_mute_timetable.py — MPV Auto-Mute-Words Plugin
https://github.com/leviathwaite/MPV-Auto-Mute-Words-Plugin

Transcribes a media file using WhisperX, matches spoken words against a
bad-words list, then writes a sidecar ".mute.json" timetable that the
active_mute.lua plugin can consume.

Usage:
    python tools/build_mute_timetable.py <media_file> [options]

Examples:
    # Basic usage — reads config/badwords.txt, writes next to media file
    python tools/build_mute_timetable.py movie.mkv

    # Custom bad-words list and output path
    python tools/build_mute_timetable.py movie.mkv \\
        --badwords my_words.txt --output movie.mute.json

    # Choose a different Whisper model and device
    python tools/build_mute_timetable.py movie.mkv \\
        --model large-v2 --device cuda --compute-type float16
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Dependency check ──────────────────────────────────────────────────────────

def _require(package: str, pip_name: str | None = None) -> Any:
    if importlib.util.find_spec(package) is None:
        pip = pip_name or package
        sys.exit(
            f"[error] Required package '{pip}' is not installed.\n"
            f"        Run:  pip install {pip}"
        )
    return importlib.import_module(package)


# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_BADWORDS = Path(__file__).parent.parent / "config" / "badwords.txt"
TIMETABLE_VERSION = 1

# Whisper model sizes supported by WhisperX
VALID_MODELS = [
    "tiny", "tiny.en",
    "base", "base.en",
    "small", "small.en",
    "medium", "medium.en",
    "large-v1", "large-v2", "large-v3",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_badwords(path: Path) -> set[str]:
    """Load the bad-words list; returns a set of lower-cased words."""
    if not path.exists():
        sys.exit(f"[error] Bad-words file not found: {path}")
    words: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            word = raw.strip().lower()
            if word and not word.startswith("#"):
                words.add(word)
    if not words:
        sys.exit(f"[error] Bad-words file is empty: {path}")
    print(f"[info ] Loaded {len(words)} bad word(s) from {path}")
    return words


def normalise_word(token: str) -> str:
    """Strip punctuation and lower-case a token for matching."""
    return re.sub(r"[^a-z0-9']", "", token.lower())


def detect_device() -> str:
    """Return 'cuda' if a GPU is available, otherwise 'cpu'."""
    torch = _require("torch")
    return "cuda" if torch.cuda.is_available() else "cpu"


# ── Core pipeline ─────────────────────────────────────────────────────────────

def transcribe(
    media_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    language: str | None,
    batch_size: int,
) -> list[dict[str, Any]]:
    """
    Run WhisperX transcription + word-level alignment.
    Returns a flat list of word-level dicts:
      { "word": str, "start": float, "end": float }
    """
    whisperx = _require("whisperx")
    print(f"[info ] Loading WhisperX model '{model_name}' on {device} …")
    model = whisperx.load_model(
        model_name,
        device=device,
        compute_type=compute_type,
        language=language,
    )

    print(f"[info ] Transcribing {media_path} …")
    audio = whisperx.load_audio(str(media_path))
    result = model.transcribe(audio, batch_size=batch_size, language=language)

    detected_lang = result.get("language", language or "unknown")
    print(f"[info ] Detected language: {detected_lang}")

    print("[info ] Aligning word timestamps …")
    align_model, metadata = whisperx.load_align_model(
        language_code=detected_lang, device=device
    )
    aligned = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        audio,
        device=device,
        return_char_alignments=False,
    )

    words: list[dict[str, Any]] = []
    for segment in aligned.get("segments", []):
        for w in segment.get("words", []):
            start = w.get("start")
            end   = w.get("end")
            text  = w.get("word", "")
            if start is not None and end is not None and text:
                words.append({"word": text, "start": float(start), "end": float(end)})

    print(f"[info ] Aligned {len(words)} word(s)")
    return words


def build_entries(
    words: list[dict[str, Any]],
    badwords: set[str],
    merge_gap: float,
) -> list[dict[str, float]]:
    """
    Return a sorted list of mute intervals for words that match the bad-words
    list.  Adjacent / overlapping intervals separated by less than *merge_gap*
    seconds are merged into one.

    Each entry: { "start": float, "end": float }
    """
    raw: list[tuple[float, float]] = []
    for w in words:
        if normalise_word(w["word"]) in badwords:
            raw.append((w["start"], w["end"]))

    if not raw:
        print("[warn ] No bad words found in transcript")
        return []

    # Sort and merge
    raw.sort()
    merged: list[tuple[float, float]] = [raw[0]]
    for start, end in raw[1:]:
        prev_start, prev_end = merged[-1]
        if start - prev_end <= merge_gap:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    entries = [{"start": round(s, 4), "end": round(e, 4)} for s, e in merged]
    print(f"[info ] Built {len(entries)} mute interval(s) "
          f"(from {len(raw)} matched word(s))")
    return entries


def write_timetable(
    output_path: Path,
    media_path: Path,
    entries: list[dict[str, float]],
    model_name: str,
) -> None:
    """Serialise the timetable to JSON."""
    payload = {
        "version": TIMETABLE_VERSION,
        "media": media_path.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "entry_count": len(entries),
        "entries": entries,
    }
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"[info ] Timetable written to {output_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("media", metavar="MEDIA_FILE",
                   help="Path to the video or audio file to transcribe")
    p.add_argument("-b", "--badwords", metavar="FILE",
                   default=str(DEFAULT_BADWORDS),
                   help="Path to bad-words list (default: config/badwords.txt)")
    p.add_argument("-o", "--output", metavar="FILE", default=None,
                   help="Output .mute.json path (default: <media>.mute.json)")
    p.add_argument("-m", "--model", metavar="MODEL", default="base",
                   choices=VALID_MODELS,
                   help="WhisperX model size (default: base)")
    p.add_argument("-d", "--device", metavar="DEVICE", default=None,
                   help="Compute device: cpu | cuda (default: auto-detect)")
    p.add_argument("-c", "--compute-type", metavar="TYPE", default="int8",
                   help="Whisper compute type (default: int8)")
    p.add_argument("-l", "--language", metavar="LANG", default=None,
                   help="Force transcription language, e.g. 'en' (default: auto)")
    p.add_argument("--batch-size", metavar="N", type=int, default=16,
                   help="WhisperX batch size (default: 16)")
    p.add_argument("--merge-gap", metavar="SEC", type=float, default=0.1,
                   help="Merge mute intervals closer than SEC seconds (default: 0.1)")
    return p


def main() -> None:
    args = build_parser().parse_args()

    media_path   = Path(args.media).resolve()
    badwords_path = Path(args.badwords).resolve()

    if not media_path.exists():
        sys.exit(f"[error] Media file not found: {media_path}")

    output_path = (
        Path(args.output).resolve()
        if args.output
        else media_path.parent / (media_path.stem + ".mute.json")
    )

    device       = args.device or detect_device()
    compute_type = args.compute_type

    print(f"[info ] Media   : {media_path}")
    print(f"[info ] Output  : {output_path}")
    print(f"[info ] Device  : {device}")
    print(f"[info ] Model   : {args.model}")

    badwords = load_badwords(badwords_path)

    words   = transcribe(
        media_path,
        model_name=args.model,
        device=device,
        compute_type=compute_type,
        language=args.language,
        batch_size=args.batch_size,
    )

    entries = build_entries(words, badwords, merge_gap=args.merge_gap)

    write_timetable(output_path, media_path, entries, model_name=args.model)

    if not entries:
        print("[info ] Done — no mute intervals generated")
    else:
        total = sum(e["end"] - e["start"] for e in entries)
        print(f"[info ] Done — {len(entries)} interval(s), "
              f"{total:.2f}s of audio will be muted")


if __name__ == "__main__":
    main()
