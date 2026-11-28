# Stark Quill: help

Short answers to the questions writers ask most. If yours is not here, write to us: the address is on the
Stark Quill page on itch.io (or under About in the app, once it is set). Tell us your Quill version (About)
and what you were doing. Please never send us your book unless you want to; we do not need it to help.

## My writing

**Where is my book?** In `Documents\My_Books\<book title>`. Each chapter is a plain text file. You can open,
copy or back them up with anything.

**Quill closed (or the power went out) while I was typing.** Start Quill again. Each save goes to a journal first
and is checked before it replaces the chapter file, so Quill restores the last save it received and shows a note
at the top when it did. The browser also keeps a copy of what you typed since the last save and sends it again.

**I deleted a paragraph I want back.** Open Graveyard (left side). Every paragraph you delete is kept there, and
in `Deleted_Graveyard.md` in the book folder. Put the cursor where you want it and press *Put back at the cursor*.

**I want yesterday's version of a chapter.** Open Backups, pick a backup, press *Show chapters*, then *Preview*
and *Restore this version*. Quill backs up the current text first, so a restore can be undone too.

**I deleted a whole chapter.** Backups, under *Deleted chapters*, press *Restore*. It comes back at the end of the
book; drag it where it belongs.

**Can I edit the files in another program?** Yes, with Quill closed. Keep the number at the start of each file
name: that is the chapter order.

## Exports

**Which format do I need?** EPUB for e-book stores and readers, Word in *Manuscript* layout for agents and
editors (standard manuscript format: 12 pt, double spaced, US Letter), PDF in *Book* layout for print-on-demand
(6 x 9 in, 5.5 x 8.5 in or A5, fonts embedded).

**My chapter titles show numbers like "01".** They should not: Quill strips the number. If a title still looks
wrong, rename the chapter in Quill.

**An empty chapter is missing from the export.** Quill leaves out chapters with no text yet and says which ones.

## Pro and licences

**I bought PRO, 12 months on itch.io.** It is already in the download (`key.txt`). The 12 months start the first
time you open Quill on a computer; Licence (bottom left) shows the end date.

**What happens when Pro ends?** Quill goes back to the Free plan. Nothing is deleted or locked; you can still
read, edit and export every book as text, Markdown or a web page.

**A new computer.** Install Quill there. A PRO download activates itself; a key from an email: open Licence,
paste it, press Activate. To stop using Pro on the old computer, open Licence there and press *Turn Pro off on
this PC*.

**Licence says something about the clock.** Set your computer's date and time correctly. A clock that ran
backwards never removes Pro; it simply cannot add time.

## Phone

**Write from your phone (Pro).** Phone and PC must be on the same Wi-Fi. On the PC: More, then *Write from your
phone*. Scan the code with the phone's camera and type the PIN. The book stays on the PC; the phone edits it. If
both change the same lines while apart, Quill keeps both versions between `<<<<<<<` and `>>>>>>>` lines.

## Local AI notes (Pro)

**What does it do?** It asks a language model that runs on your own PC (through Ollama) for questions about
continuity in your lore list and open chapter. It never writes or changes your text. Without Ollama, the rest
of the Lore panel works as before.

**It says no model is running.** Install Ollama from ollama.com, then run `ollama pull llama3.2` once.

## Updates

Quill asks once whether it may check for updates. You can change the answer under About. Updates are signed;
an unsigned or altered update is never installed. If a new version fails to start, the next start puts the old
one back.

## Uninstall

Delete `%LOCALAPPDATA%\StarkQuill` and the desktop shortcut. Your books stay in `Documents\My_Books`.
