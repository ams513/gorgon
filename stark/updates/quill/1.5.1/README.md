# Stark Quill

A place to write a novel, a screenplay, an essay or a thesis that keeps every word in plain files on your own
computer. Version 1.5.1.

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
| Corkboard with a note and a status for each chapter | yes | yes |
| Search the whole book, and replace (a backup is made first) | yes | yes |
| Automatic backups, and a graveyard for paragraphs you delete | yes | yes |
| Focus mode, page and type settings, daily goal and streak | yes | yes |
| A word target with a finish date: Quill works out the words a day | yes | yes |
| Read aloud from the cursor with the voices on this PC, sentence by sentence | yes | yes |
| Paste from Word or Google Docs keeps italics and bold | yes | yes |
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
| Export a screenplay PDF (Courier, MORE and CONT'D) and a Final Draft (.fdx) file | | yes |
| Write from your phone over your home Wi-Fi | | yes |
| Continuity questions from a local AI model (through Ollama, on your PC) | | yes |

On itch.io, Pro is sold as **Stark Quill PRO, 12 months**: the download comes with its key inside, and the 12
months start the first time you open Quill on a computer. When they end, Quill switches to Free. Every book
stays yours, readable, editable and exportable as text.

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
  every speaking part, and the running time. Export a .fountain file for free; a screenplay PDF and a Final Draft
  file with Pro.
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
