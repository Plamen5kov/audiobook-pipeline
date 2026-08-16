# Book corpus builder

Pairs a book's text with its professional narration, segment by segment, and
stores the result so it can be read back two ways: chronologically, as the book
runs, or gathered per speaker, as material for building a voice.

Each segment records what is said, who says it, and exactly where that sits in
the narration audio. Annotations beyond that (emotion, quality scores,
pronunciation flags) are added by later passes without changing the schema.

## Why it exists

The TTS pipeline could previously only be judged against itself. With a
professional reading aligned to the same segment boundaries, every synthesised
segment has a human performance of the same words to compare against — which is
what turns "sounds a bit slow" into a measured number. The same clips are the
raw material for voice cloning later.

## Design

Five stages, each writing per-chapter artifacts, so a 29-hour book builds
incrementally and resumes after an interruption instead of restarting.

1. **fetch** — copy the audio and epub locally, read the embedded chapter
   markers. Chapter boundaries come from the file itself, so nothing has to be
   detected.
2. **extract** — epub to one text file per chapter, one paragraph per line.
   Chapters are cut from the NCX navigation map walked against the OPF spine,
   which reassembles chapters the publisher split across several files.
3. **segment** — text to attributed segments, reusing the `text-analyzer`
   service's deterministic nodes directly. No GPU and no model server, so the
   whole book segments in about a second.
4. **align** — forced alignment of each chapter against its slice of the
   narration, producing word-level timings collapsed to one span per segment.
5. **load** — everything into SQLite.

`clips` then cuts audio on demand for whatever selection you query.

### Alignment

`torchaudio`'s MMS_FA pipeline, chosen over MFA and WhisperX because it runs on
the PyTorch stack already proven on this hardware, needs neither Kaldi nor
CTranslate2, and is fine on CPU.

The encoder runs over 60-second windows with 6 seconds of overlap and the
emissions are stitched, half the overlap trimmed from each interior edge. A
26-minute chapter in one forward pass would allocate a self-attention matrix
orders of magnitude larger than the windowed equivalent. Windowing also turned
out to be *more* accurate than the single-pass baseline on chapter 1 (mean word
confidence 0.941 against 0.912) and about 2.4x faster.

Alignment runs on the workstation, not the DGX Spark: it is batch CPU work, and
the Spark cannot reliably start new CUDA processes.

### Two coverage numbers

`coverage` is span extent over chapter duration and is the health check — well
below 1.0 means the transcript ran out before the audio did. `speech_ratio` is
the share of the chapter that is speech rather than pause, and is expected to
sit lower; on chapter 1 it is 0.88, the difference being 97 natural pauses
averaging 1.2 seconds.

## Schema

`segments` is the spine. `book_seq` gives chronological order across the whole
book and is spaced per chapter, so re-segmenting one chapter never renumbers
the rest. Indexes cover speaker lookup and ordering, so both access patterns
are cheap.

- `books`, `chapters` — sources and per-chapter alignment quality
- `segments` — text, speaker, kind, character offsets, audio span, align score
- `words` — word-level timings, for precise re-cutting without re-aligning
- `segment_meta` — open-ended `(key, value, source)` annotations for later
  passes
- `characters` — book-level names with pronoun-vote gender
- `segments_fts` — full-text search over segment text
- `v_voice_corpus` — aligned segments in chronological order

`kind` is `dialogue`, `narration`, or `heading`; headings are narrated chapter
titles, kept so they align but marked so they stay out of voice corpora.

## Usage

Build the image once:

    docker build -t audiobook-corpus:cpu -f Dockerfile .

Stages 1-3 need no image and run on the system Python:

    python3 -m builder --slug hwfwm-b1 fetch \
        --audio "nas-server:/path/book.mp3" --epub "nas-server:/path/book.epub" \
        --title "..." --author "..." --series "..." --book-number 1
    python3 -m builder --slug hwfwm-b1 extract
    python3 -m builder --slug hwfwm-b1 segment

Alignment runs in the container. Chapters can be split across workers, since
each writes its own files:

    docker run --rm --user $(id -u):$(id -g) \
        -v "$PWD/..":/app -v "$HOME/.cache/torch-corpus":/cache/torch \
        -w /app/corpus audiobook-corpus:cpu \
        --slug hwfwm-b1 align --chapters 1-39

Then load and inspect:

    python3 -m builder --slug hwfwm-b1 load
    python3 -m builder --slug hwfwm-b1 stats

Merge labels that name the same character. Always inspect the dry run first:

    python3 -m builder --slug hwfwm-b1 aliases            # dry run
    python3 -m builder --slug hwfwm-b1 aliases --apply

Three rules fire. A bare surname resolves to a first name only when the whole
book's prose shows that surname following exactly one character, because
families share surnames: Geller belongs to four different people in Book 1.
Evidence is taken from the prose rather than the labels, since the labels are
what is being corrected. A label ending in another label is that label with
noise on the front, and the cleaner form wins even when the noisy one is
commoner. A "First Last" label folds into "First".

The mapping is stored in `speaker_aliases` and the original attribution is kept
in `segments.speaker_raw`, so a merge is auditable and reversible. `load`
reapplies stored aliases, so reloading a chapter cannot silently un-merge a
character.

Export cloning references, a few clips per character with their exact text:

    python3 -m builder --slug hwfwm-b1 voicebank --out voicebank/hwfwm-b1

These are cut tighter than listening clips, because a reference should contain
the character and nothing else: a neighbouring word bleeding in at the edge
matters more than a slightly clipped consonant. Clips are drawn from across the
whole book rather than one scene, since a character's delivery drifts with the
story. The manifest pairs each clip with the words spoken in it, which is what
lets Qwen3-TTS clone in in-context mode instead of from a speaker embedding
alone; that transcript is normally the missing piece when cloning from an
audiobook.

Note that cloning requires the **Base** checkpoint
(`Qwen/Qwen3-TTS-12Hz-1.7B-Base`). The CustomVoice checkpoint has nine preset
speakers and refuses to clone; VoiceDesign takes written style instructions.
Only one can be loaded at a time, and the Base model accepts no emotion
instruct, which is a deliberate trade: varying the instruct is what makes a
correctly cast character stop sounding like himself across a chapter.

Cut clips for a voice, filtering on alignment confidence and length:

    python3 -m builder --slug hwfwm-b1 clips --speaker Jason --kind dialogue \
        --min-score 0.85 --min-duration 2.0 --limit 200 --out clips/jason

Every export writes a `manifest.csv` linking each clip back to its segment id,
chapter, text, span and score. `--jsonl` selects without cutting audio.

## Adding annotations later

Later passes write to `segment_meta` rather than altering `segments`:

    INSERT INTO segment_meta (segment_id, key, value, numeric_value, source)
    VALUES (?, 'emotion', 'curious', 0.7, 'ollama-qwen2.5');

Existing candidates: emotion and intensity from the pipeline's Ollama nodes,
speaker re-attribution for segments left `unknown`, duration ratio against
synthesised audio, and reference-free quality scores.

## Known limitations

- **Gender inference is biased male, and irrelevant to the reference audio.**
  Votes come from pronouns in narration *adjacent* to a character's lines, but
  in a first-person book that narration usually refers to the narrator, so
  every character accrues spurious male votes. On this book 11 of the 44
  characters with 20+ lines have votes closer than 2:1. It does not affect the
  corpus: this audiobook is performed entirely by one man, so every clip is the
  same voice and gender cannot be a selection criterion. It does affect casting
  distinct synthetic voices, where a first-name dataset or an LLM pass would be
  more reliable.
- Attribution is deterministic only. The AI attribution node is not run during
  the build; segments it cannot resolve stay `unknown` (about 2% on this book).
- Spelling variants are not merged. "Gabriele" sits one letter from both
  "Gabriel" and "Gabrielle", who are different people, and nothing in the text
  disambiguates them. A stray rare label costs a few lines; a wrong merge
  corrupts two characters.
- Speaking rate has a small tail of outliers, mostly very short segments where
  one mis-snapped word boundary dominates. Filter on `align_score`.
- Reloading a chapter regenerates segment ids. Annotations are carried across
  by position and text, but an annotation on a segment whose text changed is
  dropped on purpose, since it described different words.
