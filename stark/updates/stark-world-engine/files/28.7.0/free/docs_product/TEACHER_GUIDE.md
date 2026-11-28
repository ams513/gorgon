# Teacher Guide: Classroom mode

Classroom mode is part of the Stark Education Suite. It adds six starter lessons, a student-safe setting and a one-click hand-in to Stark World Engine. This guide says what it does, how to run it, and what it does not do. It makes no claims about test scores or learning results, because we have no data on that.

## What you need to know first

- **It runs offline.** The studio runs on the computer you start it on and opens in a browser on that computer. There is no account and no sign-in.
- **Students' work stays on that computer.** Games are saved in the `My_Games` folder. Nothing is sent to us or anyone else.
- **Licence.** Read `EULA.txt`, section 2, before you install on several computers. The Free edition is licensed to one person on computers that person owns. There is no separate school licence today. If you need to install on a shared lab, ask the seller first and get the answer in writing.
- **Free edition limit.** The Free edition exports the first level of a game only, and adds a small "Made with Stark World Engine" note to the exported game. Lesson 4 (Three Acts) has three levels, so a student on the Free edition will only export Act 1. Use a Pro or Studio key on the teacher's machine if you need all three levels, or have students hand in the saved game from the studio screen instead.
- **Computers.** Windows 10 or 11, or macOS or Linux with Python 3.10 or newer. A current Chrome, Edge or Firefox with WebGL2. We have not tested on school-managed or locked-down machines, so try one before the lesson.

## Turn on the student-safe profile

Open `config.json` in the studio folder (or in `Documents\Stark World Engine` for the Steam version) and set:

```
"classroom_profile": true
```

Save the file, then restart the studio. To turn it off, set it to `false`.

With the profile on:

- The update controls are removed from the page, and the studio refuses update checks and the Godot download. These are the only parts of the studio that can reach the internet.
- The publishing helper (`publishers.py`), YouTube upload and the Creator Hub, which includes the phone hub, refuse to start.
- The studio itself still only accepts connections from the same computer (`127.0.0.1`).

Not changed by the profile: building, playing, saving, the Pixel-Art Canvas and sound tools, and exporting to HTML5. Godot and Unreal export still work for Pro and Studio keys, because they only write files on the computer.

Limits to be honest about: the profile is a setting in a text file. A student who can edit that file can change it, and it does not replace your school's own network filtering or device management. If students can open files in the studio folder, protect `config.json` with your normal file permissions.

## The six lessons

Open the **Learn** tab (called Classroom before 28.7). Pick a lesson, then press **Start this lesson**. Each press saves a new copy in `My_Games`, so students never overwrite the starter or each other's work on a shared machine unless they share a folder. Every lesson shows a goal, steps and a "Check your work" list. The times are our planning guesses, not measured results, so adjust them for your class.

### 1. Loops and patterns: Lantern Row (about 40 minutes)
Concept: loops and patterns. Students find the step in a row of lamps and a ring of crystals ("repeat 6 times, move 4"), extend it, then make a pattern of their own. This is loop thinking done by hand. The studio has no code editor, so students do not write loops in a programming language.

### 2. Cause and effect: The Dark Path (about 45 minutes)
Concept: cause and effect with quests. Students trace the chain from talking to Mira, to a quest starting, to crystals counting, to the quest finishing. They then change the numbers and add a second quest that depends on the first.

### 3. Coordinates and maps: Treasure Map (about 40 minutes)
Concept: coordinates and maps. Lamps mark the x and z axes every 10 metres. Students read positions, follow a quest that lists three places as (x, z), draw the map and place objects at coordinates a partner gives them. The coordinate numbers are the studio's metres, not map grid squares.

### 4. Story structure: Three Acts (about 50 minutes)
Concept: story structure. Three levels, one for setup, conflict and resolution. Students replace the problem, the obstacle and the ending so the three acts still fit together.

### 5. A simple economy: Market Day (about 45 minutes)
Concept: items, value and trade. Students read item values, add up the gold a player can earn, change a quest's price and argue for a fair one. It is a small economy: one seller, a few items and two chests.

### 6. Debugging: The Broken Level (about 45 minutes)
Concept: debugging. The level cannot be finished. Students find out why, state a guess, change one thing and test again. The **Check my level** button lists things worth a look. It points at problems and never fixes them.

## Answer key for Lesson 6 (teachers only)

The key is not shown in the studio. The starter level has four bugs:

- The door needs a Brass Key and no key exists in the level (place the item).
- The quest needs 3 crystals and only 1 exists (place 2 more).
- The Warden points at the dialogue "warden_chat", which does not exist (the existing one is "warden_talk").
- The "warden_talk" dialogue never starts the quest "q_gate", so it can never begin.

The checker lists all four at once, in plain words. Do not hand students this list before they try.

## Check my level: what it can and cannot see

It checks four kinds of problem: a locked door with no key anywhere; a quest asking for more of an item than exists; a person pointing at a dialogue that does not exist (or a quest given by a person who is not in the game); and a quest that nothing can start. It also notes when no quest ends the game. It does not play the game, so it cannot find a level that is merely too hard, too dull or badly laid out. Always have students play their level from the start.

## Hand-in: the finished game and a reflection

On the Learn tab, students write their name (optional), answer the three reflection questions for the lesson, and press **Export my game + reflection**. Two files appear in `My_Games\<game name>\export\`:

- `<game>_cartridge.html`: the finished game as one HTML5 file. It plays offline in a current browser, with its art inside the file.
- `<game>_REFLECTION.txt`: a short text file with the student's name, the lesson, a count of levels, objects and quests, the checker's result and the student's answers. It ends with a line saying it was written on this computer and nothing was uploaded.

Collect the two files the way you collect other student work. The studio does not send them anywhere. The reflection is text the student typed, so read it for what the student understood; the counts in it come from the saved game, not from judging quality.

## Running a session

1. Before the class, start the studio on one machine, open the Learn tab and do the lesson yourself. It is the fastest way to find out what your computers will do.
2. Set `"classroom_profile": true` on each student computer.
3. Students open the Learn tab, choose the lesson and press **Start this lesson**.
4. Students tick the "Check your work" items as they go. The ticks are not saved; they are for the student.
5. At the end, students write the reflection and export.

## What it does not do

- It does not grade, track progress or keep a record of students. There is no teacher dashboard.
- It is not a programming language course. The lessons use ideas from programming (patterns, cause and effect, debugging) through building a game.
- It does not log in, sync or collect anything, and it has no way to share games between computers. Move the exported files yourself.
- It has not been tested in classrooms, and we have no measured learning results to show you. Try it with a small group first.
- It does not make your school's use compliant with any privacy law or policy. It sends no data, which makes review simpler, but the decision stays with your school.
- The Godot and Unreal exports are advanced extras and are not part of the lessons.

## If something goes wrong

- The Learn tab says nothing happens when you press Start: check that the studio is still running and that the `My_Games` folder is writable.
- Updates and the phone hub say they are "switched off in classroom mode": that is the profile working. Set `classroom_profile` to `false` to use them.
- Anything else: see `TROUBLESHOOTING.md`, and the **Fix my setup** button in the keyboard-shortcuts card.
