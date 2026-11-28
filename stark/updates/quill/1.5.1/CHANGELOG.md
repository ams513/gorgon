# Stark Quill changes

## 1.5.1 (2026-11-21)

Changed
- Quill's own folder is now %LOCALAPPDATA%\StarkQuill. On its first start Quill copies the folder earlier versions
  used (settings, licence and the app) to it, checks every file, points the desktop shortcut at it and then says
  once that the old folder can be deleted. Nothing is deleted for you, and if the copy does not match, Quill keeps
  using the old folder. The phone-mode certificate folder moves the same way, so phones need nothing new.
- Updates come from the Stark address. The earlier address is still tried when the new one cannot be reached.
- New licence keys name their plan stark-quill/pro. Every key issued before keeps working, on as many computers.
- Every file, launcher, shortcut and message carries the Stark name only.

## 1.5.0 (2026-11-20)

New
- Kinds of project: novel, novel with a three-act outline, short story, screenplay, essay or research paper,
  thesis, nonfiction book and a 30-day, 50,000-word novel. Each starts with its chapters, word target and
  settings. The kind can be changed in Book details.
- Import a manuscript from Word (.docx), OpenDocument (.odt), Markdown, plain text or Fountain. Quill shows the
  chapters it found before it makes the book, splits at headings, "Chapter 3" lines or page breaks, keeps italics,
  bold and footnotes, leaves tracked deletions out and never drops text. The original file is not changed.
- Screenplays in Fountain: an element bar (scene heading, action, character, parenthetical, dialogue,
  transition) and Tab to change the current line, a script-page preview, and a Script panel with every scene (page
  and length in eighths), every speaking part and the running time. Exports: Fountain (free), screenplay PDF in
  Courier 12 with MORE and CONT'D, scene numbers and a title page, and Final Draft .fdx (both Pro).
- Sources and citations: books, articles, chapters, web pages, news and films in Sources.json, formatted in MLA 9,
  APA 7 or Chicago 17. Cite puts the in-text citation at the cursor (a footnote for Chicago). Exports can add the
  Works Cited, References or Bibliography list after the last chapter. Library clips become sources in one click.
- Comments ({>>a note<<}, Ctrl+Alt+M) and footnotes ([^1]) kept in the chapter text, with a Comments panel to go
  to or resolve each one. Comments are not counted as words and never appear in exports; footnotes print as
  numbered notes at the end of their chapter.
- Compare a chapter with any backup, word by word (Backups panel, Compare).
- Scene details (point of view, setting, story time) and plotlines, shown on the corkboard, which can show one
  plotline or point of view at a time; a plot grid of plotlines by chapter, with the point-of-view balance.
- Writing sprints with a timer, an optional word goal and a record of each sprint.
- Revise: readability (Flesch reading ease, school grade, reading and speaking time) and passive voice.
- Quick search: Ctrl+K jumps to a chapter, a book or a command.

Changed
- The brand line is "A Stark product", set as text. Download files are named stark-quill-*, and the launcher in
  the download is START_STARK_QUILL.bat (start_stark_quill.sh on macOS and Linux). Existing installs keep their
  folder and shortcut, and update as before.

## 1.4.0 (2026-11-20)

The full release. Everything in the 1.4.0 previews below is in it, plus:

New
- Progress: a calendar heatmap of the last 26 weeks, words per writing session, your 14-day pace, and a finish date projected from that pace against the book's word target (More menu, "Progress and finish date").
- Plot outlines: Three acts, Save the Cat or Hero's Journey. Each beat gets a question, the chapters that carry it and a note; the corkboard shows each chapter's beat, and a pacing view shows words per beat against the share it usually takes.
- Submissions: a tracker for agents, publishers and contests (sent, status, their answer, follow-up dates), plus fill-in templates for a query letter and a synopsis. You write every word; there is no AI and no network.
- Series bible: one list of characters, places and dates for several books, filled from Lore if you like, with Lore's continuity checks run across the whole series in order.
- Revise: overused words, repeated phrases, sentence-length variety, adverbs, filler words and dialogue-tag checks, highlighted in the chapter. Rules only; each check can be switched off.
- All of these are Free, work offline and keep their data in your books folder (Submissions.json in the book, .quill_series.json for series).

## 1.4.0 preview 1 (2026-11-19)

New
- A word target with a finish date. In Book details, give the book a target ("80,000 words") and a date to finish
  by. Quill shares the words still to write over the days left, today and the finish date included, and shows
  today's share in the ring at the top (when no daily goal is set) and in full under Today. The day is your own
  local date. If you miss a day, the words left are shared over fewer days, so the daily figure goes up.
- Read aloud, from the More menu: Quill reads the chapter from the sentence with the cursor, one sentence at a
  time, and highlights the sentence being read. It uses only the voices installed on this computer, so it works
  without the internet; if there is none, it says so. Pause, Stop (or Esc), voice and speed are in the bar at
  the bottom. Typing stops the reading.
- Zip / USB backup, in the sidebar: the whole book (chapters, book.json, notes, research, dictionary, exports) in
  one zip file, downloaded or saved to a USB stick or memory card in a folder called "Stark Quill backups".
  Quill reads the zip back and checks every file before it says it is done. Unzip it to get the book folder
  back. The Backups panel's own copies are not in the zip.
- Paste from Word or Google Docs keeps italics and bold (as *italics* and **bold**) and the paragraphs, and drops
  everything else: fonts, sizes, colours, underline and links (their words stay). A paste without bold or
  italics works exactly as before.

## 1.3.0 preview 2 (2026-11-19)

Help
- A one-page getting-started guide inside Quill. Open it from the first screen ("New here? Read the one-page
  guide"), the tips bar, the More menu (Getting started) or About.

Fixed
- When OneDrive, Dropbox or another program has a chapter file locked, the save light says "Not saved yet: file
  locked" and a note names the cause. The text stays in this browser and in Quill's journal, and Quill tries the
  save again every few seconds until it works. An older save that was held up this way is never put back over
  newer text when Quill next starts.
- Exporting again no longer replaces the earlier file. The newest export keeps the plain name and the earlier one
  is kept as "name (1)", then "name (2)" and so on. The export window says which copy was kept.
- A browser that reloads or closes mid-answer is written to .quill_health.log as one line, not a full error
  report, so real problems are easy to find.

## 1.3.0 preview 1 (2026-11-19)

New
- Spell check that knows your book. Quill underlines spelling slips using the spell checker built into Windows,
  on your computer, with no internet. Character and place names that Lore finds go into the book's own
  dictionary (dictionary.txt in the book folder, one word per line), so they are never underlined. Add any word
  from the Lore panel, or edit the file. Turn it off in Page and type. Where Windows' checker is not available,
  the browser's spell check works as before.
- ACTIVATE_PRO.bat: turn Pro on with a double-click and a pasted key, using the Python that comes with Quill.
  The Licence screen (Free plan, then Activate) works the same way. Neither needs Python on your PC.

Fixed
- After Quill restarts, a page left open reconnects by itself and keeps saving. If the restarted Quill is a
  different version, the page says "Reload this page" with a button, and the text you typed is kept for the
  reload. It no longer says "Not paired / not authorised".
- Lore: names used only at the start of sentences are found again, and "the village of Gullstone" counts as a
  place.
- Brand: the Stark mark replaces the old mark in the app, icons, installer shortcut and store art.

## 1.2.0 preview 1 (2026-11-19)

New
- Library panel, off until you turn it on (it is the only part of Quill that goes online). Search and download
  public-domain books from Project Gutenberg and Standard Ebooks into a Quill Library folder and read them in
  Quill; look up words in Wiktionary (definitions, synonyms, etymology), short Wikipedia summaries and Open
  Library book details. Every result shows its source, licence and link.
- Research: clip a quote into the book's Research.md with its source, licence, address and date. Clips never go
  into your chapters; if you paste one in yourself, Lore shows where it came from and what its licence asks.

Fixed
- Typing could stall for a moment in long chapters while the save light, word count and sidebar updated. The
  header, sidebar and page are now laid out separately, the word count waits for a short pause, and nothing on
  the screen is rewritten unless it changed.
- Chapter titles keep their colons and question marks ("Part Two: Salt", "Who Rang the Bell?") in the list, the
  header and every export. Windows does not allow those characters in file names, so the file uses look-alike
  letters.
- Lore: one ordinary sentence with "remembered" no longer switches off every check in its chapter. Only that
  sentence counts as a memory; a whole chapter is a flashback when its title or opening says so.
- Lore: names with accents (Zoë, Ångrim) are found whole, and words that only start sentences ("Beyond", "Tell")
  are no longer listed as characters. New check: the same event given two different years.
- Updates: feeds signed by the Stark release engine are accepted too; an unsigned or altered feed is still
  refused, and there is no setting that turns the check off.

## 1.1.0 preview 1 (2026-11-19)

First public preview.

New
- A first screen that asks for the book's title and your name, and a short sample book to look around in.
- Chapters can be renamed, reordered (drag or Move up / Move down) and deleted. Deleted chapters wait in
  Backups. The order is kept in the file names, so other programs see the same book.
- Corkboard: one card per chapter, with a note and a status.
- Search the whole book, with Replace (a backup is made first).
- Graveyard and Backups panels: put a deleted paragraph back, preview and restore any backup.
- Focus mode that keeps the word count and save state at the bottom; page and type settings (theme, typeface,
  size, spacing, line length, typewriter scrolling).
- Daily goal, words today and a streak, shared by the PC and a paired phone.
- Licence screen: paste a key, see the end date, turn Pro off on one PC. A 12-month key for the itch.io
  download that starts on first use.
- Book details (title, subtitle, author, language, word target) in `book.json`.
- Exports set like a printed book: title page, optional copyright page, chapter openings with a small-caps
  lead-in, curly quotes and proper dashes. PDF embeds a book face (Palatino Linotype on Windows), justifies with
  whole-paragraph line breaking, kerns, uses ligatures, keeps widows and orphans off page edges, and prints
  running heads with mirrored margins. Word export in Book or standard Manuscript layout. Every EPUB is checked
  by a structural checker before Quill says it is done.
- Local AI continuity questions (Pro) use whichever Ollama model is installed and never write story text.
- Signed updates on a preview or stable channel, only after you allow update checks.

Fixed
- Exported chapter headings no longer show the file number ("01 Chapter One").
- The day for the daily goal now starts at local midnight, not UTC.
- Turning Pro off on a computer now stays off until the key is pasted again.
- A clock that moved backwards no longer switches Pro off.
- Chapter word counts in the sidebar update as you type.
