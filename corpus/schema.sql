-- Aligned book corpus: text segments paired with spans of the professional
-- narration, chronological across a whole book and queryable per speaker.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS books (
    id               INTEGER PRIMARY KEY,
    slug             TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    author           TEXT,
    series           TEXT,
    book_number      INTEGER,
    audio_source     TEXT,
    epub_source      TEXT,
    audio_duration_s REAL
);

CREATE TABLE IF NOT EXISTS chapters (
    id               INTEGER PRIMARY KEY,
    book_id          INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    number           INTEGER NOT NULL,
    title            TEXT,
    audio_start_s    REAL NOT NULL,
    audio_end_s      REAL NOT NULL,
    word_count       INTEGER,
    segment_count    INTEGER,
    align_mean_score REAL,
    align_coverage   REAL,
    UNIQUE (book_id, number)
);

-- Labels that attribution produced for a character who already has one. Kept
-- as data rather than applied destructively, so the mapping can be revised or
-- reverted without rebuilding the corpus.
CREATE TABLE IF NOT EXISTS speaker_aliases (
    book_id   INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    alias     TEXT NOT NULL,
    canonical TEXT NOT NULL,
    reason    TEXT,
    evidence  INTEGER,
    PRIMARY KEY (book_id, alias)
) WITHOUT ROWID;

-- Per-chapter pronoun tallies are kept so book-level gender can be recomputed
-- from the whole book no matter how many passes it was loaded in.
CREATE TABLE IF NOT EXISTS chapter_characters (
    book_id        INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    name           TEXT NOT NULL,
    male_votes     INTEGER DEFAULT 0,
    female_votes   INTEGER DEFAULT 0,
    PRIMARY KEY (book_id, chapter_number, name)
) WITHOUT ROWID;

-- Judgement about who each speaker label actually is, from the registry pass.
-- Kept apart from `characters`, which is a tally recomputed from segments on
-- every load: a verdict has to survive a reload, a tally must not.
CREATE TABLE IF NOT EXISTS character_registry (
    book_id      INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    is_character INTEGER,
    canonical    TEXT,
    gender       TEXT,
    confidence   REAL,
    reason       TEXT,
    verdict      TEXT NOT NULL DEFAULT 'proposed',
    model        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (book_id, name)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS characters (
    id            INTEGER PRIMARY KEY,
    book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    gender        TEXT,
    male_votes    INTEGER DEFAULT 0,
    female_votes  INTEGER DEFAULT 0,
    segment_count INTEGER DEFAULT 0,
    UNIQUE (book_id, name)
);

-- book_seq is the chronological spine: ordering by it replays the book in
-- reading order regardless of how segments are filtered.
CREATE TABLE IF NOT EXISTS segments (
    id                 INTEGER PRIMARY KEY,
    book_id            INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_number     INTEGER NOT NULL,
    chapter_seq        INTEGER NOT NULL,
    book_seq           INTEGER NOT NULL,
    kind               TEXT NOT NULL,
    speaker            TEXT NOT NULL,
    speaker_raw        TEXT,
    attribution_source TEXT,
    text               TEXT NOT NULL,
    word_count         INTEGER,
    char_start         INTEGER,
    char_end           INTEGER,
    audio_start_s      REAL,
    audio_end_s        REAL,
    duration_s         REAL,
    align_score        REAL,
    align_words        INTEGER,
    UNIQUE (book_id, chapter_number, chapter_seq)
);

CREATE INDEX IF NOT EXISTS idx_seg_speaker ON segments (book_id, speaker, book_seq);
CREATE INDEX IF NOT EXISTS idx_seg_order   ON segments (book_id, book_seq);
CREATE INDEX IF NOT EXISTS idx_seg_score   ON segments (book_id, align_score);
CREATE INDEX IF NOT EXISTS idx_seg_kind    ON segments (book_id, kind, book_seq);

-- Word timings drive precise clip boundaries and let a later pass trim or
-- re-cut without re-running alignment.
CREATE TABLE IF NOT EXISTS words (
    segment_id INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    word       TEXT NOT NULL,
    start_s    REAL,
    end_s      REAL,
    score      REAL,
    PRIMARY KEY (segment_id, idx)
) WITHOUT ROWID;

-- Open-ended annotation store. Later passes (emotion, intensity, MOS, speaker
-- embeddings, pronunciation flags) add keys here instead of altering segments.
CREATE TABLE IF NOT EXISTS segment_meta (
    segment_id    INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,
    value         TEXT,
    numeric_value REAL,
    source        TEXT NOT NULL DEFAULT 'manual',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (segment_id, key, source)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_meta_key ON segment_meta (key, value);

-- book_id rides along unindexed purely so a search can be narrowed to one
-- book; without it a MATCH silently spans the whole library.
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5 (
    text,
    book_id UNINDEXED,
    content = 'segments',
    content_rowid = 'id'
);

-- Everything needed to pick clips for a voice, already ordered chronologically.
-- book_id leads the ordering because book_seq restarts at each book, so
-- ordering by it alone interleaves every book in the library.
CREATE VIEW IF NOT EXISTS v_voice_corpus AS
SELECT s.id, s.book_id, s.speaker, s.speaker_raw, s.kind, s.chapter_number,
       s.book_seq, s.text, s.word_count, s.audio_start_s, s.audio_end_s,
       s.duration_s, s.align_score
FROM segments s
WHERE s.audio_start_s IS NOT NULL
ORDER BY s.book_id, s.book_seq;
