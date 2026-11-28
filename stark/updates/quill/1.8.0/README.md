# Stark Quill

A place to write a novel, a screenplay, an essay or a thesis that keeps every word in plain files on your own
computer. Version 1.7.0.

Your book lives in `Documents\My_Books\<your book>`: one text file per chapter, named in reading order
(`01 Prologue.md`, `02 The Harbour.md`), so any text editor can open it today or in twenty years. Nothing is
uploaded. If you stop using Quill, your book is still right there.

## Install

**Windows.** Unzip the download and double-click `install_quill.bat`. It copies Quill to your own user folder
(no admin rights), creates `Documents\My_Books` if it is not there yet (an existing one is left exactly as it
is), puts a Stark Quill shortcut on your desktop and opens Quill in your browser. The `-windows` download
carries its own copy of Python. The `-small` download needs Python 3.10 or newer from python.org.

**macOS and Linux.** Install Python 3.10 or newer, unzip the `-small` download and run
`sh start_stark_quill.sh` in that folder.

## What you get

| | Free | Pro |
|---|---|---|
| Writing, chapters you can rename, reorder and delete (deleted ones stay restorable) | yes | yes |
| Kinds of project: novel, short story, screenplay, essay or paper, thesis, nonfiction, 30-day novel | yes | yes |
| Import a manuscript (.docx, .odt, .md, .txt, .fountain), split into chapters at its headings | yes | yes |
| Screenplays in Fountain: element buttons and Tab, script preview, scenes, speaking parts, running time | yes | yes |
| Sources and citations in MLA, APA or Chicago, with the reference list added to exports | yes | yes |
| Comments and footnotes in the text; exports leave comments out and number the footnotes | yes | yes |
| Compare a chapter with any backup, word by word | yes | yes |
| Scene details (point of view, setting, story time), plotlines and a plot grid | yes | yes |
| Writing sprints, readability and passive voice, quick search (Ctrl+K) | yes | yes |
| Story X-ray: mood arc, echo finder, character web and typo-forgiving find | yes | yes |
| Voice and rhythm: a bar for every sentence, and each character's dialogue measured | yes | yes |
| Corkboard with a note and a status for each chapter | yes | yes |
| Chapter word targets, keywords and a printable chapter outline | yes | yes |
| Reference pane: read another chapter beside the one you write | yes | yes |
| Milestones: named copies of the whole book, compare and restore a chapter | yes | yes |
| Before you send it: a manuscript checklist, and a contact block on the first page | yes | yes |
| Writing week with rest days that keep your run, printable | yes | yes |
| Collections: saved lists of chapters, by hand or by rule | yes | yes |
| Rescue sheet: one printable page that says where the book is and how to get it back | yes | yes |
| Search the whole book, and replace (a backup is made first) | yes | yes |
| Automatic backups, and a graveyard for paragraphs you delete | yes | yes |
| Focus mode, page and type settings, daily goal and streak | yes | yes |
| A word target with a finish date: Quill works out the words a day | yes | yes |
| Read aloud from the cursor with the voices on this PC, sentence by sentence | yes | yes |
| Paste from other word processors keeps italics and bold | yes | yes |
| Back up the whole book to one checked zip, downloaded or on a USB stick | yes | yes |
| Lore panel: characters, places, dates and continuity checks | yes | yes |
| Progress heatmap, words per session, projected finish date | yes | yes |
| Plot outlines (three acts, Save the Cat, Hero's Journey) with a pacing view | yes | yes |
| Submission tracker with query letter and synopsis templates | yes | yes |
| Series bible shared by several books, with continuity checks across them | yes | yes |
| Revision checks: overused words, sentence variety, adverbs, filler, dialogue tags | yes | yes |
| Spell check on this PC that knows your character and place names (Windows) | yes | yes |
| Library (opt-in, online): public-domain books, Wiktionary, Wikipedia, Open Library, research clips with citations | yes | yes |
| Export to plain text, Markdown, a web page and Fountain | yes | yes |
| Export to EPUB, Word (book or standard manuscript) and print-ready PDF | | yes |
| Export a screenplay PDF (Courier, MORE and CONT'D) and a script-format (.fdx) file | | yes |
| Write from your phone over your home Wi-Fi | | yes |
| Continuity questions from a local AI model (through Ollama, on your PC) | | yes |

On itch.io, Pro is sold as **Stark Quill PRO, 12 months**: the download comes with its key inside, and the 12
months start the first time you open Quill on a computer. When they end, Quill switches to Free. Every book
stays yours, readable, editable and exportable as text.

## What's new in 1.8.0

- **The desk.** Before you send it (Ctrl+Alt+P): a checklist for the manuscript layout, with the word count as the first
  page prints it. Book details take a contact block that the manuscript Word and PDF files print at the top of page
  one. Today and goals shows your week and lets you mark rest days, so a day off does not break your run, and it
  tells you how many words keep today's run alive. Collections save lists of chapters by hand or by rule. The rescue
  sheet prints where the book lives and how to get it back. All free, all on this PC.
- **Screenplays.** Scene numbers on request, and dual dialogue (a second cue ending in ^) set in two columns in the
  script PDF and written as DualDialogue in the .fdx file.

## What's new in 1.7.0

- **The writing bench.** Five free tools for working on a whole book. Chapter targets: a word target for any chapter,
  shown as a bar on the corkboard and in the outline and as "800 of 1,500 words" above the page. Keywords: your own
  tags on chapters, with the corkboard and outline able to show one at a time. Chapter outline: a table of the book
  (Ctrl+Alt+O) with status, words against target, keywords and note, which prints cleanly and copies as Markdown.
  Reference pane: read another chapter and its note beside the one you are writing (Ctrl+Alt+R), read only. Milestones:
  a named copy of the whole book (Ctrl+Alt+S) that stays until you delete it, with a word-by-word compare against
  today's text and a restore for any one chapter. Targets and keywords live in book.json, never in your chapters.

## What's new in 1.6.1

- **Voice and rhythm.** Two more pictures of your manuscript, counted on your PC. Sentence rhythm draws one bar for
  every sentence in a chapter, tints any run of six or more sentences of the same kind, and lists every chapter's
  average and longest sentence. Character voices takes the lines of dialogue that sit beside each character's
  name and measures them: words a line, how often they ask or shout, contractions, their own favourite words. Pairs
  that measure alike are listed, so you can check two people do not sound the same. A line counts for a character
  only when exactly one name stands beside the quotation marks, and speech is read from double quotation marks
  (dialogue in single quotation marks is not read yet). Hints, not verdicts; nothing in your chapters is changed.
  Free, in the More menu and Ctrl+K.
- **Safer phone pairing and file checks.** New phone-pairing certificates made on Windows may only vouch for this
  PC's own names and home-network addresses, and the EPUB and Word file checkers refuse a file that unpacks to
  gigabytes.

## What's new in 1.6.0

- **Print cover size.** For a paperback: the page count (estimated from your words, or the one you type), the
  spine width for white, cream or colour paper, and the full wrap-around cover size with bleed in inches,
  millimetres and pixels at 300 dpi, with a to-scale layout guide (back, spine, front, safe areas) to download as
  SVG. Free, in the More menu and Ctrl+K. Printers change their figures: check yours before you order.
- **Story X-ray.** Four pictures of your manuscript, all worked out on your PC from plain word lists and counts:
  a mood arc (how warm or dark each stretch reads, with a dashed line for how tense the pace is, and the share of
  dialogue), an echo finder (a distinctive word used again within a few lines), a character web (who shares
  scenes, how strongly, where each pair first meets, and strong characters who never do), and a find that
  forgives typos, missing accents and word order and looks at sentences, not files. They are second opinions, not
  verdicts, and nothing in your chapters is changed. Free, in the More menu and Ctrl+K.

## What's new in 1.5.0

- Kinds of project. A new book can be a novel (with or without a three-act outline), a short story, a
  screenplay, an essay or research paper, a thesis, a nonfiction book or a 30-day, 50,000-word novel. Each starts
  with its chapters, a word target and the right settings. Change the kind later in Book details.
- Import a manuscript. Bring in a Word, OpenDocument, Markdown, text or Fountain file: Quill splits it into
  chapters at its headings (or at "Chapter 3" lines, or page breaks), keeps italics, bold and footnotes, and never
  drops text. Book menu, then Import a manuscript.
- Screenplays. A screenplay book is written in Fountain, the plain-text screenplay format. The bar above the page
  sets the current line as a scene heading, action, character, parenthetical, dialogue or transition (Tab does the
  same). Preview shows script pages. The Script panel lists every scene with its page and length in eighths,
  every speaking part, and the running time. Export a .fountain file for free; a screenplay PDF and a script-format .fdx file with Pro.
- Sources and citations. Keep the books, articles, web pages and films you draw on in Sources.json, formatted in
  MLA 9, APA 7 or Chicago 17. Cite puts an in-text citation at the cursor (Chicago adds a footnote). Exports can
  add the Works Cited, References or Bibliography list after the last chapter. Library clips become sources in
  one click.
- Comments and footnotes. Ctrl+Alt+M adds a comment, kept in the text as {>>a note<<}; comments do not count as
  words and never appear in an export. Footnotes are kept as [^1] with their note at the end of the chapter, and
  exports print them as numbered notes. The Comments panel lists them all.
- Compare with a backup: the Backups panel shows, word by word, what changed in a chapter since any backup.
- Scene details and plotlines: give each chapter a point of view, a setting and a story time, and name your
  plotlines. The corkboard shows them and can show one plotline or point of view at a time; the plot grid shows
  which chapters carry each thread and how the words are shared between points of view.
- Writing sprints: a timer with an optional word goal, the words you add while it runs, and a record of each one.
- Revise checks readability (reading ease, school grade, reading and speaking time) and passive voice.
- Quick search: Ctrl+K jumps to any chapter, book or command.

## Library

The Library is off until you turn it on. When it is on, Quill asks Project Gutenberg, Standard Ebooks,
Wiktionary, Wikipedia or Open Library only when you search, and sends nothing about you or your books. Books you
download go to `Documents\Quill Library`. Quotes you clip go to `Research.md` in the book folder with their source
and licence, never into your chapters. Wiktionary and Wikipedia text is CC BY-SA: credit it and share alike if you
publish it.

## Updates

The first time Quill starts, it asks whether it may check for updates. If you say yes, it asks
`ams513.github.io` once a day whether a newer version exists. Each update is checked against the Stark
signature, and against the size and SHA-256 of every file, before anything is replaced. The new version must
start cleanly once; if it does not, the next start puts the previous version back. You can change your answer
under About. Updates keep your licence working. Please tell us about anything that goes wrong (see
`SUPPORT.md`).

## Licence keys, plainly

- A key is one line of text starting with `GQ1`. Quill checks it on your computer, with no account and no
  internet. To use one, click **Free plan** at the bottom left, paste it, and press **Activate**. You can also
  double-click `ACTIVATE_PRO.bat` in the Quill folder and paste the key there. Neither needs Python on your PC.
- The licence terms allow 2 of your own computers. Quill does not phone home to count them, so that part
  rests on trust.
- A wound-back PC clock never takes Pro away, and it cannot add time either.
- This is light protection. It keeps honest people honest; someone determined could get around anything that
  runs offline.

## Where things are

| What | Where |
|---|---|
| Your books | `Documents\My_Books` |
| Backups of a book | `.backups` inside the book's folder (zip files) |
| Deleted paragraphs | `Deleted_Graveyard.md` inside the book's folder |
| Book details, chapter notes, scene details, plotlines | `book.json` inside the book's folder |
| Sources for citations | `Sources.json` inside the book's folder |
| Writing sprints | `.quill_sprints.json` in your books folder |
| Exports | `exports` inside the book's folder |
| The app itself, its settings and licence | `%LOCALAPPDATA%\StarkQuill` (copies from before 1.5.1 used another folder: Quill copies it here on its first start, checks every file and tells you when the old one can be deleted) |

To remove Quill, delete that folder and the desktop shortcut. Your books stay where they are.

A Stark product.
