# Stark Quill changes

## 1.8.0 (2026-11-27)

The desk: five things for the week you send a book out, free on every plan, all worked out on this PC
- Before you send it (Ctrl+Alt+P, also in the export dialog). A checklist for the standard manuscript layout: no
  author name or contact details, TODO / TK / XXX left in the text, paragraphs indented with spaces, two spaces after
  a full stop, very short chapters. It shows the word count rounded as the first page prints it and the running
  header ("Surname / TITLE / page"). A hint list, not a verdict on the writing; print it.
- Contact block. Book details now take up to six lines (address, email, phone). The manuscript Word and PDF files print
  them at the top left of the first page; before, they printed a "[Your address, email and phone]" placeholder.
- Writing week and rest days. Today and goals shows Monday to Sunday with the words for each day, and what keeps
  today's run alive ("180 words keep your 3-day run going"; the goal ring gets a gold edge until you have written
  them). Mark up to five weekdays as rest days: a rest day you do not write on neither breaks nor adds to your run.
  Print the week for the fridge.
- Collections. Saved lists of chapters: hand-picked, or a rule that fills itself (status, keyword, a word in the title
  or note, under or over the chapter's word target). Open any chapter from its list. Kept in book.json.
- Rescue sheet. One printable page: where the book lives, how many words and chapters, how many automatic copies and
  the date of the latest, the finish line, and four plain steps to get the book back.
- Screenplays: scene numbers on request (Book details; numbers you type, like #12#, are kept and the count carries on
  from them) in the script PDF and the .fdx file, and dual dialogue: a second cue ending in ^ now sits in
  a column beside the speech above it in the PDF, and is written as DualDialogue in the .fdx file. The title page
  takes the contact block too.

Notes
- Nothing in a chapter file changes: contact, scene numbers and collections live in book.json, rest days in the
  books folder's stats file.
- A dual-dialogue pair is never split over two pages. The script preview on screen still shows it in one column.

## 1.7.0 (2026-11-26)

The writing bench: five tools for working on a whole book, free on every plan, all counted and kept on this PC
- Chapter targets. Give any chapter a word target. The corkboard card, the outline and the counter above the page show
  words against it, with a bar that fills and turns green when you get there. The target is kept in book.json.
- Keywords. Tag chapters with your own words (grief, harbour, a character, a thread). The corkboard and the outline can
  show one keyword at a time, and the outline can be copied as Markdown.
- Chapter outline. A table of the whole book beside the corkboard (Cards / Outline switch, Ctrl+Alt+O): chapter,
  status, words and target, keywords and note. Status and targets change in place. Print sets it as a clean page.
- Reference pane. Read any other chapter, with its note, beside the one you are writing (Ctrl+Alt+R). Read only, so
  nothing in it can be typed over by mistake; it remembers which chapter you had open, per book. On a phone it opens
  under the page.
- Milestones. Name a copy of the whole book (Ctrl+Alt+S) with a note, such as "First draft done". Unlike the automatic
  backups a milestone stays until you delete it. For any chapter you can compare the milestone with today's text
  word by word, or restore that chapter (a backup of the current book is made first).

Notes
- Nothing in a chapter file changes: targets and keywords live in book.json, milestones in the book's .backups folder.
- A keyword is up to 24 characters and a chapter holds up to 12; a book holds up to 100 milestones.

## 1.6.1 (2026-11-22)

Voice and rhythm (More menu, Ctrl+K), free on every plan
- Sentence rhythm: one bar for every sentence in a chapter, grey for short, coloured for long, with a tint over any run
  of six or more sentences of the same kind. Notes give the average, the longest sentence and how even the lengths are;
  a table lists every chapter.
- Character voices: for each character Lore found, the lines of dialogue that sit beside their name: words a line,
  how often they ask or shout, contractions, their own favourite words and a sample line. Pairs that measure alike
  are listed, so you can check two people do not sound the same.
- What is counted: a speech tag stays with its line ("Stop!" she said. is one sentence), a sentence is not cut at
  Mr., Dr., an initial or a number like 3.5, and comments ({>>a note<<}) are left out. Speech is read from double
  quotation marks, straight or curly; dialogue in single quotation marks is not read yet.
- All of it is counted on this PC. A line is matched to a character only when exactly one name stands beside the
  quotation marks; the rest are counted as unmatched. Hints, not verdicts; nothing in a chapter is changed.

Safety
- Phone-pairing certificates made with Windows PowerShell now carry the same name limits as the OpenSSL ones (this
  PC's names and home-network addresses only), and a VPN or public address is left out of them so phones still
  accept them. A certificate already installed on a phone is not changed and keeps working as before, with every
  address it carried. One made from 1.6.1 on names this PC, so renaming the PC makes a new one for phones to install.
- The EPUB and Word file checkers open files with the same size limits as import, so a file that unpacks to
  gigabytes is reported instead of being unpacked.

Fixed
- Story X-ray and Plot outline no longer print the word "null" where a list is empty (a mood arc with no notes, a
  character web with no pairs, an outline with every chapter placed).

## 1.6.0 (2026-11-21)

Story X-ray (More menu, Ctrl+K), free on every plan
- Mood arc: how warm or dark the writing reads across the book and how tense the pace is, from a small word list
  (with "not" and "never" turning a word round), sentence length and punctuation; plus the share of dialogue.
  Notes point out a run of chapters with the same low tension. A table gives every figure, for screen readers.
- Echo finder: a distinctive word used again within a few lines of itself, chapter by chapter.
- Character web: which characters Lore found share paragraphs, how often, where each pair first meets, and strong
  characters who never share one. A table lists the same pairs.
- Find that forgives typos, missing accents and word order, and shows the sentence it found.
- All of it is worked out on this PC from word lists and counts. These are hints, not verdicts, and nothing in a
  chapter is changed.

Print cover size (More menu, Ctrl+K), free
- Page count, spine width (white, cream or colour paper) and the full wrap-around cover size with 0.125 in bleed,
  in inches, millimetres and 300 dpi pixels, with a to-scale SVG layout guide to download.
- A typed page count outside 24 to 828, or an odd one, is rounded into range and the note says so.

Safety and speed
- Word, OpenDocument, hand-in and contest zips are read with per-part (20 MB) and total (200 MB) limits, so a
  small file that unpacks to gigabytes is refused with a clear message.
- The page runs only its own script (a fresh nonce per page load); the phone page escapes apostrophes too.
- New phone-pairing certificates made with OpenSSL may only vouch for this PC's own names and home-network
  addresses (name constraints); a VPN or public address is left out of the certificate so phones still accept it.
- The update check treats only 127.0.0.1, localhost and ::1 as this PC for plain http.
- Saving skips re-reading the whole safety journal when nothing else is pending, and the journal stores
  non-Latin text as UTF-8, about a third of the size.

## 1.5.2 (2026-11-21)

Easier first session
- The first screen offers the sample book as a real button, Try the sample book, next to Start writing. One click
  opens it with three short chapters to change freely. A bar in the sample says so and offers Start my own book.
  The book menu can open the sample again later.
- Export or print sits at the top of the page at every width, as well as in the chapter list and the More menu.
  The export window picks a format your plan can export, names it on its button (Export Web page) and says what
  happens next. A Pro format on the Free plan says which formats are free.
- Print, free: the whole book or this chapter, set as pages with a title page and each chapter on a new page,
  black on white whatever the theme, comments left out. Ctrl+P opens it. No file is written.
- The save light says Saved, Saving... or Not saved yet in words on phones too, not just a dot.
- When Quill is not answering, the page says so in plain words, keeps the text in the browser and saves it as
  soon as Quill is back. Other messages no longer show bare error codes or programmer words.
- Phones and touch screens: every button, menu item and field is at least 44 px tall. Chapter menus show without
  hovering. The page has a top heading for screen readers, the welcome screen keeps keyboard focus on itself,
  the editor shows a focus line, and the editor's hint text meets AA contrast.

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
  Courier 12 with MORE and CONT'D, scene numbers and a title page, and script-format .fdx (both Pro).
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
