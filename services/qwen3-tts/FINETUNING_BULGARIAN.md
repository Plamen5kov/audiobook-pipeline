# Fine-Tuning Qwen3-TTS for Bulgarian (Single Speaker)

Fine-tune the Qwen3-TTS-12Hz-1.7B model with your own voice to generate Bulgarian speech.
Bulgarian Cyrillic is already in the Qwen BPE tokenizer (119 languages covered), so no vocab changes are needed.

## Prerequisites

- NVIDIA GPU with 16+ GB VRAM (DGX Spark is fine)
- Conda or venv with Python 3.12
- A decent microphone and quiet recording environment

## 1. Recording Your Voice

### What to record

- **Target: 1-3 hours** of Bulgarian speech (30-60 min works as a proof of concept)
- **One reference clip** (`ref.wav`): a single clean 3-10 second sentence in your natural voice
- Vary sentence lengths: mix short (5s) and long (30s) utterances
- Include emotional variety: neutral, happy, serious, excited, contemplative
- Read diverse content: fiction, news, technical text, dialogue

### Recording requirements

| Parameter     | Value                    |
|---------------|--------------------------|
| Sample rate   | 24,000 Hz (24kHz)        |
| Channels      | Mono                     |
| Format        | WAV (uncompressed)       |
| Environment   | Quiet room, no echo      |
| Mic distance  | Consistent throughout    |
| Silence pad   | ~1 second at end of each clip (prevents speech acceleration artifact) |

### Convert existing recordings

```bash
ffmpeg -i input.mp3 -ar 24000 -ac 1 output.wav
```

## 2. Environment Setup

```bash
conda create -n qwen3-tts-finetune python=3.12 -y
conda activate qwen3-tts-finetune

pip install -U qwen-tts
pip install -U flash-attn --no-build-isolation

git clone https://github.com/QwenLM/Qwen3-TTS.git
cd Qwen3-TTS/finetuning
```

## 3. Prepare Training Data

Create `train_raw.jsonl` — one JSON object per line:

```json
{"audio": "./data/utt0001.wav", "text": "Здравейте, как сте днес?", "ref_audio": "./data/ref.wav"}
{"audio": "./data/utt0002.wav", "text": "Днес времето е хубаво.", "ref_audio": "./data/ref.wav"}
```

- `audio` — path to the training audio clip
- `text` — exact transcription in Bulgarian Cyrillic
- `ref_audio` — use the **same** `ref.wav` for ALL lines (improves speaker consistency)

### Auto-transcription option

If you don't want to transcribe manually, use [sruckh's toolkit](https://github.com/sruckh/Qwen3-TTS-finetune) which runs WhisperX with Bulgarian support:

```bash
git clone https://github.com/sruckh/Qwen3-TTS-finetune.git
./train.sh --audio_dir ./my_audio --ref_audio ./ref.wav --speaker_name bg_voice --whisper_language bg
```

Or use the [EasyFinetuning WebUI](https://github.com/mozi1924/Qwen3-TTS-EasyFinetuning):

```bash
git clone https://github.com/mozi1924/Qwen3-TTS-EasyFinetuning.git
cd Qwen3-TTS-EasyFinetuning && docker compose up -d
# WebUI at http://localhost:7860 — tabs: Audio Split → ASR → Tokenize → Train
```

## 4. Tokenize Audio

Converts your WAV files into discrete audio codes the model understands:

```bash
python prepare_data.py \
  --device cuda:0 \
  --tokenizer_model_path Qwen/Qwen3-TTS-Tokenizer-12Hz \
  --input_jsonl train_raw.jsonl \
  --output_jsonl train_with_codes.jsonl
```

## 5. Apply Bug Fixes to sft_12hz.py

There are 3 known bugs you MUST fix before training:

### Bug 1: Double label-shifting (Issue #179)

HuggingFace's `ForCausalLMLoss` internally shifts labels, but the script also manually shifts
them — causing speech to get progressively faster each epoch. Fix by removing the manual shift
or replacing HF loss with direct `F.cross_entropy()`.

https://github.com/QwenLM/Qwen3-TTS/issues/179

### Bug 2: Speaker encoder deletion (Issue #204)

Lines 139-144 delete speaker encoder weights before saving checkpoints, causing crashes when
resuming. Comment out those lines.

https://github.com/QwenLM/Qwen3-TTS/issues/204

### Bug 3: Text projection layer (Issue #39)

Missing text projection step in the embedding pipeline. Less critical for the 1.7B model but
still recommended to fix.

https://github.com/QwenLM/Qwen3-TTS/issues/39

## 6. Fine-Tune

```bash
python sft_12hz.py \
  --init_model_path Qwen/Qwen3-TTS-12Hz-1.7B-Base \
  --output_model_path output \
  --train_jsonl train_with_codes.jsonl \
  --batch_size 2 \
  --lr 2e-6 \
  --num_epochs 10 \
  --speaker_name bulgarian_speaker
```

### Critical: learning rate MUST be 2e-6

The default `2e-5` produces noise/gibberish. This was the #1 issue discovered by the community
(Turkish fine-tuning, Issue #27, Issue #39). Do not go higher than 5e-6.

### Hyperparameter reference

| Parameter    | Value   | Notes                                            |
|--------------|---------|--------------------------------------------------|
| lr           | 2e-6    | CRITICAL — default 2e-5 produces garbage         |
| batch_size   | 2       | Reduce to 1 if OOM                               |
| num_epochs   | 10-20   | More for a new language; evaluate every 5 epochs |
| grad_accum   | 4       | Effective batch = batch_size x grad_accum        |
| precision    | bfloat16| Automatic                                        |

### VRAM usage

| Config               | VRAM     |
|----------------------|----------|
| 1.7B + batch_size=2  | ~16+ GB  |
| 1.7B + batch_size=1  | ~12-14 GB|

Checkpoints saved as `output/checkpoint-epoch-N` (~3.4 GB each).

## 7. Test the Result

```python
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

tts = Qwen3TTSModel.from_pretrained(
    "output/checkpoint-epoch-5",   # try multiple checkpoints
    device_map="cuda:0",
    dtype=torch.bfloat16,
)

wavs, sr = tts.generate_custom_voice(
    text="Здравейте, как сте днес?",
    speaker="bulgarian_speaker",   # must match --speaker_name from training
)
sf.write("test_bg.wav", wavs[0], sr)
```

No `language=` parameter needed — the model learns Bulgarian from the training data, not a tag.

## 8. Integrate into the Pipeline

After finding a good checkpoint, update `services/qwen3-tts/app/main.py` to load
the fine-tuned model instead of the base CustomVoice model. The `/synthesize` API
stays the same.

## Tips for Bulgarian Specifically

- **Russian overlap helps**: Bulgarian shares Cyrillic with Russian (a supported language),
  so the model already has some phonetic grounding. Expect better results than Turkish had.
- **Watch for "ъ"**: The Bulgarian vowel "ъ" (er golyam) has no Russian equivalent. Monitor
  its pronunciation in test outputs.
- **Start small**: Try 30 minutes first, evaluate, then scale to 1-3 hours.
- **Quality > quantity**: 30 min of clean studio audio beats 3 hours of noisy recordings.
- **Diverse lengths matter**: Using only short clips causes quality decay on longer generations.

## References

- [Qwen3-TTS repo](https://github.com/QwenLM/Qwen3-TTS)
- [Official fine-tuning scripts](https://github.com/QwenLM/Qwen3-TTS/tree/main/finetuning)
- [EasyFinetuning toolkit + WebUI](https://github.com/mozi1924/Qwen3-TTS-EasyFinetuning)
- [One-command fine-tuning (sruckh)](https://github.com/sruckh/Qwen3-TTS-finetune)
- [Issue #27 — Turkish fine-tuning success story](https://github.com/QwenLM/Qwen3-TTS/issues/27)
- [Issue #39 — Learning rate + text projection fix](https://github.com/QwenLM/Qwen3-TTS/issues/39)
- [Issue #179 — Double label-shifting bug](https://github.com/QwenLM/Qwen3-TTS/issues/179)
- [Issue #204 — Speaker encoder deletion bug](https://github.com/QwenLM/Qwen3-TTS/issues/204)
- [EasyFinetuning guide](https://mozi1924.com/article/qwen3-tts-finetuning-en/)
