# How this project works, in plain language

This document explains the whole audiobook system from the ground up. It
assumes no background knowledge. Technical words are used only where there is
no plain equivalent, and every one of them is defined in the index below, so
you can jump back up whenever a term stops making sense.

## Terminology index

**Alignment (forced alignment).** Working out exactly when each written word is
spoken in a recording. You give the computer both the audio and the text it
already knows is being read, and it returns a start and end time for every
word. It is far more reliable than transcription, because the answer is
already known and only the timing has to be found.

**ASR (automatic speech recognition).** Software that listens to audio and
writes down what it hears. Transcription. We use it to check our own output,
not to produce it.

**Attribution.** Deciding which character speaks a given line. The book says
the words; it does not always say who said them.

**Casting.** Assigning an actual voice to each character, the way a director
casts actors.

**Chapter markers.** Bookmarks stored inside an audio file that record where
each chapter starts and ends. Not every audiobook has them. This one does,
which saved a great deal of work.

**Checkpoint.** One trained version of a model. The same software can load
different checkpoints with different abilities: ours comes in one that offers
a fixed set of stock voices, one that copies a voice from a sample, and one
that follows written style directions. Only one can be loaded at a time.

**In-context cloning.** Copying a voice by giving the model both a sample
clip and the exact words spoken in it. Supplying the words gives a better
result than the clip alone. Normally the words are the hard part, because a
clip cut from an audiobook comes with no transcript; our alignment already
knows them.

**Replica.** A second or third copy of the same service running side by side.
Used when one copy can only work on one thing at a time.

**Confidence score.** A number from 0 to 1 the alignment produces for each
word, saying how sure it is that it found the right moment. Low scores flag
places worth inspecting.

**Container (Docker).** A sealed box holding a program and everything it needs
to run. It keeps projects from interfering with each other and means the same
setup runs identically on another machine.

**Emotion instruct.** A sentence of plain-English direction handed to the voice
model along with the text, such as "speak slowly, as if pondering deeply". The
equivalent of a stage direction.

**Epub.** The standard ebook file format. Underneath it is a zip archive of web
pages plus a table of contents.

**GPU.** The graphics chip. It performs many small calculations at once, which
is what AI models need. Ours lives in a shared machine called the Spark.

**LLM (large language model).** The kind of AI that understands and writes
text, like Claude or the local Ollama models. We use one for judgement calls
that rules cannot make.

**Narrator.** The storytelling voice, distinct from any character. In first
person books the narrator and the protagonist are the same person but are
still cast as separate voices, because narration and speech sound different.

**Ollama.** A program that runs an LLM locally on your own hardware rather than
calling a paid service.

**Orchestrator.** The component that runs the other components in the right
order and keeps track of the job.

**Prosody.** The music of speech: rhythm, stress, rising and falling pitch. It
is what makes a sentence sound alive rather than recited.

**RTF (real-time factor).** How long generation takes compared to the length of
audio produced. An RTF of 1.5 means ninety seconds of computing for sixty
seconds of speech. Below 1.0 is faster than real time.

**Sample rate.** How many times per second the audio was measured, in Hertz.
Higher means more detail and larger files. Speech is fine at 24,000.

**Segment.** One short piece of the book, either a run of narration or a single
piece of dialogue. Segments are the unit everything else is built on.

**SQLite.** A complete database kept in a single ordinary file. No server to
run or maintain.

**TTS (text to speech).** Software that reads text aloud in a synthetic voice.
The core of the project.

**Voice cloning.** Getting a TTS model to imitate a specific real voice from a
short sample, rather than using one of its stock voices.

**Whisper.** A well-known speech recognition model from OpenAI. We use it to
check our own audio.

## What the project is for

The goal is to take a book and produce an audiobook where each character has
their own distinct voice, using models that run on our own hardware rather than
a commercial service.

That splits into two problems that are easy to confuse. The first is making the
audio at all. The second, harder one, is knowing whether the audio is any good.
Machines are poor judges of their own speech, so most of the recent work has
gone into building an independent yardstick.

## Part one: turning a chapter into audio

Seven small programs, each in its own container, do one job apiece. An
orchestrator runs them in order.

**Reading the text and working out who says what.** The chapter is split into
segments. A run of narration is one segment, and every piece of dialogue is
another. The split happens at quotation marks, character by character, which
sounds crude but has an important property: the text is never rewritten, only
divided, so nothing can be silently lost or altered.

Then each line of dialogue has to be attributed to a speaker. Sometimes the
book says so outright. Often it does not, because in a two-person conversation
a human reader simply tracks the alternation. The system does the same, in
escalating steps: use the explicit attribution when there is one, otherwise
alternate between the two established speakers, and if only one character is
present, give them the line.

This part is deliberately cautious. It would rather leave a line unattributed
than guess wrongly, because an unattributed line can be fixed later, whereas a
wrong guess invents a character who then gets cast with their own voice and
audibly derails the chapter. That exact failure happened early on and produced
sixteen imaginary characters.

**Casting the voices.** Each character is assigned one of nine stock voices and
keeps it for the whole book. Two rules matter. Gender is inferred from the
pronouns used near a character's lines and never crossed, because a man
speaking in a woman's voice is the most jarring error the system can make. And
the narrator is cast first, with their voice reserved, so that no character can
accidentally end up sounding identical to the narration.

**Speaking it.** Each segment goes to the TTS model separately and the
resulting clips are joined end to end with pauses inserted between them.

Originally each line was sent with a short written direction describing the
emotion wanted. That has been dropped in favour of copying a voice from a real
sample, partly because the checkpoint that copies voices cannot accept written
directions, and partly because changing the direction from line to line was
itself making a character stop sounding like himself across a chapter.

One copy of the model can only speak one line at a time, so asking it for more
at once simply forms a queue. Running three copies side by side was what
actually made it faster, and generation now runs slightly quicker than the
audio it produces.

This one design decision, generating segment by segment and stitching, causes
most of the remaining quality problems. The model has no memory across the
join, so the natural rise and fall of a sentence resets at every boundary.

**Checking it.** The finished audio is transcribed back with Whisper and
compared against the text it was supposed to be reading. Where the two disagree
badly, the segment is flagged. On the reference chapter this caught four
genuine faults, two of which were independently confirmed by listening.

The check has a known blind spot worth understanding. Speech recognition tries
hard to hear what makes sense, so if a word is mispronounced but the sentence
is otherwise clear, it will helpfully transcribe the word that was intended.
The check therefore catches missing, invented, or wrong words, but not bad
pronunciation.

## Part two: the reference corpus, and why it exists

Everything above can tell you the system produced the right words. None of it
can tell you whether the delivery is any good, because there is nothing to
compare against.

The solution is to use the professionally narrated commercial audiobook of the
same text as a benchmark. Not to copy the narrator, but to have a human
performance of the very same sentence sitting next to ours.

Doing this needs the recording broken into exactly the same segments as our
text. That is what alignment provides. We already know the words; we only need
their timings. The result is that every segment of the book has both our
synthetic version and the professional version of the identical sentence, so
they can be compared directly.

The first measurement this made possible was blunt and useful: our chapter runs
about twenty-two percent longer than the professional reading. More
importantly, the excess is not spread evenly. It sits almost entirely in short
segments, where our model drawls badly. One short line took over four seconds
against the professional's three quarters of a second. Long passages are paced
correctly. Without the comparison this would have looked like a vague pacing
complaint instead of one specific, fixable defect.

The same aligned clips have a second use later. Voice cloning needs only a few
seconds of clean reference audio, so once cut, these become raw material for
building new voices. That is worth stating plainly: cloning a commercial
narrator's voice runs into performance rights and voice likeness protections in
several countries, so anything built that way is strictly personal and not for
distribution. Using the recording purely as a quality yardstick, which is the
main purpose here, raises none of that.

## How the corpus is built

Five steps, each saving its results per chapter so a long run can be stopped
and resumed rather than restarted.

First the audio and ebook are copied locally and the chapter markers read
straight out of the audio file. This book has clean markers for all 113
chapters, so no boundary detection is needed.

Second the ebook is unpacked into one plain text file per chapter, using its
table of contents. Publishers often split a chapter across several internal
files, so these are stitched back together.

Third each chapter is split into segments and attributed, reusing the same code
the audiobook generator uses. This step needs no GPU and finishes the entire
book in about a second.

Fourth comes alignment, which is the slow part and the only step needing real
computing power. It runs on the desktop rather than the shared GPU machine,
because it is bulk work that tolerates being slow.

Fifth everything is loaded into a single database file.

## How the results are stored, and why that shape

Everything lands in one SQLite file. Each segment records what is said, who
says it, whether it is narration or dialogue, where it falls in the book, and
precisely where it sits in the narration audio.

Two ways of reading it were needed, and they pull in opposite directions. You
want the book in order, front to back. You also want every line a particular
character speaks, gathered together, to build their voice. Storing a single
running sequence number for the whole book gives the first, and indexing by
speaker gives the second, so both are direct lookups rather than one being
reconstructed painfully from the other.

Timings are also kept for every individual word, not just each segment. That
means clip boundaries can be tightened later without redoing the alignment,
which is the expensive step.

Extra information gets its own separate table, holding simple key and value
pairs attached to a segment, along with a note of what produced it. Emotion
labels, quality scores and pronunciation flags can all be added later without
altering the existing structure, and a later pass can disagree with an earlier
one without overwriting it. This is what makes the metadata extensible in
practice rather than in principle.

Audio clips are deliberately not cut in advance. The book holds roughly
eighteen thousand segments, and cutting every one would cost hours and
gigabytes to produce material used a few hundred clips at a time. The exact
timings are stored instead, and clips are cut on demand for whatever selection
is asked for, along with a manifest listing what each clip contains.

## Making a new book that matches the old ones

The eventual point of all this is to narrate a book that has never been
recorded, in a way that sits comfortably beside the eleven that have.

The voices come from the corpus. For every character we hold clean clips of
the real narrator performing them, together with the exact words in each clip,
so the model can be told "sound like this" rather than being handed a stock
voice. A handful of the best clips per character are exported as a voice bank.
Building that instruction is slow, so it is done once per character and reused
for every line they speak, rather than once per line.

Characters new to the story present an obvious problem: there is no recording
of them, because nobody has ever narrated them. This turns out not to matter.
Since no performance exists, none can be contradicted, and no listener has an
expectation to violate. Those characters are given spare voices from the bank,
which are still the real narrator in his character range. The assignment is
arbitrary but deliberately stable, so a character keeps the same voice every
time the book is regenerated. What genuinely must be right is the narrator and
the protagonist, who carry most of the running time, and those we hold hours
of material for.

Two things about the new chapters differ from the corpus work. There is no
human recording to align against, so the only automatic check is whether the
right words were spoken. And the rule-based guessing of who is speaking, which
is nearly always right across a novel's prose, struggles badly in
dialogue-dense scenes with many people in the room, so the language model is
brought in to settle those. On the test chapters that took the unresolved
share from about one line in eight to none.

## What is currently wrong

Honest list, because these shape what happens next.

The biggest is that short segments are drawled badly, which is the single
defect behind the pacing gap and probably behind some garbled lines too.
Merging short segments into their neighbours before generation is the most
promising fix and would help several problems at once.

Emotion instructs change the perceived identity of a voice. A character given
eight different emotional directions across a chapter can stop sounding like
the same person, even when correctly attributed and correctly cast. Anchoring
identity with a cloned reference voice is the likely fix, which is why the
corpus work comes first.

Generation is slower than the hardware should allow, because requests are
processed strictly one at a time. The GPU sits mostly idle.

Text that is not ordinary prose is handled poorly. Game style stat blocks with
brackets and question marks come out as gibberish, fractions are read wrongly,
and non-English phrases are mangled because every segment is declared to be
English.

Attribution during the corpus build is rule based only, so about two percent of
segments have no identified speaker. Characters are also matched by exact name,
so a person referred to two different ways currently counts as two people.

## Running it

The audiobook generator runs as a set of containers started together from the
project directory. The corpus builder is a separate command line tool under
`corpus/`, documented in `corpus/README.md`, whose steps are run in order:
fetch, extract, segment, align, load. After that, `stats` summarises what was
built and `clips` cuts audio for a chosen character or filter.
