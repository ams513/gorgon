"""Stark Quill editor page (one self-contained HTML document: CSS + JS inline, no network requests).

writer_app.render_page() serves PAGE with the per-launch token filled in. Everything a writer sees lives here,
so the wording can be read and checked in one place (tests/test_quill_ui.py lints it for banned words).
"""
from __future__ import annotations

import json

try:  # Quill's product mark and lockups (brand/BRAND_ARCHITECTURE.md); the "A Stark product" endorsement is text
    from quill_brand import ENDORSE_DARK, ENDORSE_LIGHT, QUILL_FAVICON, QUILL_LOCKUP_DARK, QUILL_LOCKUP_LIGHT, QUILL_MARK
except Exception:  # the marks are decoration: the editor works without them
    ENDORSE_DARK = ENDORSE_LIGHT = QUILL_FAVICON = QUILL_LOCKUP_DARK = QUILL_LOCKUP_LIGHT = QUILL_MARK = ""

# An original, very short sample book. One continuity slip is planted on purpose so the Lore panel has
# something to find in the first minutes (Tamsin dies in chapter 2 and speaks in chapter 3).
SAMPLE_BOOK = {
    "folder": "Sample - The Lamp at Gullstone",
    "title": "The Lamp at Gullstone",
    "author": "A Stark Quill sample",
    "chapters": [
        ("The Keeper's Daughter",
         "Wren Halloway climbed the tower stairs at dusk on 14 October 1889, counting the steps out of habit. "
         "There were one hundred and twelve. Her father had counted them for thirty years and never once lost "
         "his place.\n\n"
         "Captain Elias Morrow was already at the top, his coat dark with spray. \"The glass is falling,\" he "
         "said. \"You'll want the shutters before midnight.\"\n\n"
         "\"I'll want my supper first,\" Wren replied. She was nineteen that autumn and had stopped pretending "
         "to be afraid of him.\n\n"
         "Below them the village of Gullstone lit its lamps one window at a time. Out past the harbour wall the "
         "sea was the colour of a slate roof, and getting darker.\n"),
        ("Low Water",
         "Old Tamsin Vey had kept the tide tables in her head since before the lighthouse was built, and she "
         "was the first to see the wreck.\n\n"
         "\"There,\" she said, pointing with her pipe. \"Past the Black Teeth. A schooner, and she's lost her "
         "mainmast.\"\n\n"
         "Wren took the boat out with the Morrow boys while the Captain shouted instructions nobody could hear. "
         "They brought back four men, a dog and a crate of oranges that nobody could explain.\n\n"
         "***\n\n"
         "Tamsin died in the night, quietly, in the chair by her own fire. The village said it was the cold. "
         "Wren thought it more likely that Tamsin had simply decided she had seen enough.\n"),
        ("The Oranges",
         "On 2 November 1889 the schooner's master was well enough to walk to the inn at Kettle Bay, and he "
         "would not say where the oranges had come from.\n\n"
         "\"Bad luck to ask,\" Tamsin said, taking one from the crate and peeling it with her thumbnail.\n\n"
         "Wren watched the stranger's hands. They were not a sailor's hands. Somebody, she decided, was going "
         "to find out what he carried besides fruit, and it was not going to be the Captain.\n"),
    ],
}

# The one-page getting-started guide (Q73). It opens from the first screen ("New here? Read the one-page guide"),
# the tips bar, the More menu and About. Each line says only what this build does; tests lint it like the page.
GUIDE_TITLE = "Getting started with Stark Quill"
GUIDE = [
    ("Your book is plain files",
     "Each book is a folder in your books folder ({folder}). Each chapter is a text file, numbered in reading "
     "order, so any text editor can open it, today or in twenty years."),
    ("Write, and Quill saves",
     "Type on the page. Quill saves after a short pause in typing, and the light at the top says Saved. Ctrl+S "
     "saves at once. Each save goes to a journal first and is checked before it replaces the file. If a save "
     "cannot finish, for example because a sync app has the file locked, the light says so, your text stays in "
     "this browser, and Quill tries again by itself."),
    ("Chapters",
     "Add one with + New chapter in the sidebar. The \u22ef button beside a chapter renames it, moves it up or "
     "down, or deletes it; you can also drag chapters into a new order. Deleted chapters wait in Backups."),
    ("Italics, bold and scene breaks",
     "Wrap words in *asterisks* for italics and **two** for bold (Ctrl+I and Ctrl+B). Put *** on a line of its "
     "own for a scene break."),
    ("Nothing is thrown away",
     "Backups keeps copies of the whole book as you work, at most one every two minutes; preview one and put "
     "any chapter back. The Graveyard keeps paragraphs you delete, so you can restore them. Zip / USB backup "
     "saves the whole book as one checked zip file, to download or to put on a USB stick."),
    ("Find your way around",
     "Search (Ctrl+Shift+F) looks through the whole book and can replace, after making a backup. The Corkboard "
     "shows one card per chapter with a note and a status. Lore lists the characters, places and dates it finds "
     "and points out continuity slips. Focus (Ctrl+Shift+Enter) hides everything but the page."),
    ("Targets, reading aloud and paste",
     "Give the book a word target and a finish date in Book details, and Quill works out how many words a day it "
     "takes; a missed day raises the figure. Read aloud, in the More menu, reads from the cursor with the voices "
     "on this computer and highlights each sentence. Pasting from other word processors keeps italics and bold."),
    ("Spelling",
     "Quill underlines spelling slips with the spell checker built into Windows. Names that Lore finds go into "
     "the book's own dictionary, so they are not underlined. Turn it off in Page and type."),
    ("Screenplays, essays and notes",
     "New book asks what you are writing. A screenplay gets script pages, a scene list, and Tab to set each "
     "line's element. An essay or thesis gets Sources for MLA, APA or Chicago citations. Import a manuscript "
     "from the book menu. Ctrl+Alt+M adds a comment that never prints; Ctrl+K jumps anywhere."),
    ("Export and print",
     "Export or print, at the top, writes plain text, Markdown or a web page for free; EPUB, Word and PDF are part of "
     "Pro. A screenplay exports Fountain for free, and script PDF and .fdx with Pro. Files go into the "
     "book's exports folder. If an export with the same name is already there, the older one is kept as name (1), "
     "name (2) and so on. Print (Ctrl+P) is free."),
    ("Your writing stays here",
     "Nothing you write leaves this computer unless you send it somewhere yourself. Quill goes online only to "
     "look for updates if you allow it, and for the Library panel if you turn it on. Dictation in Chrome sends "
     "audio to Google."),
    ("More help",
     "About lists the keyboard shortcuts and the version. SUPPORT.md, in the folder where Quill is installed, "
     "says how to get help."),
]


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>Stark Quill</title>
<link rel="icon" href="__FAVICON__">
<style>
:root{
 --bg:#f6f4ee;--surface:#ffffff;--ink:#18211d;--muted:#55615a;--line:#dcd8cc;--line-strong:#77837c;
 --accent:#7f5c1c;--accent-hover:#664a16;--on-accent:#ffffff;--gold:#c9963b;--gold-text:#7f5c1c;--error:#b3261e;
 --focus:#7f5c1c;--mark:#1f7a5a;--ok:#1f7a5a;--warn-bg:#fbf1dc;--hl:rgba(201,150,59,.32);--shadow:0 10px 30px rgba(24,33,29,.12);
 --ui:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
 --display:"Source Serif 4","Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
 --mono:"JetBrains Mono",ui-monospace,"Cascadia Mono",Consolas,"Courier New",monospace;
 --font:var(--display);--size:19px;--lh:1.7;--measure:35em;--r:8px;
}
:root[data-theme=sepia]{--bg:#f1e8d7;--surface:#f8f2e6;--ink:#3a2e1f;--muted:#6b5a43;--line:#ddd0b8;--line-strong:#8a7a62;
 --accent:#7a5518;--accent-hover:#634513;--on-accent:#ffffff;--gold:#b8862f;--gold-text:#7a5518;--error:#a3241c;--focus:#7a5518;
 --mark:#1d6b4f;--ok:#1d6b4f;--warn-bg:#f5e3c0;--hl:rgba(184,134,47,.32);--shadow:0 10px 30px rgba(58,46,31,.14)}
:root[data-theme=dark]{--bg:#0e1412;--surface:#16201c;--ink:#e7efe9;--muted:#9fb0a6;--line:#26332d;--line-strong:#5d6e66;
 --accent:#d9a441;--accent-hover:#e2ad48;--on-accent:#0e1412;--gold:#d9a441;--gold-text:#d9a441;--error:#f2b8b5;--focus:#e2ad48;
 --mark:#3dbb8c;--ok:#3dbb8c;--warn-bg:#2b2416;--hl:rgba(217,164,65,.35);--shadow:0 10px 30px rgba(0,0,0,.45)}
@media (prefers-color-scheme:dark){:root:not([data-theme]){--bg:#0e1412;--surface:#16201c;--ink:#e7efe9;--muted:#9fb0a6;
 --line:#26332d;--line-strong:#5d6e66;--accent:#d9a441;--accent-hover:#e2ad48;--on-accent:#0e1412;--gold:#d9a441;
 --gold-text:#d9a441;--error:#f2b8b5;--focus:#e2ad48;--mark:#3dbb8c;--ok:#3dbb8c;--warn-bg:#2b2416;--hl:rgba(217,164,65,.35);
 --shadow:0 10px 30px rgba(0,0,0,.45)}}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);font:15px/1.5 var(--ui);-webkit-font-smoothing:antialiased;overflow:hidden}
button,input,select,textarea{font:inherit;color:inherit}
button{cursor:pointer;background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:6px 11px;line-height:1.3}
button:hover{border-color:var(--line-strong)}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--on-accent);font-weight:600}
button.primary:hover{background:var(--accent-hover);border-color:var(--accent-hover)}
button.ghost{background:transparent;border-color:transparent}
button.ghost:hover{background:var(--surface);border-color:var(--line)}
button.danger{color:var(--error)}
button:disabled{opacity:.55;cursor:default}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
input[type=text],input[type=number],input[type=search],select,textarea.field{background:var(--surface);border:1px solid var(--line-strong);
 border-radius:var(--r);padding:7px 9px;width:100%}
label{display:block;font-size:13px;color:var(--muted);margin:10px 0 4px}
label.check{display:flex;gap:8px;align-items:center;color:var(--ink);font-size:14px}
label.check input{margin:0}
.muted{color:var(--muted)}.small{font-size:13px}.tiny{font-size:12px}
.pmark{width:30px;height:30px;border-radius:7px;flex:none;display:block}
.lockup{height:54px;width:auto;display:block}.endorse{height:16px;width:auto}
.on-dark{display:none!important}
:root[data-theme=dark] .on-dark{display:block!important}:root[data-theme=dark] .on-light{display:none!important}
@media (prefers-color-scheme:dark){:root:not([data-theme]) .on-dark{display:block!important}:root:not([data-theme]) .on-light{display:none!important}}
.pill{display:inline-block;font-size:11px;letter-spacing:.04em;text-transform:uppercase;border:1px solid var(--line-strong);
 border-radius:99px;padding:1px 8px;color:var(--muted)}
.pro{color:var(--gold-text);border-color:var(--gold)}
#app{display:flex;height:100%}
/* ---- sidebar */
aside{width:262px;flex:none;background:var(--surface);border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
.brand{display:flex;align-items:center;gap:9px;padding:14px 16px 6px}
.brand b{font:600 17px var(--display);letter-spacing:.01em}
.brand span{font-size:11px;color:var(--muted)}
#bookbtn{margin:6px 12px 8px;text-align:left;display:flex;justify-content:space-between;align-items:center;gap:6px;background:var(--bg)}
#bookbtn b{font:600 15px var(--display);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#chapters{flex:1;overflow:auto;padding:2px 8px;margin:0;list-style:none;min-height:60px}
#chapters li{display:flex;align-items:center;border-radius:6px;position:relative}
#chapters li button.ch{flex:1;min-width:0;display:flex;gap:8px;align-items:baseline;background:none;border:0;padding:7px 8px;
 text-align:left;border-radius:6px}
#chapters li button.ch:hover{background:var(--bg)}
#chapters li.on button.ch{background:var(--bg);box-shadow:inset 3px 0 var(--accent)}
#chapters .n{color:var(--muted);font-size:12px;min-width:18px;text-align:right;font-variant-numeric:tabular-nums}
#chapters .t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#chapters .w{color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
#chapters li .dots{visibility:hidden;padding:2px 7px;border:0;background:none;color:var(--muted)}
#chapters li:hover .dots,#chapters li.on .dots,#chapters li .dots:focus{visibility:visible}
#chapters li.drop-before{box-shadow:0 -2px 0 var(--accent)}
#chapters li.dragging{opacity:.45}
.sidebtns{padding:6px 12px 8px;display:flex;flex-direction:column;gap:4px;border-top:1px solid var(--line)}
.sidebtns button{text-align:left;background:none;border-color:transparent}
.sidebtns button:hover{background:var(--bg)}
#newch{margin:8px 12px;border-style:dashed;color:var(--muted)}
.sidefoot{padding:10px 14px;border-top:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:6px}
/* ---- main */
main{flex:1;min-width:0;display:flex;flex-direction:column;position:relative}
header#top{display:flex;align-items:center;gap:8px;padding:8px 14px;border-bottom:1px solid var(--line);min-height:50px;height:50px;contain:strict;overflow:hidden}
#writeview{contain:strict}aside{contain:layout paint}#save,#words{contain:layout paint}
#menu{display:none}
#where{min-width:0;display:flex;flex-direction:column}
#where h1{font:600 16px var(--display);margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#where span{font-size:12px;color:var(--muted)}
.sp{flex:1}
.hbtn{background:none;border-color:transparent;color:var(--ink)}
.hbtn:hover,.hbtn.on{background:var(--surface);border-color:var(--line)}
#save{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);min-width:94px;white-space:nowrap}
#save i{width:8px;height:8px;border-radius:50%;background:var(--ok);display:inline-block;flex:none;transition:background .3s}
#save.dirty i{background:transparent;box-shadow:inset 0 0 0 1.5px var(--line-strong)}
#save.saving i{background:var(--gold)}
#save.off i{background:var(--gold)}#save.err i{background:var(--error)}#save.err{color:var(--error)}
#exportbtn{border-color:var(--line-strong);white-space:nowrap}#exportbtn:hover{border-color:var(--accent)}
#ed:focus-visible{box-shadow:inset 0 2px 0 var(--focus)}body.focus #ed:focus-visible{box-shadow:none}
#words{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
#goalbtn{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);background:none;border-color:transparent;padding:4px 8px}
#goalbtn:hover{border-color:var(--line);background:var(--surface)}
#goalbtn svg{width:22px;height:22px;transform:rotate(-90deg)}
#goalbtn circle{fill:none;stroke-width:3.2}
#goalbtn .trk{stroke:var(--line)}#goalbtn .val{stroke:var(--accent);stroke-linecap:round;transition:stroke-dashoffset .5s}
#assignbar{padding:8px 16px;background:var(--surface);border-bottom:1px solid var(--line);font-size:14px;display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap}
#assignbar details{flex:1 1 20em}#assignbar summary{cursor:pointer;font-weight:600}#assignbar p{margin:6px 0 2px;white-space:pre-wrap;max-width:60em}
#assignbar[hidden]{display:none}body.classroom{zoom:1.25}
#banner{padding:9px 16px;background:var(--warn-bg);border-bottom:1px solid var(--line);font-size:14px;display:flex;gap:10px;align-items:center}
#banner[hidden]{display:none}
#views{flex:1;min-height:0;display:flex;position:relative}
#writeview{flex:1;min-width:0;display:flex;flex-direction:column}
#writeview[hidden]{display:none}
#writeview{position:relative}#ed{position:relative;z-index:1}
#spell{position:absolute;left:0;top:0;z-index:0;overflow:hidden;pointer-events:none;color:transparent;white-space:pre-wrap;
 overflow-wrap:break-word;word-wrap:break-word;box-sizing:border-box;visibility:hidden}
#spell.on{visibility:visible}#spell mark.say{background:var(--hl);text-decoration:none;border-radius:3px}
#saybar{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);display:flex;gap:8px;align-items:center;font-size:13px;z-index:5;
 background:var(--surface);border:1px solid var(--line);border-radius:99px;padding:5px 8px 5px 14px;box-shadow:var(--shadow);max-width:calc(100% - 24px);flex-wrap:wrap}
#saybar[hidden]{display:none}#saybar select{font-size:13px;padding:2px 4px;max-width:14em}#spell mark{background:none;color:transparent;text-decoration:underline wavy var(--error);
 text-decoration-thickness:1px;text-underline-offset:3px;text-decoration-skip-ink:none}
#ed{flex:1;width:100%;border:0;outline:0;resize:none;background:transparent;color:var(--ink);font-family:var(--font);
 font-size:var(--size);line-height:var(--lh);padding:56px max(28px,calc((100% - var(--measure)) / 2)) 45vh;
 font-kerning:normal;text-rendering:optimizeLegibility;caret-color:var(--accent);tab-size:4}
#ed::placeholder{color:var(--muted);opacity:1;font-style:italic}
#ed::selection{background:var(--hl)}
/* ---- corkboard */
#board{flex:1;overflow:auto;padding:24px clamp(16px,4vw,48px) 80px}
#board[hidden]{display:none}
.boardhead{display:flex;align-items:baseline;gap:12px;margin:0 0 16px;flex-wrap:wrap}
.boardhead h2{font:600 22px var(--display);margin:0}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 12px 10px;display:flex;flex-direction:column;
 gap:8px;min-height:190px;box-shadow:0 1px 0 var(--line)}
.card.dragging{opacity:.45}.card.drop-before{box-shadow:-3px 0 0 var(--accent)}
.card .top{display:flex;justify-content:space-between;align-items:center;gap:6px;font-size:12px;color:var(--muted)}
.card .title{font:600 16px var(--display);text-align:left;background:none;border:0;padding:0;color:var(--ink)}
.card .title:hover{color:var(--accent)}
.card textarea{flex:1;min-height:76px;resize:vertical;border:1px dashed var(--line);border-radius:6px;background:transparent;padding:6px 7px;
 font:14px/1.45 var(--ui);color:var(--ink)}
.card textarea:focus{border-style:solid;border-color:var(--line-strong)}
.card .open{font-size:12.5px;color:var(--muted);font-style:italic;max-height:3.9em;overflow:hidden}
.card .foot{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--muted)}
.card select{width:auto;padding:2px 6px;font-size:12px}
.card .mv button{padding:1px 7px;font-size:12px}
.card .kw{width:100%;font-size:12px;padding:3px 6px}.card .tgt{width:5.5em;font-size:12px;padding:2px 5px}
.pbar{height:5px;border-radius:3px;background:var(--line);overflow:hidden;min-width:60px}.pbar i{display:block;height:100%;background:var(--accent)}
.pbar.done i{background:var(--ok)}
.viewtog{display:inline-flex;gap:0}.viewtog button{border-radius:0;padding:3px 10px;font-size:13px}.viewtog button:first-child{border-radius:6px 0 0 6px}
.viewtog button:last-child{border-radius:0 6px 6px 0;margin-left:-1px}.viewtog button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.otable{width:100%;border-collapse:collapse;font-size:14px}.otable th{text-align:left;font:600 11px var(--ui);letter-spacing:.08em;text-transform:uppercase;
 color:var(--muted);border-bottom:1px solid var(--line-strong);padding:6px 8px}
.otable td{border-bottom:1px solid var(--line);padding:8px;vertical-align:top}.otable td.num{color:var(--muted);width:2.5em}
.otable td.syn{color:var(--muted);max-width:34em}.otable .title{background:none;border:0;padding:0;font:600 15px var(--display);color:var(--ink);text-align:left}
.otable .title:hover{color:var(--accent)}.otable select,.otable input.tgt{font-size:12px;padding:2px 5px;width:auto}
.chip{display:inline-block;border:1px solid var(--line-strong);border-radius:99px;padding:0 8px;font-size:12px;margin:0 4px 3px 0;color:var(--ink)}
.empty{border:1px dashed var(--line-strong);border-radius:10px;padding:22px;max-width:36em;color:var(--muted)}.empty b{color:var(--ink)}
#refpane{flex:0 0 min(42%,520px);min-width:0;display:flex;flex-direction:column;border-left:1px solid var(--line);background:var(--surface)}
#refpane[hidden]{display:none}#refpane header{display:flex;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line)}
#refpane header select{flex:1;min-width:0}#refbody{flex:1;overflow:auto;padding:16px 20px;white-space:pre-wrap;font:calc(var(--size) - 3px)/1.6 var(--ui);color:var(--ink)}
#refbody .rnote{font:13px var(--ui);color:var(--muted);border-bottom:1px solid var(--line);margin:0 0 12px;padding-bottom:10px;white-space:normal}
.msrow{display:flex;gap:8px;align-items:center;justify-content:space-between;margin-top:6px;font-size:13px}
@media (max-width:760px){#views{flex-direction:column}#refpane{flex:0 0 42%;max-height:42%;min-height:0;border-left:0;border-top:1px solid var(--line)}#writeview{min-height:0}.otable .syn,.otable th.syn{display:none}.otable td,.otable th{padding:6px 4px}.otable input.tgt{width:4.6em}}
@media print{body{overflow:visible;background:#fff;color:#000}html,body,#app,main,#views{height:auto!important;display:block!important;position:static!important}
 aside,header#top,#panel,#refpane,.tips,#toast,#saybar,#focusbar,.boardhead button,.boardhead select,.boardhead .viewtog,#writeview{display:none!important}
 #board{overflow:visible;padding:0}#board[hidden]{display:block!important}.card,.otable tr{break-inside:avoid}.otable select,.otable input{border:0;appearance:none;background:none}
 .pbar{border:1px solid #888}.pbar i{background:#444!important}}
/* ---- desk tools: writing week, submission check, collections, rescue sheet */
.weekcard{margin:10px 0}.weekline{font-weight:600;margin:6px 0}.week{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}
.wd{border:1px solid var(--line);border-radius:8px;padding:6px 2px;text-align:center;font-size:12px;min-height:44px}.wd b{display:block;font-size:11px;color:var(--muted)}
.wd.met{background:var(--accent);color:var(--on-accent)}.wd.met b{color:inherit}.wd.rest{border-style:dashed}.wd.fut{opacity:.55}.wd.today{outline:2px solid var(--gold)}
.restrow{display:flex;flex-wrap:wrap;gap:0 12px;align-items:center;margin:6px 0}.restrow label{display:inline-flex;gap:4px;align-items:center;min-height:44px;font-size:13px}
#goalbtn.risk{border-color:var(--gold)}
.pflist{list-style:none;padding:0;margin:8px 0}.pflist li{border-left:3px solid var(--line);padding:6px 10px;margin:8px 0}.pf-stop{border-left-color:#a33!important}.pf-fix{border-left-color:var(--gold)!important}
.rescue dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px}.rescue dt{font-weight:600}.rescue dd{margin:0;word-break:break-word}
.rescue table{width:100%;border-collapse:collapse;margin:6px 0}.rescue td,.rescue th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left}
.cpick{max-height:220px;overflow:auto;margin:8px 0}.cpick label{display:flex;min-height:44px;align-items:center;gap:8px}
#printsheet{display:none}
@media print{body.printing>*:not(#printsheet){display:none!important}body.printing{background:#fff!important}
 body.printing #printsheet{display:block!important;padding:20px;color:#000;background:#fff}#printsheet .noprint{display:none!important}
 #printsheet .wd.met{background:#ddd;color:#000}}
/* ---- side panel */
#panel{width:380px;flex:none;border-left:1px solid var(--line);background:var(--surface);display:flex;flex-direction:column;min-height:0}
#panel[hidden]{display:none}
#panel>header{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--line)}
#panel>header h2{font:600 17px var(--display);margin:0;flex:1}
#pbody{flex:1;overflow:auto;padding:12px 14px 40px}
.section{margin:4px 0 18px}
.section h3{font:600 12px var(--ui);letter-spacing:.07em;text-transform:uppercase;color:var(--muted);margin:16px 0 8px}
.hit{display:block;width:100%;text-align:left;background:none;border:0;border-radius:6px;padding:6px 8px;font-size:14px;line-height:1.45}
.hit:hover{background:var(--bg)}
.hit mark{background:var(--hl);color:inherit;border-radius:2px;padding:0 1px}
.grp{font:600 14px var(--display);margin:12px 0 2px;display:flex;justify-content:space-between}
.tag{display:inline-block;margin:3px 4px 3px 0;padding:3px 10px;border-radius:99px;border:1px solid var(--line);background:var(--bg);font-size:13px}
button.tag:hover{border-color:var(--accent)}
.warnc{border-left:3px solid var(--gold);background:var(--bg);padding:8px 10px;border-radius:6px;margin:8px 0;font-size:14px}
.warnc em{display:block;color:var(--muted);font-size:13px;margin-top:4px}
.entry{border:1px solid var(--line);border-radius:8px;padding:9px 10px;margin:0 0 10px;background:var(--bg)}
.entry .meta{font-size:12px;color:var(--muted);display:flex;justify-content:space-between;gap:8px}
.entry .txt{font-family:var(--font);font-size:15px;line-height:1.5;margin:6px 0;white-space:pre-wrap;max-height:9em;overflow:hidden}
.entry.open .txt{max-height:none}
.entry .acts{display:flex;gap:6px;flex-wrap:wrap}.entry .acts button{font-size:13px;padding:4px 9px}
.ai{border:1px solid var(--line);border-radius:8px;padding:10px;background:var(--bg);font-size:14px;white-space:pre-wrap}
.row{display:flex;gap:8px;align-items:center}.row>*{min-width:0}
.grow{flex:1}
/* ---- craft tools */
input[type=date]{background:var(--surface);border:1px solid var(--line-strong);border-radius:var(--r);padding:6px 9px}
.heat{display:grid;grid-template-rows:repeat(7,11px);grid-auto-flow:column;grid-auto-columns:11px;gap:3px;overflow-x:auto;padding:6px 0 8px}
.heat i,.heatkey i{display:inline-block;width:11px;height:11px;border-radius:2px;background:var(--line)}
.heat i.l1,.heatkey i.l1{background:var(--accent);opacity:.3}.heat i.l2,.heatkey i.l2{background:var(--accent);opacity:.55}
.heat i.l3,.heatkey i.l3{background:var(--accent);opacity:.8}.heat i.l4,.heatkey i.l4{background:var(--accent)}
.heatkey{display:flex;gap:3px;align-items:center;justify-content:flex-end;margin-bottom:6px}.heatkey i{vertical-align:middle}
.tplbtn{display:flex;justify-content:space-between;align-items:center;width:100%;margin:6px 0;padding:10px 12px;text-align:left}
.beatrow,.subrow{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:8px 0;display:flex;flex-direction:column;gap:5px}
.subrow.due{border-color:var(--accent)}
.pace{position:relative;height:8px;background:var(--line);border-radius:4px}.pace i{display:block;height:100%;background:var(--accent);border-radius:4px}
.pace u{position:absolute;top:-3px;width:2px;height:14px;background:var(--ink);text-decoration:none}
.tag{border:1px solid var(--line);border-radius:10px;padding:0 7px;white-space:nowrap}.tag.short,.tag.long{border-color:var(--accent)}
.card .beat{font-size:12px;color:var(--gold-text);font-weight:600}
.revcard{border-top:1px solid var(--line);padding:6px 0}
.revtext{white-space:pre-wrap;font:14px/1.5 var(--font);max-height:260px;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;margin-top:8px}
mark{color:inherit;border-radius:3px;padding:0 1px}mark.rv-overused{background:rgba(217,164,65,.45)}mark.rv-phrase{background:rgba(110,150,200,.4)}
mark.rv-adverb{background:rgba(190,120,160,.35)}mark.rv-filler{background:rgba(140,140,140,.35)}mark.rv-tag{background:rgba(220,90,70,.35)}
mark.rv-run,mark.rv-long{background:rgba(100,180,120,.35)}.revcard+div mark{margin-right:6px}
/* ---- dialogs, menus, toast */
dialog{border:1px solid var(--line);border-radius:14px;background:var(--surface);color:var(--ink);padding:0;width:min(560px,94vw);
 box-shadow:var(--shadow);max-height:90vh}
dialog::backdrop{background:rgba(14,20,18,.42)}
.dlg{display:flex;flex-direction:column;max-height:90vh}
.dlg>h2{font:600 20px var(--display);margin:0;padding:18px 20px 4px}
.dlg .dbody{padding:4px 20px 8px;overflow:auto}
.dlg .dacts{display:flex;justify-content:flex-end;gap:8px;padding:12px 20px 18px;flex-wrap:wrap}
.dlg p{margin:8px 0}
.choices{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:6px 0}
.choice{display:flex;flex-direction:column;align-items:flex-start;gap:2px;text-align:left;padding:10px 11px;border-radius:10px}
.choice b{font-size:14.5px}.choice span{font-size:12.5px;color:var(--muted)}
.choice.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.menu{position:fixed;z-index:50;background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);padding:5px;
 min-width:210px;max-height:80vh;overflow:auto}
.menu[hidden]{display:none}
.menu button{display:flex;justify-content:space-between;gap:16px;width:100%;text-align:left;border:0;background:none;padding:7px 10px;border-radius:6px}
.menu button:hover,.menu button:focus{background:var(--bg)}
.menu hr{border:0;border-top:1px solid var(--line);margin:4px 2px}
.menu kbd{font:12px var(--mono);color:var(--muted)}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%) translateY(20px);background:var(--ink);color:var(--bg);
 padding:9px 16px;border-radius:10px;font-size:14px;opacity:0;pointer-events:none;transition:.25s;z-index:60;max-width:92vw}
#toast.show{opacity:1;transform:translateX(-50%)}
.bars{display:flex;gap:3px;align-items:flex-end;height:46px;margin:10px 0 4px}
.bars i{flex:1;background:var(--line);border-radius:2px 2px 0 0;min-height:3px}
.bars i.met{background:var(--accent)}
.big{font:600 30px var(--display);margin:0}
kbd{font:12px var(--mono);border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:0 5px;background:var(--bg)}
table.keys{border-collapse:collapse;width:100%;font-size:14px}table.keys td{padding:4px 0;border-bottom:1px solid var(--line)}
table.keys td:last-child{text-align:right}
pre.preview{white-space:pre-wrap;font-family:var(--font);font-size:15px;line-height:1.55;background:var(--bg);border:1px solid var(--line);
 border-radius:8px;padding:12px;max-height:52vh;overflow:auto}
/* ---- welcome + pairing */
#welcome{position:fixed;inset:0;z-index:40;background:var(--bg);display:flex;align-items:center;justify-content:center;padding:24px;overflow:auto}
#welcome[hidden],#pairview[hidden]{display:none}
.wcard{width:min(520px,100%)}
.wcard .lockup{height:62px}
.wcard h1{font:600 clamp(28px,5vw,38px)/1.15 var(--display);margin:18px 0 10px}
.wcard p{font-size:16px;line-height:1.6;color:var(--muted);margin:0 0 16px}
.wcard .lede{color:var(--ink)}
.wcard input{font-size:16px;padding:10px 12px}
.wcard .go{margin-top:18px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.wcard .go button.primary{padding:12px 20px;font-size:17px;flex:1 1 13em;min-height:50px}
.wcard .go button,.wcard p button{min-height:44px}.wcard select,.wcard input{min-height:44px}
.wcard .go button.second{border-color:var(--line-strong);padding:10px 16px;font-size:16px;flex:1 1 11em}
.wcard footer{margin-top:28px;font-size:13px;color:var(--muted);display:flex;gap:8px;align-items:center}
#pairview{position:fixed;inset:0;z-index:45;background:var(--bg);display:flex;align-items:center;justify-content:center;padding:24px}
#pin{font:600 30px var(--mono);letter-spacing:.25em;text-align:center}
.qr{background:#fff;padding:8px;border-radius:8px;display:inline-block}.qr svg{width:220px;height:220px;display:block}
.tips{display:flex;gap:12px;align-items:flex-start;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--surface);font-size:14px}
.tips[hidden]{display:none}
.guide h3{font:600 15px/1.3 var(--ui);margin:14px 0 4px}.guide h3:first-child{margin-top:0}.guide p{margin:0;font-size:14px;line-height:1.55}
/* ---- focus mode */
#focusbar{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);display:none;gap:14px;align-items:center;font-size:13px;
 color:var(--muted);background:var(--surface);border:1px solid var(--line);border-radius:99px;padding:5px 8px 5px 14px;transition:opacity .6s}
body.focus #focusbar{display:flex}
body.focus.calm #focusbar{opacity:0}
body.focus aside,body.focus header#top,body.focus #panel,body.focus #banner,body.focus .tips{display:none!important}
body.focus #ed{padding-top:18vh}
body.focus.calm #ed{cursor:none}
/* ---- phones and small windows */
@media (max-width:860px){#panel{position:fixed;inset:0;width:auto;z-index:30;border-left:0}}
@media (max-width:760px){
 aside{position:fixed;inset:0 auto 0 0;z-index:35;width:min(300px,86vw);transform:translateX(-102%);transition:transform .2s;box-shadow:var(--shadow)}
 body.side aside{transform:none}
 #menu{display:inline-flex}
 header#top{gap:4px;padding:6px 8px}
 .wide{display:none!important}
 header#top{height:56px;min-height:56px}
 #save{min-width:0;max-width:8.5em;flex:none}#save span{overflow:hidden;text-overflow:ellipsis}
 #ed{padding:26px 18px 45vh;font-size:calc(var(--size) - 1px)}
 dialog{width:96vw}
 .cards{grid-template-columns:1fr}
}
@media (max-width:1100px){.mid{display:none!important}}
/* touch layouts: every target at least 44 x 44 px (Stark UX standard 3) */
@media (max-width:760px),(pointer:coarse){
 button,select,summary,input[type=text],input[type=number],input[type=search],input[type=date],input[type=file]{min-height:44px}
 button{min-width:44px}label.check{min-height:44px}#chapters li .dots{visibility:visible}
 .menu button,#palette li{min-height:44px;align-items:center}
}
body.screenplay #ed{font-family:"Courier Prime","Courier New",Courier,monospace;font-size:max(15px,calc(var(--size)*.88));max-width:min(100%,66ch)}
#scriptbar{display:flex;gap:4px;flex-wrap:wrap;align-items:center;padding:5px 16px;border-bottom:1px solid var(--line);background:var(--surface);font-size:13px}
#scriptbar[hidden]{display:none}#scriptbar button{padding:3px 8px;font-size:13px}#scriptbar button.on{border-color:var(--accent);color:var(--gold-text);font-weight:600}
#scriptbar label.check{margin:0 6px;font-size:12px;color:var(--muted)}
.script{font-family:"Courier Prime","Courier New",Courier,monospace;font-size:14px;line-height:1.3;background:#fff;color:#111;padding:22px 20px 22px 34px;border:1px solid var(--line);border-radius:6px;max-height:62vh;overflow:auto}
.script .sc-scene{font-weight:700;margin:1.3em 0 .7em;text-transform:uppercase}.script .sc-action{margin:0 0 1em;white-space:pre-wrap}
.script .sc-character{margin:1em 0 0 37%;text-transform:uppercase}.script .sc-paren{margin:0 0 0 29%;max-width:34%}
.script .sc-dialogue{margin:0 16% 1em 19%}.script .sc-lyric{font-style:italic;margin:0 16% 1em 19%}
.script .sc-transition{text-align:right;margin:1em 0;text-transform:uppercase}.script .sc-centered{text-align:center;margin:1em 0}
.script .sc-section,.script .sc-synopsis{color:#6b6b6b;font:italic 12px var(--ui);margin:.7em 0}.script .sc-break{border-top:1px dashed #999;margin:1em 0}
.script .sc-no{float:left;margin-left:-2.4em;color:#666}
.statrow{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 10px}.stat{border:1px solid var(--line);border-radius:8px;padding:6px 12px;min-width:80px}
.stat b{display:block;font:600 20px var(--display)}
.diff{white-space:pre-wrap;font:15px/1.6 var(--font);max-height:56vh;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
ins{background:rgba(31,122,90,.2);text-decoration:none;border-bottom:2px solid var(--ok)}del{background:rgba(179,38,30,.13);color:var(--muted)}
.srcrow{border-top:1px solid var(--line);padding:8px 0;display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap}.srcrow .grow{min-width:min(100%,260px);flex:1}
.cmt{border:1px solid var(--line);border-left:3px solid var(--gold);border-radius:8px;padding:8px 10px;margin:8px 0}
.cmt q{display:block;font-style:italic;color:var(--muted);font-size:13px;margin-bottom:3px}.cmt q::before,.cmt q::after{content:""}
#sprintpill{font-variant-numeric:tabular-nums;border-color:var(--accent);color:var(--gold-text);font-weight:600;white-space:nowrap}#sprintpill[hidden]{display:none}
.pgrid{border-collapse:collapse;font-size:13px}.pgrid th,.pgrid td{border:1px solid var(--line);padding:4px 7px;text-align:center}
.pgrid th:first-child,.pgrid td:first-child{text-align:left;white-space:nowrap}.gridwrap{overflow-x:auto;max-width:100%;margin:6px 0}
.dlg mark{background:var(--gold);color:var(--on-accent);border-radius:3px;padding:0 2px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:middle;flex:none}
.card .sd{font-size:12px;color:var(--muted);display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:2px 0}.card.dim{opacity:.32}
.implist{max-height:34vh;overflow:auto;padding-left:22px}.implist li{margin:3px 0}
#palette{position:fixed;inset:0;z-index:80;background:rgba(10,14,12,.3);display:flex;align-items:flex-start;justify-content:center;padding:12vh 16px 16px}
#palette[hidden]{display:none}#palette .box{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);width:min(560px,100%);overflow:hidden}
#palette input{border:0;border-bottom:1px solid var(--line);border-radius:0;font-size:16px;padding:12px 14px;width:100%;background:var(--surface)}
#palette ul{list-style:none;margin:0;padding:6px;max-height:50vh;overflow:auto}
#palette li{display:flex;justify-content:space-between;gap:12px;padding:7px 10px;border-radius:6px;cursor:pointer}
#palette li.on{background:var(--bg);box-shadow:inset 0 0 0 1px var(--line-strong)}#palette li span{color:var(--muted);font-size:12px;white-space:nowrap}
.endorse-text{font:600 11px var(--ui);letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:22px 0 0}
mark.rv-passive{background:rgba(70,150,190,.35)}
</style></head><body>
<div id="app">
<aside id="side" aria-label="Book and chapters">
 <div class="brand"><img class="pmark" src="__QMARK__" alt=""><div><b>Stark Quill</b><br><span>a Stark product</span></div></div>
 <button id="bookbtn" title="Switch or add a book"><b id="booktitle">Your book</b><span aria-hidden="true">&#9662;</span></button>
 <ul id="chapters" aria-label="Chapters"></ul>
 <button id="newch">+ New chapter</button>
 <div class="sidebtns">
  <button id="boardbtn">Corkboard</button>
  <button data-dlg="book">Book details</button>
  <button data-dlg="export">Export or print</button>
  <button data-dlg="zip" title="The whole book in one zip file, downloaded or saved to a USB stick">Zip / USB backup</button>
  <button data-panel="backups">Backups</button>
  <button data-panel="graveyard">Graveyard</button>
  <button data-panel="notes" title="Comments and footnotes in the book (Ctrl+Alt+M adds one)">Comments</button>
  <button data-dlg="sources" title="Books, articles and web pages you cite: MLA, APA or Chicago">Sources</button>
  <button data-panel="script" id="scriptbtn" hidden title="Scenes, characters and pages of the screenplay">Script</button>
  <button data-panel="library">Library</button>
 </div>
 <div class="sidefoot"><button id="planbtn" class="ghost small">Free plan</button><button id="aboutbtn" class="ghost small">About</button></div>
</aside>
<main>
 <header id="top">
  <button id="menu" class="hbtn" aria-label="Chapters">&#9776;</button>
  <div id="where"><h1 id="chtitle">&nbsp;</h1><span id="booksub"></span></div>
  <div class="sp"></div>
  <button id="goalbtn" class="mid" title="Today's words and your daily goal"><svg viewBox="0 0 24 24" aria-hidden="true">
   <circle class="trk" cx="12" cy="12" r="9"/><circle class="val" cx="12" cy="12" r="9" stroke-dasharray="56.55" stroke-dashoffset="56.55"/></svg>
   <span id="goaltext">0 today</span></button>
  <button id="sprintpill" class="mid" hidden title="Writing sprint"></button>
  <span id="save" class="ok" role="status" aria-live="polite"><i></i><span>Saved</span></span>
  <span id="words" class="wide">0 words</span>
  <button class="hbtn" id="exportbtn" aria-label="Export or print the book" title="Export a file, or print the book (Ctrl+P)">Export<span class="wide"> or print</span></button>
  <button class="hbtn wide" data-panel="search" title="Search the whole book (Ctrl+Shift+F)">Search</button>
  <button class="hbtn wide" data-panel="lore" title="Characters, places, dates and continuity">Lore</button>
  <button class="hbtn wide" id="focusbtn" title="Focus: just you and the page (Ctrl+Shift+Enter)">Focus</button>
  <button class="hbtn" id="more" aria-haspopup="menu" aria-label="More" title="More">&#8943;</button>
 </header>
 <div id="assignbar" hidden role="region" aria-label="Assignment"></div>
 <div id="scriptbar" hidden role="toolbar" aria-label="Screenplay elements"></div>
 <div id="banner" hidden><span id="bannertext"></span><span class="sp"></span><button id="bannerx" class="ghost small">Dismiss</button></div>
 <div class="tips" id="tips" hidden><span class="grow">Wrap words in <b>*asterisks*</b> for italics, and put <b>***</b> on a line
  of its own for a scene break. Quill saves while you type, and keeps a backup as you go.</span><button id="tipsguide" class="ghost small">Guide</button><button id="tipsx" class="ghost small">Got it</button></div>
 <div class="tips" id="samplebar" hidden role="region" aria-label="Sample book"><span class="grow">This is the sample book, so change
  anything you like. One continuity slip is hidden in it: open Lore to find it.</span><button id="sampleown" class="primary small">Start my own book</button></div>
 <div id="views">
  <section id="writeview"><div id="spell" aria-hidden="true"></div><textarea id="ed" spellcheck="true" aria-label="Chapter text"></textarea></section>
  <section id="refpane" hidden aria-label="Reference pane"><header><select id="refsel" aria-label="Chapter to look at"></select>
   <button id="refreload" class="ghost small" aria-label="Reload the reference chapter" title="Load it again">&#8635;</button>
   <button id="refclose" class="ghost small" aria-label="Close reference pane" title="Close (Ctrl+Alt+R)">&#10005;</button></header><div id="refbody" tabindex="0"></div></section>
  <section id="board" hidden aria-label="Corkboard"></section>
  <section id="panel" hidden aria-label="Panel"><header><h2 id="ptitle"></h2><button id="pclose" class="ghost" aria-label="Close panel">&#10005;</button></header>
   <div id="pbody"></div></section>
 </div>
 <div id="saybar" hidden role="region" aria-label="Read aloud"><span>Reading aloud</span><select id="sayvoice" aria-label="Voice"></select>
  <select id="sayrate" aria-label="Speed"></select><button id="saypause" class="ghost small">Pause</button><button id="saystop" class="ghost small">Stop (Esc)</button></div>
 <div id="focusbar"><span id="fwords">0 words</span><span id="fsave">Saved</span><button id="fexit" class="ghost small">Leave focus (Esc)</button></div>
</main>
</div>
<div id="welcome" hidden><div class="wcard">
 <img class="lockup on-light" src="__QLOCK_L__" alt="Stark Quill"><img class="lockup on-dark" src="__QLOCK_D__" alt="Stark Quill">
 <h1>A quiet place to write your book.</h1>
 <p class="lede">Quill keeps every word in plain files on this computer, in <b id="wfolder">Documents\My_Books</b>.
 Any text editor can open them, today or in twenty years.</p>
 <label for="wtitle">Working title</label><input id="wtitle" type="text" maxlength="200" placeholder="It can change later" autocomplete="off">
 <label for="wauthor">Your name as it should appear on the title page (optional)</label><input id="wauthor" type="text" maxlength="200" autocomplete="off">
 <label for="wkind">What are you writing?</label><select id="wkind"><option value="novel">Novel</option></select>
 <div class="go"><button class="primary" id="wstart">Start writing</button><button class="second" id="wsample">Try the sample book</button></div>
 <p class="small" style="margin-top:6px"><button class="ghost small" id="wimport">Bring in a manuscript you already have</button><span class="tiny muted" style="display:block;margin:0 12px">Word, OpenDocument, Markdown, plain text or Fountain</span></p>
 <p class="small" style="margin-top:14px"><button class="ghost small" id="wguide">New here? Read the one-page guide</button></p>
 <footer><span>Nothing you write leaves this computer unless you send it somewhere yourself.</span></footer>
 <p class="endorse-text">A Stark product</p>
</div></div>
<div id="pairview" hidden><div class="wcard" style="text-align:center"><img class="pmark" src="__QMARK__" alt="" style="width:56px;height:56px;margin:0 auto;border-radius:13px"><h1>Write from your phone</h1>
 <p id="pairmsg">Type the 6-digit PIN shown on your PC.</p>
 <input id="pairpin" inputmode="numeric" maxlength="6" autocomplete="one-time-code" style="font-size:26px;text-align:center;letter-spacing:.3em">
 <div class="go" style="justify-content:center"><button class="primary" id="pairbtn">Connect</button></div></div></div>
<dialog id="dlg"></dialog>
<div id="pop" class="menu" role="menu" hidden></div>
<div id="toast"></div>
<div id="palette" hidden role="dialog" aria-label="Quick search"><div class="box"><input id="palinput" type="search" autocomplete="off" placeholder="Jump to a chapter, a book or a command" aria-label="Quick search"><ul id="pallist" role="listbox"></ul></div></div>
<script>
"use strict";
const PAUSE=__PAUSE__, VERSION="__VERSION__", GUIDE=__GUIDE__, SAMPLE=__SAMPLE__;let TOKEN=__QUILL_TOKEN__;
const $=id=>document.getElementById(id), ed=$("ed");
const LS={get(k,d){try{const v=localStorage.getItem(k);return v===null?d:JSON.parse(v)}catch(e){return d}},
 set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}},del(k){try{localStorage.removeItem(k)}catch(e){}}};
if(!TOKEN)TOKEN=LS.get("quill.session","");
const S={book:null,meta:null,chapters:[],ch:null,baseText:"",baseSha:"",timer:null,saving:false,plan:"FREE",status:null,
 savedAt:null,panel:null,view:"write",stats:null,lastLore:0,bookWords:0,searchQ:"",aiModel:LS.get("quill.aimodel","")};
const NF=new Intl.NumberFormat();const n=x=>NF.format(x||0);
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function h(tag,attrs,...kids){const e=document.createElement(tag);for(const k in attrs||{}){const v=attrs[k];if(v==null||v===false)continue;
 if(k==="class")e.className=v;else if(k==="html")e.innerHTML=v;else if(k.startsWith("on"))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v===true?"":v)}
 for(const c of kids.flat(Infinity)){if(c==null||c===false)continue;e.append(c.nodeType?c:document.createTextNode(String(c)))}return e}
function words(t){t=t||"";if(t.indexOf("{>>")>=0)t=t.replace(/\{>>[\s\S]*?<<\}/g," ");const m=t.match(/\S*[\p{L}\p{N}]\S*/gu);return m?m.length:0}
function plural(k,one,many){return n(k)+" "+(k===1?one:many)}
let toastT=null;function toast(t,ms){const e=$("toast");e.textContent=t;e.classList.add("show");clearTimeout(toastT);toastT=setTimeout(()=>e.classList.remove("show"),ms||3200)}
function banner(t){$("bannertext").textContent=t||"";$("banner").hidden=!t}
$("bannerx").onclick=()=>banner("");
/* After Quill restarts, this PC's open tab holds the old per-launch key. The page Quill serves to this PC carries
   the new key, so the tab fetches it and carries on (same version only). Otherwise it asks for a reload; unsaved
   text is kept in this browser either way. */
const ON_THIS_PC=/^(127\.|localhost$|\[::1\]$)/.test(location.hostname);let reconP=null;
function reconnect(){return reconP||(reconP=(async()=>{try{const t=await(await fetch("/",{cache:"no-store"})).text();
 const m=/let TOKEN=("[^"]*")/.exec(t),v=/VERSION="([^"]*)"/.exec(t);const tok=m?JSON.parse(m[1]):"";
 if(!tok||!v||v[1]!==VERSION)return false;TOKEN=tok;toast("Quill restarted. Reconnected, and nothing was lost.");return true}
 catch(e){return false}finally{setTimeout(()=>{reconP=null},0)}})())}
function staleTab(){try{buffer()}catch(e){}
 banner("Quill was restarted. Reload this page to keep writing; your text is kept in this browser.");
 if(!$("bannerreload")){const b=h("button",{id:"bannerreload",class:"primary small",onclick:()=>{try{buffer()}catch(e){}location.reload()}},"Reload");$("bannerx").before(b)}}
const NO_ANSWER="Quill is not answering. Check that Quill is still running (start it again if it was closed), then try once more.";
async function api(path,body,again){const o={headers:{"X-Quill-Token":TOKEN}};
 if(body!==undefined){o.method="POST";o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body)}
 let r;try{r=await fetch(path,o)}catch(e){const x=new Error(NO_ANSWER);x.status=0;throw x}let j={};try{j=await r.json()}catch(e){}
 if(r.status===401&&TOKEN&&ON_THIS_PC&&!again){if(await reconnect())return api(path,body,true);staleTab();j.error=j.error||"Quill was restarted. Reload this page."}
 if(r.status===401&&!TOKEN){showPair();throw new Error("pair")}
 if(!r.ok&&!j.need_base){const e=new Error(j.error||("Quill could not do that (error "+r.status+"). Try again; if it keeps happening, close Quill and start it again."));e.status=r.status;e.data=j;throw e}return j}
const Q=s=>encodeURIComponent(s);
/* The writer's own local date (YYYY-MM-DD): word targets count days where the writer is, not where the PC is. */
function localDay(d){d=d||new Date();const p=x=>String(x).padStart(2,"0");return d.getFullYear()+"-"+p(d.getMonth()+1)+"-"+p(d.getDate())}
function fmtDay(s){const [y,m,d]=String(s).split("-").map(Number);return new Date(y,m-1,d).toLocaleDateString(undefined,{day:"numeric",month:"long",year:"numeric"})}

/* ---------------------------------------------------------------- page & type preferences */
const FONTS={book:'var(--display)',georgia:'Georgia,"Times New Roman",serif',sans:'var(--ui)',mono:'"Courier Prime","Courier New",var(--mono)'};
const WIDTHS={narrow:"30em",medium:"35em",wide:"41em"};
function prefs(){return Object.assign({theme:"",font:"book",size:19,lh:1.7,width:"medium",typewriter:false,focusCount:true,spell:true},LS.get("quill.prefs",{}))}
function applyPrefs(p){p=p||prefs();const r=document.documentElement;
 if(p.theme)r.setAttribute("data-theme",p.theme);else r.removeAttribute("data-theme");
 r.style.setProperty("--font",FONTS[p.font]||FONTS.book);r.style.setProperty("--size",(+p.size||19)+"px");
 r.style.setProperty("--lh",String(+p.lh||1.7));r.style.setProperty("--measure",WIDTHS[p.width]||WIDTHS.medium);
 $("fwords").style.display=p.focusCount?"":"none"}
function setPref(k,v){const p=prefs();p[k]=v;LS.set("quill.prefs",p);applyPrefs(p);if(typeof spellSoon==="function"){spellHide();spellSoon(250)}}
(function migrate(){const t=LS.get("quill.theme","");if(t&&!LS.get("quill.prefs",null)){setPref("theme",t)}})();
applyPrefs();

/* ---------------------------------------------------------------- save state */
let lastSave="";
function saveState(kind,text){if(kind+"|"+text===lastSave)return;lastSave=kind+"|"+text;  /* typing calls this on every key: touch the page only on change */
 const s=$("save");s.className=kind;s.querySelector("span").textContent=text;$("fsave").textContent=text;
 s.title=kind==="ok"&&S.savedAt?"Saved at "+S.savedAt.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"}):text}
function savedLabel(){if(!S.savedAt)return "Saved";const m=Math.round((Date.now()-S.savedAt)/60000);
 return m<1?"Saved":m<60?"Saved "+m+" min ago":"Saved at "+S.savedAt.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}
setInterval(()=>{if($("save").className==="ok")saveState("ok",savedLabel())},30000);
let savingShown=null;
const pkey=()=>"quill.pending."+S.book+"/"+S.ch;
let wordsT=null,lastLen=0;function updWordsSoon(){if(wordsT)return;wordsT=setTimeout(()=>{wordsT=null;updWords()},400)}  /* off the keystroke path */
function setText(el,t){if(el&&el.textContent!==t)el.textContent=t}
function updWords(){clearTimeout(wordsT);wordsT=null;lastLen=ed.value.length;const w=words(ed.value);const c=S.chapters.find(c=>c.name===S.ch);
 if(c){const diff=w-c.words;c.words=w;S.bookWords+=diff;const li=document.querySelector('#chapters li[data-name="'+CSS.escape(S.ch)+'"] .w');if(li)setText(li,n(w));if(diff&&S.target)ring()}
 setText($("words"),c&&c.target?n(w)+" of "+n(c.target)+" words"+(w>c.target?" ("+n(w-c.target)+" over)":""):plural(w,"word","words"));setText($("fwords"),plural(w,"word","words"));
 const t="Whole book: "+plural(S.bookWords,"word","words");if($("words").title!==t)$("words").title=t}
ed.addEventListener("input",()=>{if(!S.ch)return;saveState("dirty","Not saved yet");
 const len=ed.value.length;if(Math.abs(len-lastLen)>40)updWords();else updWordsSoon();lastLen=len;  /* a paste counts at once; keys after a pause */clearTimeout(S.timer);S.timer=setTimeout(save,PAUSE);calmSoon();typewriter()});
/* ---------------------------------------------------------------- spell check (local; knows the book's names)
   Quill asks its own server which words are misspelt (Windows' built-in checker, no internet) and leaves out the
   book dictionary (dictionary.txt in the book folder, filled with the names Lore finds). Underlines are drawn on a
   layer behind the editor, only after a pause in typing, so the keystroke path stays as it was. Where that checker
   is not available, the browser's own spell check is used, as before. */
const SP={avail:null,words:new Set(),key:"",t:null,shown:false,busy:false};
function spellOn(){return prefs().spell!==false&&SP.avail!==false}
function spellHide(){if(SP.shown){$("spell").classList.remove("on");SP.shown=false}}
function spellSoon(ms){clearTimeout(SP.t);SP.t=setTimeout(spellRun,ms==null?700:ms)}
async function spellRun(){if(SAY.on)return;if(!S.book||!S.ch||S.view==="board"||!prefs().spell){spellHide();return}
 if(SP.busy){spellSoon(300);return}const text=ed.value,key=S.book+"\u0000"+S.ch+"\u0000"+text.length+"\u0000"+text.slice(-64);
 if(key!==SP.key){SP.busy=true;try{const j=await api("/api/spell",{book:S.book,text});SP.avail=!!j.available;SP.words=new Set(j.words||[]);SP.dict=j.dictionary||0;SP.key=key}
  catch(e){SP.busy=false;return}SP.busy=false}
 ed.spellcheck=!SP.avail||!prefs().spell;if(!SP.avail){spellHide();return}
 if(ed.value!==text){spellSoon(300);return}spellDraw()}
const SPELL_RX=/[\p{L}]+(?:['\u2019][\p{L}]+)*/gu;
function layerSync(){const L=$("spell"),cs=getComputedStyle(ed);
 for(const k of["fontFamily","fontSize","fontWeight","fontStyle","lineHeight","letterSpacing","wordSpacing","fontKerning","textRendering","tabSize","paddingTop","paddingRight","paddingBottom","paddingLeft","fontFeatureSettings","fontVariantLigatures"])L.style[k]=cs[k];
 L.style.width=ed.clientWidth+"px";L.style.height=ed.clientHeight+"px";L.style.left=ed.offsetLeft+"px";L.style.top=ed.offsetTop+"px";return L}
function spellDraw(){const L=layerSync();
 const t=ed.value,frag=document.createDocumentFragment();let last=0,m;SPELL_RX.lastIndex=0;
 if(SP.words.size){while((m=SPELL_RX.exec(t))){if(!SP.words.has(m[0]))continue;
  if(m.index>last)frag.append(t.slice(last,m.index));const mk=document.createElement("mark");mk.textContent=m[0];frag.append(mk);last=m.index+m[0].length}}
 frag.append(t.slice(last)+"\n");L.replaceChildren(frag);L.scrollTop=ed.scrollTop;L.classList.add("on");SP.shown=true}
ed.addEventListener("scroll",()=>{if(SP.shown)$("spell").scrollTop=ed.scrollTop},{passive:true});
ed.addEventListener("input",()=>{spellHide();spellSoon()});
window.addEventListener("resize",()=>{spellHide();spellSoon(300)});
/* ---------------------------------------------------------------- read aloud (proofreading by ear)
   Uses only the voices installed on this computer (speechSynthesis voices marked localService), so it works
   without the internet; voices that need it are never offered. Reading starts at the sentence with the cursor,
   one sentence at a time, and that sentence is highlighted on the layer behind the page (the spell check's). */
const SAY={on:false,paused:false,list:[],i:0,voice:null,voices:[],token:0};
const SAY_ABBR=/(?:^|[\s("'\u201c\u2018])(?:Mr|Mrs|Ms|Dr|St|Mt|Jr|Sr|Prof|Capt|Col|Gen|Lt|Sgt|Rev|vs|etc|e\.g|i\.e|No)\.$/;
function sentenceRanges(t,from){const out=[],N=t.length;let s=0;from=from||0;
 const push=(a,b)=>{while(a<b&&/\s/.test(t[a]))a++;while(b>a&&/\s/.test(t[b-1]))b--;if(b>a&&b>from&&/[\p{L}\p{N}]/u.test(t.slice(a,b)))out.push([a,b])};
 for(let i=0;i<N;i++){const c=t[i];
  if(c==="\n"){push(s,i);s=i+1;continue}
  if(c==="."||c==="!"||c==="?"||c==="\u2026"){let j=i+1;while(j<N&&/[.!?\u2026"'\u201d\u2019)\]*_]/.test(t[j]))j++;
   if(j<N&&!/\s/.test(t[j]))continue;let k=j;while(k<N&&(t[k]===" "||t[k]==="	"))k++;if(k<N&&/\p{Ll}/u.test(t[k]))continue;  /* "Is it?" she asked: one sentence */
   if(c==="."&&SAY_ABBR.test(t.slice(Math.max(s,i-8),i+1)))continue;push(s,j);s=j;i=j-1}}
 push(s,N);return out}
function sayText(x){return x.replace(/\*+/g,"").replace(/^#+\s*/,"").replace(/\s+/g," ").trim()}
function pickVoices(all,lang){const loc=(all||[]).filter(v=>v.localService===true),l=String(lang||"en").toLowerCase().split("-")[0];
 const mine=v=>String(v.lang||"").toLowerCase().replace("_","-").split("-")[0]===l;return loc.filter(mine).concat(loc.filter(v=>!mine(v)))}
function voicesReady(){return new Promise(res=>{const v=speechSynthesis.getVoices();if(v.length)return res(v);
 const t=setTimeout(()=>res(speechSynthesis.getVoices()),1500);speechSynthesis.addEventListener("voiceschanged",()=>{clearTimeout(t);res(speechSynthesis.getVoices())},{once:true})})}
async function readAloud(){if(SAY.on){sayStop();return}
 if(!("speechSynthesis" in window)){openDialog("Read aloud",h("p",null,"This browser cannot read aloud. Chrome, Edge and Firefox can, with the voices installed on this computer."));return}
 if(!S.ch||S.view!=="write"){toast("Open a chapter to read it aloud.");return}
 const vs=pickVoices(await voicesReady(),S.meta&&S.meta.language);
 if(!vs.length){openDialog("Read aloud",[h("p",null,"No voice on this computer can read without the internet, and Quill only uses voices that stay on this computer."),
  h("p",{class:"small muted"},"On Windows you can add voices under Settings, Time & language, Speech.")]);return}
 const t=ed.value;let list=sentenceRanges(t,ed.selectionStart);if(!list.length)list=sentenceRanges(t,0);
 if(!list.length){toast("There is nothing to read in this chapter yet.");return}
 SAY.voices=vs;SAY.voice=vs.find(v=>v.voiceURI===LS.get("quill.voice",""))||vs[0];SAY.list=list;SAY.i=0;SAY.on=true;SAY.paused=false;
 spellHide();sayBar();sayNext()}
function sayNext(){if(!SAY.on||SAY.paused)return;if(SAY.i>=SAY.list.length){sayStop();toast("Finished reading the chapter.");return}
 const [a,b]=SAY.list[SAY.i];sayDraw(a,b);const tok=++SAY.token;const u=new SpeechSynthesisUtterance(sayText(ed.value.slice(a,b)));
 u.voice=SAY.voice;u.lang=SAY.voice.lang;u.rate=+LS.get("quill.sayrate",1)||1;
 u.onend=()=>{if(tok===SAY.token&&SAY.on&&!SAY.paused){SAY.i++;sayNext()}};
 u.onerror=e=>{if(tok===SAY.token&&e.error!=="interrupted"&&e.error!=="canceled"){sayStop();toast("Reading stopped: "+e.error)}};
 speechSynthesis.speak(u)}
function sayDraw(a,b){const L=layerSync(),t=ed.value,mk=document.createElement("mark");mk.className="say";mk.textContent=t.slice(a,b);
 L.replaceChildren(t.slice(0,a),mk,t.slice(b)+"\n");const top=mk.offsetTop,hh=mk.offsetHeight;
 if(top<ed.scrollTop||top+hh>ed.scrollTop+ed.clientHeight)ed.scrollTop=Math.max(0,top-ed.clientHeight/3);
 L.scrollTop=ed.scrollTop;L.classList.add("on");SP.shown=true}
function sayRestart(){SAY.token++;try{speechSynthesis.cancel()}catch(e){}if(!SAY.paused)sayNext()}
function sayPause(){if(!SAY.on)return;if(SAY.paused){SAY.paused=false;sayNext()}else{SAY.paused=true;SAY.token++;speechSynthesis.cancel()}$("saypause").textContent=SAY.paused?"Resume":"Pause"}
function sayStop(){if(!SAY.on)return;SAY.on=false;SAY.paused=false;SAY.token++;try{speechSynthesis.cancel()}catch(e){}$("saybar").hidden=true;spellHide();spellSoon(300)}
function sayBar(){const v=$("sayvoice"),r=$("sayrate");v.replaceChildren(...SAY.voices.map(x=>h("option",{value:x.voiceURI,selected:x===SAY.voice},x.name+" ("+x.lang+")")));
 r.replaceChildren(...[[0.8,"Slower"],[1,"Normal speed"],[1.2,"Faster"],[1.5,"Fast"]].map(([k,l])=>h("option",{value:k,selected:k===(+LS.get("quill.sayrate",1)||1)},l)));
 v.onchange=()=>{SAY.voice=SAY.voices.find(x=>x.voiceURI===v.value)||SAY.voice;LS.set("quill.voice",SAY.voice.voiceURI);sayRestart()};
 r.onchange=()=>{LS.set("quill.sayrate",+r.value);sayRestart()};$("saypause").textContent="Pause";$("saybar").hidden=false}
$("saypause").onclick=sayPause;$("saystop").onclick=sayStop;
ed.addEventListener("input",()=>{if(SAY.on){sayStop();toast("Reading stopped because the text changed.")}});
/* Paste from Word or Google Docs: keep *italics* and **bold** and the paragraphs, drop the rest (quill_paste.py).
   Clipboards without bold or italics paste exactly as before (the browser's plain text). */
ed.addEventListener("paste",async e=>{const cd=e.clipboardData;if(!cd||!S.ch)return;const html=cd.getData("text/html");
 if(!html||!/<(b|strong|i|em)[\s>]|font-weight|font-style/i.test(html))return;
 const plain=cd.getData("text/plain");e.preventDefault();const a=ed.selectionStart,b=ed.selectionEnd,before=ed.value;let t=plain;
 if(html.length<=6e6){try{t=(await api("/api/paste",{html})).text||plain}catch(err){t=plain}}
 else toast("Pasted as plain text: the copied text is too large to keep its italics and bold.",5000);
 if(ed.value===before)ed.setRangeText(t,a,b,"end");else ed.setRangeText(t,ed.selectionStart,ed.selectionEnd,"end");
 ed.dispatchEvent(new Event("input"))});
async function addToDictionary(word){if(!S.book)return;try{await api("/api/spell/add",{book:S.book,word});SP.key="";SP.words.delete(word);
 toast("Added \u201c"+word+"\u201d to this book's dictionary.");spellSoon(0);if(S.panel==="lore")renderLore()}catch(e){toast(e.message)}}
function buffer(){if(S.book&&S.ch)LS.set(pkey(),{book:S.book,name:S.ch,text:ed.value,base_sha:S.baseSha,base:S.baseText})}
async function save(force){clearTimeout(S.timer);if(!S.book||!S.ch)return;if(S.saving){S.timer=setTimeout(save,PAUSE);return}
 const sent=ed.value;if(!force&&sent===S.baseText&&S.baseSha){saveState("ok",savedLabel());return}
 buffer();S.saving=true;clearTimeout(savingShown);savingShown=setTimeout(()=>saveState("saving","Saving..."),350);
 try{let p={book:S.book,name:S.ch,text:sent,base_sha:S.baseSha,today:localDay()};let j=await api("/api/save",p);
  if(j.need_base){p.base=S.baseText;j=await api("/api/save",p)}
  if(j.name&&j.name!==S.ch){S.ch=j.name}if(S.lockShown||S.offShown){S.lockShown=S.offShown=false;banner("")}
  if(j.merged){if(ed.value===sent){keepCaret(j.text);S.baseText=j.text;S.baseSha=j.sha}else{S.baseText=sent;S.baseSha="resync";setTimeout(save,10)}
   if(j.conflicts&&j.conflicts.length)banner("Your phone and this PC both changed the same lines while they were apart. Both versions are kept, between the <<<<<<< and >>>>>>> lines. Keep the one you want and delete the rest.")}
  else{S.baseText=sent;S.baseSha=j.sha}
  if(ed.value===sent)LS.del(pkey());S.savedAt=new Date();clearTimeout(savingShown);
  saveState(ed.value===sent?"ok":"dirty",ed.value===sent?"Saved":"Not saved yet");
  if(j.stats)showStats(j.stats);
  if(S.panel==="lore"&&Date.now()-S.lastLore>5000)renderLore();
 }catch(e){clearTimeout(savingShown);
  if(e.status===401&&ON_THIS_PC){saveState("off","Reload to keep saving")}
  else if(!e.status){saveState("off","Not saved yet");$("save").title="Quill is not answering. Your text is kept in this browser and saved when Quill is back.";
   if(!S.offShown){S.offShown=true;banner("Quill is not answering, so your latest words are not saved to the book yet. They are kept in this browser, and Quill saves them as soon as it is running again.")}setTimeout(()=>save(true),3000)}
  else if(e.status===423){saveState("err","Not saved yet: file locked");if(!S.lockShown){S.lockShown=true;banner(e.message)}  /* a sync app holds the file: the text stays buffered here and in the journal */
   clearTimeout(S.timer);S.timer=setTimeout(()=>save(true),(e.data&&e.data.retry_ms)||4000)}
  else{saveState("err","Not saved: "+e.message)}}
 finally{S.saving=false}}
function keepCaret(t){const a=ed.selectionStart,b=ed.selectionEnd;ed.value=t;ed.setSelectionRange(Math.min(a,t.length),Math.min(b,t.length));updWords()}
window.addEventListener("online",()=>flushPending());
document.addEventListener("visibilitychange",()=>{if(document.hidden){buffer();save()}else wake()});
window.addEventListener("beforeunload",buffer);
async function flushPending(){const keys=[];for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k&&k.startsWith("quill.pending."))keys.push(k)}
 for(const k of keys){const p=LS.get(k,null);if(!p||(p.book===S.book&&p.name===S.ch))continue;
  try{let j=await api("/api/save",p);if(j.need_base){p.base=p.base||"";j=await api("/api/save",p)}LS.del(k)}catch(e){}}}
function migrateKeys(book,renamed){for(const [o,nw] of Object.entries(renamed||{})){if(o===nw)continue;
 const k="quill.pending."+book+"/"+o,v=LS.get(k,null);if(v){v.name=nw;LS.set("quill.pending."+book+"/"+nw,v);LS.del(k)}
 if(LS.get("quill.ch."+book,"")===o)LS.set("quill.ch."+book,nw)}}

/* ---------------------------------------------------------------- books */
async function loadBooks(prefer){const j=await api("/api/books");S.books=j.books;
 if(!j.books.length){showWelcome();return}
 const last=prefer||LS.get("quill.book",j.books[0].name);const b=j.books.find(b=>b.name===last)||j.books[0];await openBook(b.name)}
async function openBook(name){if(S.ch)await save();S.book=name;LS.set("quill.book",name);S.ch=null;
 const j=await api("/api/book?book="+Q(name)+"&today="+localDay());S.meta=j.meta;S.chapters=j.chapters;S.bookWords=j.words;S.target=j.target||null;ring();
 $("booktitle").textContent=j.meta.title;document.title=j.meta.title+" - Stark Quill";studioBook();
 renderChapters();const last=LS.get("quill.ch."+name,null);const c=S.chapters.find(c=>c.name===last)||S.chapters[0];
 if(c)await openChapter(c.name);else noChapters()
 renderAssign();sampleBar();if(S.view==="board")renderBoard();if(S.panel)openPanel(S.panel)}
function sampleBar(){$("samplebar").hidden=S.book!==SAMPLE}
$("sampleown").onclick=()=>newBookDialog();
async function openSample(){try{const j=await api("/api/book/sample",{});$("welcome").hidden=true;await loadBooks(j.name);return true}catch(e){toast(e.message);return false}}
function bookMenu(anchor){const items=(S.books||[]).map(b=>({label:b.title,hint:b.name===S.book?"open":"",act:()=>openBook(b.name)}));
 items.push("-",{label:"New book...",act:newBookDialog},{label:"Import a manuscript...",act:importDialog},{label:"Book details...",act:bookDialog},
  ...(S.book===SAMPLE?[]:[{label:"Open the sample book",act:openSample}]));showMenu(anchor,items)}
$("bookbtn").onclick=e=>bookMenu(e.currentTarget);
/* newBookDialog: see the kinds of project below */

/* ---------------------------------------------------------------- chapters */
function renderChapters(){const ul=$("chapters");ul.innerHTML="";
 S.chapters.forEach((c,i)=>{const li=h("li",{"data-name":c.name,draggable:"true",class:c.name===S.ch?"on":""},
  h("button",{class:"ch",onclick:()=>openChapter(c.name),ondblclick:()=>renameChapter(c),title:c.title,"aria-current":c.name===S.ch?"page":null},
   h("span",{class:"n"},String(i+1)),h("span",{class:"t"},c.title),h("span",{class:"w"},n(c.words))),
  h("button",{class:"dots","aria-label":"Chapter actions for "+c.title,onclick:e=>chapterMenu(e.currentTarget,c)},"⋯"));
  dnd(li,c.name,"#chapters li");ul.append(li)})}
async function openChapter(name){if(S.ch&&S.ch!==name)await save();
 const j=await api("/api/chapter?book="+Q(S.book)+"&name="+Q(name));
 S.ch=j.name;S.baseText=j.text;S.baseSha=j.sha;LS.set("quill.ch."+S.book,S.ch);ed.disabled=false;
 const pend=LS.get(pkey(),null);ed.value=j.text;
 if(pend&&pend.text!==j.text){ed.value=pend.text;S.baseText=pend.base!=null?pend.base:j.text;S.baseSha=pend.base_sha||j.sha;saveState("saving","Restoring unsaved text...");save(true)}
 else saveState("ok",savedLabel());
 const c=S.chapters.find(c=>c.name===S.ch);$("chtitle").textContent=j.title;
 $("booksub").textContent=S.meta?(S.meta.title+(c?" · chapter "+(S.chapters.indexOf(c)+1)+" of "+S.chapters.length:"")):"";
 ed.placeholder=j.title+" starts here.";if(S.meta&&S.meta.language)ed.setAttribute("lang",S.meta.language);
 document.querySelectorAll("#chapters li").forEach(li=>{const on=li.dataset.name===S.ch;li.classList.toggle("on",on);li.querySelector(".ch").toggleAttribute("aria-current",on)});
 updWords();flushDictation();document.body.classList.remove("side");if(S.view==="board")setView("write");
 ed.scrollTop=0;spellHide();spellSoon(150);refRestore();if(!matchMedia("(max-width:760px)").matches)ed.focus()}
function noChapters(){ed.value="";ed.disabled=true;$("chtitle").textContent="No chapters yet";saveState("ok","Saved");
 ed.placeholder="This book has no chapters yet. Add the first one with + New chapter in the chapter list.";sampleBar()}
function chapterMenu(anchor,c){const i=S.chapters.indexOf(c);showMenu(anchor,[
 {label:"Rename...",act:()=>renameChapter(c)},{label:"New chapter after this one...",act:()=>newChapter(c.name)},{label:"Scene details...",act:()=>detailsDialog(c)},"-",
 {label:"Move up",disabled:i===0,act:()=>moveChapter(c.name,-1)},{label:"Move down",disabled:i===S.chapters.length-1,act:()=>moveChapter(c.name,1)},"-",
 {label:"Delete chapter...",danger:true,act:()=>deleteChapter(c)}])}
async function newChapter(after){const t=await ask("New chapter","Chapter title",after?"":"Chapter "+(S.chapters.length+1),"Add chapter");
 if(!t)return;await save(true);try{const j=await api("/api/chapter/new",{book:S.book,title:t,after:after||null});await refreshChapters();await openChapter(j.name)}catch(e){toast(e.message)}}
$("newch").onclick=()=>newChapter(null);
async function refreshChapters(renamed){const j=await api("/api/book?book="+Q(S.book));S.meta=j.meta;S.chapters=j.chapters;S.bookWords=j.words;studioBook();
 if(renamed){migrateKeys(S.book,renamed);if(renamed[S.ch])S.ch=renamed[S.ch]}renderChapters();if(S.view==="board")renderBoard()}
async function renameChapter(c){const t=await ask("Rename chapter","Title",c.title,"Rename");if(!t||t===c.title)return;await save(true);
 try{const j=await api("/api/chapter/rename",{book:S.book,name:c.name,title:t});const r={};r[c.name]=j.name;
  await refreshChapters(r);if(S.ch===j.name)$("chtitle").textContent=j.title;toast("Renamed to "+j.title)}catch(e){toast(e.message)}}
async function applyOrder(order){await save(true);try{const j=await api("/api/chapter/order",{book:S.book,order});await refreshChapters(j.renamed);
 const c=S.chapters.find(c=>c.name===S.ch);if(c)$("booksub").textContent=S.meta.title+" · chapter "+(S.chapters.indexOf(c)+1)+" of "+S.chapters.length}catch(e){toast(e.message);refreshChapters()}}
function moveChapter(name,dir){const o=S.chapters.map(c=>c.name),i=o.indexOf(name),k=i+dir;if(k<0||k>=o.length)return;
 o.splice(i,1);o.splice(k,0,name);applyOrder(o)}
async function deleteChapter(c){const ok=await confirmDlg("Delete “"+c.title+"”?","Quill keeps a copy: you can bring it back from Backups at any time. The other chapters keep their order.","Delete chapter",true);
 if(!ok)return;await save(true);try{await api("/api/chapter/delete",{book:S.book,name:c.name});const was=S.ch===c.name;await refreshChapters();
  if(was){S.ch=null;if(S.chapters[0])await openChapter(S.chapters[0].name);else noChapters()}
  toast("Deleted. It is in Backups if you want it back.")}catch(e){toast(e.message)}}
let dragName=null;
function dnd(node,name,selector){node.addEventListener("dragstart",e=>{dragName=name;node.classList.add("dragging");e.dataTransfer.effectAllowed="move";try{e.dataTransfer.setData("text/plain",name)}catch(_){}});
 node.addEventListener("dragend",()=>{node.classList.remove("dragging");document.querySelectorAll(".drop-before").forEach(x=>x.classList.remove("drop-before"))});
 node.addEventListener("dragover",e=>{if(!dragName||dragName===name)return;e.preventDefault();document.querySelectorAll(".drop-before").forEach(x=>x.classList.remove("drop-before"));node.classList.add("drop-before")});
 node.addEventListener("drop",e=>{e.preventDefault();const from=dragName;dragName=null;node.classList.remove("drop-before");if(!from||from===name)return;
  const o=S.chapters.map(c=>c.name).filter(x=>x!==from);o.splice(o.indexOf(name),0,from);applyOrder(o)})}

/* ---------------------------------------------------------------- corkboard */
function setView(v){S.view=v;$("board").hidden=v!=="board";$("writeview").hidden=v==="board";$("boardbtn").textContent=v==="board"?"Back to writing":"Corkboard";
 if(v==="board")renderBoard();else ed.focus()}
$("boardbtn").onclick=()=>{save();setView(S.view==="board"?"write":"board")};
const STATUS={idea:"Idea",draft:"Draft",revised:"Revised",final:"Final"};
function renderBoard(){const b=$("board");b.innerHTML="";
 const target=S.meta&&S.meta.target_words?" of "+n(S.meta.target_words):"";
 b.append(h("div",{class:"boardhead"},h("h2",null,S.meta?S.meta.title:"Corkboard"),
  h("span",{class:"muted"},plural(S.chapters.length,"chapter","chapters")+" · "+n(S.bookWords)+target+" words"),
  h("span",{class:"sp"}),boardFilter(),boardToggle(),h("button",{class:"small",onclick:plotGridDialog},"Plot grid"),
  S.boardMode==="outline"?h("button",{class:"small",onclick:()=>window.print()},"Print"):null,S.boardMode==="outline"?h("button",{class:"small",onclick:copyOutline},"Copy as Markdown"):null,
  h("span",{class:"muted small"},S.boardMode==="outline"?"Status and targets change here and stay in book.json, never in your text.":"Drag cards to change the order. Notes stay in book.json, never in your text.")));
 if(!S.chapters.length){b.append(h("div",{class:"empty"},h("b",null,"No chapters yet."),h("p",null,"Add one with + New chapter. Each chapter gets a card here with a note, a status, a word target and keywords.")));return}
 if(S.boardMode==="outline"){b.append(outlineTable());return}
 const grid=h("div",{class:"cards"});const pls=(S.meta&&S.meta.plotlines)||[];const pcol=Object.fromEntries(pls.map(p=>[p.id,p]));
 S.chapters.forEach((c,i)=>{const syn=h("textarea",{placeholder:"What happens in this chapter?","aria-label":"Notes for "+c.title},c.synopsis||"");
  syn.addEventListener("change",()=>api("/api/chapter/note",{book:S.book,name:c.name,synopsis:syn.value}).then(()=>{c.synopsis=syn.value}).catch(e=>toast(e.message)));
  const st=h("select",{"aria-label":"Status"},Object.entries(STATUS).map(([k,v])=>h("option",{value:k,selected:(c.status||"draft")===k},v)));
  st.onchange=()=>api("/api/chapter/note",{book:S.book,name:c.name,status:st.value}).then(()=>{c.status=st.value}).catch(e=>toast(e.message));
  const f=S.boardFilter||"",dim=f&&!(f.startsWith("pov:")?c.pov===f.slice(4):f.startsWith("kw:")?(c.keywords||[]).includes(f.slice(3)):(c.plotlines||[]).includes(f));
  const tg=targetBox(c),kw=kwBox(c);
  const card=h("div",{class:"card"+(dim?" dim":""),draggable:"true"},
   h("div",{class:"top"},h("span",null,"Chapter "+(i+1)),st),c.beats&&c.beats.length?h("div",{class:"beat"},c.beats.join(" · ")):null,
   h("div",{class:"sd"},(c.plotlines||[]).filter(x=>pcol[x]).map(x=>h("i",{class:"dot",title:pcol[x].name,style:"background:"+pcol[x].color})),
    c.pov?h("span",null,"POV "+c.pov):null,c.setting?h("span",null,"· "+c.setting):null,c.when?h("span",null,"· "+c.when):null,
    h("button",{class:"ghost small",style:"margin-left:auto;padding:1px 6px",onclick:()=>detailsDialog(c),"aria-label":"Scene details for "+c.title},"Details")),
   h("button",{class:"title",onclick:()=>openChapter(c.name)},c.title),syn,
   c.opening?h("div",{class:"open"},c.opening+(c.words>40?"...":"")):h("div",{class:"open"},"Nothing written yet."),
   kw,progBar(c),h("div",{class:"foot"},h("span",null,plural(c.words,"word","words")),tg,h("span",{class:"mv"},
    h("button",{class:"ghost","aria-label":"Move earlier",disabled:i===0,onclick:()=>moveChapter(c.name,-1)},"←"),
    h("button",{class:"ghost","aria-label":"Move later",disabled:i===S.chapters.length-1,onclick:()=>moveChapter(c.name,1)},"→"))));
  dnd(card,c.name,".card");grid.append(card)});
 b.append(grid)}

function boardFilter(){const pls=(S.meta&&S.meta.plotlines)||[],povs=[...new Set(S.chapters.map(c=>c.pov).filter(Boolean))],kws=[...new Set(S.chapters.flatMap(c=>c.keywords||[]))].sort((a,b)=>a.localeCompare(b));
 if(!pls.length&&!povs.length&&!kws.length)return null;const sel=h("select",{"aria-label":"Show",style:"width:auto"},h("option",{value:""},"All chapters"),
  pls.map(p=>h("option",{value:p.id,selected:S.boardFilter===p.id},"Plotline: "+p.name)),povs.map(v=>h("option",{value:"pov:"+v,selected:S.boardFilter==="pov:"+v},"POV: "+v)),kws.map(v=>h("option",{value:"kw:"+v,selected:S.boardFilter==="kw:"+v},"Keyword: "+v)));
 sel.onchange=()=>{S.boardFilter=sel.value;renderBoard()};return sel}

/* ---------------------------------------------------------------- chapter targets, keywords, outline */
S.boardMode=LS.get("quill.boardmode","cards")==="outline"?"outline":"cards";
function setOutlineView(){S.boardMode="outline";LS.set("quill.boardmode","outline");save();setView("board");if(S.view==="board")renderBoard()}
function boardToggle(){const mk=(m,t)=>h("button",{class:"small"+(S.boardMode===m?" on":""),"aria-pressed":S.boardMode===m?"true":"false",onclick:()=>{S.boardMode=m;LS.set("quill.boardmode",m);renderBoard()}},t);
 return h("span",{class:"viewtog",role:"group","aria-label":"Show chapters as"},mk("cards","Cards"),mk("outline","Outline"))}
function progBar(c){if(!c.target)return h("div",{class:"pbar",style:"visibility:hidden","aria-hidden":"true"},h("i"));const pct=Math.min(100,Math.round(100*c.words/c.target));
 return h("div",{class:"pbar"+(pct>=100?" done":""),role:"progressbar","aria-valuemin":"0","aria-valuemax":"100","aria-valuenow":String(pct),"aria-label":"Words against the target for "+c.title,title:n(c.words)+" of "+n(c.target)+" words"},h("i",{style:"width:"+pct+"%"}))}
function targetBox(c){const i=h("input",{type:"number",class:"tgt",min:"0",max:"1000000",step:"100",placeholder:"Target","aria-label":"Word target for "+c.title,value:c.target||""});
 i.addEventListener("change",()=>api("/api/chapter/note",{book:S.book,name:c.name,target:i.value===""?0:+i.value}).then(j=>{c.target=j.note.target||0;updWords();renderBoard()}).catch(e=>toast(e.message)));return i}
function kwBox(c){const i=h("input",{type:"text",class:"kw",maxlength:"200",placeholder:"Keywords: grief, harbour","aria-label":"Keywords for "+c.title,value:(c.keywords||[]).join(", ")});
 i.addEventListener("change",()=>api("/api/chapter/note",{book:S.book,name:c.name,keywords:i.value}).then(j=>{c.keywords=j.note.keywords||[];renderBoard()}).catch(e=>toast(e.message)));return i}
function outlineTable(){const f=S.boardFilter||"";
 const rows=S.chapters.map((c,i)=>({c,i})).filter(({c})=>!f||(f.startsWith("pov:")?c.pov===f.slice(4):f.startsWith("kw:")?(c.keywords||[]).includes(f.slice(3)):(c.plotlines||[]).includes(f)));
 const tb=h("tbody");rows.forEach(({c,i})=>{const st=h("select",{"aria-label":"Status of "+c.title},Object.entries(STATUS).map(([k,v])=>h("option",{value:k,selected:(c.status||"draft")===k},v)));
  st.onchange=()=>api("/api/chapter/note",{book:S.book,name:c.name,status:st.value}).then(()=>{c.status=st.value}).catch(e=>toast(e.message));
  tb.append(h("tr",null,h("td",{class:"num"},String(i+1)),h("td",null,h("button",{class:"title",onclick:()=>openChapter(c.name)},c.title),c.pov?h("div",{class:"tiny muted"},"POV "+c.pov):null),
   h("td",null,st),h("td",null,h("div",null,n(c.words)+" words"),targetBox(c),progBar(c)),
   h("td",null,(c.keywords||[]).map(k=>h("span",{class:"chip"},k))),h("td",{class:"syn"},c.synopsis||"")))});
 return h("table",{class:"otable"},h("thead",null,h("tr",null,h("th",null,"#"),h("th",null,"Chapter"),h("th",null,"Status"),h("th",null,"Words and target"),h("th",null,"Keywords"),h("th",{class:"syn"},"Note"))),tb)}
async function copyOutline(){try{const j=await api("/api/outline/text?book="+Q(S.book));await copyText(j.markdown)}catch(e){toast(e.message)}}

/* ---------------------------------------------------------------- reference pane: read another chapter beside this one */
function refKey(){return "quill.ref."+S.book}
async function refShow(name){const body=$("refbody"),sel=$("refsel");if(!S.book)return;
 if(!S.chapters.length){sel.replaceChildren();body.textContent="This book has no chapters yet.";return}
 if(!S.chapters.some(c=>c.name===name))name=S.chapters[0].name;
 sel.replaceChildren(...S.chapters.map((c,i)=>h("option",{value:c.name,selected:c.name===name},(i+1)+". "+c.title)));
 try{const j=await api("/api/chapter?book="+Q(S.book)+"&name="+Q(name));const c=S.chapters.find(c=>c.name===name);
  body.replaceChildren();if(c&&c.synopsis)body.append(h("p",{class:"rnote"},"Note: "+c.synopsis));body.append(document.createTextNode(j.text||"(empty chapter)"));body.scrollTop=0;
  LS.set(refKey(),{open:true,name})}catch(e){body.textContent=e.message}}
function refOpen(name){$("refpane").hidden=false;refShow(name||(LS.get(refKey(),{})||{}).name||(S.chapters.find(c=>c.name!==S.ch)||S.chapters[0]||{}).name)}
function refClose(){$("refpane").hidden=true;LS.set(refKey(),{open:false,name:$("refsel").value});ed.focus()}
function refToggle(){if(!S.book)return;$("refpane").hidden?refOpen():refClose()}
$("refsel").onchange=()=>refShow($("refsel").value);$("refreload").onclick=()=>refShow($("refsel").value);$("refclose").onclick=refClose;
function refRestore(){const r=LS.get(refKey(),null);if(r&&r.open&&$("refpane").hidden)refOpen(r.name);else if(!r||!r.open)$("refpane").hidden=true}

/* ---------------------------------------------------------------- desk tools: writing week, submission check, collections, rescue sheet */
function printSheet(node){let p=$("printsheet");if(!p){p=h("div",{id:"printsheet"});document.body.append(p)}p.replaceChildren(node.cloneNode(true));document.body.classList.add("printing");
 const done=()=>{document.body.classList.remove("printing");window.removeEventListener("afterprint",done)};window.addEventListener("afterprint",done);setTimeout(()=>window.print(),30)}
const WD=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
function weekCard(st){if(!st.card)return null;const box=h("div",{class:"weekcard"});let boxes=[];
 const draw=c=>{boxes=WD.map((nm,i)=>{const cb=h("input",{type:"checkbox",checked:c.rest.includes(i),"aria-label":nm+" is a rest day"});
   cb.onchange=async()=>{const rest=boxes.map((b,k)=>b.checked?k:-1).filter(k=>k>=0);try{const j=await api("/api/stats/rest",{rest});showStats(j);draw(j.card)}catch(e){toast(e.message);draw(c)}};return cb});
  box.replaceChildren(h("p",{class:"weekline",role:"status"},c.line),
   h("div",{class:"week","aria-label":"This week, Monday to Sunday"},c.week.map(d=>h("div",{class:"wd"+(d.met?" met":"")+(d.rest?" rest":"")+(d.future?" fut":"")+(d.today?" today":""),title:d.date},h("b",null,WD[d.weekday]),h("span",null,d.future?"":n(d.words))))),
   h("p",{class:"small muted"},n(c.words)+" words this week"+(c.goal?"; "+c.days_met+" of "+(7-c.rest.length)+" writing days at your goal":"")+"."),
   h("div",{class:"restrow"},h("span",{class:"small"},"Rest days:"),WD.map((nm,i)=>h("label",null,boxes[i],nm))),
   h("p",{class:"tiny muted"},"A rest day you do not write on never breaks your run. Up to five."),
   h("button",{class:"small noprint",style:"margin-top:6px",onclick:()=>printSheet(box)},"Print this week"))};
 draw(st.card);return box}

async function preflightDialog(){if(!S.book)return;await save(true);let j;try{j=await api("/api/preflight?book="+Q(S.book))}catch(e){toast(e.message);return}
 const LV={stop:"Fix first",fix:"Fix",note:"Good to know"};
 const sheet=h("div",{class:"pfsheet"},h("h3",null,S.meta.title+": before you send it"),
  h("p",{class:"big",role:"status"},j.ready?"Nothing stands in the way.":(j.can_export?plural(j.counts.fix,"thing","things")+" to fix before you send it.":plural(j.counts.stop,"thing","things")+" must be fixed first.")),
  h("p",{class:"small muted"},"About "+n(j.words_rounded)+" words in "+plural(j.chapters,"chapter","chapters")+". The manuscript layout prints this at the top of every page after the first: "+j.header+"."),
  j.items.length?h("ul",{class:"pflist"},j.items.map(i=>h("li",{class:"pf-"+i.level},h("span",{class:"pill"},LV[i.level]),h("b",null," "+i.title),h("div",{class:"small"},i.detail),i.where.length?h("div",{class:"tiny muted"},i.where.slice(0,8).join("; ")):null))):h("p",{class:"muted"},"No placeholders, stray indents or missing details found."),
  h("p",{class:"tiny muted"},"This checks what the manuscript layout prints and what editors commonly send back. It does not judge the writing, and every agent has their own guidelines: read those too."));
 const r=await openDialog("Before you send it",[sheet],[{label:"Close"},{label:"Print list",value:"print"},{label:"Book details...",value:"details"},{label:"Export a manuscript",primary:true,value:"export"}]);
 if(r==="print")printSheet(sheet);else if(r==="details")bookDialog();else if(r==="export")exportDialog()}

function collBox(c){const list=h("div",{class:"acts",style:"flex-wrap:wrap"},c.chapters.map(ch=>h("button",{class:"ghost small",title:n(ch.words)+" words, "+ch.status,onclick:async()=>{$("dlg").close();await openChapter(ch.name)}},ch.title)));
 return h("div",{class:"entry"},h("div",{class:"meta"},h("b",null,c.name),h("span",null,(c.kind==="smart"?"Rule: "+c.rule_text:"Hand-picked")+" · "+plural(c.chapters.length,"chapter","chapters")+" · "+plural(c.words,"word","words"))),
  c.chapters.length?list:h("p",{class:"small muted"},"No chapter fits right now."),
  h("div",{class:"acts"},h("button",{onclick:()=>collectionForm(c)},"Edit"),h("button",{class:"ghost",onclick:async()=>{if(await confirmDlg("Delete the collection “"+c.name+"”?","Only the list goes. Every chapter stays where it is.","Delete collection",true)){try{await api("/api/collection/delete",{book:S.book,id:c.id})}catch(e){toast(e.message)}collectionsDialog()}else collectionsDialog()}},"Delete")))}
async function collectionsDialog(){if(!S.book)return;await save(true);let j;try{j=await api("/api/collections?book="+Q(S.book))}catch(e){toast(e.message);return}
 const r=await openDialog("Collections",[h("p",{class:"small muted"},"Saved lists of chapters. A hand-picked list holds the chapters you choose. A rule list fills itself, for example every chapter whose status is Draft."),
  ...(j.collections.length?j.collections.map(collBox):[h("p",{class:"muted"},"None yet. Try one for the chapters still in draft, or a reading list of what to revise this week.")])],[{label:"Close"},{label:"New collection",primary:true,value:"new"}]);
 if(r==="new")collectionForm(null)}
async function collectionForm(c){const name=h("input",{type:"text",maxlength:"40",id:"c_n",value:c?c.name:""}),
 kind=h("select",{id:"c_k"},[["manual","Hand-picked chapters"],["smart","A rule (fills itself)"]].map(([k,v])=>h("option",{value:k,selected:(c?c.kind:"manual")===k},v)));
 const pick=new Set((c&&c.picked)||[]);const list=h("div",{class:"cpick"},S.chapters.map(ch=>{const cb=h("input",{type:"checkbox",checked:pick.has(ch.name)});cb.onchange=()=>{if(cb.checked)pick.add(ch.name);else pick.delete(ch.name)};return h("label",null,cb,ch.title)}));
 const rule=(c&&c.rule)||{};const st=h("select",{id:"c_s"},[["","Any status"],["idea","Idea"],["draft","Draft"],["revised","Revised"],["final","Final"]].map(([k,v])=>h("option",{value:k,selected:(rule.status||"")===k},v)));
 const kw=h("input",{type:"text",maxlength:"24",id:"c_w",value:rule.keyword||"",placeholder:"A keyword, such as grief"}),tx=h("input",{type:"text",maxlength:"60",id:"c_x",value:rule.text||"",placeholder:"A word in the title or note"});
 const tg=h("select",{id:"c_t"},[["","Any word target"],["under","Under its word target"],["met","At or over its word target"],["none","Has no word target"]].map(([k,v])=>h("option",{value:k,selected:(rule.target||"")===k},v)));
 const rbox=h("div",null,h("label",{for:"c_s"},"Status is"),st,h("label",{for:"c_w"},"Tagged with the keyword"),kw,h("label",{for:"c_x"},"Title or note has the words"),tx,h("label",{for:"c_t"},"Word target"),tg);
 const sync=()=>{list.style.display=kind.value==="manual"?"":"none";rbox.style.display=kind.value==="smart"?"":"none"};kind.onchange=sync;sync();
 await openDialog(c?"Edit collection":"New collection",[h("label",{for:"c_n"},"Name"),name,h("label",{for:"c_k"},"Kind"),kind,list,rbox],[{label:"Cancel"},{label:"Save",primary:true,onClick:async()=>{
  try{await api("/api/collection",{book:S.book,collection:{id:c?c.id:undefined,name:name.value,kind:kind.value,chapters:[...pick],rule:{status:st.value,keyword:kw.value,text:tx.value,target:tg.value}}});return true}catch(e){toast(e.message);return false}}}]);
 collectionsDialog()}

async function rescueDialog(){if(!S.book)return;await save(true);let j;try{j=await api("/api/rescue?book="+Q(S.book)+"&today="+localDay())}catch(e){toast(e.message);return}
 const sheet=h("div",{class:"rescue"},h("h3",null,"Rescue sheet: "+j.title),h("p",{class:"small muted"},(j.author?j.author+" · ":"")+"printed "+j.date),
  h("dl",null,h("dt",null,"The book lives in"),h("dd",null,j.folder),h("dt",null,"How much"),h("dd",null,plural(j.words,"word","words")+" in "+plural(j.chapters.length,"chapter","chapters")+(j.streak?"; a run of "+plural(j.streak,"day","days"):"")),
   h("dt",null,"Copies"),h("dd",null,j.risk+" "+plural(j.milestones,"named milestone","named milestones")+"."),j.plan?h("dt",null,"Finish line"):null,j.plan?h("dd",null,planText(j.plan)):null),
  h("table",null,h("tr",null,h("th",null,"Chapter"),h("th",null,"Status"),h("th",null,"Words")),j.chapters.map(c=>h("tr",null,h("td",null,c.title),h("td",null,c.status),h("td",null,n(c.words))))),
  h("h4",null,"If something goes wrong"),h("ol",null,j.steps.map(s=>h("li",null,s))));
 const r=await openDialog("Rescue sheet",[h("p",{class:"small muted"},"One page to print and keep with your backup, so you or someone at home knows where the book is and how to get it back."),sheet],
  [{label:"Close"},{label:"Back up now...",value:"zip"},{label:"Print",primary:true,value:"print"}]);
 if(r==="print")printSheet(sheet);else if(r==="zip")zipDialog()}

/* ---------------------------------------------------------------- milestones: named copies of the whole book */
async function milestoneNew(){if(!S.book)return;const label=h("input",{type:"text",class:"field",maxlength:"80",id:"ms_l",placeholder:"First draft done"}),note=h("textarea",{class:"field",id:"ms_n",rows:"3",maxlength:"2000",placeholder:"What is true of the book at this point (optional)"});
 const ok=await openDialog("Make a milestone",[h("p",{class:"small muted"},"A milestone is a named copy of every chapter as it is now. Unlike the automatic backups it stays until you delete it, so you can come back to it, compare any chapter with today's text, or restore one."),
  h("label",{for:"ms_l"},"Name"),label,h("label",{for:"ms_n",style:"margin-top:8px"},"Note"),note],
  [{label:"Cancel"},{label:"Save milestone",primary:true,onClick:async()=>{if(!label.value.trim()){toast("Give it a name first.");return false}
   try{await save(true);await api("/api/milestone",{book:S.book,label:label.value,note:note.value});return true}catch(e){toast(e.message);return false}}}]);
 if(ok){toast("Milestone saved.");if(S.panel==="backups")renderBackups()}}
async function milestoneBox(){const box=h("div",{class:"section"},h("h3",null,"Milestones"));let j;try{j=await api("/api/milestones?book="+Q(S.book))}catch(e){box.append(h("p",{class:"muted"},e.message));return box}
 box.append(h("p",{class:"small muted"},"Named copies you make yourself, kept until you delete them (Ctrl+Alt+S)."),h("button",{class:"primary",onclick:milestoneNew},"Make a milestone"));
 if(!j.milestones.length){box.append(h("p",{class:"muted"},"None yet. Make one before a big rewrite, and you can always see exactly what changed."));return box}
 j.milestones.forEach(m=>{const list=h("div");const e=h("div",{class:"entry"},h("div",{class:"meta"},h("b",null,m.label),h("span",null,m.created+" · "+plural(m.words,"word","words"))),m.note?h("div",{class:"small"},m.note):null,
  h("div",{class:"acts"},h("button",{onclick:async ev=>{ev.target.disabled=true;await milestoneShow(m,list)}},"Show chapters"),
   h("button",{class:"ghost",onclick:async()=>{if(await confirmDlg("Delete the milestone “"+m.label+"”?","The automatic backups are not touched. This copy is removed for good.","Delete milestone",true)){try{await api("/api/milestone/delete",{book:S.book,id:m.id});renderBackups()}catch(x){toast(x.message)}}}},"Delete")),list);box.append(e)});return box}
async function milestoneShow(m,list){const j=await api("/api/milestone?book="+Q(S.book)+"&id="+Q(m.id));list.innerHTML="";
 j.chapters.forEach(c=>list.append(h("div",{class:"msrow"},h("span",{class:"grow"},c.title+" ",h("span",{class:"muted"},"("+n(c.words)+")")),
  h("span",null,h("button",{class:"ghost small",onclick:()=>milestoneCompare(m,c)},"Compare with now"),h("button",{class:"ghost small",onclick:()=>milestoneRestore(m,c)},"Restore")))))}
async function milestoneCompare(m,c){let j;try{await save(true);j=await api("/api/milestone/compare?book="+Q(S.book)+"&id="+Q(m.id)+"&name="+Q(c.name))}catch(e){toast(e.message);return}
 const view=h("div",{class:"diff"});j.ops.forEach(([o,t])=>view.append(o==="="?document.createTextNode(t):h(o==="+"?"ins":"del",null,t)));
 openDialog("Since “"+m.label+"”: "+c.title,[h("p",{class:"small"},j.exists?(j.added||j.removed?plural(j.added,"word","words")+" added, "+plural(j.removed,"word","words")+" taken out, in "+plural(j.changed,"paragraph","paragraphs")+".":"No changes since this milestone."):"This chapter is not in the book now, so everything shows as taken out."),
  h("p",{class:"tiny muted"},h("ins",null,"Added since"),"  ",h("del",null,"Taken out since")),view],[{label:"Close",primary:true}])}
async function milestoneRestore(m,c){if(!await confirmDlg("Restore “"+c.title+"” from “"+m.label+"”?","The chapter's text becomes the milestone's. Quill backs up the current book first, so this can be undone.","Restore chapter"))return;
 await save(true);try{const r=await api("/api/milestone/restore",{book:S.book,id:m.id,name:c.name});await refreshChapters();await openChapter(r.name);toast("Restored from the milestone.")}catch(e){toast(e.message)}}
document.addEventListener("keydown",e=>{if(!(e.ctrlKey||e.metaKey)||!e.altKey||e.shiftKey||!S.book)return;
 if(e.code==="KeyR"){e.preventDefault();refToggle()}else if(e.code==="KeyO"){e.preventDefault();setOutlineView()}else if(e.code==="KeyS"){e.preventDefault();milestoneNew()}else if(e.code==="KeyP"){e.preventDefault();preflightDialog()}});

/* ---------------------------------------------------------------- panels */
const PANELS={search:"Search the book",lore:"Lore",graveyard:"Graveyard",backups:"Backups",library:"Library",notes:"Comments",script:"Script"};
function openPanel(name){S.panel=name;$("panel").hidden=false;$("ptitle").textContent=PANELS[name];
 document.querySelectorAll("[data-panel]").forEach(b=>b.classList.toggle("on",b.dataset.panel===name));
 ({search:renderSearch,lore:renderLore,graveyard:renderGraveyard,backups:renderBackups,library:renderLibrary,notes:renderNotes,script:renderScript})[name]()}
function closePanel(){S.panel=null;$("panel").hidden=true;document.querySelectorAll("[data-panel]").forEach(b=>b.classList.remove("on"));ed.focus()}
$("pclose").onclick=closePanel;
document.querySelectorAll("[data-panel]").forEach(b=>b.onclick=()=>{document.body.classList.remove("side");S.panel===b.dataset.panel?closePanel():openPanel(b.dataset.panel)});
const pb=()=>$("pbody");
function renderSearch(){const box=pb();box.innerHTML="";
 const q=h("input",{type:"search",placeholder:"Word or phrase",value:S.searchQ,"aria-label":"Search for"});
 const cs=h("input",{type:"checkbox"}),ww=h("input",{type:"checkbox"});
 const rep=h("input",{type:"text",placeholder:"Replace with","aria-label":"Replace with"});
 const res=h("div");const go=h("button",{class:"primary",style:"white-space:nowrap"},"Search");
 const repBtn=h("button",null,"Replace all");
 box.append(h("div",{class:"row"},q,go),h("div",{class:"row small",style:"margin:8px 0 2px;gap:14px"},h("label",{class:"check"},cs,"Match case"),h("label",{class:"check"},ww,"Whole words")),
  h("details",{style:"margin:8px 0"},h("summary",{class:"small muted"},"Replace"),h("div",{class:"row",style:"margin-top:6px"},rep,repBtn),
   h("p",{class:"tiny muted"},"Replace changes every chapter at once. A backup is made first, so you can undo it from Backups.")),res);
 async function run(){const v=q.value;S.searchQ=v;if(!v.trim()){res.innerHTML="";return}await save(true);
  try{const j=await api("/api/search?book="+Q(S.book)+"&q="+Q(v)+"&case="+(cs.checked?1:0)+"&word="+(ww.checked?1:0));res.innerHTML="";
   if(!j.total){res.append(h("p",{class:"muted"},"Not found anywhere in this book."));return}
   res.append(h("p",{class:"small muted"},plural(j.total,"match","matches")+" in "+plural(new Set(j.hits.map(x=>x.name)).size,"chapter","chapters")+(j.truncated?" (showing the first "+j.hits.length+")":"")));
   let cur=null,grp=null;j.hits.forEach(x=>{if(x.name!==cur){cur=x.name;grp=h("div");res.append(h("div",{class:"grp"},h("span",null,x.title),h("span",{class:"muted small"},String(j.hits.filter(y=>y.name===x.name).length))),grp)}
    grp.append(h("button",{class:"hit",onclick:()=>jumpTo(x.name,x.start,x.end)},h("span",{html:esc(x.before)+"<mark>"+esc(x.match)+"</mark>"+esc(x.after)})))})}catch(e){res.innerHTML="";res.append(h("p",{class:"muted"},e.message))}}
 go.onclick=run;q.addEventListener("keydown",e=>{if(e.key==="Enter")run()});
 repBtn.onclick=async()=>{const v=q.value;if(!v.trim())return;await save(true);
  const j=await api("/api/search?book="+Q(S.book)+"&q="+Q(v)+"&case="+(cs.checked?1:0)+"&word="+(ww.checked?1:0));
  if(!j.total){toast("Nothing to replace.");return}
  const ok=await confirmDlg("Replace "+plural(j.total,"match","matches")+"?","“"+v+"” becomes “"+rep.value+"” in "+plural(new Set(j.hits.map(x=>x.name)).size,"chapter","chapters")+". Quill makes a backup first.","Replace all");
  if(!ok)return;try{const r=await api("/api/replace",{book:S.book,q:v,with:rep.value,case:cs.checked,word:ww.checked});
   await refreshChapters();if(S.ch){const c=await api("/api/chapter?book="+Q(S.book)+"&name="+Q(S.ch));S.baseText=c.text;S.baseSha=c.sha;keepCaret(c.text)}
   toast("Replaced "+plural(r.replaced,"match","matches")+". The backup from just before is in Backups.");run()}catch(e){toast(e.message)}};
 setTimeout(()=>q.focus(),30);if(S.searchQ)run()}
async function jumpTo(name,start,end){if(name!==S.ch)await openChapter(name);if(S.view!=="write")setView("write");
 ed.focus();ed.setSelectionRange(start,end);ed.scrollTop=Math.max(0,caretY(start)-ed.clientHeight*.35);
 if(matchMedia("(max-width:860px)").matches)closePanel()}
/* caret position via a mirror element (textareas do not expose it) */
let MIR=null;function caretY(pos){if(!MIR){MIR=h("div",{"aria-hidden":"true"});document.body.append(MIR)}
 const cs=getComputedStyle(ed);const st=MIR.style;st.position="absolute";st.visibility="hidden";st.left="-10000px";st.top="0";
 st.whiteSpace="pre-wrap";st.overflowWrap="break-word";st.wordBreak=cs.wordBreak;st.boxSizing="border-box";st.width=ed.clientWidth+"px";
 for(const k of ["fontFamily","fontSize","fontWeight","fontStyle","lineHeight","letterSpacing","paddingTop","paddingLeft","paddingRight","tabSize","textTransform","wordSpacing"])st[k]=cs[k];
 MIR.textContent=ed.value.slice(0,pos);const m=document.createElement("span");m.textContent="​";MIR.append(m);return m.offsetTop}
let twPending=false;function typewriter(){if(!prefs().typewriter||twPending)return;twPending=true;
 requestAnimationFrame(()=>{twPending=false;const y=caretY(ed.selectionStart);ed.scrollTop=Math.max(0,y-ed.clientHeight*.42)})}
ed.addEventListener("keyup",e=>{if(e.key.startsWith("Arrow")||e.key==="PageDown"||e.key==="PageUp")typewriter()});
ed.addEventListener("click",typewriter);

async function renderLore(ai){const box=pb();S.lastLore=Date.now();if(!S.book)return;
 if(!ai)box.innerHTML='<p class="muted">Reading the book...</p>';
 let j;try{j=await api("/api/lore?book="+Q(S.book)+(ai?"&ai=1&chapter="+Q(S.ch||"")+"&model="+Q(S.aiModel||""):""))}catch(e){box.innerHTML="";box.append(h("p",{class:"muted"},e.message));return}
 if(!j.available){box.innerHTML='<p class="muted">The lore module is not installed.</p>';return}
 box.innerHTML="";
 box.append(h("p",{class:"small muted"},"Quill reads your chapters and lists who and where you have named, with a few continuity checks. It only reads: nothing here edits your text."));
 const w=h("div",{class:"section"},h("h3",null,"Continuity"));
 if(j.anomalies.length)j.anomalies.forEach(a=>w.append(h("div",{class:"warnc"},h("b",null,a.chapter+": "),a.message,a.excerpt?h("em",null,a.excerpt):null)));
 else w.append(h("p",{class:"muted small"},"Nothing out of place so far. Quill looks for people acting after their death, dates that run backwards and ages that do not add up."));
 box.append(w);
 const lookup=nm=>{S.searchQ=nm;openPanel("search")};
 const chs=h("div",{class:"section"},h("h3",null,"Characters"));
 if(j.characters.length)j.characters.forEach(c=>chs.append(h("button",{class:"tag",title:plural(c.mentions,"mention","mentions")+", first in "+c.first+". Click to see every mention.",onclick:()=>lookup(c.name)},c.name)));
 else chs.append(h("p",{class:"muted small"},"Named people show up here as you write."));
 box.append(chs);
 const pl=h("div",{class:"section"},h("h3",null,"Places"));
 if(j.locations.length)j.locations.forEach(c=>pl.append(h("button",{class:"tag",title:"Click to see every mention",onclick:()=>lookup(c.name)},c.name)));
 else pl.append(h("p",{class:"muted small"},"Places appear when you write things like “in Kettle Bay”."));
 box.append(pl);
 if(SP.avail&&prefs().spell){const sp=h("div",{class:"section"},h("h3",null,"Spelling in this chapter"));const ws=[...SP.words];
  if(ws.length){sp.append(h("p",{class:"tiny muted"},"Click a word to add it to this book's dictionary (dictionary.txt in the book folder). The names above are in it already."));
   ws.slice(0,80).forEach(w=>sp.append(h("button",{class:"tag",title:"Add to this book's dictionary",onclick:()=>addToDictionary(w)},w)))}
  else sp.append(h("p",{class:"muted small"},"No spelling slips in this chapter. Character and place names are in the book's dictionary, so they are never underlined."));
  box.append(sp)}
 if(j.timeline.length){const tl=h("div",{class:"section"},h("h3",null,"Dates"));j.timeline.forEach(t=>tl.append(h("div",{class:"small"},h("b",null,t.date)," · "+t.chapter+(t.flashback?" (flashback)":""))));box.append(tl)}
 const aiS=h("div",{class:"section"},h("h3",null,"Continuity notes from a local AI"));box.append(aiS);
 if(S.plan!=="PRO"){aiS.append(h("p",{class:"small muted"},"With Pro, a language model running on this PC (through Ollama) can read your lore list and the open chapter and ask you questions about continuity. It never writes or rewrites your story."));return}
 if(ai&&j.ai){const r=j.ai;if(r.ok){aiS.append(h("div",{class:"ai"},r.notes),h("p",{class:"tiny muted"},"Questions from "+r.model+" on this PC"+(r.seconds?", in "+r.seconds+" s":"")+". Check them against your text; they are suggestions, not facts."))}
  else aiS.append(h("p",{class:"small"},r.error||"The model did not answer."))}
 const st=await api("/api/lore/ai").catch(()=>({running:false,models:[]}));
 if(!st.running){aiS.append(h("p",{class:"small muted"},st.message||"No local AI model is running. Install Ollama (ollama.com), run “ollama pull llama3.2” once, and this button will work, fully offline."));return}
 const sel=h("select",{"aria-label":"Model"},st.models.map(m=>h("option",{value:m,selected:(S.aiModel||st.default)===m},m)));
 sel.onchange=()=>{S.aiModel=sel.value;LS.set("quill.aimodel",sel.value)};if(!S.aiModel)S.aiModel=st.default||"";
 const btn=h("button",null,ai?"Ask again":"Ask for continuity notes");
 btn.onclick=async()=>{btn.disabled=true;btn.textContent="Reading... the first answer can take a minute";await renderLore(true)};
 aiS.append(h("label",null,"Model on this PC"),sel,h("div",{style:"margin-top:8px"},btn))}

/* ---------------------------------------------------------------- library (opt-in, the only part that goes online) */
let libTab="books";
function srcLine(e){return h("p",{class:"tiny muted"},"Source: ",h("a",{href:e.url,target:"_blank",rel:"noopener"},e.url.replace(/^https:\/\//,"")),
 " \u00b7 ",h("a",{href:e.licence_url,target:"_blank",rel:"noopener"},e.licence),e.share_alike?". If you publish text from here, credit it and share it under the same licence.":"")}
async function clip(text,e){if(!S.book){toast("Open a book first.");return}try{await api("/api/research/clip",{book:S.book,text,source:e.source,
 title:e.title||e.word||"",url:e.url,licence:e.licence,licence_url:e.licence_url,share_alike:e.share_alike});toast("Clipped to this book's Research, with its source.")}catch(err){toast(err.message)}}
async function renderLibrary(){const box=pb();box.innerHTML="";let st;try{st=await api("/api/library")}catch(e){box.textContent=e.message;return}
 if(!st.enabled){box.append(h("p",null,"The Library brings public-domain books, a dictionary and short encyclopedia entries into Quill. It is off until you turn it on, because it is the only part of Quill that goes online."),
  h("p",{class:"small muted"},"When it is on, Quill asks only these places, and only when you search: Project Gutenberg and Standard Ebooks (public-domain books), Wiktionary and Wikipedia (CC BY-SA), and Open Library (book details). Nothing about you or your books is sent. Downloads go to "+st.folder+"."),
  h("button",{class:"primary",onclick:async()=>{await api("/api/library/enable",{on:true});renderLibrary()}},"Turn on the Library"));return}
 const tabs=h("div",{class:"row",style:"flex-wrap:wrap;gap:6px;margin-bottom:10px"});
 [["books","Books"],["words","Words"],["wiki","Encyclopedia"],["research","Research"]].forEach(([k,l])=>tabs.append(h("button",{class:k===libTab?"primary":"ghost",onclick:()=>{libTab=k;renderLibrary()}},l)));
 box.append(tabs);const body=h("div");box.append(body);
 if(libTab==="books"){const src=h("select",null,[["gutenberg","Project Gutenberg"],["standard","Standard Ebooks"],["openlibrary","Open Library (details only)"]].map(([v,l])=>h("option",{value:v},l)));
  const q=h("input",{type:"search",placeholder:"Title or author"}),res=h("div");const go=h("button",{class:"primary",style:"white-space:nowrap"},"Search");
  const run=async()=>{res.innerHTML='<p class="muted small">Searching...</p>';try{const j=await api("/api/library/search?src="+src.value+"&q="+Q(q.value));res.innerHTML="";
   if(!j.results.length){res.append(h("p",{class:"muted"},"Nothing found."));return}
   j.results.forEach(r=>res.append(h("div",{class:"entry"},h("b",null,r.title),h("div",{class:"small muted"},(r.author||"")+(r.year?" ("+r.year+")":"")),srcLine(r),
    h("div",{class:"acts"},r.source!=="openlibrary"?h("button",{onclick:async ev=>{ev.target.disabled=true;try{const d=await api("/api/library/download",{item:r});toast("Saved "+d.file+" in "+d.folder+".");renderLibrary()}catch(e){toast(e.message);ev.target.disabled=false}}},"Download"):null))))}catch(e){res.innerHTML="";res.append(h("p",{class:"small"},e.message))}};
  go.onclick=run;q.addEventListener("keydown",e=>{if(e.key==="Enter")run()});
  body.append(h("p",{class:"tiny muted"},"Public-domain books only. Project Gutenberg books are public domain in the USA; check the law where you live before you publish from them."),src,h("div",{class:"row",style:"margin-top:6px"},q,go),res);
  if(st.files.length){const l=h("div",{class:"section"},h("h3",null,"In your Quill Library"));st.files.forEach(f=>l.append(h("div",{class:"row small",style:"justify-content:space-between;margin:4px 0"},
   h("span",{class:"grow"},f),f.toLowerCase().endsWith(".txt")?h("button",{class:"ghost small",onclick:()=>readBook(f,0)},"Read"):h("span",{class:"tiny muted"},"EPUB: open it in your e-reader"))));body.append(l)}}
 if(libTab==="words"||libTab==="wiki"){const q=h("input",{type:"search",placeholder:libTab==="words"?"A word":"A subject"}),res=h("div"),go=h("button",{class:"primary",style:"white-space:nowrap"},"Look up");
  const run=async()=>{res.innerHTML='<p class="muted small">Looking up...</p>';try{const j=await api("/api/library/"+(libTab==="words"?"word":"wiki")+"?q="+Q(q.value));const e=j.entry;res.innerHTML="";
   if(libTab==="words"){e.definitions.forEach(d=>res.append(h("p",{class:"small"},h("i",null,d.pos+" "),d.text)));
    if(e.synonyms.length)res.append(h("h3",{class:"small muted"},"Synonyms"),h("p",null,e.synonyms.map(w=>h("button",{class:"tag",onclick:()=>{q.value=w;run()}},w))));
    if(e.etymology)res.append(h("h3",{class:"small muted"},"Etymology"),h("p",{class:"small"},e.etymology));
    res.append(srcLine(e),h("button",{class:"ghost small",onclick:()=>clip(e.word+": "+e.definitions.map(d=>d.text).join("; ")+(e.etymology?" Etymology: "+e.etymology:""),e)},"Clip to Research"))}
   else res.append(h("b",null,e.title),h("p",{class:"small"},e.extract),srcLine(e),h("button",{class:"ghost small",onclick:()=>clip(e.extract,e)},"Clip to Research"))}
   catch(err){res.innerHTML="";res.append(h("p",{class:"small"},err.message))}};
  go.onclick=run;q.addEventListener("keydown",e=>{if(e.key==="Enter")run()});body.append(h("div",{class:"row"},q,go),res)}
 if(libTab==="research"){if(!S.book){body.append(h("p",{class:"muted"},"Open a book first."));return}const j=await api("/api/research?book="+Q(S.book));
  body.append(h("p",{class:"small muted"},"Quotes you clip are kept in Research.md in the book folder, with their source. Quill never puts them into your chapters. If you paste one into a chapter, Lore shows its source and licence."));
  if(!j.clips.length)body.append(h("p",{class:"muted"},"No clips yet."));
  j.clips.slice().reverse().forEach(c=>body.append(h("div",{class:"entry"},h("div",{class:"txt"},c.text),h("div",{class:"tiny muted"},c.source_name+" \u00b7 "+c.licence+" \u00b7 "+c.date),
   h("div",{class:"acts"},h("button",{onclick:()=>copyText(c.text+"\n\n"+c.source_name+", "+(c.title?c.title+", ":"")+c.url+" ("+c.licence+", retrieved "+c.date+")")},"Copy with source"),
    h("a",{href:c.url,target:"_blank",rel:"noopener",class:"small",style:"align-self:center"},"Open source"),
    h("button",{class:"ghost danger",onclick:async()=>{await api("/api/research/remove",{book:S.book,id:c.id});renderLibrary()}},"Remove")))))}}
async function readBook(name,start){let j;try{j=await api("/api/library/read?name="+Q(name)+"&start="+start)}catch(e){toast(e.message);return}
 const m=/Gutenberg (\d+)/.exec(name);const e={source:m?"gutenberg":"standard",title:name.replace(/ \((Gutenberg \d+|Standard Ebooks)\)\.\w+$/,""),
  url:m?"https://www.gutenberg.org/ebooks/"+m[1]:"https://standardebooks.org/",licence:m?"Public domain in the USA (Project Gutenberg)":"CC0 1.0 (Standard Ebooks)",
  licence_url:m?"https://www.gutenberg.org/policy/license.html":"https://creativecommons.org/publicdomain/zero/1.0/",share_alike:false};
 const pre=h("pre",{class:"preview"},j.text);const acts=[{label:"Close"}];
 if(start>0)acts.push({label:"Previous page",onClick:()=>{setTimeout(()=>readBook(name,Math.max(0,start-20000)),20);return true}});
 if(start+20000<j.total)acts.push({label:"Next page",onClick:()=>{setTimeout(()=>readBook(name,start+20000),20);return true}});
 acts.push({label:"Clip the selected text",primary:true,onClick:async()=>{const t=String(getSelection()||"").trim();if(!t){toast("Select some text in the book first.");return false}await clip(t,e);return false}});
 openDialog(e.title+" ("+Math.round(100*start/Math.max(1,j.total))+"%)",[pre,h("p",{class:"tiny muted"},"Reading only. Clips go to Research with their source; nothing goes into your chapters.")],acts)}
async function renderGraveyard(){const box=pb();box.innerHTML='<p class="muted">Opening the graveyard...</p>';
 let j;try{j=await api("/api/graveyard?book="+Q(S.book))}catch(e){box.textContent=e.message;return}box.innerHTML="";
 box.append(h("p",{class:"small muted"},"When you delete a paragraph, Quill keeps it here (and in Deleted_Graveyard.md in the book folder), in case you want it back."));
 if(!j.entries.length){box.append(h("p",{class:"muted"},"Empty so far. Nothing you have cut is lost."));return}
 j.entries.forEach(x=>{const e=h("div",{class:"entry"},h("div",{class:"meta"},h("span",null,x.title),h("span",null,x.when)),h("div",{class:"txt"},x.text),
  h("div",{class:"acts"},h("button",{onclick:()=>{putBack(x.text)}},"Put back at the cursor"),h("button",{class:"ghost",onclick:()=>copyText(x.text)},"Copy"),
   x.text.length>420?h("button",{class:"ghost",onclick:ev=>{e.classList.toggle("open");ev.target.textContent=e.classList.contains("open")?"Show less":"Show all"}},"Show all"):null));box.append(e)})}
function putBack(t){if(!S.ch){toast("Open a chapter first.");return}const a=ed.selectionStart,b=ed.selectionEnd;const before=ed.value.slice(0,a);
 const pre=before&&!before.endsWith("\n\n")?(before.endsWith("\n")?"\n":"\n\n"):"";ed.setRangeText(pre+t+"\n\n",a,b,"end");ed.dispatchEvent(new Event("input"));ed.focus();toast("Put back into "+$("chtitle").textContent+".")}
async function copyText(t){try{await navigator.clipboard.writeText(t);toast("Copied.")}catch(e){const ta=h("textarea",null,t);document.body.append(ta);ta.select();document.execCommand("copy");ta.remove();toast("Copied.")}}

async function renderBackups(){const box=pb();box.innerHTML='<p class="muted">Listing backups...</p>';
 let j;try{j=await api("/api/backups?book="+Q(S.book))}catch(e){box.textContent=e.message;return}box.innerHTML="";box.append(await milestoneBox());
 const now=h("button",{class:"primary"},"Back up now");now.onclick=async()=>{await save(true);await api("/api/backup/now",{book:S.book});toast("Backup made.");renderBackups()};
 box.append(h("p",{class:"small muted"},"Quill keeps a backup of the whole book every couple of minutes while you write: the last 25, then one a day for a month and one a week after that. They are zip files in the book's .backups folder."),now);
 if(j.deleted.length){const d=h("div",{class:"section"},h("h3",null,"Deleted chapters"));
  j.deleted.forEach(x=>d.append(h("div",{class:"entry"},h("div",{class:"meta"},h("b",null,x.title),h("span",null,x.when)),h("div",{class:"tiny muted"},plural(x.words,"word","words")),
   h("div",{class:"acts"},h("button",{onclick:async()=>{try{const r=await api("/api/chapter/undelete",{book:S.book,file:x.file});await refreshChapters();toast("Restored at the end of the book.");renderBackups();openChapter(r.name)}catch(e){toast(e.message)}}},"Restore")))));box.append(d)}
 const l=h("div",{class:"section"},h("h3",null,"Backups of the whole book"));box.append(l);
 if(!j.snapshots.length){l.append(h("p",{class:"muted"},"The first backup appears after your first save."));return}
 j.snapshots.forEach(s=>{const e=h("div",{class:"entry"},h("div",{class:"meta"},h("b",null,s.when),h("span",null,plural(s.words,"word","words")+" · "+plural(s.chapters,"chapter","chapters"))));
  const list=h("div");const acts=h("div",{class:"acts"},h("button",{onclick:async ev=>{ev.target.disabled=true;await showSnap(s,list)}},"Show chapters"),
   h("button",{class:"ghost",onclick:()=>download("/api/backup/file?book="+Q(S.book)+"&snap="+Q(s.name),"backup.zip")},"Download zip"));
  e.append(acts,list);l.append(e)})}
async function showSnap(s,list){const j=await api("/api/backup?book="+Q(S.book)+"&snap="+Q(s.name));list.innerHTML="";
 j.chapters.forEach(c=>list.append(h("div",{class:"row small",style:"justify-content:space-between;margin-top:6px"},h("span",{class:"grow"},c.title+" ",h("span",{class:"muted"},"("+n(c.words)+")")),
  h("span",null,h("button",{class:"ghost small",onclick:()=>compareSnap(s,c)},"Compare"),h("button",{class:"ghost small",onclick:()=>previewSnap(s,c)},"Preview")))))}
async function previewSnap(s,c){const j=await api("/api/backup/chapter?book="+Q(S.book)+"&snap="+Q(s.name)+"&name="+Q(c.name));
 const ok=await openDialog(c.title+", as it was at "+s.when,[h("pre",{class:"preview"},j.text||"(empty)"),h("p",{class:"small muted"},"Restoring replaces the chapter's current text. Quill backs up the current version first, so this can be undone too.")],
  [{label:"Close"},{label:"Restore this version",primary:true,value:true}]);
 if(!ok)return;await save(true);try{const r=await api("/api/backup/restore",{book:S.book,snap:s.name,name:c.name});await refreshChapters();await openChapter(r.name);toast("Restored. The text it replaced is in the newest backup.")}catch(e){toast(e.message)}}
async function download(url,fallbackName){try{const r=await fetch(url,{headers:{"X-Quill-Token":TOKEN}});if(!r.ok){let m="Download failed";try{m=(await r.json()).error||m}catch(_){ }throw new Error(m)}
 const cd=r.headers.get("Content-Disposition")||"";let name=fallbackName;const m=/filename\*=UTF-8''([^;]+)/.exec(cd);if(m)name=decodeURIComponent(m[1]);
 const blob=await r.blob();const a=h("a",{href:URL.createObjectURL(blob),download:name});document.body.append(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},4000);return true}catch(e){toast(e.message);return false}}
async function zipDialog(){if(!S.book)return;if(S.ch)await save();const out=h("p",{class:"small",role:"status"}),drives=h("div",{class:"row",style:"gap:8px;flex-wrap:wrap;margin-top:8px"});
 const dl=h("button",{class:"primary",onclick:async()=>{out.textContent="Making the zip...";
  out.textContent=await download("/api/book/zip?book="+Q(S.book),S.book+" backup.zip")?"Downloaded. Quill read every file back from the zip and checked it before sending it.":""}},"Download zip");
 async function listDrives(){drives.replaceChildren();if(!ON_THIS_PC)return;let j;try{j=await api("/api/drives")}catch(e){return}
  if(!j.drives.length)drives.append(h("span",{class:"small muted"},"No USB stick or memory card found. Plug one in, then press Look again."));
  j.drives.forEach(d=>drives.append(h("button",{onclick:async()=>{out.textContent="Copying to "+d.label+"...";
   try{const r=await api("/api/book/zip/drive",{book:S.book,drive:d.id});out.textContent="Saved "+r.file+" on "+r.where+". "+plural(r.files,"file was","files were")+" read back and checked."}catch(e){out.textContent=e.message}}},"Save to "+d.label+" ("+d.id+")")));
  drives.append(h("button",{class:"ghost small",onclick:listDrives},"Look again"))}
 listDrives();
 await openDialog("Back up the whole book",[h("p",null,"One zip file with every chapter, book.json, notes, research, the book's dictionary and its exports. Quill reads the zip back and checks every file before it says it is done."),
  h("div",{class:"row",style:"gap:8px;flex-wrap:wrap"},dl),drives,out,
  h("p",{class:"small muted"},"To bring the book back, unzip it and put its folder into your books folder. The copies in the Backups panel stay on this PC and are not in the zip.")],[{label:"Close"}])}

/* ---------------------------------------------------------------- dialogs */
let dlgOwner=null;   // the resolver of the dialog on screen; a new dialog first ends the old one as cancelled
function openDialog(title,content,actions){return new Promise(res=>{const d=$("dlg");
 if(dlgOwner){const prev=dlgOwner;dlgOwner=null;prev(undefined)}d.innerHTML="";
 const body=h("div",{class:"dbody"},content);const acts=h("div",{class:"dacts"});let result;
 (actions||[{label:"Close"}]).forEach(a=>acts.append(h("button",{class:(a.primary?"primary":"")+(a.danger?" danger":""),onclick:async()=>{
  if(a.onClick){const r=await a.onClick();if(r===false)return;result=r===undefined?a.value:r}else result=a.value;d.close()}},a.label)));
 d.append(h("div",{class:"dlg"},h("h2",null,title),body,acts));const mine=v=>res(v);dlgOwner=mine;
 d.onclose=()=>{if(dlgOwner===mine){dlgOwner=null;mine(result)}};if(!d.open)d.showModal();
 const f=d.querySelector("input,select,textarea.field");if(f)setTimeout(()=>{f.focus();if(f.select)f.select()},20);
 else{const safe=acts.querySelector("button.primary:not(.danger)")||[...acts.querySelectorAll("button")].find(x=>!x.classList.contains("danger"));if(safe)setTimeout(()=>safe.focus(),20)}})}
async function form(title,fields,okLabel){const inputs={};const nodes=fields.map(f=>{const i=h("input",{type:"text",value:f.value||"",maxlength:"200",id:"f_"+f.id});inputs[f.id]=i;
 i.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();$("dlg").querySelector("button.primary").click()}});return [h("label",{for:"f_"+f.id},f.label),i]});
 const r=await openDialog(title,nodes,[{label:"Cancel"},{label:okLabel||"OK",primary:true,onClick:()=>{const o={};for(const k in inputs)o[k]=inputs[k].value;return o}}]);return r||null}
async function ask(title,label,value,okLabel){const r=await form(title,[{id:"v",label,value}],okLabel);return r?r.v.trim():null}
function confirmDlg(title,text,okLabel,danger){return openDialog(title,h("p",null,text),[{label:"Cancel"},{label:okLabel,primary:!danger,danger:!!danger,value:true}])}
document.querySelectorAll("[data-dlg]").forEach(b=>b.onclick=()=>{document.body.classList.remove("side");({book:bookDialog,export:exportDialog,zip:zipDialog,sources:sourcesDialog})[b.dataset.dlg]()});

async function bookDialog(){if(!S.book)return;const m=S.meta;
 const f={title:h("input",{type:"text",value:m.title,maxlength:"200",id:"b_t"}),author:h("input",{type:"text",value:m.author,maxlength:"200",id:"b_a"}),
  subtitle:h("input",{type:"text",value:m.subtitle||"",maxlength:"200",id:"b_s"}),language:h("input",{type:"text",value:m.language||"en",maxlength:"20",id:"b_l"}),
  target:h("input",{type:"number",min:"0",step:"1000",value:m.target_words||"",id:"b_w",placeholder:"For example 80000"}),
  deadline:h("input",{type:"date",value:m.deadline||"",id:"b_d"}),kind:h("select",{id:"b_k"},Object.entries(KIND_NAMES).map(([k,v])=>h("option",{value:k,selected:(m.kind||"novel")===k},v)))};
 const nums=h("input",{type:"checkbox",checked:!!m.chapter_numbers}),copy=h("input",{type:"checkbox",checked:m.copyright!==false}),scn=h("input",{type:"checkbox",checked:!!m.scene_numbers});
 f.contact=h("textarea",{class:"field",rows:"3",maxlength:"500",id:"b_c",placeholder:"Wren Marsh\n12 Quay Road, Town\nwren@example.com"});f.contact.value=m.contact||"";
 const r=await openDialog("Book details",[h("label",{for:"b_t"},"Title"),f.title,h("label",{for:"b_s"},"Subtitle (optional)"),f.subtitle,
  h("label",{for:"b_a"},"Author name, as printed on the title page"),f.author,h("label",{for:"b_k"},"Kind of book (a screenplay gets script pages and scene tools)"),f.kind,
  h("div",{class:"row"},h("div",{class:"grow"},h("label",{for:"b_l"},"Language code (en, en-GB, fr, de...)"),f.language),h("div",{class:"grow"},h("label",{for:"b_w"},"Word target for the book"),f.target),h("div",{class:"grow"},h("label",{for:"b_d"},"Finish by (optional)"),f.deadline)),
  h("label",{class:"check",style:"margin-top:14px"},nums,"Number the chapters in exports (Chapter 1, Chapter 2...)"),
  h("label",{class:"check"},copy,"Add a copyright page after the title page (needs an author name)"),
  h("label",{for:"b_c",style:"margin-top:10px"},"Contact details for a manuscript's first page (up to 6 lines: address, email, phone)"),f.contact,
  isScript()?h("label",{class:"check"},scn,"Scene numbers in the script PDF and .fdx file (numbers you type, like #12#, are kept)"):null,
  h("p",{class:"tiny muted"},"These details live in book.json inside the book folder, next to your chapters.")],
  [{label:"Cancel"},{label:"Save",primary:true,onClick:()=>({title:f.title.value,author:f.author.value,subtitle:f.subtitle.value,language:f.language.value||"en",
   target_words:+f.target.value||0,deadline:f.deadline.value||"",chapter_numbers:nums.checked,copyright:copy.checked,kind:f.kind.value,contact:f.contact.value,scene_numbers:scn.checked})}]);
 if(!r)return;try{const j=await api("/api/book/meta",{book:S.book,meta:r,today:localDay()});S.meta=j.meta;S.target=j.target||null;ring();$("booktitle").textContent=j.meta.title;document.title=j.meta.title+" - Stark Quill";studioBook();
  const b=(S.books||[]).find(b=>b.name===S.book);if(b)b.title=j.meta.title;toast("Book details saved.");if(S.view==="board")renderBoard()}catch(e){toast(e.message)}}

const FORMATS=[{id:"epub",name:"Ebook (EPUB)",note:"For Kindle, Apple Books, Kobo",pro:true},{id:"docx",name:"Word (DOCX)",note:"For editors and agents",pro:true},
 {id:"pdf",name:"PDF",note:"Print-ready pages",pro:true},{id:"html",name:"Web page",note:"Opens in any browser"},
 {id:"md",name:"Markdown",note:"Plain text with headings"},{id:"txt",name:"Plain text",note:"Just the words"}];
const SCRIPT_FORMATS=[{id:"script",name:"Screenplay PDF",note:"Script pages: Courier 12, standard margins",pro:true},{id:"fdx",name:"Script file (FDX)",note:"Script-format XML that many screenwriting apps open",pro:true},
 {id:"fountain",name:"Fountain",note:"Plain text that Fountain apps open"},{id:"txt",name:"Plain text",note:"Just the words"}];
async function exportDialog(){if(!S.book)return;await save(true);const sc=isScript(),FM=sc?SCRIPT_FORMATS:FORMATS,pro=S.plan==="PRO";
 let fmt=LS.get(sc?"quill.sfmt":"quill.fmt",""),layout="book",page=S.meta.page||"trade";
 if(!FM.find(f=>f.id===fmt))fmt=(FM.find(f=>pro||!f.pro)||FM[0]).id;  /* first time: a format this plan can export */const grid=h("div",{class:"choices"});const extra=h("div");const out=h("div");
 const bib=h("input",{type:"checkbox"});bib.checked=!!S.meta.bibliography;bib.onchange=()=>api("/api/sources/style",{book:S.book,bibliography:bib.checked}).then(()=>{S.meta.bibliography=bib.checked}).catch(e=>toast(e.message));
 const notes=sc?h("p",{class:"tiny muted"},"Comments become Fountain [[notes]] in the .fountain file and are left out of the PDF and .fdx files."):
  h("div",null,h("label",{class:"check small"},bib,"Add the list of sources after the last chapter"),h("p",{class:"tiny muted"},"Comments are left out of every export. Footnotes become numbered notes at the end of their chapter."));
 function relabel(){const b=$("dlg").querySelector(".dacts button.primary"),f=FM.find(x=>x.id===fmt);if(b&&f)b.textContent="Export "+f.name}
 function draw(){relabel();grid.innerHTML="";FM.forEach(f=>grid.append(h("button",{class:"choice"+(f.id===fmt?" on":""),"aria-pressed":f.id===fmt?"true":"false",onclick:()=>{fmt=f.id;LS.set(sc?"quill.sfmt":"quill.fmt",fmt);draw()}},
  h("b",null,f.name," ",f.pro?h("span",{class:"pill pro"},"Pro"):null),h("span",null,f.note))));
  extra.innerHTML="";if(fmt==="docx"||fmt==="pdf"){extra.append(h("label",null,"Layout"),h("div",{class:"choices"},
   h("button",{class:"choice"+(layout==="book"?" on":""),onclick:()=>{layout="book";draw()}},h("b",null,"Book"),h("span",null,"Typeset pages, like a printed novel")),
   h("button",{class:"choice"+(layout==="manuscript"?" on":""),onclick:()=>{layout="manuscript";draw()}},h("b",null,"Manuscript"),h("span",null,"Double-spaced, for agents and editors"))));
   if(layout==="book"){const sel=h("select",null,[["trade","6 × 9 in (trade paperback)"],["digest","5.5 × 8.5 in"],["a5","A5"]].map(([k,v])=>h("option",{value:k,selected:k===page},v)));
    sel.onchange=()=>{page=sel.value;api("/api/book/meta",{book:S.book,meta:{page}}).then(j=>S.meta=j.meta).catch(()=>{})};extra.append(h("label",null,"Page size"),sel)}else extra.append(h("button",{class:"ghost small",style:"margin-top:8px",onclick:()=>setTimeout(preflightDialog,30)},"Check before sending..."))}
  if(fmt==="epub"||fmt==="pdf"||fmt==="docx")extra.append(h("p",{class:"tiny muted"},"Title page, chapter openings and curly quotes are set for you. "+(S.meta.author?"":"Add your name in Book details to put it on the title page.")))
  if(fmt==="script"||fmt==="fdx")extra.append(h("p",{class:"tiny muted"},"A title page from Book details (or a Title: block at the top of the first chapter), scene numbers where you give them (#12#), and MORE / CONT'D where a speech runs over a page."))}
 draw();
 const shown=openDialog("Export or print “"+S.meta.title+"”",[h("p",{class:"small muted"},"Pick a kind of file, then Export. Quill saves it in the book's exports folder and offers it to download. For paper, use Print."),grid,extra,notes,out],
  [{label:"Close"},{label:"Print...",onClick:()=>{setTimeout(printDialog,50)}},{label:"Export",primary:true,onClick:async()=>{
  const f=FM.find(x=>x.id===fmt);if(f.pro&&!pro){out.innerHTML="";out.append(h("p",{class:"small"},f.name+" export is part of Stark Quill Pro. "+(sc?"Fountain and plain text exports are free.":"Plain text, Markdown and web page exports are free, and so is Print.")+" Pick one of those, or see Licence in the More menu for Pro."));return false}
  out.innerHTML='<p class="muted">Exporting...</p>';
  try{const j=await api("/api/export",{book:S.book,fmt,layout,page});out.innerHTML="";
   const kb=j.size>1048576?(j.size/1048576).toFixed(1)+" MB":Math.max(1,Math.round(j.size/1024))+" KB";
   out.append(h("p",null,"Saved ",h("b",null,j.file)," ("+kb+") in the book's exports folder."));
   if(j.kept_previous)out.append(h("p",{class:"small muted"},"The earlier export with that name is kept as ",h("b",null,j.kept_previous),"."));
   if(fmt==="epub"||fmt==="docx"||fmt==="pdf"||fmt==="script"||fmt==="fdx")out.append(h("p",{class:"small "+(j.problems.length?"":"muted")},j.problems.length?"Checker found "+plural(j.problems.length,"problem","problems")+": "+j.problems.slice(0,3).join("; "):"Checked the file's structure: no problems found."));
   if(j.skipped&&j.skipped.length)out.append(h("p",{class:"small muted"},"Left out "+plural(j.skipped.length,"empty chapter","empty chapters")+": "+j.skipped.join(", ")+"."));
   if(j.warnings&&j.warnings.length)out.append(h("p",{class:"small muted"},j.warnings.slice(0,2).join(" ")));
   out.append(h("div",{class:"row",style:"margin-top:8px"},h("button",{onclick:()=>download("/api/export/file?book="+Q(S.book)+"&file="+Q(j.file),j.file)},"Download"),
    h("button",{class:"ghost",onclick:async()=>{const k=await api("/api/reveal",{book:S.book,what:"exports"}).catch(e=>({error:e.message}));toast(k.opened?"Opened the exports folder.":"The file is in "+(k.folder||"the book's exports folder")+".")}},"Show in folder")))}
  catch(e){out.innerHTML="";out.append(h("p",{class:"small"},e.message))}return false}}]);relabel();await shown}
/* Print: the book (or this chapter) set as pages, sent to the browser's print window. Free; no file is written. */
async function printDialog(){if(!S.book)return;const one=!!S.ch&&S.chapters.length>1;
 const r=await openDialog("Print",[h("p",null,"Quill sets the pages like a book: a title page, then each chapter on a new page. Comments are left out."),
  h("p",{class:"small muted"},"Your computer's print window opens next, where you pick the printer and the pages.")],
  [{label:"Cancel"},...(one?[{label:"Print this chapter",value:"chapter"}]:[]),{label:"Print the book",primary:true,value:"book"}]);if(r)printBook(r)}
async function printBook(scope){if(!S.book)return;await save(true);toast("Setting the pages...");
 try{const j=await api("/api/print",{book:S.book,name:scope==="chapter"?S.ch:undefined});const old=$("printframe");if(old)old.remove();
  const f=h("iframe",{id:"printframe",title:"Pages to print","aria-hidden":"true",tabindex:"-1",style:"position:fixed;right:0;bottom:0;width:0;height:0;border:0"});
  const loaded=new Promise(res=>f.addEventListener("load",res,{once:true}));f.srcdoc=j.html;document.body.append(f);await loaded;
  toast("Print window open: "+plural(j.chapters,"chapter","chapters")+".");f.contentWindow.focus();f.contentWindow.print()}catch(e){toast(e.message,6000)}}

async function typeDialog(){const p=prefs();const preview=h("p",{style:"font-family:var(--font);font-size:var(--size);line-height:var(--lh);max-width:var(--measure);margin:10px 0"},
 "The lamp at Gullstone had burned for forty years without a night off, and Wren meant to keep it that way.");
 const pick=(k,opts)=>{const s=h("select",null,opts.map(([v,l])=>h("option",{value:v,selected:String(p[k])===String(v)},l)));s.onchange=()=>{setPref(k,s.value)};return s};
 const size=h("input",{type:"range",min:"15",max:"26",step:"1",value:String(p.size),"aria-label":"Text size"});size.oninput=()=>setPref("size",+size.value);
 const tw=h("input",{type:"checkbox",checked:!!p.typewriter}),fc=h("input",{type:"checkbox",checked:p.focusCount!==false}),sp=h("input",{type:"checkbox",checked:p.spell!==false});
 tw.onchange=()=>setPref("typewriter",tw.checked);fc.onchange=()=>setPref("focusCount",fc.checked);sp.onchange=()=>{setPref("spell",sp.checked);ed.spellcheck=!sp.checked||SP.avail===false};
 await openDialog("Page and type",[h("div",{class:"row"},h("div",{class:"grow"},h("label",null,"Theme"),pick("theme",[["","Match this computer"],["light","Paper"],["sepia","Sepia"],["dark","Night"]])),
   h("div",{class:"grow"},h("label",null,"Typeface"),pick("font",[["book","Book serif"],["georgia","Georgia"],["sans","Sans serif"],["mono","Typewriter"]]))),
  h("label",null,"Text size"),size,h("div",{class:"row"},h("div",{class:"grow"},h("label",null,"Line spacing"),pick("lh",[["1.5","Snug"],["1.7","Comfortable"],["1.9","Airy"],["2.1","Double"]])),
   h("div",{class:"grow"},h("label",null,"Line length"),pick("width",[["narrow","Short lines"],["medium","Book width"],["wide","Long lines"]]))),
  preview,h("label",{class:"check"},tw,"Keep the line I am writing in the middle of the screen"),h("label",{class:"check"},fc,"Show the word count in focus mode"),h("label",{class:"check"},sp,"Underline spelling slips (uses this computer's own dictionary and the book's, with your character and place names)"),
  h("p",{class:"tiny muted"},"These settings belong to this device. Your files are not changed.")],[{label:"Done",primary:true}])}

function showStats(st){S.stats=st;ring()}
/* The ring at the top: the daily goal when one is set; otherwise, for a book with a word target and a finish date,
   today's share of it (words added to this book since the day began). */
function ring(){const st=S.stats;if(!st)return;const p=S.target;let w,goal,text,title;
 if(!st.goal&&p&&p.status==="on"){w=Math.max(0,S.bookWords-p.day_start);goal=p.per_day;text=n(w)+" / "+n(goal);
  title="Today's target for this book: "+plural(goal,"word","words")+" to finish by "+fmtDay(p.deadline)}
 else{goal=st.goal;w=st.today;text=goal?n(w)+" / "+n(goal):n(w)+" today";title=goal?(w>=goal?"Goal met today. ":"")+plural(st.streak,"day","days")+" in a row":"Set a daily goal"}
 if(st.card&&st.card.at_risk){title=st.card.line;$("goalbtn").classList.add("risk")}else $("goalbtn").classList.remove("risk");
 const pct=goal?Math.min(1,w/goal):0;const off=String(56.55*(1-pct)),c=document.querySelector("#goalbtn .val");if(c.style.strokeDashoffset!==off)c.style.strokeDashoffset=off;
 setText($("goaltext"),text);if($("goalbtn").title!==title)$("goalbtn").title=title}
function planText(p){if(!p)return null;const d=fmtDay(p.deadline);
 if(p.status==="done")return "This book has reached its target of "+plural(p.target,"word","words")+".";
 if(p.status==="past")return "The finish date, "+d+", has passed with "+plural(p.remaining,"word","words")+" to go. Set a new date in Book details.";
 const w=Math.max(0,S.bookWords-p.day_start),left=Math.max(0,p.per_day-w);
 return "To reach "+n(p.target)+" words by "+d+": "+plural(p.per_day,"word","words")+" a day for the next "+plural(p.days_left,"day","days")+", today included. Today: "+n(w)+" written"+(left?", "+n(left)+" to go.":", so today's share is done.")}
function refreshPlan(){if(!S.book)return;api("/api/target?book="+Q(S.book)+"&today="+localDay()).then(j=>{S.target=j.target||null;ring()}).catch(()=>{})}
setInterval(()=>{if(S.target&&S.target.today!==localDay())refreshPlan()},60000);  /* a new day: a new figure (missed days raise it) */
async function goalDialog(){let st=S.stats||await api("/api/stats");const max=Math.max(1,...st.days.map(d=>d.words),st.goal||0);
 const bars=h("div",{class:"bars","aria-label":"Words per day, last two weeks"},st.days.map(d=>h("i",{class:d.met?"met":"",title:d.date+": "+n(d.words),style:"height:"+Math.max(3,Math.round(44*d.words/max))+"px"})));
 const goal=h("input",{type:"number",min:"0",step:"50",value:st.goal||"",placeholder:"For example 500",id:"g_g"});
 const book=S.meta&&S.meta.target_words?h("div",null,h("p",{class:"small"},"This book: "+n(S.bookWords)+" of "+n(S.meta.target_words)+" words ("+Math.min(100,Math.round(100*S.bookWords/S.meta.target_words))+"%)."),
  S.target?h("p",{class:"small"},planText(S.target)):h("p",{class:"small muted"},"Add a finish date in Book details and Quill works out how many words a day it takes.")):null;
 const r=await openDialog("Today",[h("p",{class:"big"},plural(st.today,"word","words")),h("p",{class:"small muted"},"Written today on this PC and any paired phone (new words minus deleted ones)."),
  bars,weekCard(st),h("p",{class:"small"},st.streak?plural(st.streak,"day","days")+" in a row"+(st.goal?" at or over your goal":" with some writing")+". Longest run so far: "+plural(st.best_streak,"day","days")+".":"Write on two days in a row and Quill starts counting your run."),
  book,h("label",{for:"g_g"},"Daily goal in words (0 for none)"),goal],[{label:"Close"},{label:"Heatmap and pace",value:"progress"},{label:"Save goal",primary:true,onClick:()=>+goal.value||0}]);
 if(r==="progress"){progressDialog();return}
 if(r===undefined||r===null)return;try{showStats(await api("/api/stats/goal",{goal:r}));toast(r?"Daily goal: "+n(r)+" words.":"Daily goal cleared.")}catch(e){toast(e.message)}}
$("goalbtn").onclick=goalDialog;

/* ---------------------------------------------------------------- craft tools: progress, outline, revise, submissions, series */
const todayQ="today="+localDay();
function txtIn(v,ph,w,on){const i=h("input",{type:"text",value:v||"",placeholder:ph||"",maxlength:"200",style:w?"width:"+w:""});i.oninput=()=>on(i.value);return i}
function areaIn(v,ph,on,rows){const t=h("textarea",{class:"field",rows:rows||3,placeholder:ph||"",maxlength:"4000",style:"width:100%"},v||"");t.oninput=()=>on(t.value);return t}
function selIn(opts,v,on){const s=h("select",{style:"width:auto"},opts.map(o=>h("option",{value:o[0],selected:o[0]===v},o[1])));s.onchange=()=>on(s.value);return s}
function endDlg(){const d=$("dlg");if(d.open)d.close()}

/* progress, pace and the finish date */
async function progressDialog(){let j;try{j=await api("/api/progress?"+(S.book?"book="+Q(S.book)+"&":"")+todayQ)}catch(e){toast(e.message);return}
 const heat=h("div",{class:"heat",role:"img",tabindex:"0","aria-label":"Words written each day, last 26 weeks"},j.heat.map(c=>h("i",{class:"l"+c.level,title:fmtDay(c.date)+": "+plural(c.words,"word","words")})));
 const rows=[["Today",plural(j.today,"word","words")],["Sessions today",n(j.sessions_today)+(j.sessions_today?" ("+plural(j.per_session_today,"word","words")+" each)":"")],
  ["Average session, all days",plural(j.per_session_all,"word","words")],["Current run",plural(j.streak,"day","days")],["Longest run",plural(j.best_streak,"day","days")],
  ["Days written",n(j.days_written)],["Pace, last 14 days",plural(j.pace,"word","words")+" a day"]];
 const goal=h("input",{type:"number",min:"0",step:"50",value:j.goal||"",id:"p_goal"});let target,dead,proj=null;
 if(j.book){const b=j.book,p=b.projection;target=h("input",{type:"number",min:"0",step:"1000",value:b.target||"",id:"p_target"});dead=h("input",{type:"date",value:b.deadline||"",id:"p_dead"});
  proj=h("p",{class:"small"},b.target?n(b.words)+" of "+n(b.target)+" words ("+Math.min(100,Math.round(100*b.words/b.target))+"%). ":"No word target for this book yet. ",
   p.status==="done"?"The target is reached.":p.status==="on"?"At "+plural(p.pace,"word","words")+" a day, you reach it about "+fmtDay(p.date)+" ("+plural(p.days,"day","days")+")."+(b.deadline&&p.date>b.deadline?" That is after your finish date, "+fmtDay(b.deadline)+".":b.deadline?" That is before your finish date.":""):
   p.reason==="pace"&&b.target?"Write on a few days and Quill can project a finish date from your pace.":"")}
 setTimeout(()=>{heat.scrollLeft=heat.scrollWidth},60);   // on a phone the newest weeks are the ones to see
 const r=await openDialog("Progress",[h("p",{class:"big"},plural(j.today,"word","words")+" today"),heat,
  h("div",{class:"heatkey tiny muted"},"Less ",[0,1,2,3,4].map(l=>h("i",{class:"l"+l}))," More"),
  h("table",{class:"keys"},rows.map(x=>h("tr",null,h("td",null,x[0]),h("td",null,x[1])))),
  j.book?[h("h3",{class:"small"},"This book"),proj,h("label",{for:"p_target"},"Word target for the book (0 for none)"),target,h("label",{for:"p_dead"},"Finish by (optional)"),dead]:null,
  h("label",{for:"p_goal"},"Daily goal in words (0 for none)"),goal,
  h("p",{class:"tiny muted"},"These figures stay on this PC. The pace and the finish date use words added over the last 14 days, so a quiet fortnight moves the date.")],
  [{label:"Close"},{label:"Save",primary:true,value:true}]);
 if(!r)return;try{showStats(await api("/api/stats/goal",{goal:+goal.value||0}));
  if(j.book){const m=await api("/api/book/meta",{book:S.book,today:localDay(),meta:{target_words:+target.value||0,deadline:dead.value||""}});S.meta=m.meta;S.target=m.target||null;ring()}
  toast("Saved.")}catch(e){toast(e.message)}}

/* plot outline: beats mapped to chapters, with a pacing view */
function syncBeats(){if(!S.book)return;api("/api/chapters?book="+Q(S.book)).then(j=>{const m={};j.chapters.forEach(c=>m[c.name]=c.beats||[]);S.chapters.forEach(c=>{c.beats=m[c.name]||[]});if(S.view==="board")renderBoard()}).catch(()=>{})}
async function outlineDialog(){if(!S.book)return;await save(true);const box=h("div");let st=null;
 const call=async(path,body)=>{try{st=body?await api(path,body):await api(path+"?book="+Q(S.book));paint();syncBeats()}catch(e){toast(e.message)}};
 function paint(){box.innerHTML="";const o=st.outline;
  if(!o){box.append(h("p",null,"Pick a structure. Each beat gets a short question, the chapters that carry it and a note. Nothing is written into your chapters."),
   ...st.templates.map(t=>h("button",{class:"tplbtn",onclick:()=>call("/api/outline",{book:S.book,template:t.id})},h("b",null,t.name),h("span",{class:"tiny muted"},plural(t.beats,"beat","beats")))));return}
  const max=Math.max(1,...o.beats.map(b=>Math.max(b.words,b.expected_words)));
  box.append(h("div",{class:"row",style:"gap:8px;margin-bottom:6px;flex-wrap:wrap"},h("b",null,o.name),h("span",{class:"sp"}),
   selIn(st.templates.map(t=>[t.id,t.name]),o.template,v=>call("/api/outline",{book:S.book,template:v})),
   h("button",{onclick:()=>call("/api/outline/clear",{book:S.book})},"Remove outline")),
   h("p",{class:"tiny muted"},"The bar is the words in the beat's chapters; the tick is the share the beat usually takes of "+(S.meta&&S.meta.target_words?"your target of "+n(o.base)+" words":"your book so far ("+n(o.base)+" words)")+". A chapter can sit in more than one beat."),
   o.unplaced.length?h("p",{class:"tiny"},"Not in any beat: "+o.unplaced.map(x=>(st.chapters.find(c=>c.name===x)||{}).title).join(", ")+"."):"");
  o.beats.forEach(b=>{const pick=h("details",null,h("summary",{class:"small"},b.titles.length?b.titles.join(", "):"Choose chapters"),
    st.chapters.map(c=>{const cb=h("input",{type:"checkbox",checked:b.chapters.includes(c.name)});
     cb.onchange=()=>{const on=new Set(b.chapters);cb.checked?on.add(c.name):on.delete(c.name);call("/api/outline/beat",{book:S.book,beat:b.id,chapters:st.chapters.map(x=>x.name).filter(x=>on.has(x))})};
     return h("label",{class:"check small"},cb,c.title+" ("+n(c.words)+")")}));
   const note=h("textarea",{class:"field",rows:2,placeholder:b.prompt,maxlength:"2000",style:"width:100%"},b.notes);note.onchange=()=>api("/api/outline/beat",{book:S.book,beat:b.id,notes:note.value}).then(j=>{st=j}).catch(e=>toast(e.message));
   box.append(h("div",{class:"beatrow"},h("div",{class:"row",style:"gap:8px"},h("b",null,b.title),h("span",{class:"tiny muted"},b.start+" to "+b.end+"%"),h("span",{class:"sp"}),
     h("span",{class:"tiny tag "+b.status},{empty:"No chapter",on:"On pace",short:"Short",long:"Long"}[b.status])),
    h("div",{class:"tiny muted"},b.prompt),pick,
    h("div",{class:"pace",title:plural(b.words,"word","words")+", aim about "+n(b.expected_words)},h("i",{style:"width:"+(100*b.words/max)+"%"}),h("u",{style:"left:"+(100*b.expected_words/max)+"%"})),
    h("div",{class:"tiny muted"},plural(b.words,"word","words")+" ("+b.share+"%), usual share "+b.expected_share+"%"),note))})}
 try{st=await api("/api/outline?book="+Q(S.book))}catch(e){toast(e.message);return}
 paint();await openDialog("Plot outline",box,[{label:"Close",primary:true}])}

/* revision checks: rules only, each can be switched off */
function revOff(){try{return JSON.parse(LS.get("quill.revoff","[]"))||[]}catch(e){return[]}}
async function reviseDialog(){if(!S.book)return;const off=new Set(revOff());const names={overused:"Overused words",phrases:"Repeated phrases",sentences:"Sentence length",adverbs:"Adverbs",filler:"Filler words",tags:"Dialogue tags",readability:"Readability",passive:"Passive voice"};
 const out=h("div"),view=h("div",{class:"revtext",hidden:true});let whole=false;
 const boxes=Object.keys(names).map(k=>{const cb=h("input",{type:"checkbox",checked:!off.has(k)});cb.onchange=()=>{cb.checked?off.delete(k):off.add(k);LS.set("quill.revoff",JSON.stringify([...off]))};return h("label",{class:"check small"},cb,names[k])});
 const wb=h("input",{type:"checkbox"});wb.onchange=()=>{whole=wb.checked};
 async function run(){out.innerHTML="";view.hidden=true;const text=ed.value;let j;
  try{j=await api("/api/revise",whole?{book:S.book,off:[...off]}:{text,off:[...off]})}catch(e){out.append(h("p",null,e.message));return}
  const c=j.checks,card=(k,...kids)=>out.append(h("div",{class:"revcard"},h("b",null,names[k]),...kids));
  out.append(h("p",{class:"small muted"},plural(j.words,"word","words")+" checked"+(whole?" across the whole book. Highlights show for one chapter at a time.":".")));
  if(c.overused)card("overused",c.overused.items.length?h("p",{class:"small"},c.overused.items.map(x=>x.word+" "+x.count+" (about "+x.per_1000+" in 1,000)").join(" · ")):h("p",{class:"small muted"},"No word stands out."));
  if(c.phrases)card("phrases",c.phrases.items.length?h("p",{class:"small"},c.phrases.items.map(x=>'"'+x.phrase+'" '+x.count+"x").join(" · ")):h("p",{class:"small muted"},"No phrase repeats."));
  if(c.sentences&&c.sentences.count){const s=c.sentences,mx=Math.max(1,...s.buckets.map(b=>b.count));
   card("sentences",h("p",{class:"small"},plural(s.count,"sentence","sentences")+", average "+s.mean+" words, from "+s.shortest+" to "+s.longest+". "+s.verdict),
    h("div",{class:"bars"},s.buckets.map(b=>h("i",{title:b.label+" words: "+b.count,style:"height:"+Math.max(3,Math.round(44*b.count/mx))+"px"}))),h("div",{class:"tiny muted"},s.buckets.map(b=>b.label).join(" / ")+" words"),
    s.runs.length?h("p",{class:"small"},plural(s.runs.length,"stretch","stretches")+" of four or more sentences with nearly the same length."):null,s.long?h("p",{class:"small"},plural(s.long,"sentence","sentences")+" over 40 words."):null)}
  if(c.adverbs)card("adverbs",h("p",{class:"small"},plural(c.adverbs.count,"adverb","adverbs")+" ("+c.adverbs.per_1000+" in 1,000 words). "+c.adverbs.items.map(x=>x.word+" "+x.count).join(" · ")));
  if(c.readability&&c.readability.words){const r=c.readability;card("readability",h("p",{class:"small"},"Reading ease "+r.ease+" of 100 ("+r.band+"), about school grade "+r.grade+". "+r.words_per_sentence+" words a sentence, "+r.complex_share+"% long words. About "+r.reading_minutes+" minutes to read, "+r.speaking_minutes+" to read aloud."),h("p",{class:"tiny muted"},"Flesch formulas, made for English. A guide for essays and reports; fiction often reads well at any grade."))}
  if(c.passive)card("passive",h("p",{class:"small"},c.passive.count?plural(c.passive.count,"passive phrase","passive phrases")+" ("+c.passive.per_1000+" in 1,000 words), "+c.passive.with_by+" with by. Passive is fine when the doer does not matter; name the doer when they do.":"No passive phrases found."));
  if(c.filler)card("filler",h("p",{class:"small"},c.filler.count?c.filler.items.map(x=>x.word+" "+x.count).join(" · "):"None found."));
  if(c.tags){const t=c.tags;card("tags",h("p",{class:"small"},"said "+t.said+", asked "+t.asked+(t.other.length?", other: "+t.other.map(x=>x.word+" "+x.count).join(", "):"")+(t.plain_share!=null?". "+t.plain_share+"% of tags are said or asked.":".")),
    t.adverb_tags?h("p",{class:"small"},plural(t.adverb_tags,"tag has","tags have")+" an adverb on it."):null,t.action_as_tag.length?h("p",{class:"small"},"Actions used as speech tags: "+t.action_as_tag.join(", ")+". Smiling is not speaking; end the quote with a full stop."):null)}
  if(!whole&&j.spans.length){view.hidden=false;view.innerHTML="";let at=0;j.spans.forEach(s=>{if(s[0]>at)view.append(text.slice(at,s[0]));view.append(h("mark",{class:"rv-"+s[2],title:s[3]},text.slice(s[0],s[1])));at=s[1]});view.append(text.slice(at));
   out.append(h("div",{class:"tiny muted"},"Highlights: ",h("mark",{class:"rv-overused"},"overused"),h("mark",{class:"rv-phrase"},"phrase"),h("mark",{class:"rv-adverb"},"adverb"),h("mark",{class:"rv-filler"},"filler"),h("mark",{class:"rv-tag"},"tag"),h("mark",{class:"rv-run"},"same length"),h("mark",{class:"rv-long"},"long"),h("mark",{class:"rv-passive"},"passive")))}}
 await save(true);
 openDialog("Revise",[h("p",{class:"small muted"},"Plain rules, no AI, nothing leaves this PC. These are prompts for a second look, not corrections: your ear decides. Untick a check to switch it off."),
  h("div",{class:"row",style:"gap:12px;flex-wrap:wrap"},boxes),h("div",{class:"row",style:"gap:12px;margin:8px 0"},h("button",{class:"primary",onclick:run},"Check this chapter"),h("label",{class:"check small"},wb,"Whole book (no highlights)")),out,view],[{label:"Close"}]);run()}

/* submissions: where the book was sent, and the letters to send */
async function subsDialog(){if(!S.book)return;
 for(;;){let st;try{st=await api("/api/submissions?book="+Q(S.book)+"&"+todayQ)}catch(e){toast(e.message);return}
  let next=null;const lab=Object.fromEntries(st.statuses.map(s=>[s.id,s.label])),sm=st.summary;
  const rows=st.items.slice().sort((a,b)=>(b.sent||"").localeCompare(a.sent||"")).map(it=>h("div",{class:"subrow"+(sm.due.includes(it.id)?" due":"")},
   h("div",{class:"row",style:"gap:8px;flex-wrap:wrap"},h("b",null,it.name),h("span",{class:"tiny muted"},it.kind),h("span",{class:"sp"}),h("span",{class:"tiny tag"},lab[it.status])),
   h("div",{class:"tiny muted"},(it.sent?"Sent "+fmtDay(it.sent):"Not sent yet")+(it.response_date?" · answer "+fmtDay(it.response_date):"")+(it.follow_up&&sm.due.includes(it.id)?" · follow up now":it.follow_up?" · follow up "+fmtDay(it.follow_up):"")),
   it.response?h("div",{class:"small"},it.response):null,
   h("div",{class:"row",style:"gap:6px"},h("button",{onclick:()=>{next={edit:it};endDlg()}},"Edit"),h("button",{class:"danger",onclick:()=>{next={del:it};endDlg()}},"Delete"))));
  const r=await openDialog("Submissions",[h("p",{class:"small"},sm.total?plural(sm.total,"entry","entries")+": "+Object.keys(sm.counts).filter(k=>sm.counts[k]).map(k=>sm.counts[k]+" "+lab[k].toLowerCase()).join(", ")+"."+(sm.avg_wait_days!=null?" Answers took "+plural(sm.avg_wait_days,"day","days")+" on average.":"")+(sm.due.length?" "+plural(sm.due.length,"follow-up is","follow-ups are")+" due.":""):"Track agents, publishers and contests: what you sent, when, and what came back. It is kept in this book's folder as Submissions.json."),
   rows.length?rows:null],[{label:"Close"},{label:"Query letter",value:"query"},{label:"Synopsis",value:"synopsis"},{label:"Add entry",primary:true,value:"add"}]);
  if(next&&next.del){if(await confirmDlg("Delete entry","Delete "+next.del.name+" from the tracker?","Delete",true))try{await api("/api/submissions/delete",{book:S.book,id:next.del.id,today:localDay()})}catch(e){toast(e.message)}continue}
  if(next&&next.edit||r==="add"){const ok=await subEdit(next&&next.edit,st);if(ok===undefined)continue;continue}
  if(r==="query"||r==="synopsis"){await letterDialog(r);continue}
  return}}
async function subEdit(it,st){const o=Object.assign({kind:"agent",status:"planned",name:"",contact:"",sent:"",follow_up:"",response_date:"",response:"",notes:""},it||{});
 const f=(label,node)=>[h("label",null,label),node];
 const r=await openDialog(it?"Edit entry":"New entry",[f("Name",txtIn(o.name,"Agent, publisher or contest",null,v=>o.name=v)),
  f("Kind",selIn(st.kinds.map(k=>[k,k]),o.kind,v=>o.kind=v)),f("Status",selIn(st.statuses.map(s=>[s.id,s.label]),o.status,v=>o.status=v)),
  f("Contact or website",txtIn(o.contact,"",null,v=>o.contact=v)),
  f("Date sent",h("input",{type:"date",value:o.sent,onchange:e=>o.sent=e.target.value})),f("Follow up on",h("input",{type:"date",value:o.follow_up,onchange:e=>o.follow_up=e.target.value})),
  f("Date of their answer",h("input",{type:"date",value:o.response_date,onchange:e=>o.response_date=e.target.value})),f("Their answer",areaIn(o.response,"",v=>o.response=v)),f("Notes",areaIn(o.notes,"",v=>o.notes=v))],
  [{label:"Cancel"},{label:"Save",primary:true,onClick:async()=>{try{await api("/api/submissions/save",{book:S.book,today:localDay(),item:o});return true}catch(e){toast(e.message);return false}}}]);return r}
async function letterDialog(kind){let j;
 for(;;){try{j=await api("/api/letter?book="+Q(S.book)+"&kind="+kind)}catch(e){toast(e.message);return}
  const vals=Object.assign({},j.values);const long=new Set(["reason","hook","stakes","bio","comps","setup","inciting","rising","midpoint","crisis","climax","ending"]);
  const preview=h("textarea",{class:"field",readonly:true,rows:12,style:"width:100%"},j.text);let act=null;
  const res=await openDialog(kind==="query"?"Query letter":"Synopsis",[h("p",{class:"small muted"},"Fill in your own words. Quill only places them in a standard layout; it writes nothing for you. Empty fields show in [brackets]. Agents differ, so check each one's guidelines."),
   j.fields.map(f=>[h("label",null,f.label),long.has(f.id)?areaIn(vals[f.id],f.hint,v=>vals[f.id]=v,3):txtIn(vals[f.id],f.hint,null,v=>vals[f.id]=v)]),
   h("h3",{class:"small"},"Preview ("+plural(j.words,"word","words")+")"),h("p",{class:"tiny muted"},j.missing.length?"Still empty: "+j.missing.join(", ")+".":"Every field has text."),preview],
   [{label:"Close"},{label:"Copy",value:"copy",onClick:async()=>{await copyText(preview.value);return false}},{label:"Save and preview",primary:true,value:"save"}]);
  if(res==="save"||res==="copy"){try{await api("/api/letter",{book:S.book,kind,fields:vals})}catch(e){toast(e.message);return}continue}
  return}}

/* series bible: characters, places and a timeline shared by several books */
async function seriesDialog(){let st;
 for(;;){try{st=await api("/api/series")}catch(e){toast(e.message);return}
  let next=null;const rows=st.series.map(s=>h("div",{class:"subrow"},h("div",{class:"row",style:"gap:8px"},h("b",null,s.name),h("span",{class:"sp"}),h("span",{class:"tiny muted"},plural(s.books.length,"book","books")+", "+plural(s.characters.length,"character","characters"))),
   h("div",{class:"row",style:"gap:6px"},h("button",{onclick:()=>{next=s;endDlg()}},"Open"))));
  const r=await openDialog("Series bible",[h("p",{class:"small muted"},"A series groups books in your books folder and keeps one list of characters, places and dates for all of them. Quill can fill the lists from the Lore panel's findings and check continuity across every book in order."),rows.length?rows:h("p",{class:"small"},"No series yet."),],
   [{label:"Close"},{label:"New series",primary:true,value:"new"}]);
  if(r==="new"){const nm=await ask("New series","Series name","","Create");if(nm){try{const j=await api("/api/series/save",{series:{name:nm,books:S.book?[S.book]:[]}});await seriesEdit(j.series.find(s=>s.id===j.saved),j.books)}catch(e){toast(e.message)}}continue}
  if(next){await seriesEdit(next,st.books);continue}
  return}}
async function seriesEdit(s,books){let cur=JSON.parse(JSON.stringify(s)),msg=h("div"),loop=true;
 while(loop){loop=false;let again=false;const box=h("div");
  const paintBooks=()=>{bk.innerHTML="";const sel=cur.books.map(b=>books.find(x=>x.name===b)).filter(Boolean),rest=books.filter(b=>!cur.books.includes(b.name));
   sel.forEach((b,i)=>bk.append(h("div",{class:"row",style:"gap:6px"},h("span",null,(i+1)+". "+b.title),h("span",{class:"sp"}),
    h("button",{class:"ghost","aria-label":"Earlier",disabled:i===0,onclick:()=>{const a=cur.books;[a[i-1],a[i]]=[a[i],a[i-1]];paintBooks()}},"↑"),
    h("button",{class:"ghost","aria-label":"Later",disabled:i===sel.length-1,onclick:()=>{const a=cur.books;[a[i+1],a[i]]=[a[i],a[i+1]];paintBooks()}},"↓"),
    h("button",{class:"ghost",onclick:()=>{cur.books=cur.books.filter(x=>x!==b.name);paintBooks()}},"Remove"))));
   rest.forEach(b=>bk.append(h("div",{class:"row",style:"gap:6px"},h("span",{class:"muted"},b.title),h("span",{class:"sp"}),h("button",{class:"ghost",onclick:()=>{cur.books.push(b.name);paintBooks()}},"Add"))))};
  const bk=h("div");
  const list=(key,title,fields)=>{const wrap=h("div");const paint=()=>{wrap.innerHTML="";cur[key].forEach((r,i)=>{const row=h("div",{class:"subrow"},fields(r),h("div",{class:"row",style:"gap:6px"},r.books&&r.books.length?h("span",{class:"tiny muted"},"In: "+r.books.map(b=>(books.find(x=>x.name===b)||{title:b}).title).join(", ")):null,h("span",{class:"sp"}),h("button",{class:"ghost",onclick:()=>{cur[key].splice(i,1);paint()}},"Remove")));wrap.append(row)});
    wrap.append(h("button",{onclick:()=>{cur[key].push(key==="timeline"?{when:"",text:"",book:""}:{name:"",notes:"",books:[],aliases:[]});paint()}},"Add "+(key==="characters"?"character":key==="places"?"place":"event")))};paint();return [h("h3",{class:"small"},title),wrap]};
  const nameIn=txtIn(cur.name,"Series name",null,v=>cur.name=v);paintBooks();
  box.append(h("label",null,"Series name"),nameIn,h("h3",{class:"small"},"Books, in reading order"),bk,
   ...list("characters","Characters",r=>[txtIn(r.name,"Name","100%",v=>r.name=v),txtIn((r.aliases||[]).join(", "),"Other names, separated by commas","100%",v=>r.aliases=v.split(",").map(x=>x.trim()).filter(Boolean)),areaIn(r.notes,"Notes: look, voice, secrets",v=>r.notes=v,2)]),
   ...list("places","Places",r=>[txtIn(r.name,"Name","100%",v=>r.name=v),areaIn(r.notes,"Notes",v=>r.notes=v,2)]),
   ...list("timeline","Timeline",r=>[txtIn(r.when,"When (a year or date)","100%",v=>r.when=v),areaIn(r.text,"What happens",v=>r.text=v,2),selIn([["","Any book"]].concat(books.filter(b=>cur.books.includes(b.name)).map(b=>[b.name,b.title])),r.book||"",v=>r.book=v)]),msg);
  const res=await openDialog("Series: "+s.name,box,[{label:"Close"},{label:"Delete series",danger:true,value:"del"},{label:"Check continuity",value:"check"},{label:"Fill from Lore",value:"import"},{label:"Save",primary:true,value:"save"}]);
  if(res==="del"){if(await confirmDlg("Delete series","Delete the series bible for "+cur.name+"? Your books are not touched.","Delete",true))try{await api("/api/series/delete",{id:cur.id})}catch(e){toast(e.message)}return}
  if(res==="save"||res==="import"||res==="check"){try{const j=await api("/api/series/save",{series:cur});cur=j.series.find(x=>x.id===j.saved);s=cur;books=j.books;
    if(res==="import"){const k=await api("/api/series/import",{id:cur.id});cur=k.series.find(x=>x.id===cur.id);toast(k.added?"Added "+k.added+" from Lore.":"Nothing new in Lore.");loop=true}
    else if(res==="check"){const k=await api("/api/series/check?id="+Q(cur.id));await seriesCheckDialog(k);loop=true}
    else toast("Saved.")}catch(e){toast(e.message);loop=res!=="save"}}}}
function seriesCheckDialog(k){return openDialog("Continuity across the series",[h("p",{class:"small"},k.books+" "+(k.books===1?"book":"books")+" read in order as one story."),
 k.anomalies.length?k.anomalies.map(a=>h("div",{class:"subrow"},h("b",{class:"small"},a.chapter),h("div",{class:"small"},a.message),a.excerpt?h("div",{class:"tiny muted"},a.excerpt):null)):h("p",{class:"small"},"No slips found. Quill checks dates, ages and people who act after they have died."),
 h("h3",{class:"small"},"Who appears where"),k.appearances.length?h("table",{class:"keys"},k.appearances.map(p=>h("tr",null,h("td",null,p.name),h("td",null,p.books.join(", ")||"not found in the text")))):h("p",{class:"small muted"},"Add characters to the bible to see them here.")],[{label:"Close"}])}

async function licenceDialog(){let j;try{j=await api("/api/license")}catch(e){toast(e.message);return}
 const parts=[h("p",null,h("b",null,j.plan==="PRO"?"Stark Quill Pro":"Stark Quill Free")),h("p",{class:"small muted"},j.message||"")];
 const acts=[{label:"Close"}];
 if(j.plan==="PRO"){if(j.term_days)parts.push(h("p",{class:"small"},"After that, Quill switches to the Free plan and every book stays yours."));
  else if(j.expires)parts.push(h("p",{class:"small"},"Paid until "+j.expires+". After that you keep 7 days of Pro while a renewal key arrives."));
  acts.unshift({label:"Turn Pro off on this PC",danger:true,onClick:async()=>{const k=await api("/api/license/deactivate",{});toast("Pro is off on this PC. Paste your key again any time.");boot(true);return true}})}
 else{const key=h("textarea",{class:"field",rows:"3",placeholder:"GQ1.xxxxx.yyyyy","aria-label":"Licence key",spellcheck:"false"});const msg=h("p",{class:"small"});
  parts.push(h("label",null,"Paste your Pro key (one line that starts with GQ1)"),key,msg,
   h("p",{class:"small muted"},"Pro adds EPUB, Word and PDF export, writing from your phone on your home Wi-Fi, and continuity notes from a local AI model. If Pro ends, Quill goes back to Free and every book stays yours."));
  if(S.status&&S.status.links&&S.status.links.pro_url)parts.push(h("p",{class:"small"},h("a",{href:S.status.links.pro_url,target:"_blank",rel:"noopener"},"Get a Pro key")));
  acts.push({label:"Activate",primary:true,onClick:async()=>{try{const r=await api("/api/license/activate",{key:key.value});msg.textContent=r.message;
   if(r.activated){toast("Pro is on. Thank you.");boot(true);return true}return false}catch(e){msg.textContent=e.message;return false}}})}
 if(j.device_code)parts.push(h("p",{class:"tiny muted"},"Support code for this PC: "+j.device_code+" (a one-way fingerprint; it reveals nothing about you)."));
 await openDialog("Licence",parts,acts)}
$("planbtn").onclick=licenceDialog;

function aboutDialog(){const st=S.status||{};const keys=[["Save now","Ctrl+S"],["Search the book","Ctrl+Shift+F"],["Focus","Ctrl+Shift+Enter"],
 ["Italic / bold","Ctrl+I / Ctrl+B"],["Quick search: chapters, books, commands","Ctrl+K"],["Add a comment","Ctrl+Alt+M"],["Screenplay: change the line's element","Tab / Shift+Tab"],["Previous / next chapter","Alt+↑ / Alt+↓"],["Close a panel or leave focus","Esc"]];
 const lk=[...document.querySelectorAll("#welcome .lockup, #welcome .endorse-text")].map(x=>x.cloneNode(true));
 openDialog("About Stark Quill",[lk[0]||null,lk[1]||null,h("p",null,h("b",null,"Version "+VERSION)),
  h("p",null,"Your books are plain text files in ",h("b",null,st.books_folder||"Documents\\My_Books"),". Each save is written to a journal first and checked before it replaces the file, a backup of the book is taken as you work, and paragraphs you delete wait in the graveyard."),
  h("p",{class:"small muted"},"Quill does not upload your writing. It goes online only to look for updates, if you allow it, and the dictation button in Chrome sends audio to Google."),
  updatesBlock(),h("p",{class:"small"},h("button",{class:"ghost small",onclick:()=>setTimeout(guideDialog,30)},"Getting started guide")),
  h("h3",{class:"small muted",style:"margin:14px 0 4px"},"Keyboard"),h("table",{class:"keys"},keys.map(([a,b])=>h("tr",null,h("td",null,a),h("td",null,h("kbd",null,b))))),
  st.links&&st.links.support_email?h("p",{class:"small"},"Questions: "+st.links.support_email):h("p",{class:"small muted"},"Help: SUPPORT.md in the folder where Quill is installed."),
  h("p",{class:"tiny muted"},"Credits: the Quill mark and the Stark lettering come from the Stark brand kit. Text is set in the fonts already on your computer; PDF exports embed one of them (Palatino Linotype where Windows has it)."),
  lk[2]||null],[{label:"Close",primary:true}])}
$("aboutbtn").onclick=aboutDialog;
function updatesBlock(){const box=h("div",{class:"small"});const cb=h("input",{type:"checkbox"});const line=h("span",{class:"muted"});
 const now=h("button",{class:"ghost small"},"Check now");
 api("/api/updates").then(u=>{cb.checked=u.consent===true;line.textContent=u.last_check?" Last checked "+new Date(u.last_check*1000).toLocaleString()+(u.detail?": "+u.detail:"."):" Not checked yet.";
  now.onclick=async()=>{now.disabled=true;await api("/api/updates",{check_now:true}).catch(()=>{});toast("Checking for updates. A new version is installed the next time Quill starts.");}}).catch(()=>{box.hidden=true});
 cb.onchange=()=>api("/api/updates",{consent:cb.checked}).then(()=>toast(cb.checked?"Quill will check for updates once a day.":"Automatic update checks are off.")).catch(e=>toast(e.message));
 box.append(h("label",{class:"check"},cb,"Check for updates automatically (once a day, signed updates only)"),h("div",null,line," ",now));return box}
async function askUpdates(){if(!S.status||S.status.updates_consent!==null&&S.status.updates_consent!==undefined)return;
 while($("dlg").open)await new Promise(r=>setTimeout(r,500));   // never on top of something the writer opened
 const yes=await openDialog("Check for updates automatically?",[h("p",null,"Quill can look for a newer version once a day. It asks the Stark Quill update page on ams513.github.io whether one exists, and sends nothing about you or your books; like any web visit, the request shows your internet address to GitHub."),
  h("p",null,"Each update is checked against the Stark signature before it is installed. If anything does not match, Quill keeps the version you have."),
  h("p",{class:"small muted"},"You can change this any time under About.")],
  [{label:"No, I will check myself",value:false},{label:"Yes, check for me",primary:true,value:true}]);
 if(yes===undefined)return;            // closed without an answer: ask again next time
 try{await api("/api/updates",{consent:yes});S.status.updates_consent=yes}catch(e){}}

/* ---------------------------------------------------------------- menus */
function showMenu(anchor,items){const m=$("pop");m.innerHTML="";
 items.forEach(it=>{if(it==="-"){m.append(h("hr"));return}const b=h("button",{role:"menuitem",class:it.danger?"danger":"",disabled:!!it.disabled,onclick:()=>{hideMenu();it.act()}},
  h("span",null,it.label),it.hint?h("kbd",null,it.hint):null);m.append(b)});
 m.hidden=false;const r=anchor.getBoundingClientRect();const w=m.offsetWidth,hh=m.offsetHeight;
 m.style.left=Math.max(8,Math.min(innerWidth-w-8,r.right-w))+"px";m.style.top=(r.bottom+hh+8>innerHeight?Math.max(8,r.top-hh-4):r.bottom+4)+"px";
 const f=m.querySelector("button:not([disabled])");if(f)f.focus()}
function hideMenu(){$("pop").hidden=true}
document.addEventListener("pointerdown",e=>{if(!$("pop").hidden&&!$("pop").contains(e.target)&&!e.target.closest("#more,.dots,#bookbtn"))hideMenu()});
$("more").onclick=e=>{const small=matchMedia("(max-width:760px)").matches;const items=[];
 if(small)items.push({label:"Search the book",act:()=>openPanel("search")},{label:"Lore",act:()=>openPanel("lore")},{label:"Focus",act:toggleFocus},"-");
 items.push({label:"Export...",act:exportDialog},{label:"Print...",hint:"Ctrl+P",act:printDialog},"-");
 items.push({label:dictating?"Stop dictation":"Dictate",act:toggleMic},{label:"Write from your phone",hint:S.plan==="PRO"?"":"Pro",act:phoneDialog},"-",
  {label:SAY.on?"Stop reading aloud":"Read aloud from the cursor",act:readAloud},{label:"Page and type...",act:typeDialog},{label:"Today and goals...",act:goalDialog},{label:"Progress and finish date...",act:progressDialog},{label:"Plot outline...",act:outlineDialog},{label:"Chapter outline and targets",hint:"Ctrl+Alt+O",act:setOutlineView},{label:$("refpane").hidden?"Reference pane":"Close reference pane",hint:"Ctrl+Alt+R",act:refToggle},{label:"Make a milestone...",hint:"Ctrl+Alt+S",act:milestoneNew},{label:"Plot grid and plotlines...",act:plotGridDialog},{label:"Revise...",act:reviseDialog},{label:"Story X-ray...",act:xrayDialog},{label:"Voice and rhythm...",act:voiceDialog},{label:"Print cover size...",act:coverDialog},{label:"Before you send it...",hint:"Ctrl+Alt+P",act:preflightDialog},{label:"Collections...",act:collectionsDialog},{label:"Rescue sheet...",act:rescueDialog},{label:"Submissions...",act:subsDialog},{label:"Series bible...",act:seriesDialog},"-",
  {label:SPR.on?"Writing sprint (running)":"Writing sprint...",act:sprintDialog},{label:"Add a comment",hint:"Ctrl+Alt+M",act:addComment},{label:"Insert a footnote...",act:footnoteDialog},{label:"Sources and citations...",act:sourcesDialog},
  ...(isScript()?[{label:"Script preview",act:scriptPreview}]:[]),{label:"Quick search",hint:"Ctrl+K",act:palOpen},{label:"Import a manuscript...",act:importDialog},{label:S.view==="board"?"Back to writing":"Corkboard",act:()=>setView(S.view==="board"?"write":"board")},"-",
  ...(S.status&&S.status.schools?[{label:"Schools...",act:schoolsDialog}]:[]),{label:"Licence...",act:licenceDialog},{label:"Getting started",act:guideDialog},{label:"About",act:aboutDialog});showMenu(e.currentTarget,items)};
$("menu").onclick=()=>document.body.classList.toggle("side");
$("exportbtn").onclick=()=>exportDialog();

/* ---------------------------------------------------------------- focus */
let calmT=null;function calmSoon(){if(!document.body.classList.contains("focus"))return;document.body.classList.remove("calm");clearTimeout(calmT);calmT=setTimeout(()=>document.body.classList.add("calm"),2200)}
function toggleFocus(on){const want=on===undefined?!document.body.classList.contains("focus"):on;document.body.classList.toggle("focus",want);
 if(want){if(S.view!=="write")setView("write");calmSoon();try{if(document.documentElement.requestFullscreen&&!document.fullscreenElement)document.documentElement.requestFullscreen().catch(()=>{})}catch(e){}}
 else{document.body.classList.remove("calm");try{if(document.fullscreenElement)document.exitFullscreen().catch(()=>{})}catch(e){}}ed.focus()}
$("focusbtn").onclick=()=>toggleFocus();$("fexit").onclick=()=>toggleFocus(false);
document.addEventListener("mousemove",()=>{if(document.body.classList.contains("focus"))calmSoon()});
document.addEventListener("fullscreenchange",()=>{if(!document.fullscreenElement&&document.body.classList.contains("focus"))toggleFocus(false)});

/* ---------------------------------------------------------------- keyboard */
function wrapSel(mk){const a=ed.selectionStart,b=ed.selectionEnd,t=ed.value.slice(a,b);ed.setRangeText(mk+t+mk,a,b,t?"select":"end");if(!t)ed.setSelectionRange(a+mk.length,a+mk.length);ed.dispatchEvent(new Event("input"))}
document.addEventListener("keydown",e=>{const mod=e.ctrlKey||e.metaKey;
 if(e.key==="Escape"&&SAY.on){sayStop();return}
 if(e.key==="Escape"){if(!$("pop").hidden){hideMenu();return}if(document.body.classList.contains("focus")){toggleFocus(false);return}if(S.panel&&!$("dlg").open){closePanel();return}}
 if(mod&&!e.shiftKey&&e.key.toLowerCase()==="s"){e.preventDefault();save(true);return}
 if(mod&&!e.shiftKey&&!e.altKey&&e.key.toLowerCase()==="p"&&S.book){e.preventDefault();printDialog();return}
 if(mod&&e.shiftKey&&e.key.toLowerCase()==="f"){e.preventDefault();openPanel("search");return}
 if(mod&&e.shiftKey&&e.key==="Enter"){e.preventDefault();toggleFocus();return}
 if(e.target===ed&&mod&&!e.shiftKey&&e.key.toLowerCase()==="i"){e.preventDefault();wrapSel("*");return}
 if(e.target===ed&&mod&&!e.shiftKey&&e.key.toLowerCase()==="b"){e.preventDefault();wrapSel("**");return}
 if(e.altKey&&(e.key==="ArrowUp"||e.key==="ArrowDown")){e.preventDefault();const i=S.chapters.findIndex(c=>c.name===S.ch)+(e.key==="ArrowUp"?-1:1);if(S.chapters[i])openChapter(S.chapters[i].name)}});

/* ---------------------------------------------------------------- wake lock + dictation */
let wl=null;async function wake(){try{if("wakeLock" in navigator&&!document.hidden&&!wl){wl=await navigator.wakeLock.request("screen");wl.addEventListener("release",()=>wl=null)}}catch(e){}}
ed.addEventListener("focus",wake);
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;let rec=null,dictating=false;
function flushDictation(){const q=LS.get("quill.dict",[]);if(!q.length||!S.ch)return;const a=ed.selectionStart;const ins=q.join(" ")+" ";
 ed.setRangeText(ins,a,ed.selectionEnd,"end");LS.del("quill.dict");ed.dispatchEvent(new Event("input"))}
function toggleMic(){if(!SR){openDialog("Dictation",h("p",null,"This browser does not offer dictation here. It needs Chrome, Edge or Safari, and on a phone the secure (https) address. Your keyboard's own voice typing works everywhere."));return}
 if(dictating){dictating=false;rec&&rec.stop();toast("Dictation stopped.");return}
 rec=new SR();rec.continuous=true;rec.interimResults=false;rec.lang=(S.meta&&S.meta.language)||navigator.language||"en-US";
 rec.onresult=e=>{for(let i=e.resultIndex;i<e.results.length;i++)if(e.results[i].isFinal){const q=LS.get("quill.dict",[]);q.push(e.results[i][0].transcript.trim());LS.set("quill.dict",q)}flushDictation()};
 rec.onend=()=>{if(dictating)try{rec.start()}catch(e){}};rec.onerror=e=>{if(e.error==="not-allowed"){dictating=false;toast("Microphone access was refused.")}};
 dictating=true;ed.focus();rec.start();toast("Listening. Chrome sends dictation audio to Google; open the menu again to stop.",5000)}

/* ---------------------------------------------------------------- phone */
async function phoneDialog(){if(S.plan!=="PRO"){openDialog("Write from your phone",[h("p",null,"With Pro you can open this book on your phone over your home Wi-Fi and keep writing on the sofa. The text stays on your PC; the phone just edits it."),
  h("p",{class:"small muted"},"Activate a Pro key under Licence to switch it on.")],[{label:"Close"},{label:"Licence...",primary:true,onClick:()=>{setTimeout(licenceDialog,50)}}]);return}
 let j;try{j=await api("/api/lan",{enable:true})}catch(e){toast(e.message);return}
 const parts=[h("p",{class:"small"},"Your phone needs to be on the same Wi-Fi as this PC.")];
 if(j.https)parts.push(h("p",null,h("b",null,"First time on this phone: "),"scan this code to trust your PC's certificate"+(j.ca_created?" (it is new, so every phone needs this step again)":"")+"."),h("div",{class:"qr",html:j.trust_qr_svg}),
  h("p",{class:"tiny muted",style:"word-break:break-all"},j.trust_url+" · fingerprint "+j.ca_sha256));
 parts.push(h("p",null,j.https?h("b",null,"Then: "):null,j.https?"scan this code with the phone's camera, and type the PIN.":"Scan this code with the phone's camera, then type the PIN on the phone."),h("div",{class:"qr",html:j.qr_svg}),h("div",{id:"pin"},j.pin),
  h("p",{class:"tiny muted",style:"word-break:break-all"},j.url),h("p",{class:"small muted"},j.https?"Dictation and keeping the screen awake work once the phone trusts the certificate. Chrome's dictation sends audio to Google.":
   "This connection is plain http, so some phones will not offer dictation or keep the screen awake. Typing works everywhere."));
 await openDialog("Write from your phone",parts,[{label:"Turn phone writing off",danger:true,onClick:async()=>{await api("/api/lan",{enable:false}).catch(()=>{});toast("Phone writing is off.");return true}},{label:"Done",primary:true}])}
function showPair(){$("pairview").hidden=false;$("app").style.display="none"}
$("pairbtn").onclick=async()=>{const pair=new URLSearchParams(location.search).get("pair")||"";
 try{const r=await fetch("/api/pair",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pair,pin:$("pairpin").value})});const j=await r.json();
  if(!r.ok)throw new Error(j.error);LS.set("quill.session",j.session);location.href="/"}catch(e){$("pairmsg").textContent=e.message}};
$("pairpin").addEventListener("keydown",e=>{if(e.key==="Enter")$("pairbtn").click()});

/* ---------------------------------------------------------------- first run */
function guideDialog(){const folder=(S.status&&S.status.books_folder)||"Documents\\My_Books";
 openDialog(GUIDE.title,[h("div",{class:"guide"},GUIDE.sections.map(([t,b])=>[h("h3",null,t),h("p",null,b.replace("{folder}",folder))]))],[{label:"Close",primary:true}])}
$("wguide").onclick=guideDialog;$("tipsguide").onclick=guideDialog;
for(const id of["welcome","pairview"])new MutationObserver(()=>{$("app").inert=!$("welcome").hidden||!$("pairview").hidden}).observe($(id),{attributes:true,attributeFilter:["hidden"]});
function showWelcome(){$("welcome").hidden=false;api("/api/projects").then(j=>{$("wkind").replaceChildren(...j.projects.map(p=>h("option",{value:p.id},p.name)))}).catch(()=>{});$("wfolder").textContent=(S.status&&S.status.books_folder)||"Documents\\My_Books";setTimeout(()=>$("wtitle").focus(),50)}
$("wstart").onclick=async()=>{const t=$("wtitle").value.trim()||"Untitled novel";try{const j=await api("/api/books",{title:t,author:$("wauthor").value.trim(),project:$("wkind").value||undefined,today:localDay()});
 $("welcome").hidden=true;LS.set("quill.tips",1);$("tips").hidden=false;await loadBooks(j.name);setTimeout(askUpdates,600)}catch(e){toast(e.message)}};
$("wtitle").addEventListener("keydown",e=>{if(e.key==="Enter")$("wstart").click()});
$("wsample").onclick=async()=>{const b=$("wsample");b.disabled=true;b.textContent="Opening the sample...";
 if(await openSample())setTimeout(askUpdates,600);b.disabled=false;b.textContent="Try the sample book"};
$("tipsx").onclick=()=>{$("tips").hidden=true;LS.set("quill.tips",2)};
$("wimport").onclick=importDialog;

/* ---------------------------------------------------------------- schools mode */
function renderAssign(){const a=S.meta&&S.meta.assignment,bar=$("assignbar"),ct=S.meta&&S.meta.contest;
 if(!a&&ct){bar.hidden=false;bar.innerHTML="";bar.append(h("details",{open:true},h("summary",null,"Contest "+ct.code+": "+ct.title+(ct.deadline?" (deadline "+ct.deadline+")":"")+(ct.word_limit?", up to "+n(ct.word_limit)+" words":"")),h("p",null,ct.prompt)),
  h("button",{class:"small primary",onclick:enterDialog},"Enter the contest"));return}
 if(!a){bar.hidden=true;return}
 bar.hidden=false;bar.innerHTML="";const done=(S.meta.rubric_checks||[]).length;
 bar.append(h("details",{open:true},h("summary",null,"Assignment: "+a.title+(a.due?" (due "+a.due+")":"")+(a.word_target?", about "+n(a.word_target)+" words":"")),h("p",null,a.prompt)),
  h("button",{class:"small",onclick:rubricDialog},"My checklist ("+done+" of "+a.rubric.length+")"),h("button",{class:"small primary",onclick:submitDialog},"Hand in"))}
async function rubricDialog(){const a=S.meta.assignment;if(!a)return;
 if(!a.rubric.length){openDialog("My checklist",h("p",null,"Your teacher did not add any criteria to this assignment."));return}
 const on=new Set(S.meta.rubric_checks||[]);
 const rows=a.rubric.map(c=>{const cb=h("input",{type:"checkbox",id:"rb_"+c.id});cb.checked=on.has(c.id);
  cb.onchange=async()=>{cb.checked?on.add(c.id):on.delete(c.id);try{const j=await api("/api/schools/rubric",{book:S.book,checks:[...on]});S.meta.rubric_checks=j.rubric.criteria.filter(x=>x.done).map(x=>x.id);renderAssign()}catch(e){toast(e.message)}};
  return h("label",{class:"check"},cb," "+c.text)});
 openDialog("My checklist",[h("p",{class:"small muted"},"This is for you. Tick what you have done, then read your work once more. Quill does not mark anything, and your teacher decides the grade."),rows],[{label:"Done",primary:true}])}
async function submitDialog(){if(!S.book)return;if(S.ch)await save();
 let drives=[];try{drives=(await api("/api/drives")).drives||[]}catch(e){}
 const name=h("input",{type:"text",maxlength:"100",id:"sub_n",value:S.meta.author||""}),refl=h("textarea",{class:"field",rows:"6",id:"sub_r",placeholder:"What went well? What would you change if you wrote it again?"});
 const sel=h("select",{id:"sub_d"},h("option",{value:""},"The Quill Submissions folder on this computer"),drives.map(d=>h("option",{value:d.id},"USB stick "+d.label+" ("+d.id+")")));
 const r=await openDialog("Hand in your work",[h("p",null,"Quill packs your book, its word count, a summary of your drafts and your reflection into one checked zip file. Nothing is sent anywhere. You hand the file in yourself, by USB stick or your school's file share."),
  h("label",{for:"sub_n"},"Your name"),name,h("label",{for:"sub_r"},"Your reflection"),refl,h("label",{for:"sub_d"},"Save it to"),sel],
  [{label:"Cancel"},{label:"Make the file",primary:true,onClick:async()=>{try{return await api("/api/schools/submit",{book:S.book,student:name.value,reflection:refl.value,drive:sel.value})}catch(e){toast(e.message);return false}}}]);
 if(r&&r.file)openDialog("Ready to hand in",[h("p",null,r.file+" has "+plural(r.words,"word","words")+" and was checked after it was written."),h("p",{class:"small muted"},"Saved in "+r.where+".")])}
async function schoolsDialog(){const prof=S.status&&S.status.profile||"standard";
 const fi=h("input",{type:"file",accept:".quillassign,application/json",id:"sc_f"});
 const open=h("button",{class:"primary",onclick:async()=>{const f=fi.files&&fi.files[0];if(!f){toast("Choose the assignment file your teacher gave you.");return}
  try{const j=await api("/api/schools/assignment/open",{text:await f.text(),student:S.meta&&S.meta.author||""});$("dlg").close();await loadBooks(j.name);toast("Assignment opened.")}catch(e){toast(e.message)}}},"Open it as a new book");
 const mk=h("button",{onclick:()=>{$("dlg").close();assignmentDialog()}},"Make an assignment...");
 const cl=h("input",{type:"checkbox",id:"sc_p"});cl.checked=prof==="classroom";
 cl.onchange=async()=>{try{const j=await api("/api/schools/profile",{profile:cl.checked?"classroom":"standard"});S.status.profile=j.profile;toast("Restart Quill to see the change.")}catch(e){toast(e.message)}};
 openDialog("Schools",[h("h3",null,"Students"),h("p",null,"Open the assignment file from your teacher. It becomes a new book with the prompt pinned above the page."),fi,h("div",null,open),
  h("h3",null,"Teachers"),h("p",null,"Write a prompt, a word target, a due date and a checklist of criteria. Quill saves one small file to hand out."),h("div",null,mk),
  h("h3",null,"Writing contests"),h("p",null,"Enter a Stark Challenge event, run one, or judge one. Everything stays on your computer."),h("div",null,h("button",{onclick:()=>contestDialog()},"Contests...")),
  h("h3",null,"Classroom profile"),h("label",{class:"check"},cl," Larger page, spell check on, update checks off (restart Quill after changing this)")])}
async function assignmentDialog(){
 const f={title:h("input",{type:"text",maxlength:"200",id:"as_t"}),prompt:h("textarea",{class:"field",rows:"5",id:"as_p"}),words:h("input",{type:"number",min:"0",step:"50",id:"as_w"}),
  due:h("input",{type:"date",id:"as_d"}),rub:h("textarea",{class:"field",rows:"5",id:"as_r",placeholder:"One criterion per line, for example: Has a clear beginning, middle and end"}),teacher:h("input",{type:"text",maxlength:"100",id:"as_n"})};
 const r=await openDialog("Make an assignment",[h("label",{for:"as_t"},"Title"),f.title,h("label",{for:"as_p"},"Prompt"),f.prompt,h("label",{for:"as_w"},"Word target (optional)"),f.words,
  h("label",{for:"as_d"},"Due date (optional)"),f.due,h("label",{for:"as_r"},"Checklist for students"),f.rub,h("label",{for:"as_n"},"Your name (optional)"),f.teacher],
  [{label:"Cancel"},{label:"Save the file",primary:true,onClick:async()=>{try{return await api("/api/schools/assignment/new",{title:f.title.value,prompt:f.prompt.value,word_target:+f.words.value||0,due:f.due.value,
   rubric:f.rub.value.split("\n"),teacher:f.teacher.value})}catch(e){toast(e.message);return false}}}]);
 if(r&&r.file)openDialog("Assignment saved",[h("p",null,r.file+" is in "+r.where+"."),h("p",{class:"small muted"},"Give students this one file by USB stick or your school's file share.")])}
/* ---------------------------------------------------------------- writing contests (Stark Challenge) */
const DIVS={kids:"Kids",teens:"Teens",couples:"Couples",families:"Families",business:"Business teams",schools:"Schools",colleges:"Colleges and universities",open:"Open"};
const readFile=(inp,msg)=>{const f=inp.files&&inp.files[0];if(!f){toast(msg);return null}return f.text()};
async function driveSelect(id,label){let drives=[];try{drives=(await api("/api/drives")).drives||[]}catch(e){}
 return h("select",{id},h("option",{value:""},label),drives.map(d=>h("option",{value:d.id},"USB stick "+d.label+" ("+d.id+")")))}
function contestDialog(){
 const fi=h("input",{type:"file",accept:".quillcontest,application/json",id:"ct_f"}),pk=h("input",{type:"file",accept:".quillpack,application/json",id:"ct_p"}),oc=h("input",{type:"file",accept:".quillcontest,application/json",id:"ct_o"});
 const nm=h("input",{type:"text",maxlength:"60",id:"ct_n",value:S.meta&&S.meta.author||""});
 const open=h("button",{class:"primary",onclick:async()=>{const t=await readFile(fi,"Choose the contest file from the organiser.");if(t==null)return;
  try{const j=await api("/api/contests/open",{contest:t,entrant:nm.value});$("dlg").close();await loadBooks(j.name);toast("Contest opened. Write, then press Enter the contest.")}catch(e){toast(e.message)}}},"Open it as a new book");
 const judge=h("button",{onclick:async()=>{const t=await readFile(pk,"Choose the judging pack from the organiser.");if(t==null)return;
  try{const j=await api("/api/contests/judge/open",{pack:t});judgeDialog(t,j.pack)}catch(e){toast(e.message)}}},"Open the pack to score");
 const org=h("button",{onclick:async()=>{const t=await readFile(oc,"Choose the contest file first.");if(t==null)return;organiserDialog(t)}},"Entries, judging and results...");
 openDialog("Writing contests",[h("h3",null,"Entrants"),h("p",null,"Open the contest file from the organiser. It becomes a new book with the prompt pinned above the page."),
  h("label",{for:"ct_n"},"Your first name, or your team's name"),nm,fi,h("div",null,open),
  h("h3",null,"Organisers"),h("p",null,"Make the event pack, then collect entries, make the anonymous judging pack and get ranked results and certificates."),
  h("div",null,h("button",{onclick:()=>contestNewDialog()},"Make a contest...")),h("p",{class:"small muted"},"To run an existing contest, choose its file:"),oc,h("div",null,org),
  h("h3",null,"Judges"),h("p",null,"Open the judging pack. You will see the entries without names and score them against the rubric."),pk,h("div",null,judge)])}
async function contestNewDialog(){
 const f={code:h("input",{type:"text",maxlength:"40",id:"cn_c",placeholder:"SPRING-2027"}),title:h("input",{type:"text",maxlength:"200",id:"cn_t"}),prompt:h("textarea",{class:"field",rows:"4",id:"cn_p"}),
  words:h("input",{type:"number",min:"0",step:"50",id:"cn_w"}),due:h("input",{type:"date",id:"cn_d"}),
  rub:h("textarea",{class:"field",rows:"4",id:"cn_r",placeholder:"One criterion per line, with its top score after a colon. Leave empty for the standard four (10 points each).\nVoice and originality: 10"}),org:h("input",{type:"text",maxlength:"100",id:"cn_o"})};
 const checks=Object.keys(DIVS).map(k=>{const c=h("input",{type:"checkbox",id:"cn_dv_"+k,value:k});c.checked=true;return c});
 const drv=await driveSelect("cn_u","The Quill Contests folder on this computer");
 const r=await openDialog("Make a contest",[h("label",{for:"cn_c"},"Event code (everyone enters with this)"),f.code,h("label",{for:"cn_t"},"Title"),f.title,h("label",{for:"cn_p"},"Prompt"),f.prompt,
  h("label",{for:"cn_w"},"Word limit (optional)"),f.words,h("label",{for:"cn_d"},"Deadline (optional)"),f.due,
  h("p",null,"Divisions"),checks.map(c=>h("label",{class:"check"},c," "+DIVS[c.value])),
  h("label",{for:"cn_r"},"Judging rubric"),f.rub,h("label",{for:"cn_o"},"Organiser (optional)"),f.org,h("label",{for:"cn_u"},"Save it to"),drv],
  [{label:"Cancel"},{label:"Save the file",primary:true,onClick:async()=>{try{
   const rub=f.rub.value.split("\n").map(l=>l.trim()).filter(Boolean).map(l=>{const m=l.match(/^(.*?)\s*:\s*(\d{1,3})$/);return m?{text:m[1],max:+m[2]}:{text:l,max:10}});
   return await api("/api/contests/new",{code:f.code.value,title:f.title.value,prompt:f.prompt.value,word_limit:+f.words.value||0,deadline:f.due.value,
    divisions:checks.filter(c=>c.checked).map(c=>c.value),rubric:rub.length?rub:null,organiser:f.org.value,drive:drv.value})}catch(e){toast(e.message);return false}}}]);
 if(r&&r.file)openDialog("Contest saved",[h("p",null,r.file+" is in "+r.where+"."),h("p",{class:"small muted"},"Give entrants this one file. Keep your own copy: you need it to make the judging pack and the results.")])}
async function enterDialog(){if(!S.book||!S.meta.contest)return;if(S.ch)await save();const ct=S.meta.contest;
 const who=h("input",{type:"text",maxlength:"60",id:"en_n",value:S.meta.author||""}),mem=h("input",{type:"text",id:"en_m",placeholder:"Amy, Ben (first names only; leave empty if you write alone)"}),
  dv=h("select",{id:"en_d"},ct.divisions.map(k=>h("option",{value:k},DIVS[k]))),st=h("textarea",{class:"field",rows:"3",id:"en_s",placeholder:"Optional: a line about your entry"});
 const drv=await driveSelect("en_u","The Quill Entries folder on this computer");
 const r=await openDialog("Enter the contest",[h("p",null,"Quill signs your entry and packs it with its word count into one checked zip. Any change after this is detected. Nothing is sent anywhere: you give the file to the organiser yourself."),
  h("label",{for:"en_d"},"Division"),dv,h("label",{for:"en_n"},"Your first name, or your team or family name"),who,h("label",{for:"en_m"},"Team members, by first name only"),mem,
  h("label",{for:"en_s"},"A line about your entry"),st,h("label",{for:"en_u"},"Save it to"),drv],
  [{label:"Cancel"},{label:"Sign and save the entry",primary:true,onClick:async()=>{try{return await api("/api/contests/enter",{book:S.book,division:dv.value,entrant:who.value,members:mem.value,statement:st.value,drive:drv.value})}catch(e){toast(e.message);return false}}}]);
 if(r&&r.file)openDialog("Entry ready",[h("p",null,r.file+" has "+plural(r.words,"word","words")+" and is signed (entry "+r.entry_id+")."),h("p",{class:"small muted"},"Saved in "+r.where+".")])}
async function organiserDialog(ctext){let ct;try{ct=JSON.parse(ctext)}catch(e){toast("That is not a contest file.");return}
 const drv=await driveSelect("or_u","The Quill folders on this computer"),late=h("input",{type:"checkbox",id:"or_l"}),part=h("input",{type:"checkbox",id:"or_pt"}),out=h("pre",{id:"or_out",class:"small",style:"white-space:pre-wrap"});
 const show=(j,extra)=>{out.textContent=(extra||"")+((j.rejected||[]).length?"\nNot counted:\n"+j.rejected.map(x=>"  "+x.file+": "+x.problems.join("; ")).join("\n"):"")};
 const pack=h("button",{onclick:async()=>{try{const j=await api("/api/contests/pack",{contest:ctext,drive:drv.value,allow_late:late.checked});show(j,j.file+" holds "+plural(j.entries,"entry","entries")+" without names, saved in "+j.where+". Give it to each judge.")}catch(e){toast(e.message)}}},"Make the anonymous judging pack");
 const res=h("button",{class:"primary",onclick:async()=>{try{const j=await api("/api/contests/results",{contest:ctext,drive:drv.value,allow_late:late.checked,participation:part.checked});show(j,j.text+"\nSaved in "+j.where+": "+Object.values(j.files).join(", "))}catch(e){toast(e.message)}}},"Get ranked results and certificates");
 openDialog("Run: "+ct.title+" ("+ct.code+")",[h("p",null,"Entries are read from the Quill Entries folder and score sheets from Quill Judging (or the USB stick chosen below). Changed, late or over-limit entries are listed and left out."),
  h("label",{for:"or_u"},"Folders on"),drv,h("label",{class:"check"},late," Accept late entries"),h("label",{class:"check"},part," Certificates for everyone scored, not only the top three"),
  h("div",null,pack," ",res),out],[{label:"Close"}])}
async function judgeDialog(ptext,pack){
 const crit=pack.rubric,scores={},cmts={},nm=h("input",{type:"text",maxlength:"60",id:"jd_n"});
 const rows=pack.items.map(it=>{scores[it.id]={};
  const inputs=crit.map(c=>{const i=h("input",{type:"number",min:"0",max:String(c.max),step:"1",id:"jd_"+it.id+"_"+c.id,style:"width:5em"});i.oninput=()=>{scores[it.id][c.id]=i.value===""?null:+i.value};return h("label",{class:"check"},c.text+" (0-"+c.max+") ",i)});
  const cm=h("textarea",{class:"field",rows:"2",id:"jd_c_"+it.id,placeholder:"Comment (optional)"});cm.oninput=()=>{cmts[it.id]=cm.value};
  return h("details",{open:pack.items.length===1},h("summary",null,"Entry "+it.id+": "+it.title+" ("+DIVS[it.division]+", "+n(it.words)+" words)"),h("p",{style:"white-space:pre-wrap;max-height:16em;overflow:auto"},it.text),inputs,cm)});
 const r=await openDialog("Judge: "+pack.title,[h("p",null,"Prompt: "+pack.prompt),h("p",{class:"small muted"},"Names are removed. Score every entry on every criterion, whole points only. Your sheet is signed so the organiser can tell it is unchanged."),
  h("label",{for:"jd_n"},"Your name"),nm,rows],
  [{label:"Cancel"},{label:"Save my score sheet",primary:true,onClick:async()=>{try{return await api("/api/contests/judge/sheet",{pack:ptext,judge:nm.value,scores,comments:cmts})}catch(e){toast(e.message);return false}}}]);
 if(r&&r.file)openDialog("Score sheet saved",[h("p",null,r.file+" holds scores for "+plural(r.entries,"entry","entries")+". It is in "+r.where+"."),h("p",{class:"small muted"},"Send this one file to the organiser.")])}
/* ---------------------------------------------------------------- 1.5 studio: screenplays, sources, import, comments,
   compare, kinds of project, sprints, scene details and plotlines, quick search. Everything stays in the book folder. */
const isScript=()=>!!(S.meta&&S.meta.kind==="screenplay");
const KIND_NAMES={novel:"Novel",short:"Short story",screenplay:"Screenplay",essay:"Essay or paper",thesis:"Thesis",nonfiction:"Nonfiction"};
function studioBook(){const sc=isScript();document.body.classList.toggle("screenplay",sc);$("scriptbtn").hidden=!sc;$("scriptbar").hidden=!sc;
 if(sc)drawScriptBar();if(!sc&&S.panel==="script")closePanel()}
function b64(buf){const u=new Uint8Array(buf);let s="";for(let i=0;i<u.length;i+=0x8000)s+=String.fromCharCode.apply(null,u.subarray(i,i+0x8000));return btoa(s)}
function stat(v,label){return h("div",{class:"stat"},h("b",null,String(v)),h("span",{class:"tiny muted"},label))}
function lineStart(text,line){let p=0;for(let i=0;i<line;i++){const k=text.indexOf("\n",p);if(k<0)return text.length;p=k+1}return p}
async function jumpLine(name,line){if(name!==S.ch)await openChapter(name);const a=lineStart(ed.value,line);let b=ed.value.indexOf("\n",a);if(b<0)b=ed.value.length;jumpTo(S.ch,a,b)}

/* screenplay lines: Fountain elements by rule, the same rules the script pages use */
const SC_RX=/^(?:INT\.?\/EXT|INT\/EXT|I\/E|INT|EXT|EST)[.\s]/i;
function lineAt(v,p){const a=v.lastIndexOf("\n",p-1)+1;let b=v.indexOf("\n",p);if(b<0)b=v.length;return [a,b]}
function caps(s){const c=s.replace(/\([^()]*\)\s*$/,"").replace(/\^$/,"").trim();return !!c&&c===c.toUpperCase()&&/\p{Lu}/u.test(c)&&!/:$/.test(c)}
function prevLine(v,a){if(a<=0)return null;const e=a-1,s=v.lastIndexOf("\n",e-1)+1;return [s,v.slice(s,e)]}
function lineKind(v,p){const [a,b]=lineAt(v,p);const s=v.slice(a,b).trim();if(!s)return "empty";
 const pl=prevLine(v,a),prev=pl?pl[1].trim():"";let nb=v.indexOf("\n",b+1);if(nb<0)nb=v.length;const next=b<v.length?v.slice(b+1,nb).trim():"";
 if(/^\.(?!\.)/.test(s)||(!prev&&SC_RX.test(s)))return "scene";
 if(/^>.*<$/.test(s))return "centered";
 if(/^>/.test(s)||(!prev&&!next&&s===s.toUpperCase()&&/TO:$/.test(s)))return "transition";
 if(/^!/.test(s))return "action";
 if(/^@/.test(s)||(!prev&&next&&caps(s)))return "character";
 let st=pl,first=null;while(st&&st[1].trim()){first=st;st=prevLine(v,st[0])}
 if(first){const ft=first[1].trim(),fp=prevLine(v,first[0]);if(!(fp&&fp[1].trim())&&!SC_RX.test(ft)&&(/^@/.test(ft)||caps(ft)))return /^\(.*\)$/.test(s)?"paren":"dialogue"}
 return "action"}
const SC_KINDS=[["scene","Scene"],["action","Action"],["character","Character"],["paren","Parenthetical"],["dialogue","Dialogue"],["transition","Transition"]];
function baseLine(s){return s.replace(/^[.!@>~]\s*/,"").replace(/\s*<$/,"").replace(/^\((.*)\)$/,"$1").trim()}
function toElement(kind){const v=ed.value,[a,b]=lineAt(v,ed.selectionStart);let s=baseLine(v.slice(a,b).trim());
 if(kind==="scene"){s=s.toUpperCase();if(!SC_RX.test(s))s=s?"."+s:"INT. "}
 else if(kind==="character")s=s.toUpperCase();
 else if(kind==="paren")s="("+s+")";
 else if(kind==="transition"){s=(s||"CUT TO:").toUpperCase();if(!/TO:$/.test(s))s=">"+s}
 else if(kind==="action"&&s&&caps(s))s="!"+s;
 const before=v.slice(0,a);const pre=["scene","character","transition"].includes(kind)&&before.trim()&&!/\n[ \t]*\n$/.test(before)?"\n":"";
 ed.setRangeText(pre+s,a,b,"end");if(kind==="paren")ed.setSelectionRange(a+pre.length+s.length-1,a+pre.length+s.length-1);
 ed.dispatchEvent(new Event("input"));ed.focus();showKind()}
function cycleElement(dir){const k=lineKind(ed.value,ed.selectionStart),[a,b]=lineAt(ed.value,ed.selectionStart);
 if(k==="empty"){const prevK=a>0?lineKind(ed.value,a-1):"empty";toElement(prevK==="character"||prevK==="dialogue"?"paren":"scene");return}
 const order=SC_KINDS.map(x=>x[0]);let i=order.indexOf(k);if(i<0)i=1;toElement(order[(i+dir+order.length)%order.length])}
let kindT=0;function showKind(){if(!isScript())return;cancelAnimationFrame(kindT);kindT=requestAnimationFrame(()=>{const k=lineKind(ed.value,ed.selectionStart);
 document.querySelectorAll("#scriptbar [data-k]").forEach(b=>b.classList.toggle("on",b.dataset.k===k))})}
function drawScriptBar(){const bar=$("scriptbar");if(bar.childNodes.length)return;const tab=h("input",{type:"checkbox",id:"sc_tab"});tab.checked=LS.get("quill.sctab",true);tab.onchange=()=>LS.set("quill.sctab",tab.checked);
 bar.append(h("span",{class:"tiny muted"},"This line:"),...SC_KINDS.map(([k,l])=>h("button",{class:"ghost","data-k":k,onclick:()=>toElement(k)},l)),h("span",{class:"sp"}),
  h("label",{class:"check tiny",title:"Tab and Shift+Tab change the line's element. Untick to use Tab to move between controls."},tab,"Tab changes lines"),
  h("button",{class:"ghost",onclick:scriptPreview},"Preview"),h("button",{class:"ghost",onclick:()=>openPanel("script")},"Scenes"))}
ed.addEventListener("keyup",showKind);ed.addEventListener("click",showKind);
async function scriptPreview(){if(!S.ch)return;let j;try{j=await api("/api/script/preview",{text:ed.value})}catch(e){toast(e.message);return}
 openDialog("Script pages: "+$("chtitle").textContent,[h("div",{class:"script",html:j.html||"<p>Nothing written yet.</p>"}),
  h("p",{class:"tiny muted"},"A preview of the layout. Screenplay PDF and .fdx files are made from the same rules in Export.")],[{label:"Close",primary:true}])}
async function renderScript(){const box=pb();box.innerHTML='<p class="muted">Reading the script...</p>';await save(true);let j;
 try{j=await api("/api/script?book="+Q(S.book))}catch(e){box.textContent=e.message;return}box.innerHTML="";
 box.append(h("div",{class:"statrow"},stat(j.pages,j.pages===1?"page":"pages"),stat(j.minutes,"minutes on screen"),stat(j.scenes.length,"scenes"),stat(j.characters.length,"speaking parts")),
  h("p",{class:"small muted"},"One script page runs about a minute on screen. INT "+j.int+", EXT "+j.ext+(j.times.length?", "+j.times.slice(0,4).map(t=>t.time.toLowerCase()+" "+t.count).join(", "):"")+". Dialogue is "+j.dialogue_share+"% of the words."),
  h("button",{class:"primary",onclick:scriptPreview},"Preview this chapter"));
 const sc=h("div",{class:"section"},h("h3",null,"Scenes"));box.append(sc);
 if(!j.scenes.length)sc.append(h("p",{class:"muted small"},"No scene headings yet. Start a line with INT. or EXT."));
 j.scenes.forEach(s=>sc.append(h("button",{class:"hit",onclick:()=>jumpLine(s.chapter,s.line)},h("b",null,s.number+". "+s.heading),
  h("span",{class:"tiny muted"}," "+s.chapter_title+" · page "+s.page+" · "+s.length+" pg"+(s.characters.length?" · "+s.characters.join(", "):"")))));
 const cs=h("div",{class:"section"},h("h3",null,"Characters"));box.append(cs);
 if(j.characters.length)cs.append(h("div",{class:"gridwrap"},h("table",{class:"pgrid"},h("tr",null,h("th",null,"Name"),h("th",null,"Speeches"),h("th",null,"Words"),h("th",null,"Scenes"),h("th",null,"From page")),
  j.characters.map(c=>h("tr",null,h("td",null,c.name),h("td",null,n(c.speeches)),h("td",null,n(c.words)),h("td",null,n(c.scenes)),h("td",null,String(c.first_page)))))));
 else cs.append(h("p",{class:"muted small"},"A name in capitals over a line of speech makes a character."));
 box.append(h("details",{class:"section"},h("summary",null,"How to write a screenplay in plain text (Fountain)"),j.cheatsheet.map(([k,v])=>h("p",{class:"small"},h("b",null,k+": "),v))))}

/* comments and footnotes: kept in the chapter text as {>>a note<<} and [^1]; never printed, never exported as comments */
async function addComment(){if(!S.ch)return;const a=ed.selectionStart,b=ed.selectionEnd,sel=ed.value.slice(a,b);
 const note=await ask(sel?"Comment on the selected words":"Add a comment","Your note (it stays in the file, and exports leave it out)","","Add comment");
 if(!note)return;const t=(sel?"{=="+sel+"==}":"")+"{>>"+note.replace(/<<\}/g,"<< }")+"<<}";ed.focus();ed.setRangeText(t,a,b,"end");ed.dispatchEvent(new Event("input"));toast("Comment added. Comments panel lists them all.")}
function nextNote(t){let m=0;for(const x of t.matchAll(/\[\^(\d{1,6})\]/g))m=Math.max(m,+x[1]);return m+1}
/* a dialog can reset the page's cursor, so callers take the selection before they open one */
function insertFootnote(text,a,b){if(a==null){a=ed.selectionStart;b=ed.selectionEnd}const k=nextNote(ed.value);ed.focus();ed.setRangeText("[^"+k+"]",a,b,"end");
 const pos=ed.selectionStart;ed.value=ed.value.replace(/\s*$/,"")+"\n\n[^"+k+"]: "+text+"\n";ed.setSelectionRange(pos,pos);ed.dispatchEvent(new Event("input"));return k}
async function footnoteDialog(){if(!S.ch)return;const a=ed.selectionStart,b=ed.selectionEnd;const t=await ask("Insert a footnote","Footnote text (it goes to the end of the chapter as [^n]: and prints as a numbered note)","","Insert");
 if(!t)return;const k=insertFootnote(t,a,b);toast("Footnote "+k+" added at the end of the chapter.")}
async function renderNotes(){const box=pb();box.innerHTML='<p class="muted">Looking for comments...</p>';await save(true);let j;
 try{j=await api("/api/comments?book="+Q(S.book))}catch(e){box.textContent=e.message;return}box.innerHTML="";
 box.append(h("p",{class:"small muted"},"Comments are notes to yourself inside the text, written {>>like this<<}. They are not counted as words and never appear in an export. Add one with Ctrl+Alt+M or the More menu."),
  h("div",{class:"row",style:"gap:8px"},h("button",{class:"primary",onclick:addComment},"Add a comment"),h("button",{onclick:footnoteDialog},"Insert a footnote")));
 if(!j.chapters.length){box.append(h("p",{class:"muted"},"No comments or footnotes in this book yet."));return}
 box.append(h("p",{class:"small"},plural(j.total,"comment","comments")+" in this book."));
 j.chapters.forEach(c=>{const g=h("div",{class:"section"},h("h3",null,c.title+(c.footnotes?" · "+plural(c.footnotes,"footnote","footnotes"):"")));
  c.comments.forEach((m,i)=>{const nth=c.comments.slice(0,i).filter(x=>x.raw===m.raw).length;
   g.append(h("div",{class:"cmt"},m.quote?h("q",null,m.quote):null,h("div",null,m.note),h("div",{class:"row",style:"gap:6px;margin-top:6px"},
    h("button",{class:"small",onclick:()=>goComment(c.name,m.raw,nth)},"Go to"),h("button",{class:"small ghost",onclick:()=>resolveComment(c.name,m,nth)},"Resolve (remove)"))))});box.append(g)})}
function findNth(t,s,k){let p=-1;for(let i=0;i<=k;i++){p=t.indexOf(s,p+1);if(p<0)return -1}return p}
async function goComment(name,raw,k){if(name!==S.ch)await openChapter(name);const p=findNth(ed.value,raw,k);if(p<0){toast("That comment has changed since the list was made.");return}jumpTo(S.ch,p,p+raw.length)}
async function resolveComment(name,m,k){if(name!==S.ch)await openChapter(name);const p=findNth(ed.value,m.raw,k);if(p<0){toast("That comment has changed since the list was made.");return}
 const hl=/^\{==([\s\S]*?)==\}/.exec(m.raw);ed.setRangeText(hl?hl[1]:"",p,p+m.raw.length,"start");ed.dispatchEvent(new Event("input"));await save(true);toast("Comment removed. The words around it stay.");renderNotes()}

/* compare a chapter with a backup */
async function compareSnap(s,c){let j;try{await save(true);j=await api("/api/backup/compare?book="+Q(S.book)+"&snap="+Q(s.name)+"&name="+Q(c.name))}catch(e){toast(e.message);return}
 const view=h("div",{class:"diff"});j.ops.forEach(([o,t])=>view.append(o==="="?document.createTextNode(t):h(o==="+"?"ins":"del",null,t)));
 openDialog("Changes since "+s.when+": "+c.title,[h("p",{class:"small"},j.exists?(j.added||j.removed?plural(j.added,"word","words")+" added, "+plural(j.removed,"word","words")+" taken out, in "+plural(j.changed,"paragraph","paragraphs")+".":"No changes since this backup."):"This chapter is not in the book now, so everything shows as taken out."),
  h("p",{class:"tiny muted"},h("ins",null,"Added since"),"  ",h("del",null,"Taken out since")),view],[{label:"Close",primary:true}])}

/* kinds of project: a new book starts with the right chapters, target and settings */
async function newBookDialog(){let pj=[];try{pj=(await api("/api/projects")).projects}catch(e){}let pick=LS.get("quill.project","novel");
 const t=h("input",{type:"text",maxlength:"200",id:"nb_t"}),au=h("input",{type:"text",maxlength:"200",id:"nb_a",value:S.meta?S.meta.author:""});const grid=h("div",{class:"choices"});
 function draw(){grid.replaceChildren(...pj.map(p=>h("button",{class:"choice"+(p.id===pick?" on":""),onclick:()=>{pick=p.id;draw()}},h("b",null,p.name),h("span",null,p.note))))}draw();
 const r=await openDialog("New book",[h("label",{for:"nb_t"},"Working title"),t,h("label",{for:"nb_a"},"Author name (optional, used on title pages)"),au,pj.length?h("label",null,"What are you writing?"):null,grid,
  h("p",{class:"tiny muted"},"You can change the kind later in Book details. Have a manuscript already? Use Import a manuscript in the book menu.")],
  [{label:"Cancel"},{label:"Create book",primary:true,onClick:()=>t.value.trim()?{title:t.value.trim(),author:au.value.trim()}:(toast("Give the book a title."),false)}]);
 if(!r)return;LS.set("quill.project",pick);try{const j=await api("/api/books",{title:r.title,author:r.author,project:pj.length?pick:undefined,today:localDay()});await loadBooks(j.name);toast("New book ready: "+j.title)}catch(e){toast(e.message)}}

/* import a manuscript */
async function importDialog(){const fi=h("input",{type:"file",id:"im_f",accept:".docx,.odt,.md,.markdown,.txt,.fountain"});const out=h("div");let file=null,data="";
 const title=h("input",{type:"text",maxlength:"200",id:"im_t"}),au=h("input",{type:"text",maxlength:"200",id:"im_a"});
 fi.onchange=async()=>{file=fi.files&&fi.files[0];out.replaceChildren();if(!file)return;out.append(h("p",{class:"muted small"},"Reading "+file.name+"..."));
  try{data=b64(await file.arrayBuffer());const j=await api("/api/import/preview",{filename:file.name,data});title.value=j.title;au.value=j.author||"";out.replaceChildren(h("div",null,
   h("p",{class:"small"},h("b",null,plural(j.chapters.length,"chapter","chapters")+", "+plural(j.words,"word","words")+". "),j.notes.join(" ")),
   h("label",{for:"im_t"},"Title"),title,h("label",{for:"im_a"},"Author"),au,
   h("ol",{class:"small implist"},j.chapters.slice(0,60).map(c=>h("li",null,h("b",null,c.title)," ",h("span",{class:"muted"},"("+n(c.words)+") "+c.opening)))),
   j.chapters.length>60?h("p",{class:"tiny muted"},"and "+(j.chapters.length-60)+" more."):null))}
  catch(e){data="";out.replaceChildren(h("p",{class:"small"},e.message))}};
 const r=await openDialog("Import a manuscript",[h("p",null,"Bring in a book you already have: Word (.docx), OpenDocument (.odt), Markdown, plain text or a Fountain screenplay. Quill makes a new book with one plain file per chapter. Your original file is not changed."),
  fi,out,h("p",{class:"tiny muted"},"Chapters are found at headings, at lines such as Chapter 12 or Prologue, or at page breaks. Italics, bold and footnotes come across; pictures and comments do not.")],
  [{label:"Cancel"},{label:"Make the book",primary:true,onClick:async()=>{if(!data){toast("Choose a file first.");return false}
   try{return await api("/api/import",{filename:file.name,data,title:title.value,author:au.value})}catch(e){toast(e.message);return false}}}]);
 if(r&&r.name){$("welcome").hidden=true;await loadBooks(r.name);toast("Imported "+plural(r.chapters,"chapter","chapters")+", "+plural(r.words,"word","words")+".")}}

/* sources and citations */
const SRC_FIELDS={book:["authors","title","edition","editors","publisher","place","year","url","doi"],article:["authors","title","container","volume","issue","pages","year","month","doi","url"],
 chapter:["authors","title","container","editors","pages","publisher","place","year"],web:["authors","title","container","publisher","year","month","day","url","accessed"],
 news:["authors","title","container","year","month","day","pages","url","accessed"],film:["editors","title","publisher","year","url"]};
const SRC_LABEL={authors:"Authors: Last, First; Last, First (or {Organisation})",title:"Title",container:"Published in (journal, website, newspaper or book)",editors:"Editors",edition:"Edition (for example 2nd)",
 publisher:"Publisher",place:"City of publication (Chicago uses it)",year:"Year",month:"Month",day:"Day",volume:"Volume",issue:"Issue",pages:"Pages (for example 10-24)",url:"Web address",doi:"DOI",accessed:"Date you read it",note:"Your own note (not printed)"};
async function sourceForm(src,kinds){src=Object.assign({kind:"book"},src||{});const box=h("div");const vals=Object.assign({},src);
 function draw(){box.replaceChildren(h("div",null,h("label",{for:"sf_k"},"Kind of source"),Object.assign(selIn(Object.entries(kinds),vals.kind,v=>{vals.kind=v;draw()}),{id:"sf_k"}),
  [...SRC_FIELDS[vals.kind],"note"].map(f=>{const id="sf_"+f;const lab=f==="editors"&&vals.kind==="film"?"Director":SRC_LABEL[f];
   const i=f==="accessed"?h("input",{type:"date",id,value:vals[f]||""}):h("input",{type:"text",id,maxlength:"600",value:vals[f]||""});i.oninput=()=>{vals[f]=i.value};return [h("label",{for:id},lab),i]})))}
 draw();return openDialog(src.id?"Edit source":"Add a source",[box],[{label:"Cancel"},{label:"Save source",primary:true,onClick:async()=>{try{return await api("/api/sources/save",{book:S.book,source:vals})}catch(e){toast(e.message);return false}}}])}
async function insertCitation(id,a,b){const st=S.cite||"mla";const loc=await ask("Cite this source",st==="chicago"?"Page or place (optional). Chicago puts the citation in a footnote.":"Page or place (optional)","","Insert citation");
 if(loc===null)return;try{const j=await api("/api/sources/cite",{book:S.book,id,locator:loc});if(j.kind==="note"){insertFootnote(j.text,a,b);toast("Footnote added with the citation.")}
  else{const pre=a>0&&!/\s$/.test(ed.value.slice(0,a))?" ":"";ed.focus();ed.setRangeText(pre+j.text,a,b,"end");ed.dispatchEvent(new Event("input"))}}catch(e){toast(e.message)}}
async function sourcesDialog(){if(!S.book)return;const selA=ed.selectionStart,selB=ed.selectionEnd;let st;try{st=await api("/api/sources?book="+Q(S.book))}catch(e){toast(e.message);return}S.cite=st.style;
 for(;;){let next=null;const body=h("div");
  const style=selIn(Object.entries(st.styles),st.style,async v=>{try{st=await api("/api/sources/style",{book:S.book,style:v});S.cite=st.style;next={act:"redraw"};endDlg()}catch(e){toast(e.message)}});
  const bib=h("input",{type:"checkbox",id:"src_bib"});bib.checked=st.bibliography;bib.onchange=async()=>{try{st=await api("/api/sources/style",{book:S.book,bibliography:bib.checked})}catch(e){toast(e.message)}};
  body.append(h("div",{class:"row",style:"gap:10px;flex-wrap:wrap;align-items:center"},h("span",{class:"small"},"Style"),style,h("label",{class:"check small"},bib,"Add the "+st.list_title+" list after the last chapter in exports")));
  if(!st.sources.length)body.append(h("p",{class:"muted"},"No sources yet. Add the books, articles, web pages and films you quote or draw on."));
  st.sources.forEach(s=>body.append(h("div",{class:"srcrow"},h("div",{class:"grow"},h("div",{class:"small",html:esc(s.entry).replace(/\*([^*]+)\*/g,"<i>$1</i>")}),h("div",{class:"tiny muted"},KIND_SRC(st,s)+" · in the text: "+s.cite)),
   h("div",{class:"row",style:"gap:4px"},h("button",{class:"small primary",disabled:!S.ch,onclick:()=>{next={act:"cite",id:s.id};endDlg()}},"Cite"),h("button",{class:"small",onclick:()=>{next={act:"edit",src:s};endDlg()}},"Edit"),
    h("button",{class:"small ghost danger",onclick:async()=>{try{st=await api("/api/sources/delete",{book:S.book,id:s.id});next={act:"redraw"};endDlg()}catch(e){toast(e.message)}}},"Delete")))));
  if(st.sources.length)body.append(h("details",null,h("summary",{class:"small"},"The "+st.list_title+" list as text"),h("pre",{class:"preview"},st.list.join("\n\n").replace(/\*/g,""))));
  const r=await openDialog("Sources and citations",[h("p",{class:"small muted"},"Your sources live in Sources.json in the book folder. Quill formats them; check anything unusual against your style guide or your teacher's rules."),body],
   [{label:"Close"},{label:"From Library clips",value:"clips"},{label:"Add a source",primary:true,value:"add"}]);
  if(r==="add"||(next&&next.act==="edit")){const j=await sourceForm(next&&next.src,st.kinds);if(j&&j.sources)st=j;continue}
  if(r==="clips"){try{st=await api("/api/sources/import",{book:S.book});toast(st.added?"Added "+plural(st.added,"source","sources")+" from the Library's research clips.":"No new clips to add.")}catch(e){toast(e.message)}continue}
  if(next&&next.act==="cite"){await insertCitation(next.id,selA,selB);return}
  if(next&&next.act==="redraw")continue;return}}
function KIND_SRC(st,s){return st.kinds[s.kind]||s.kind}

/* writing sprints */
const SPR={on:false};
function sprintTick(){const p=$("sprintpill");if(!SPR.on){p.hidden=true;return}const left=Math.max(0,SPR.end-Date.now());
 const w=S.book===SPR.book?S.bookWords-SPR.w0:0;const m=Math.floor(left/60000),s=Math.floor(left%60000/1000);
 p.hidden=false;p.textContent=m+":"+String(s).padStart(2,"0")+" · "+plural(Math.max(0,w),"word","words")+(SPR.goal?" of "+n(SPR.goal):"");
 if(left<=0)finishSprint(true)}
setInterval(()=>{if(SPR.on)sprintTick()},1000);
function startSprint(minutes,goal){Object.assign(SPR,{on:true,end:Date.now()+minutes*60000,start:Date.now(),w0:S.bookWords,goal:goal||0,book:S.book,minutes});
 LS.set("quill.sprint",{end:SPR.end,start:SPR.start,w0:SPR.w0,goal:SPR.goal,book:SPR.book,minutes});sprintTick();ed.focus();toast("Sprint started: "+plural(minutes,"minute","minutes")+". Just write.")}
async function finishSprint(done){if(!SPR.on)return;SPR.on=false;LS.del("quill.sprint");$("sprintpill").hidden=true;await save(true);
 const mins=Math.max(.25,(Math.min(Date.now(),SPR.end)-SPR.start)/60000),w=S.book===SPR.book?S.bookWords-SPR.w0:0;
 let sum=null;try{sum=await api("/api/sprints",{sprint:{minutes:mins,words:w,goal:SPR.goal,day:localDay(),book:SPR.book,finished:done}})}catch(e){toast(e.message)}
 openDialog(done?"Sprint done":"Sprint stopped",[h("p",{class:"big"},plural(Math.max(0,w),"word","words")),h("p",null,"In "+(Math.round(mins*10)/10)+" minutes, about "+(w>0?Math.round(w/mins):0)+" a minute."+(SPR.goal?(w>=SPR.goal?" Goal met.":" Goal: "+n(SPR.goal)+"."):"")),
  sum?h("p",{class:"small muted"},plural(sum.count,"sprint","sprints")+" so far; the most words in one: "+n(sum.best)+".")
 :null],[{label:"Close"},{label:"Another sprint",primary:true,onClick:()=>{setTimeout(sprintDialog,50)}}])}
async function sprintDialog(){if(SPR.on){const r=await openDialog("Writing sprint",[h("p",null,"Sprint under way: "+$("sprintpill").textContent+".")],[{label:"Keep writing",primary:true},{label:"Stop now",danger:true,value:"stop"}]);if(r==="stop")finishSprint(false);return}
 if(!S.book)return;let sum=null;try{sum=await api("/api/sprints?"+todayQ)}catch(e){}
 const mins=selIn([["5","5 minutes"],["10","10 minutes"],["15","15 minutes"],["20","20 minutes"],["25","25 minutes"],["30","30 minutes"],["45","45 minutes"],["60","60 minutes"]],String(LS.get("quill.sprintmin","15")),v=>LS.set("quill.sprintmin",v));
 const goal=h("input",{type:"number",min:"0",step:"50",id:"sp_g",value:LS.get("quill.sprintgoal","")||"",placeholder:"Optional"});
 const r=await openDialog("Writing sprint",[h("p",null,"Write without stopping until the timer ends. Quill counts the words you add to this book and keeps a record of each sprint."),
  h("div",{class:"row",style:"gap:12px;align-items:end;flex-wrap:wrap"},h("div",null,h("label",null,"Length"),mins),h("div",null,h("label",{for:"sp_g"},"Word goal"),goal)),
  sum&&sum.count?h("div",{class:"section"},h("h3",null,"Your sprints"),h("div",{class:"statrow"},stat(sum.count,"sprints"),stat(n(sum.words),"words"),stat(n(sum.best),"most in one"),stat(sum.wpm,"words a minute")),
   h("ul",{class:"small"},sum.recent.slice(0,5).map(x=>h("li",null,fmtDay(x.day)+": "+plural(x.words,"word","words")+" in "+x.minutes+" min"+(x.goal?(x.words>=x.goal?" (goal met)":" (goal "+n(x.goal)+")"):"")))))
  :null],[{label:"Cancel"},{label:"Start",primary:true,value:"go"}]);
 if(r!=="go")return;LS.set("quill.sprintgoal",goal.value);startSprint(+mins.value||15,+goal.value||0)}
$("sprintpill").onclick=sprintDialog;
(function resumeSprint(){const s=LS.get("quill.sprint",null);if(s&&s.end>Date.now()){Object.assign(SPR,s,{on:true});}else if(s)LS.del("quill.sprint")})();

/* scene details, plotlines and the plot grid */
async function detailsDialog(c){if(!c)return;const pls=(S.meta&&S.meta.plotlines)||[];const povs=[...new Set(S.chapters.map(x=>x.pov).filter(Boolean))];
 const pov=h("input",{type:"text",maxlength:"80",id:"dt_p",value:c.pov||"",list:"dt_pl"}),set=h("input",{type:"text",maxlength:"120",id:"dt_s",value:c.setting||""}),when=h("input",{type:"text",maxlength:"60",id:"dt_w",value:c.when||"",placeholder:"Day 3, evening"});
 const on=new Set(c.plotlines||[]);const boxes=pls.map(p=>{const cb=h("input",{type:"checkbox"});cb.checked=on.has(p.id);cb.onchange=()=>cb.checked?on.add(p.id):on.delete(p.id);return h("label",{class:"check"},cb,h("i",{class:"dot",style:"background:"+p.color}),p.name)});
 const r=await openDialog("Scene details: "+c.title,[h("datalist",{id:"dt_pl"},povs.map(p=>h("option",{value:p}))),h("label",{for:"dt_p"},"Point of view (whose eyes)"),pov,h("label",{for:"dt_s"},"Setting"),set,h("label",{for:"dt_w"},"When it happens in the story"),when,
  h("label",null,"Plotlines"),pls.length?boxes:h("p",{class:"small muted"},"No plotlines yet. Add them in the Plot grid (More menu)."),h("p",{class:"tiny muted"},"Saved in book.json, never in your text.")],
  [{label:"Cancel"},{label:"Save",primary:true,onClick:async()=>{try{return await api("/api/chapter/details",{book:S.book,name:c.name,pov:pov.value,setting:set.value,when:when.value,plotlines:[...on]})}catch(e){toast(e.message);return false}}}]);
 if(r&&r.ok){await refreshChapters();toast("Scene details saved.")}}
async function plotGridDialog(){if(!S.book)return;let g;try{g=await api("/api/plotgrid?book="+Q(S.book))}catch(e){toast(e.message);return}
 for(;;){let next=null;const rows=g.plotlines.map(p=>({id:p.id,name:p.name,color:p.color}));
  const ed2=h("div");function drawEd(){ed2.replaceChildren(...rows.map((p,i)=>h("div",{class:"row",style:"gap:6px;margin:4px 0"},
   h("select",{"aria-label":"Colour",style:"width:auto;color:"+p.color,onchange:e=>{p.color=e.target.value;e.target.style.color=p.color}},g.colors.map(c=>h("option",{value:c,selected:c===p.color,style:"color:"+c},"●"))),
   Object.assign(h("input",{type:"text",maxlength:"60",value:p.name,"aria-label":"Plotline name"}),{oninput:e=>{p.name=e.target.value}}),
   h("button",{class:"ghost small",onclick:()=>{rows.splice(i,1);drawEd()}},"Remove"))),h("button",{class:"small",onclick:()=>{rows.push({name:"",color:g.colors[rows.length%g.colors.length]});drawEd()}},"+ Plotline"))}drawEd();
  const tbl=h("table",{class:"pgrid"},h("tr",null,h("th",null,"Plotline"),g.chapters.map((c,i)=>h("th",{title:c.title},String(i+1)))),
   g.plotlines.map(p=>h("tr",null,h("td",null,h("i",{class:"dot",style:"background:"+p.color}),p.name),g.chapters.map(c=>{const cb=h("input",{type:"checkbox","aria-label":p.name+" in "+c.title});cb.checked=(c.plotlines||[]).includes(p.id);
    cb.onchange=async()=>{const set=new Set(c.plotlines||[]);cb.checked?set.add(p.id):set.delete(p.id);c.plotlines=[...set];try{await api("/api/chapter/details",{book:S.book,name:c.name,plotlines:c.plotlines})}catch(e){toast(e.message)}};return h("td",null,cb)}))));
  const povs=g.grid.pov.length?h("div",null,h("h3",null,"Point of view"),g.grid.pov.map(p=>h("div",{class:"small"},h("b",null,p.pov)," "+plural(p.chapters,"chapter","chapters")+", "+plural(p.words,"word","words")+" ("+p.share+"%)"))):h("p",{class:"small muted"},"Give chapters a point of view in Scene details to see the balance here.");
  const r=await openDialog("Plot grid",[h("p",{class:"small muted"},"Plotlines are the threads of the story. Tick the chapters that carry each one; the corkboard can show one thread at a time."),
   g.plotlines.length?h("div",{class:"gridwrap"},tbl):null,g.grid.untagged.length&&g.plotlines.length?h("p",{class:"tiny muted"},plural(g.grid.untagged.length,"chapter carries","chapters carry")+" no plotline yet."):null,
   povs,h("details",{open:!g.plotlines.length},h("summary",{class:"small"},"Name the plotlines"),ed2)],[{label:"Close"},{label:"Save plotlines",primary:true,value:"save"}]);
  if(r==="save"){try{g=await api("/api/plotlines",{book:S.book,plotlines:rows.filter(p=>p.name.trim())});S.meta.plotlines=g.plotlines;await refreshChapters();toast("Plotlines saved.");continue}catch(e){toast(e.message);continue}}
  await refreshChapters();return}}

/* ---------------------------------------------------------------- Story X-ray: mood arc, echoes, character web, typo-tolerant find */
function svg(tag,a,...kids){const e=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const k in a||{})e.setAttribute(k,a[k]);kids.forEach(c=>e.append(c));return e}
function moodChart(m){const W=600,H=170,P=26,rows=m.chapters,pts=m.curve,N=Math.max(pts.length-1,1);
 const x=i=>P+(W-2*P)*i/N,yv=v=>H/2-(H/2-14)*v,yt=t=>H-14-(H-30)*t;
 const root=svg("svg",{viewBox:"0 0 "+W+" "+H,role:"img",width:"100%","aria-label":"Mood and tension across the book. The table below gives the same figures."});
 root.append(svg("line",{x1:P,x2:W-P,y1:H/2,y2:H/2,style:"stroke:var(--line-strong);stroke-width:1"}));
 let prev=-1;pts.forEach((p,i)=>{if(p.chapter!==prev){prev=p.chapter;root.append(svg("line",{x1:x(i),x2:x(i),y1:8,y2:H-8,style:"stroke:var(--line);stroke-width:1"}));
  const t=svg("text",{x:x(i)+2,y:H-2,style:"fill:var(--muted);font-size:10px"});t.textContent=String(p.chapter+1);root.append(t)}});
 const line=(f,sty)=>svg("polyline",{points:pts.map((p,i)=>x(i).toFixed(1)+","+f(p).toFixed(1)).join(" "),fill:"none",style:sty});
 root.append(line(p=>yv(p.valence),"stroke:var(--ink);stroke-width:2.2"),line(p=>yt(p.tension),"stroke:var(--accent);stroke-width:2.2;stroke-dasharray:6 4"));return root}
async function coverDialog(){if(!S.book)return;await save(true);
 const TR={"5x8":"5 x 8 in","5.25x8":"5.25 x 8 in","5.5x8.5":"5.5 x 8.5 in","6x9":"6 x 9 in","8.5x11":"8.5 x 11 in"},PA={white:"White paper",cream:"Cream paper",color:"Colour pages"};
 const trim=h("select",{id:"cvtrim"},Object.keys(TR).map(k=>h("option",{value:k,selected:k==="6x9"},TR[k]))),paper=h("select",{id:"cvpaper"},Object.keys(PA).map(k=>h("option",{value:k,selected:k==="cream"},PA[k])));
 const pages=h("input",{id:"cvpages",type:"number",min:"24",max:"828",step:"2",placeholder:"estimate","aria-describedby":"cvhelp",style:"width:110px"});
 const out=h("div",{"aria-live":"polite"}),pic=h("div",{style:"margin-top:8px"});let last=null,t=null;
 async function go(){try{const j=await api("/api/cover?book="+Q(S.book)+"&trim="+Q(trim.value)+"&paper="+Q(paper.value)+(pages.value?"&pages="+Q(pages.value):""));last=j;pages.removeAttribute("aria-invalid");
   out.replaceChildren(h("p",null,h("b",null,n(j.pages)+" pages"),j.estimated?" (estimated from "+n(j.words)+" words)":""),
    h("ul",{class:"small"},h("li",null,"Spine: "+j.spine_in+" in ("+j.spine_mm+" mm)"+(j.spine_text?"":", too thin for text")),
     h("li",null,"Full cover with bleed: "+j.width_in+" x "+j.height_in+" in ("+j.width_mm+" x "+j.height_mm+" mm)"),
     h("li",null,"At 300 dpi: "+n(j.width_px)+" x "+n(j.height_px)+" pixels")),
    h("ul",{class:"small muted"},j.notes.map(x=>h("li",null,x))));
   pic.replaceChildren(h("img",{src:"data:image/svg+xml;charset=utf-8,"+encodeURIComponent(j.svg),alt:"Cover layout: back, spine and front with bleed and safe areas",style:"max-width:100%;border:1px solid var(--line)"}))}
  catch(e){pages.setAttribute("aria-invalid","true");out.replaceChildren(h("p",{role:"alert"},e.message))}}
 const later=()=>{clearTimeout(t);t=setTimeout(go,250)};trim.onchange=go;paper.onchange=go;pages.oninput=later;
 const dl=()=>{if(!last)return false;const a=h("a",{href:URL.createObjectURL(new Blob([last.svg],{type:"image/svg+xml"})),download:"cover-template-"+last.trim+"-"+last.pages+"p.svg"});document.body.append(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},1000);toast("Cover template downloaded.");return false};
 go();await openDialog("Print cover size",[h("p",{class:"small muted"},"For a paperback: how wide the spine is and what size the whole wrap-around cover must be. Nothing in your book changes."),
  h("div",{class:"row",style:"gap:8px;flex-wrap:wrap"},h("label",null,"Trim size ",trim),h("label",null,"Paper ",paper),h("label",null,"Pages ",pages)),
  h("p",{id:"cvhelp",class:"tiny muted"},"Leave Pages empty for an estimate, or type the page count of your print PDF."),out,pic],
  [{label:"Download template (SVG)",onClick:dl},{label:"Close",primary:true}])}
async function xrayDialog(){if(!S.book)return;await save(true);let j;try{j=await api("/api/xray?book="+Q(S.book))}catch(e){toast(e.message);return}
 const names=["Mood arc","Echoes","Character web","Find"];let cur=0;const bar=h("div",{class:"row",role:"tablist","aria-label":"Story X-ray views",style:"gap:6px;margin-bottom:8px"}),body=h("div");
 function paintBar(){bar.replaceChildren(...names.map((t,i)=>h("button",{class:i===cur?"primary small":"small",role:"tab","aria-selected":i===cur?"true":"false",onclick:()=>{cur=i;paint()}},t)))}
 function mood(){const m=j.mood;if(!m.curve.length){body.append(h("p",{class:"muted"},"Write a few chapters first; the arc needs some text."));return}
  body.append(h("p",{class:"small muted"},"Solid line: how warm (up) or dark (down) the writing reads. Dashed line: how tense the pace is. Numbers under the line are chapters."),moodChart(m),
   m.notes.length?h("ul",{class:"small"},m.notes.map(t=>h("li",null,t))):"",
   h("p",{class:"small"},"Dialogue makes up "+Math.round(m.dialogue*100)+"% of the book."),
   h("div",{class:"gridwrap"},h("table",{class:"pgrid"},h("tr",null,["Chapter","Words","Warm to dark","Tension","Dialogue"].map(t=>h("th",null,t))),
    m.chapters.map((c,i)=>h("tr",null,h("td",null,(i+1)+". "+c.title),h("td",null,n(c.words)),h("td",null,c.valence.toFixed(2)),h("td",null,c.tension.toFixed(2)),h("td",null,Math.round(c.dialogue*100)+"%"))))),
   h("p",{class:"tiny muted"},m.note))}
 function echoPane(){const sel=h("select",{"aria-label":"Chapter to check",style:"width:auto"},S.chapters.map(c=>h("option",{value:c.name,selected:c.name===S.ch},c.title)));const out=h("div",{"aria-live":"polite"});
  async function go(){try{const r=await api("/api/xray/echoes?book="+Q(S.book)+"&name="+Q(sel.value));out.replaceChildren(r.echoes.length?h("ul",{class:"small"},r.echoes.map(e=>h("li",null,h("b",null,e.word),
    " used "+e.count+" times, as close as "+e.closest+" words apart"))):h("p",{class:"small"},"No distinctive word repeats closely in this chapter."))}catch(e){out.replaceChildren(h("p",{class:"small"},e.message))}}
  sel.onchange=go;body.append(h("p",{class:"small muted"},"Distinctive words used again within a few lines. Often fine; sometimes the word you reached for twice without noticing."),sel,out);go()}
 function web(){const w=j.web;if(w.nodes.length<2){body.append(h("p",{class:"muted"},"The web needs at least two characters that Lore has found. Name your characters in the text and open Lore."));return}
  const R=Math.min(150,60+w.nodes.length*10),C=R+70,max=Math.max(...w.edges.map(e=>e.weight),1),pos={};
  w.nodes.forEach((nd,i)=>{const a=-Math.PI/2+2*Math.PI*i/w.nodes.length;pos[nd.name]=[C+R*Math.cos(a),C+R*Math.sin(a),a]});
  const root=svg("svg",{viewBox:"0 0 "+2*C+" "+2*C,role:"img",width:"100%",style:"max-width:520px","aria-label":"Which characters share scenes. The list below gives the same pairs."});
  w.edges.forEach(e=>{const a=pos[e.a],b=pos[e.b];root.append(svg("line",{x1:a[0],y1:a[1],x2:b[0],y2:b[1],style:"stroke:var(--accent);opacity:.75;stroke-width:"+(1+5*e.weight/max).toFixed(1)}))});
  const mp=Math.max(...w.nodes.map(x=>x.paragraphs),1);
  w.nodes.forEach(nd=>{const p=pos[nd.name],r=5+9*Math.sqrt(nd.paragraphs/mp);root.append(svg("circle",{cx:p[0],cy:p[1],r:r,style:"fill:var(--surface);stroke:var(--ink);stroke-width:2"}));
   const right=Math.cos(p[2])>=0.2,left=Math.cos(p[2])<=-0.2,t=svg("text",{x:p[0]+(right?r+5:left?-r-5:0),y:p[1]+(right||left?4:Math.sin(p[2])>0?r+14:-r-6),"text-anchor":right?"start":left?"end":"middle",style:"fill:var(--ink);font-size:12px"});t.textContent=nd.name;root.append(t)});
  body.append(h("p",{class:"small muted"},"Two characters are linked when their names share a paragraph; thicker means more shared paragraphs."),root,
   w.edges.length?h("div",{class:"gridwrap"},h("table",{class:"pgrid"},h("tr",null,["Pair","Shared paragraphs","First together"].map(t=>h("th",null,t))),w.edges.slice(0,12).map(e=>h("tr",null,h("td",null,e.a+" and "+e.b),h("td",null,String(e.weight)),h("td",null,"Chapter "+(e.first+1)+": "+e.first_title))))):"",
   w.never_together.length?h("p",{class:"small"},"Strong characters who never share a paragraph: "+w.never_together.map(p=>p[0]+" and "+p[1]).join("; ")+". Fine if intended."):"",h("p",{class:"tiny muted"},w.note))}
 function find(){const inp=h("input",{type:"text",maxlength:"200",placeholder:"Type a few words; typos and accents are forgiven","aria-label":"Find in the whole book"}),out=h("div",{"aria-live":"polite"});let t=0;
  inp.oninput=()=>{clearTimeout(t);t=setTimeout(async()=>{if(!inp.value.trim()){out.replaceChildren();return}try{const r=await api("/api/xray/find?book="+Q(S.book)+"&q="+Q(inp.value));
   out.replaceChildren(r.hits.length?h("div",{class:"list"},r.hits.map(x=>{const parts=[];let at=0;x.marks.forEach(([a,b])=>{if(a>=at){parts.push(x.text.slice(at,a),h("mark",null,x.text.slice(a,b)));at=b}});parts.push(x.text.slice(at));
    return h("button",{class:"ghost small",style:"text-align:left;white-space:normal",onclick:()=>{$("dlg").close();openChapter(x.name)}},h("b",null,x.title+": "),...parts)})):h("p",{class:"small"},"Nothing close to that."))}catch(e){out.replaceChildren(h("p",{class:"small"},e.message))}},120)};
  body.append(h("p",{class:"small muted"},"Finds sentences that hold every word you type, even with a letter wrong or an accent missing, in any order."),inp,out)}
 function paint(){paintBar();body.replaceChildren();[mood,echoPane,web,find][cur]()}
 paint();await openDialog("Story X-ray",[h("p",{class:"tiny muted"},"Plain word counts and lists, not understanding. A second pair of eyes; nothing is changed in your chapters."),bar,body],[{label:"Close"}])}

/* ---------------------------------------------------------------- Voice and rhythm: sentence heartbeat, character dialogue voices */
function rhythmChart(r){const W=600,H=120,P=8,k=r.sentences,N=Math.max(k.length,1),mx=Math.max(40,...k),bw=(W-2*P)/N;
 const root=svg("svg",{viewBox:"0 0 "+W+" "+H,role:"img",width:"100%","aria-label":"Sentence lengths in order, one bar per sentence. The notes and table below give the same figures."});
 r.runs.forEach(u=>{const a=u.start-1;if(a<N)root.append(svg("rect",{x:P+a*bw-1,y:2,width:Math.min(u.length,N-a)*bw+2,height:H-4,style:"fill:var(--accent);opacity:.14"}))});
 k.forEach((v,i)=>{const hh=Math.max(2,(H-14)*v/mx);root.append(svg("rect",{x:(P+i*bw+bw*0.12).toFixed(1),y:(H-6-hh).toFixed(1),width:Math.max(bw*0.76,0.8).toFixed(1),height:hh.toFixed(1),style:"fill:"+(v>=25?"var(--accent)":v<=8?"var(--muted)":"var(--ink)")}))});
 return root}
async function voiceDialog(){if(!S.book)return;await save(true);let j;try{j=await api("/api/voice?book="+Q(S.book))}catch(e){toast(e.message);return}
 const names=["Sentence rhythm","Character voices"];let cur=0;const bar=h("div",{class:"row",role:"tablist","aria-label":"Voice and rhythm views",style:"gap:6px;margin-bottom:8px"}),body=h("div");
 function paintBar(){bar.replaceChildren(...names.map((t,i)=>h("button",{class:i===cur?"primary small":"small",role:"tab","aria-selected":i===cur?"true":"false",onclick:()=>{cur=i;paint()}},t)))}
 function rhy(){const sel=h("select",{"aria-label":"Chapter to read",style:"width:auto"},S.chapters.map(c=>h("option",{value:c.name,selected:c.name===S.ch},c.title)));const out=h("div",{"aria-live":"polite"});
  async function go(){try{const r=await api("/api/voice/rhythm?book="+Q(S.book)+"&name="+Q(sel.value));
   out.replaceChildren(r.sentence_count?h("div",null,rhythmChart(r),
    h("p",{class:"tiny muted"},"Grey bars are short sentences (8 words or fewer), dark bars middling, coloured bars long (25 or more). A tinted band marks six or more sentences in a row of the same kind."),
    h("p",{class:"small"},n(r.sentence_count)+" sentences, "+r.mean+" words on average, longest "+r.longest+". Short "+Math.round(r.short*100)+"%, long "+Math.round(r.long*100)+"%, variation "+r.variation.toFixed(2)+"."),
    r.notes.length?h("ul",{class:"small"},r.notes.map(t=>h("li",null,t))):h("p",{class:"small"},"Nothing stands out in this chapter's rhythm."),
    r.runs.length?h("div",{class:"gridwrap"},h("table",{class:"pgrid"},h("tr",null,["From sentence","In a row","Kind","Starts with"].map(t=>h("th",null,t))),r.runs.map(u=>h("tr",null,h("td",null,String(u.start)),h("td",null,String(u.length)),h("td",null,u.band),h("td",null,u.excerpt))))):null,
    h("p",{class:"tiny muted"},r.note)):h("p",{class:"small"},"This chapter has no text yet."))}catch(e){out.replaceChildren(h("p",{class:"small"},e.message))}}
  sel.onchange=go;body.append(h("p",{class:"small muted"},"The heartbeat of a chapter: one bar for every sentence."),sel,out,
   h("div",{class:"gridwrap"},h("table",{class:"pgrid"},h("tr",null,["Chapter","Sentences","Average","Variation","Longest","Drones"].map(t=>h("th",null,t))),
    j.chapters.map((c,i)=>h("tr",null,h("td",null,(i+1)+". "+c.title),h("td",null,n(c.sentences)),h("td",null,String(c.mean)),h("td",null,c.variation.toFixed(2)),h("td",null,String(c.longest)),h("td",null,String(c.runs)))))));go()}
 function vox(){const v=j.voices;if(!v.characters.length){body.append(h("p",{class:"muted"},"Voices need characters that Lore has found, each with at least three spoken lines next to their name, like: \"Come in,\" Anna said. Open Lore after writing some dialogue."));return}
  const mxw=Math.max(...v.characters.map(c=>c.avg_words),1);
  body.append(h("p",{class:"small muted"},"Each character's own lines of dialogue, measured. The bar is how many words a line runs on average."),
   h("div",{class:"list"},v.characters.map(c=>h("div",{class:"card",style:"padding:8px 10px;margin-bottom:6px;border:1px solid var(--line);border-radius:8px"},
    h("div",{class:"row",style:"justify-content:space-between;gap:8px"},h("b",null,c.name),h("span",{class:"tiny muted"},n(c.lines)+" lines, "+n(c.words)+" words")),
    h("div",{style:"height:8px;background:var(--line);border-radius:4px;margin:6px 0",role:"img","aria-label":c.avg_words+" words per line on average"},h("div",{style:"height:8px;border-radius:4px;background:var(--accent);width:"+Math.round(100*c.avg_words/mxw)+"%"})),
    h("div",{class:"small"},c.avg_words+" words a line. Asks "+Math.round(c.questions*100)+"%, shouts "+Math.round(c.exclaims*100)+"%, contractions "+(c.contractions*100).toFixed(1)+"% of words, \"I\" and \"me\" "+(c.first_person*100).toFixed(1)+"%."),
    c.favourites.length?h("div",{class:"small"},"Their own words: "+c.favourites.map(f=>f.word+" ("+f.count+")").join(", ")):null,
    h("div",{class:"tiny muted"},"“"+c.sample+"”")))),
   v.alike.length?h("p",{class:"small"},"These pairs measure alike, so check they sound like different people: "+v.alike.map(p=>p[0]+" and "+p[1]).join("; ")+"."):"",
   v.notes.length?h("ul",{class:"small muted"},v.notes.map(t=>h("li",null,t))):"",h("p",{class:"tiny muted"},v.note))}
 function paint(){paintBar();body.replaceChildren();[rhy,vox][cur]()}
 paint();await openDialog("Voice and rhythm",[h("p",{class:"tiny muted"},"Plain counts, not understanding. Nothing is changed in your chapters and nothing leaves this PC."),bar,body],[{label:"Close"}])}

/* quick search: Ctrl+K jumps to a chapter, a book or a command */
const PAL={items:[],sel:0};
function fold(s){return String(s).normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase()}
function palItems(){const cmds=[["Search the book",()=>openPanel("search")],["Lore",()=>openPanel("lore")],["Comments",()=>openPanel("notes")],["Add a comment",addComment],["Insert a footnote",footnoteDialog],
 ["Sources and citations",sourcesDialog],["Writing sprint",sprintDialog],["Plot grid",plotGridDialog],["Scene details for this chapter",()=>detailsDialog(S.chapters.find(c=>c.name===S.ch))],
 ["Corkboard",()=>setView(S.view==="board"?"write":"board")],["Focus",()=>toggleFocus()],["Export",exportDialog],["Book details",bookDialog],["New book",newBookDialog],["Import a manuscript",importDialog],
 ["Backups",()=>openPanel("backups")],["Graveyard",()=>openPanel("graveyard")],["Revise",reviseDialog],["Story X-ray",xrayDialog],["Voice and rhythm",voiceDialog],["Sentence rhythm chart",voiceDialog],["Character dialogue voices",voiceDialog],["Print cover size and spine",coverDialog],["Before you send it: check the manuscript",preflightDialog],["Collections: saved lists of chapters",collectionsDialog],["Rescue sheet for the fridge",rescueDialog],["Rest days and the writing week",goalDialog],["Mood arc and character web",xrayDialog],["Find with typos forgiven",xrayDialog],["Plot outline",outlineDialog],["Chapter outline and word targets",setOutlineView],["Reference pane: read another chapter beside this one",refToggle],["Make a milestone: a named copy of the book",milestoneNew],["Progress and finish date",progressDialog],["Today and goals",goalDialog],
 ["Submissions",subsDialog],["Series bible",seriesDialog],["Page and type",typeDialog],["Read aloud from the cursor",readAloud],["Zip / USB backup",zipDialog],["Getting started",guideDialog],["About",aboutDialog]];
 if(isScript())cmds.unshift(["Script: scenes and characters",()=>openPanel("script")],["Script preview",scriptPreview]);
 return [...S.chapters.map((c,i)=>({label:c.title,hint:"Chapter "+(i+1),act:()=>openChapter(c.name)})),...cmds.map(([l,a])=>({label:l,hint:"Command",act:a})),
  ...(S.books||[]).filter(b=>b.name!==S.book).map(b=>({label:b.title,hint:"Book",act:()=>openBook(b.name)}))]}
function palDraw(){const q=fold($("palinput").value).split(/\s+/).filter(Boolean);const all=palItems();
 PAL.items=(q.length?all.filter(it=>{const f=fold(it.label+" "+it.hint);return q.every(w=>f.includes(w))}):all).slice(0,40);PAL.sel=Math.min(PAL.sel,Math.max(0,PAL.items.length-1));
 $("pallist").replaceChildren(...PAL.items.map((it,i)=>h("li",{class:i===PAL.sel?"on":"",role:"option","aria-selected":i===PAL.sel?"true":"false",onclick:()=>palRun(i)},it.label,h("span",null,it.hint))));
 if(!PAL.items.length)$("pallist").append(h("li",{class:"muted"},"Nothing matches."))}
function palOpen(){if(!S.book)return;$("palette").hidden=false;$("palinput").value="";PAL.sel=0;palDraw();$("palinput").focus()}
function palClose(){$("palette").hidden=true;ed.focus()}
function palRun(i){const it=PAL.items[i];palClose();if(it)it.act()}
$("palinput").addEventListener("input",()=>{PAL.sel=0;palDraw()});
$("palinput").addEventListener("keydown",e=>{if(e.key==="ArrowDown"||e.key==="ArrowUp"){e.preventDefault();PAL.sel=(PAL.sel+(e.key==="ArrowDown"?1:-1)+PAL.items.length)%Math.max(1,PAL.items.length);palDraw();
 const on=$("pallist").querySelector(".on");if(on)on.scrollIntoView({block:"nearest"})}else if(e.key==="Enter"){e.preventDefault();palRun(PAL.sel)}else if(e.key==="Escape"){e.preventDefault();e.stopPropagation();palClose()}});
$("palette").addEventListener("pointerdown",e=>{if(e.target===$("palette"))palClose()});
document.addEventListener("keydown",e=>{const mod=e.ctrlKey||e.metaKey;
 if(mod&&!e.shiftKey&&!e.altKey&&e.key.toLowerCase()==="k"){e.preventDefault();$("palette").hidden?palOpen():palClose();return}
 if(e.target===ed&&mod&&e.altKey&&e.key.toLowerCase()==="m"){e.preventDefault();addComment();return}
 if(e.target===ed&&isScript()&&e.key==="Tab"&&!mod&&!e.altKey&&LS.get("quill.sctab",true)){e.preventDefault();cycleElement(e.shiftKey?-1:1)}});

/* ---------------------------------------------------------------- start */
async function boot(again){if(!TOKEN){showPair();if(!new URLSearchParams(location.search).get("pair"))$("pairmsg").textContent="Scan the code shown on your PC (More, then Write from your phone) to connect this phone.";return}
 try{const st=await api("/api/status");S.status=st;S.plan=st.plan;$("planbtn").textContent=st.plan==="PRO"?"Pro":"Free plan";
  $("planbtn").title=st.license||"";showStats(st.stats);
  if(st.profile==="classroom"){document.body.classList.add("classroom");setPref("spell",true);ed.spellcheck=SP.avail===false}
  if(again)return;
  if(st.moved)banner(st.moved);
  if(st.recovery&&Object.keys(st.recovery).length)banner("Quill found writing that had not finished saving when it last closed, and put it back: "+Object.keys(st.recovery).join(", ")+".");
  if(LS.get("quill.tips",0)===1)$("tips").hidden=false;
  const oldGoal=+LS.get("quill.goal",0);if(oldGoal&&!st.stats.goal){api("/api/stats/goal",{goal:oldGoal}).then(showStats).catch(()=>{});LS.del("quill.goal")}
  await loadBooks();flushPending();if(SPR.on)sprintTick();setInterval(()=>{if(S.ch&&ed.value!==S.baseText&&!S.saving)save()},15000);
  if($("welcome").hidden)setTimeout(askUpdates,900)}
 catch(e){if(e.message!=="pair")saveState("err","Cannot reach Quill: "+e.message)}}
boot();
</script></body></html>"""


def render(token, pause_ms, version):
    """The page with this launch's token (or '' for a phone that still has to pair)."""
    return (PAGE.replace("__QUILL_TOKEN__", json.dumps(token)).replace("__PAUSE__", str(int(pause_ms)))
            .replace("__VERSION__", version).replace("__QMARK__", QUILL_MARK).replace("__QLOCK_L__", QUILL_LOCKUP_LIGHT)
            .replace("__QLOCK_D__", QUILL_LOCKUP_DARK).replace("__END_L__", ENDORSE_LIGHT)
            .replace("__END_D__", ENDORSE_DARK).replace("__FAVICON__", QUILL_FAVICON)
            .replace("__GUIDE__", guide_json()).replace("__SAMPLE__", json.dumps(SAMPLE_BOOK["folder"])))


def guide_json():
    """The guide as a JSON literal for the page script ('</' escaped so it cannot end the script element)."""
    return json.dumps({"title": GUIDE_TITLE, "sections": GUIDE}).replace("</", "<\\/")
