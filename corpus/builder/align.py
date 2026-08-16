"""Map chapter text onto the professional narration with forced alignment.

Uses torchaudio's MMS_FA pipeline rather than MFA or WhisperX: it runs on the
plain PyTorch stack already proven on this hardware, needs neither Kaldi nor
CTranslate2, and is fine on CPU.

The encoder runs over overlapping windows and the emissions are stitched, so
peak memory is set by the window rather than by chapter length. A single
26-minute chapter in one forward pass would allocate a self-attention matrix
several orders of magnitude larger than the windowed equivalent.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import soundfile as sf
import torch
import torchaudio
from torchaudio.pipelines import MMS_FA as bundle

WINDOW_S = 60.0
OVERLAP_S = 6.0
WORD_RE = re.compile(r"[a-z']+")

# torchaudio's alignment kernel indexes a frames x token-states trellis with a
# 32-bit int, so a chapter whose product approaches 2^31 segfaults rather than
# raising. Long chapters are therefore aligned in anchored blocks. The ceiling
# is set well below the true limit to leave room for the estimate being wrong.
TRELLIS_LIMIT = 1.6e9
BLOCK_WORDS = 1200
BLOCK_TAIL_WORDS = 150
BLOCK_AUDIO_MARGIN = 1.7
BLOCK_AUDIO_PAD_S = 15.0

_model = None
_tokenizer = None
_aligner = None


def _load(device: str):
    global _model, _tokenizer, _aligner
    if _model is None:
        _model = bundle.get_model().to(device).eval()
        _tokenizer = bundle.get_tokenizer()
        _aligner = bundle.get_aligner()
    return _model, _tokenizer, _aligner


def slice_audio(source: Path, dest: Path, start_s: float, end_s: float) -> Path:
    """Decode one chapter of the book audio to 16 kHz mono WAV.

    torchaudio 2.11 routes ``load()`` through torchcodec, which is not
    installed, so decoding happens in ffmpeg and the WAV is read with
    soundfile.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}", "-i", str(source),
         "-ac", "1", "-ar", str(bundle.sample_rate), str(dest)],
        check=True,
    )
    return dest


def _emission(waveform: torch.Tensor, device: str) -> tuple[torch.Tensor, float]:
    """Encode *waveform* in overlapping windows; return log-probs and frame rate."""
    model, _, _ = _load(device)
    sr = bundle.sample_rate
    total = waveform.shape[1]
    window = int(WINDOW_S * sr)
    overlap = int(OVERLAP_S * sr)
    hop = window - overlap

    if total <= window:
        with torch.inference_mode():
            emission, _ = model(waveform.to(device))
        return emission[0].cpu(), emission.shape[1] / total * sr

    pieces: list[torch.Tensor] = []
    fps = None
    for start in range(0, total, hop):
        end = min(start + window, total)
        with torch.inference_mode():
            emission, _ = model(waveform[:, start:end].to(device))
        chunk = emission[0].cpu()
        if fps is None:
            fps = chunk.shape[0] / (end - start) * sr

        # Drop half the overlap from each interior edge: those frames are
        # reproduced more centrally — and so more reliably — by the neighbour.
        trim = int(OVERLAP_S / 2 * fps)
        lo = 0 if start == 0 else trim
        hi = chunk.shape[0] if end == total else chunk.shape[0] - trim
        pieces.append(chunk[lo:hi])
        if end == total:
            break

    return torch.cat(pieces, dim=0), fps


def _token_states(words: list[str]) -> int:
    """Rough trellis width: one state per character plus interleaved blanks."""
    return 2 * sum(len(w) for w in words) + 1


def _align_span(waveform: torch.Tensor, words: list[str], device: str) -> list[dict]:
    """Align *words* against the whole of *waveform* in a single trellis."""
    _, tokenizer, aligner = _load(device)
    emission, fps = _emission(waveform, device)
    token_spans = aligner(emission, tokenizer(words))
    ratio = 1.0 / fps

    out = []
    for spans in token_spans:
        length = sum(len(s) for s in spans)
        out.append({
            "start": round(spans[0].start * ratio, 3),
            "end": round(spans[-1].end * ratio, 3),
            "score": round(sum(s.score * len(s) for s in spans) / max(length, 1), 3),
        })
    return out


def _align_words(waveform: torch.Tensor, words: list[str], device: str) -> list[dict]:
    """Align every word, splitting into anchored blocks when the chapter is
    too long for a single trellis.

    Each block aligns more words than it keeps: the tail is discarded and the
    next block restarts from the last committed word's end time. Alignment near
    a block's edge is unreliable precisely because the audio runs out there, so
    committing it would bake that error into the anchor.
    """
    sr = bundle.sample_rate
    total_samples = waveform.shape[1]
    frames = total_samples / sr * 50.0  # wav2vec2 emits ~50 frames per second

    if frames * _token_states(words) <= TRELLIS_LIMIT:
        return _align_span(waveform, words, device)

    rate = len(words) / (total_samples / sr)  # words per second, chapter average
    results: list[dict] = []
    w_at, a_at = 0, 0

    while w_at < len(words):
        take = min(BLOCK_WORDS, len(words) - w_at)
        block = words[w_at:w_at + take]
        last_block = w_at + take >= len(words)

        if last_block:
            budget = total_samples - a_at
        else:
            expected = take / rate * BLOCK_AUDIO_MARGIN + BLOCK_AUDIO_PAD_S
            budget = min(total_samples - a_at, int(expected * sr))

        timings = _align_span(waveform[:, a_at:a_at + budget], block, device)
        commit = take if last_block else max(1, take - BLOCK_TAIL_WORDS)

        offset = a_at / sr
        for t in timings[:commit]:
            results.append({
                "start": round(t["start"] + offset, 3),
                "end": round(t["end"] + offset, 3),
                "score": t["score"],
            })

        advance = int(timings[commit - 1]["end"] * sr)
        if advance <= 0:  # never stall, even if a block aligns degenerately
            advance = budget
        a_at += advance
        w_at += commit

    return results


def align_chapter(wav_path: Path, segments: list[dict], device: str = "cpu") -> dict:
    """Align *segments* against *wav_path*; return word and segment timings."""
    t0 = time.time()
    data, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    waveform = torch.from_numpy(data.T)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(0, keepdim=True)
    if sr != bundle.sample_rate:
        waveform = torchaudio.functional.resample(waveform, sr, bundle.sample_rate)
    duration = waveform.shape[1] / bundle.sample_rate

    # The aligner consumes a flat word sequence; remembering each word's owner
    # is what lets spans be reassembled at our own segment boundaries.
    words: list[str] = []
    owner: list[int] = []
    for s in segments:
        for w in WORD_RE.findall((s.get("text") or "").lower()):
            words.append(w)
            owner.append(s["id"])
    if not words:
        raise ValueError(f"{wav_path.name}: no alignable words")

    timings = _align_words(waveform, words, device)
    word_times = [
        {"word": words[i], "segment_id": owner[i], **t}
        for i, t in enumerate(timings)
    ]

    seg_spans: dict[int, dict] = {}
    for w in word_times:
        sid = w["segment_id"]
        span = seg_spans.setdefault(sid, {
            "segment_id": sid, "start": w["start"], "end": w["end"],
            "words": 0, "score_sum": 0.0,
        })
        span["end"] = w["end"]
        span["words"] += 1
        span["score_sum"] += w["score"]
    for span in seg_spans.values():
        span["duration"] = round(span["end"] - span["start"], 3)
        span["mean_score"] = round(span["score_sum"] / max(span["words"], 1), 3)
        del span["score_sum"]

    scores = [w["score"] for w in word_times]
    ordered = sorted(seg_spans.values(), key=lambda x: x["start"])
    # Extent is the alignment health check — anything well below 1.0 means the
    # transcript ran out before the audio did. The speech ratio is a property
    # of the narration (how much of it is pause) and is expected to sit lower.
    extent = (ordered[-1]["end"] - ordered[0]["start"]) / duration
    speech = sum(s["duration"] for s in seg_spans.values()) / duration
    return {
        "audio_duration_s": round(duration, 2),
        "word_count": len(words),
        "mean_score": round(sum(scores) / len(scores), 4),
        "low_confidence_words": sum(1 for s in scores if s < 0.5),
        "coverage": round(extent, 4),
        "speech_ratio": round(speech, 4),
        "leading_silence_s": round(ordered[0]["start"], 2),
        "trailing_silence_s": round(duration - ordered[-1]["end"], 2),
        "elapsed_s": round(time.time() - t0, 1),
        "words": word_times,
        "segments": sorted(seg_spans.values(), key=lambda x: x["segment_id"]),
    }


def run(book_audio: Path, seg_dir: Path, out_dir: Path, wav_dir: Path,
        chapters: list[dict], device: str = "cpu",
        keep_wav: bool = False) -> list[dict]:
    """Align each chapter in *chapters*, writing one result file per chapter."""
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for ch in chapters:
        n = ch["number"]
        out_path = out_dir / f"ch{n:03d}.json"
        segments = json.loads((seg_dir / f"ch{n:03d}.json").read_text())["segments"]

        wav = wav_dir / f"ch{n:03d}.wav"
        if not wav.exists():
            slice_audio(book_audio, wav, ch["audio_start_s"], ch["audio_end_s"])
        try:
            result = align_chapter(wav, segments, device)
        finally:
            if not keep_wav and wav.exists():
                wav.unlink()

        result["chapter"] = n
        result["audio_offset_s"] = ch["audio_start_s"]
        out_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
        summary.append({k: result[k] for k in
                        ("chapter", "word_count", "mean_score",
                         "coverage", "elapsed_s")})
        print(f"ch{n:03d}: {result['word_count']} words, "
              f"score {result['mean_score']:.3f}, "
              f"coverage {result['coverage']:.3f}, "
              f"{result['elapsed_s']:.0f}s", flush=True)
    return summary
