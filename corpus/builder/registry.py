"""Decide who each speaker label actually is, using an LLM over book evidence.

Attribution invents labels. The mechanical duplicates are cleaned up by
``aliases``, which only ever acts on evidence it can read out of the prose. What
is left needs judgement: whether a label names a person at all, which labels
name the same person, and what gender a character is.

Gender is the field that pays for this pass. The per-chapter registry tallies
pronouns in the narration adjacent to a character's lines, and in first-person
prose that narration is about the narrator, so every character accrues spurious
male votes. Reading the same evidence as a reader, rather than counting it,
gets the ambiguous cases right.

The model proposes and a deterministic guard vetoes. A merge that fuses two
real characters corrupts every voice corpus built from them, so a proposed
merge survives only when the prose independently supports it: an unambiguous
surname, or a label that contains another label. Two plain first names are
never merged on the model's say-so, which is what keeps Gabriel, Gabriele and
Gabrielle apart.
"""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

from . import aliases

MODEL = "qwen3.5:27b"
ENDPOINT = "http://localhost:11436"
TIMEOUT_S = 300
ATTEMPTS = 2

NOT_CHARACTERS = ("narrator", "unknown")
NARRATION_SNIPPETS = 6
DIALOGUE_SNIPPETS = 4
SNIPPET_CHARS = 260
APPLY_CONFIDENCE = 0.6

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_character": {"type": "boolean"},
        "canonical": {"type": ["string", "null"]},
        "gender": {"type": "string", "enum": ["male", "female", "unknown"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["is_character", "canonical", "gender", "confidence", "reason"],
}

SYSTEM = """You identify characters in a novel from evidence taken out of its text.

You judge one speaker label at a time. The label came from an automatic
dialogue-attribution pass, so it may be a real character, a misparse, or a
second name for a character who is already in the cast list.

Answer with JSON only:
  is_character  false if the label is not a person (a place, an object, a
                misparse, a system message, a title with no person behind it).
  canonical     the cast-list name of the same person, when this label is a
                second name for someone already listed. null when the label is
                itself the person's usual name, and null whenever you are not
                sure. Two different people who share a family name or whose
                names merely look similar are NOT the same person.
  gender        male, female, or unknown.
  confidence    0 to 1, how sure you are of the whole verdict.
  reason        one short sentence, citing what in the evidence decided it.

"unknown" and null are correct answers, not failures. The evidence is written
in the first person by the narrator, so pronouns in narration often refer to
the narrator rather than to the character being judged. Only call a gender when
the evidence shows it for this character specifically."""


def _labels(conn: sqlite3.Connection, book_id: int, min_lines: int) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT speaker AS name, COUNT(*) AS lines,
                  MIN(chapter_number) AS first_ch, MAX(chapter_number) AS last_ch
           FROM segments
           WHERE book_id = ? AND kind = 'dialogue'
             AND speaker NOT IN (?, ?)
           GROUP BY speaker HAVING COUNT(*) >= ?
           ORDER BY COUNT(*) DESC""",
        (book_id, *NOT_CHARACTERS, min_lines),
    ).fetchall()
    return {r["name"]: dict(r) for r in rows}


def _votes(conn: sqlite3.Connection, book_id: int) -> dict[str, tuple[int, int]]:
    return {r["name"]: (r["m"], r["f"]) for r in conn.execute(
        """SELECT name, SUM(male_votes) m, SUM(female_votes) f
           FROM chapter_characters WHERE book_id = ? GROUP BY name""",
        (book_id,))}


def _snippets(conn: sqlite3.Connection, book_id: int, name: str) -> dict[str, list[str]]:
    """Narration that mentions the name, plus lines the label is credited with.

    The narration is what carries gender and identity; the dialogue mostly
    shows voice. Both are capped so one prolific character cannot dominate the
    prompt.
    """
    quoted = '"' + name.replace('"', "") + '"'
    narration = [r["text"][:SNIPPET_CHARS] for r in conn.execute(
        """SELECT s.text FROM segments_fts f
           JOIN segments s ON s.id = f.rowid
           WHERE f.text MATCH ? AND s.book_id = ? AND s.kind = 'narration'
           ORDER BY s.book_seq LIMIT ?""",
        (quoted, book_id, NARRATION_SNIPPETS))]
    dialogue = [r["text"][:SNIPPET_CHARS] for r in conn.execute(
        """SELECT text FROM segments
           WHERE book_id = ? AND speaker = ? AND kind = 'dialogue'
             AND word_count BETWEEN 4 AND 40
           ORDER BY book_seq LIMIT ?""",
        (book_id, name, DIALOGUE_SNIPPETS))]
    return {"narration": narration, "dialogue": dialogue}


def gather(conn: sqlite3.Connection, book_id: int, min_lines: int = 2) -> list[dict]:
    """Assemble the evidence bundle the model judges, one entry per label."""
    labels = _labels(conn, book_id, min_lines)
    votes = _votes(conn, book_id)
    out = []
    for name, info in labels.items():
        m, f = votes.get(name, (0, 0))
        out.append({**info, "male_votes": m, "female_votes": f,
                    **_snippets(conn, book_id, name)})
    return out


def _prompt(bundle: dict, cast: list[str], title: str) -> str:
    parts = [f"Novel: {title}",
             f"Cast list (labels the attribution pass produced): {', '.join(cast)}",
             "",
             f"Label under judgement: {bundle['name']}",
             f"Speaks {bundle['lines']} lines, chapters "
             f"{bundle['first_ch']}-{bundle['last_ch']}.",
             f"Pronoun tally from adjacent narration: "
             f"{bundle['male_votes']} male, {bundle['female_votes']} female "
             f"(unreliable in first-person prose).", ""]
    if bundle["narration"]:
        parts.append("Narration mentioning this name:")
        parts += [f"  - {t}" for t in bundle["narration"]]
        parts.append("")
    if bundle["dialogue"]:
        parts.append("Lines credited to this label:")
        parts += [f"  - {t}" for t in bundle["dialogue"]]
    return "\n".join(parts)


GENDERS = ("male", "female", "unknown")


def _num(x) -> float:
    """Confidence as a float, whatever the model put in the field."""
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _bool(x):
    """Tri-state: True, False, or None when the model gave nothing usable."""
    if isinstance(x, bool):
        return x
    if isinstance(x, str) and x.lower() in ("true", "false"):
        return x.lower() == "true"
    return None


_BARE_ENUM = re.compile(r'("gender"\s*:\s*)(male|female|unknown)\s*([,}])')


def loads(text: str) -> dict:
    """Parse a verdict, repairing the one malformation ollama actually emits.

    Constrained decoding is not airtight here: the enum field comes back as a
    bare word often enough that discarding an otherwise complete verdict would
    lose real characters.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_BARE_ENUM.sub(r'\1"\2"\3', text))


def ask(prompt: str, model: str = MODEL, endpoint: str = ENDPOINT) -> dict:
    """One verdict from the model, as validated JSON."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
        "stream": False,
        "format": VERDICT_SCHEMA,
        "keep_alive": "30m",
        "think": False,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/api/chat", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        payload = json.loads(resp.read())
    return loads(payload["message"]["content"])


def classify(bundles: list[dict], title: str, model: str = MODEL,
             endpoint: str = ENDPOINT, on_result=None,
             cast: list[str] | None = None) -> list[dict]:
    # The cast is the whole book's, never just the labels being judged: it is
    # what the model picks a canonical name from, so judging a subset must not
    # hide the character the subset should merge into.
    cast = cast or [b["name"] for b in bundles]
    out = []
    for b in bundles:
        prompt = _prompt(b, cast, title)
        v = None
        for attempt in range(ATTEMPTS):
            try:
                v = ask(prompt, model, endpoint)
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                    KeyError) as exc:
                # Schema-constrained decoding still truncates occasionally. A
                # retry costs one call; a dropped verdict costs a character.
                v = {"is_character": None, "canonical": None,
                     "gender": "unknown", "confidence": 0.0,
                     "reason": f"model call failed after {attempt + 1} "
                               f"attempt(s): {exc}"}
        row = {"name": b["name"], "lines": b["lines"],
               "is_character": _bool(v.get("is_character")),
               "canonical": v.get("canonical") or None,
               "gender": v.get("gender") if v.get("gender") in GENDERS else "unknown",
               "confidence": _num(v.get("confidence")),
               "reason": str(v.get("reason") or "")[:300]}
        out.append(row)
        if on_result:
            on_result(row)
    return out


def guard(verdicts: list[dict], labels: dict[str, int], text_dir: Path) -> list[dict]:
    """Veto merges the prose does not independently support.

    Three things survive: a label that ends in another label, a bare surname
    the prose gives to exactly one first name, and nothing else. Two plain
    names are never merged, because the model cannot tell a nickname from a
    different person with a similar name and the cost of being wrong is a
    corrupted voice corpus.
    """
    anchors = aliases.anchors_of(labels)
    by_surname, ambiguous = aliases.surname_owners(text_dir, anchors)
    owner = {s: next(iter(f)) for s, f in by_surname.items() if s not in ambiguous}

    for v in verdicts:
        name, canonical = v["name"], v["canonical"]
        tokens = name.split()
        v["verdict"] = "proposed"
        if not canonical:
            continue
        if canonical == name:
            v["canonical"], v["verdict"] = None, "vetoed:self"
        elif canonical not in labels:
            v["verdict"] = "vetoed:unknown-target"
        elif len(tokens) > 1 and canonical in tokens and canonical not in ambiguous:
            # "Phoebe Geller" -> "Phoebe" is safe even though Geller is shared:
            # the merge is carried by the given name, not the family name.
            v["verdict"] = "merge"
        elif name in ambiguous or (len(tokens) > 1 and tokens[-1] in ambiguous):
            v["verdict"] = "vetoed:shared-surname"
        elif owner.get(name) == canonical:
            v["verdict"] = "merge"
        else:
            v["verdict"] = "vetoed:unsupported"
    return verdicts


def store(conn: sqlite3.Connection, book_id: int, verdicts: list[dict],
          model: str) -> None:
    conn.executemany(
        """INSERT INTO character_registry
               (book_id, name, is_character, canonical, gender, confidence,
                reason, verdict, model)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(book_id, name) DO UPDATE SET
               is_character = excluded.is_character,
               canonical    = excluded.canonical,
               gender       = excluded.gender,
               confidence   = excluded.confidence,
               reason       = excluded.reason,
               verdict      = excluded.verdict,
               model        = excluded.model,
               created_at   = datetime('now')""",
        [(book_id, v["name"],
          None if v["is_character"] is None else int(v["is_character"]),
          v["canonical"] if v["verdict"] == "merge" else None,
          v["gender"], v["confidence"], v["reason"], v["verdict"], model)
         for v in verdicts],
    )
    conn.commit()


def apply(conn: sqlite3.Connection, book_id: int,
          min_confidence: float = APPLY_CONFIDENCE) -> dict:
    """Push surviving merges into the alias table and gender into characters.

    ``min_confidence`` gates merges only. Gender is applied by
    ``rebuild_characters`` against ``APPLY_CONFIDENCE``, because that runs
    again on every corpus load: a threshold chosen once at the command line
    would silently disagree with the next reload.
    """
    from . import db

    merges = conn.execute(
        """SELECT name, canonical, confidence FROM character_registry
           WHERE book_id = ? AND verdict = 'merge' AND canonical IS NOT NULL
             AND confidence >= ?""", (book_id, min_confidence)).fetchall()
    conn.executemany(
        """INSERT INTO speaker_aliases (book_id, alias, canonical, reason, evidence)
           VALUES (?,?,?,'llm',0)
           ON CONFLICT(book_id, alias) DO UPDATE SET
               canonical = excluded.canonical, reason = 'llm'""",
        [(book_id, r["name"], r["canonical"]) for r in merges])
    conn.commit()

    aliases.apply(conn, book_id)
    db.rebuild_characters(conn, book_id)
    conn.execute("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')")
    conn.commit()
    genders = conn.execute(
        """SELECT COUNT(*) FROM characters c JOIN character_registry r
           ON r.book_id = c.book_id AND r.name = c.name
           WHERE c.book_id = ? AND r.gender IN ('male','female')
             AND r.confidence >= ?""", (book_id, APPLY_CONFIDENCE)).fetchone()[0]
    return {"merged": len(merges), "gender_set": genders}
