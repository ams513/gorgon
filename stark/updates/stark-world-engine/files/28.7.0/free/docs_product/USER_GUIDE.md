# Stark World Engine User Guide

Welcome! This guide covers every part of Stark World Engine in short, friendly sections. You don't need to read it in order: jump to what you need.

**Version:** 28.7 · **Editions:** Free, Pro, Studio

---

### What's new in 28.7
All of this is in **every edition**, Free included. Everything players use runs inside the one-file HTML5 game you export.

- **Variables and on-screen counters:** named numbers such as *coins* or *lives*, changed by a new THEN block and shown in the corner while playing. See 8.11.
- **More WHEN and IF lines:** *every few seconds* (repeats), *the player presses a key*, *a variable reaches a value*, and an IF line for a variable or a *random chance*. See 8.12.
- **Touch controls and gamepads:** exported games get an on-screen joystick and buttons on phones and tablets, and play with a gamepad. See 10.2.
- **Read text aloud:** players can have conversations and subtitles read by a voice built into their computer. See 10.2.
- **Tune Maker:** a step grid for melody and drums. The tune plays in Play mode **and inside the exported game**. See 16.
- **Guided tutorials:** six short lessons in the new **Learn** tab that tick themselves off as you work. See 13.1.
- **Remix:** open a game someone shared (its `.html` file or `world.json`) as a new game you can look inside and change. See 13.2.
- **Copy, paste, duplicate and snap to grid** while building. See 5.
- **itch.io zip:** one click makes a zip ready to upload to itch.io. See 17.
- **Watch panel:** while you test in Play mode, see your variables, flags, quests and the triggers that fired. See 10.3.

### New in 28.6
All of this is in **every edition**, Free included, and runs inside the one-file HTML5 cartridge you export.

- **Logic tab (when / then blocks):** make things happen without scripting. See 8.8.
- **Cutscenes:** a timeline of camera moves, lines of text, walking people, sounds and fades. See 8.9.
- **Behaviours and shops:** people can patrol, follow, run away or fight at your side; enemies can patrol a path, guard a post or run when hurt; a person can run a shop that buys and sells. See 8.10.
- **Three new templates:** *The Missing Bell* (a mystery village), *Cinder Crypt* (a dungeon crawl) and *Pennant Run* (a time trial). See section 13.
- **For your players:** remappable keys, a colour-blind palette, subtitles, slow mode, 10 built-in achievements (plus your own) and a local high-score table. See 10.1.

---


## 1. What Stark World Engine is (and isn't)

Stark World Engine is an **offline game builder** for 3D and 2D adventure games that runs in your browser, plus (in the Studio edition) a **creator suite** that helps you film, edit and publish videos about your games.

- **It runs on your computer.** The studio listens on `127.0.0.1` (this computer only). No account, no cloud, no subscription. Nothing you make is uploaded anywhere.
- **It makes stylized games.** Worlds use bundled CC0 models and textures (or your own glTF imports) with PBR lighting, HDRI skies, fog and weather moods. It runs in a browser; it is not a replacement for a full engine.
- **Games can be real adventures.** Quests, people to talk to, items and equipment, experience and levels, enemies and bosses, several levels in a row and three save slots all run inside the game you export.
- **It is a fast way to start.** When you want a full engine, the Pro and Studio editions export your world to **Godot 4** or **Unreal Engine 5** as a starting scene you can keep building on.

### What's in each edition

| Edition | What you get |
|---|---|
| **Free** | 3D + 2D builder, "Make me a game", quests, dialogue, items, multi-level games, 5 Living World Moods, 3 starter games, Pixel-Art Canvas, SFX Forge, soundtrack maker, HTML5 cartridge export, 8 interface languages |
| **Pro** ($30) | Everything in Free, cartridges export every level with no note, + Godot 4 project export + UE5 project export + example cartridges |
| **Studio** ($50) | Everything in Pro + creator suite for Windows: Setup Wizard, OBS setup, auto video editor, phone hub (scheduler, teleprompter, review & approve), publishing helpers |

---

## 2. Starting the studio

| | How to start |
|---|---|
| **Windows** | Double-click **`START_STARK_WORLD_ENGINE.bat`**. Python is built in (in the `runtime` folder), so there is nothing else to install. |
| **macOS** | Double-click **`start_stark_world_engine.command`**. The first time, right-click it > **Open** > **Open**, because it doesn't come from the App Store. Needs Python 3.10 or newer from python.org (see the [Quickstart](QUICKSTART.md)). |
| **Linux** | In a terminal in the folder: `bash start_stark_world_engine.sh`. Needs Python 3.10 or newer from your package manager. |

A small window opens. Leave it open while you work: it *is* the studio's engine. Close it (or press Ctrl+C in it) when you're done. Your browser opens the studio at `http://127.0.0.1:8505` (or the next free port up to 8520).

### The setup check
Before the studio starts, the launcher runs a quick **setup check** (`doctor.py`). It looks for the usual first-install problems and tells you, in plain words, how to fix them:

- running straight from inside the zip (it offers to open the folder with the zip so you can extract it);
- a folder where the studio can't save, such as `Program Files` (press **Y** and it copies itself to your Documents folder and starts from there; the original isn't touched);
- a very long folder path, or a folder inside OneDrive, Dropbox, iCloud or Google Drive (a tip, not a stop);
- missing or damaged program files (for example, removed by an antivirus);
- the built-in Python on Windows (it repairs one small settings file by itself and keeps a backup);
- whether a port between 8505 and 8520 is free, and whether the studio is **already running** (then it just opens your browser to it);
- whether Chrome, Edge or Firefox is installed, low disk space, and a computer clock that looks wrong;
- whether your license key file is valid;
- Studio edition: whether FFmpeg and OBS are installed and whether the phone hub is allowed through the firewall.

Problems marked **[STOP]** must be fixed first; **[TIP ]** items are advice and the studio starts anyway. Everything is also written to `doctor.log` in the folder.

Inside the studio, press **?** (or click the **?** button at the top) and click **Fix my setup** to run the same checks at any time.

---

## 3. The studio screen

When the studio opens you'll see:

- **Top bar:** **Make me a game**, your games list, a level picker (shown when a game has more than one level), **New 3D world**, **New 2D world**, the **Name** box, **Save**, **Undo**, **Redo**, **Build**, **▶ Play**, the language picker and the **?** button. A small status text shows what just happened ("Saved", "Export ready", or "edited - autosave in a moment").
- **Mood bar:** the five Living World Moods. Click one to relight the level you're editing.
- **The stage:** your world, live.
- **Side panel tabs:** **Tools**, **World**, **Quests**, **Dialogue**, **Items**, **Logic**, **Pixel Art**, **SFX Forge**, **Tune**, **Assets**, **Learn**, **Export**.

**Languages:** EN, ES, FR, DE, PT, IT, JA, ZH (English, Spanish, French, German, Portuguese, Italian, Japanese, Chinese). Pick from the drop-down at the top; the studio remembers your choice. The main buttons and tabs are translated; the tool palette, editor panels and in-game text are in English.

**First visit:** a short three-step tour offers **Play a starter game now** or **Make me a game**. Click **Skip tour** to hide it for good.

---

## 4. Make me a game

Don't want to start from an empty world? Click **Make me a game** at the top left.

1. **Pick a theme:** Spooky Swamp, Sky Islands, Frozen Kingdom, Lava Fortress, Sunken City or Cozy Village.
2. **Pick a size:** **Snack** (1 level, about 15 minutes), **Evening** (3 levels, about an hour) or **Epic** (6 levels, a few hours).
3. Optional: type a **Seed**. The same seed and theme always give the same game, so you can share a seed with a friend. Leave it empty for a surprise.
4. Optional: tick **2D platformer instead of 3D**.
5. Click **Make it and play!**

The studio builds a complete game: villages with people who give quests, monsters, treasure chests, keys and locked doors, checkpoints, portals between levels and a boss at the end (bigger games also mix in side-scrolling 2D levels). It is saved straight away and you start playing. Press **Build** (or **Ctrl+Enter**) to change anything: it's an ordinary game you can edit like any other.

---

## 5. Building 3D worlds

1. Click **New 3D world** (or pick a game from the list, or use **Make me a game**).
2. Give it a name in the **Name** box.
3. Open the **Tools** tab, pick something, then **click the ground** to place it:
   - **Nature:** **Tree**, **Rock**, **Ruin**, **House**, **Lamp** (glows at night), **Crystal** (collectible).
   - **Creatures:** **Slime** (chases you), **Archer** (keeps its distance and shoots), **Brute** (slow, tough, smashes the ground), **Wisp** (fast floating spirit), **Boss** (a big enemy with a health bar and special attacks).
   - **Story:** **Person** (someone to talk to), **Item** (something to pick up), **Chest** (opened with E), **Locked door** (needs a key), **Portal** (to the next level), **Checkpoint** (heals and saves progress), **Player start**.
   - **Edit:** **Select / move** (click a thing to edit it, drag to move it) and **Erase**.
4. With **Select / move**, click a placed thing to edit it in the panel: a person's name, id and dialogue; an enemy's kind, name, health and wander radius; which item a chest holds and how much gold; which key opens a door; where a portal leads and which quest unlocks it; size and rotation.

**Good to know**

- In Build mode: drag to turn the camera, mouse wheel to zoom, **WASD** to move the camera, **Q / E** to turn it, **Delete** removes the selected thing.
- A level can hold up to 5,000 objects, and a game up to 25 levels.
- Your games live in the `My_Games/` folder next to the studio, one folder per game, each with a `world.json` file.

### Copy, paste, duplicate and snap
- Select a thing (Select / move), then **Ctrl+C** copies it, **Ctrl+V** pastes a copy 2 metres away and **Ctrl+D** duplicates it. The inspector has the same **Duplicate**, **Copy** and **Paste** buttons. Copies of people, doors and enemies get their own new id (and people a numbered name), so triggers and conversations still point at the right one.
- **Snap** (next to Drone in the Tools tab): off, 1 m, 2 m or 4 m. Things you place or drag land on that grid (3D levels). The choice is remembered on this computer.
- In 2D levels only people can be copied; tiles are painted with the Tools.

### Drone mode and postcards

- **Drone (P):** a slow cinematic camera flight with letterbox bars. Great for screenshots and trailers. Press **P** again (or click the button) to stop.
- **Postcard PNG:** saves a picture of the current view into `My_Games/<game>/postcards/`.

---

## 6. Building 2D worlds

1. Click **New 2D world**. You get a flat ground, a start point and a goal flag.
2. In the **Tools** tab, pick a tile and **click tiles** on the stage to paint:
   - **Tiles:** **Ground**, **Pearl** (collectible), **Spike**, **Water** (swim through it), **Flag** (goal: next level or victory), **Exit** (swirling exit to the next level), **Start**.
   - **Creatures:** **Slime** (stomp it), **Archer** (shoots at you), **Boss** (stomp it several times).
   - **Story:** **Potion**, **Key**, **Locked door**, **Checkpoint**, **Person**.
   - **Edit:** **Select** (click a person to edit them) and **Erase**.
3. **Save**, then **▶ Play**. Move with A/D (or arrows), jump with Space.

Tip: draw your own hero in the **Pixel Art** tab and click **Use as player sprite** (see [Pixel Art Canvas](#14-pixel-art-canvas)).

---

## 7. Moods, weather and the World tab

**Living World Moods** change the sky, fog, sun, ground and leaf colours and the floating particles with one click. Each level has its own mood.

| Mood | Feel | Particles |
|---|---|---|
| **Bioluminescent Twilight** | deep blue night, glowing teal foliage | fireflies |
| **Alpine Sunset** | warm orange sky, green meadows | pollen |
| **Sub-Zero Aurora** | snowfield under a northern-lights sky | snow + aurora |
| **Crimson Citadel** | red dusk, dark stone | embers |
| **Sunken Ruins** | underwater teal light | bubbles |

The **World** tab holds everything that isn't a placed object:

- **Game:** the story text shown on the title screen, **Difficulty** (Easy, Normal, Hard) and **Made by**.
- **Soundtrack:** see [Music and sound](#16-music-and-sound).
- **This level:** level name and intro text, **Weather** (mood default, clear, rain, thunderstorm, snow, embers, pollen, bubbles, fireflies) and, on 3D levels, **Day and night** (time stands still, or a 10-minute, 5-minute or 90-second day), **Time of day**, **Water** (lakes and rivers), **Hills** and **World size**.
- **Levels:** see [Multi-level games](#86-multi-level-games-campaigns).
- **Start from a template:** open one of the starter games or the simple "Classic" templates as your own copy.
- **Safety net:** **Restore an earlier version...** (see [Saving](#11-saving-undo-and-backups)).

---

## 8. Quests, people, items, XP, enemies and campaigns

Everything in this section works in the studio's **▶ Play** mode and in exported HTML5 cartridges.

### 8.1 Quests
Open the **Quests** tab. Click **Fetch**, **Hunt**, **Talk**, **Explore** or **Escort** to add a ready-made quest to the level you're editing, then change it:

- **Title**, **Description** and **Given by** (a person, or "starts by itself").
- **starts when the level begins** and **finishing it wins the game**.
- **Steps**, each one of: **Collect items** (optionally "hand them over"), **Defeat enemies** (any, or one kind, or one named enemy), **Talk to someone**, **Go to a place** (click **Pick the spot on the map**), **Escort someone**. Add more with **+ step**.
- **Reward:** XP, gold and an item.

People who have a quest for the player show a gold **!**, and a gold beam points the player to the next objective. Tip: place a **Person** first, then click **Fetch**: you get the quest *and* that person's dialogue in one go. **Write the giver's lines for me** rewrites the offer and thank-you lines at any time.

### 8.2 People and dialogue
Place a **Person**, select it, and click **Write dialogue for this person** (or **Give them a quest**). The **Dialogue** tab shows the conversation:

- A conversation is a set of **lines**. Each line has a speaker and text, and up to 8 **answers**.
- An answer can **end the talk** or go to another line, and can also **start quest**, **finish quest**, **give item** or **take item**.
- **Say something different when...** rules pick the first line depending on whether a quest is done, in progress or not started, or whether the player has an item.
- **+ line**, **+ answer**, **+ rule**, **New** and **Delete this dialogue** do what they say.

While playing, walk up to a person and press **E** to talk; press **1-4** to pick an answer.

### 8.3 Items and equipment
The **Items** tab lets you make your own items with **+ new item**: **Quest item**, **Potion / food** (heals), **Key**, **Equipment** (weapon, armour, boots or charm, with attack, defence and max HP bonuses) or **Treasure** (turns into gold). There are also built-in items, such as Crystal, Potion, Elixir, Old Key, swords, armour, boots and a charm. Place items with the **Item** or **Chest** tool.

While playing, press **I** for the bag and **H** to drink a potion.

### 8.4 Enemies and bosses
Enemies wander, notice the player, chase and attack. Each kind fights differently (see the tool list above), and a **Boss** has its own health bar and special attacks. Hits use hit-stop and screen-shake so they feel punchy. On a quest, **Defeat enemies** can count any enemy, one kind, or one enemy you gave an id.

### 8.5 XP, levels and abilities
Defeating enemies and finishing quests earns XP. Each new level restores health and makes the player stronger, and some levels unlock an ability:

| Level | Ability | Key |
|---|---|---|
| 2 | Dash | Shift |
| 3 | Whirl (hits everything around you) | K |
| 4 | Star Bolt (a ranged shot) | F |

Falling in battle sends the player back to the last checkpoint and costs 10% of their gold.

### 8.6 Multi-level games (campaigns)
A game can have several levels. In the **World** tab, under **Levels**, click **+ 3D level**, **+ 2D level** or **Duplicate this level**; **Up** moves a level earlier and **X** deletes it. Use the level picker at the top to choose which level you're editing.

Portals (3D) and the flag or exit (2D) take the player to the next level. A portal can also lead to a specific level and stay closed until a quest is finished. XP, items and gold carry over from level to level.

### 8.7 How the player wins
- In a game **with quests:** by finishing the quests marked **finishing it wins the game** (or, if none is marked, all quests). A dialogue answer can also end the game.
- In a 3D game **without quests:** by collecting every crystal.
- In 2D: by reaching the flag on the last level.

### 8.8 The Logic tab: when / then blocks
Open the **Logic** tab. Each trigger is a card: **WHEN** something happens on this level, **THEN** a list of blocks runs in order. It works in Play mode and in the exported game.

**WHEN** can be: the player enters or leaves an area (type X, Z and the radius, or press **Pick on map**), the level starts, the player talks to someone, an enemy is defeated (a particular one by its Id, or any of a kind), the player picks up an item, a flag is set, a quest is finished, or some seconds have passed. An optional **IF** line adds a condition (the player has or lacks an item, a quest is finished or active, a flag is set, or a level is reached). **Only once** is on by default; switch it to *every time* for things like a healing spring.

**THEN** blocks: open, close or toggle a door; play a sound (with a caption for subtitles); start a conversation; show a line of text; set or clear a flag; give or take an item; change gold; give XP; heal; start or finish a quest; play a cutscene; move the player; go to another level; win the game; unlock an achievement; shake the screen; wait some seconds. Use the arrow to move a block up and **x** to remove it. A *conversation* or *cutscene* block makes the list wait until it is over.

Tips:
- To open a door from a trigger, select the door in the Build tab and look at its **Id** (the Logic tab gives unnamed doors an Id for you). Setting **Who can open it** to *Only a trigger (sealed)* makes it ignore keys; pressing E says it will not budge.
- Flags are just names you invent (for example `met_the_smith`). Dialogue answers can also set flags, so a conversation can drive a trigger.
- In **2D levels** area triggers use tile columns and rows; type them in (Pick on map is 3D only).
- Dialogue answers can also open a door, play a cutscene, unlock an achievement or open a shop (`door`, `cutscene`, `achievement`, `shop` in the world file).

### 8.9 Cutscenes
In the **Logic** tab, **Cutscenes**: press **+ new**, then add steps. Every step has a time in seconds and one action:
- **Camera move:** where the camera goes (X, Z, height), what it looks at, and how long it takes. **Use editor view** copies the camera you are looking through in Build mode.
- **Show a line of text** (a subtitle with a speaker name), **Walk someone** (a person, an enemy or `player`) to a spot, **Play a sound**, **Fade the screen**, **Shake the screen**, and the quick blocks (flag, give, take, gold, XP, heal, door, move the player, achievement).

While a cutscene plays the player cannot move, black bars slide in, and **Esc / Space / Enter** skips it (turn that off per cutscene with *Esc skips it: no*). Skipping still applies what the rest of the cutscene would have done (flags, items, where people end up). Afterwards a flag named `cut_<id>` is set, so a trigger can follow it. **Test it in Play** runs it right away. Start one from a THEN block or a conversation answer.

Limits: on 2D levels cutscenes play text, sounds, fades, walking and the quick blocks, but the camera steps do nothing.

### 8.10 Behaviours and shops
Select a person or enemy in the Build tab (3D levels).
- **People:** *Stands still*, *Patrols a path* (add points with **+ add point on map**), *Follows the player*, *Runs away from the player* (set the radius), or *Companion* (follows and fights nearby enemies, a little each second). Companions and followers wait in their level; they do not travel through portals.
- **Enemies:** *Wanders* (the old behaviour), *Patrols a path*, *Guards its post* (never chases far from it) or *Runs when hurt*. Keep a patrol path within about 25 metres of where the enemy stands.
- **Shops** (people, 2D and 3D): choose *Buys and sells*, then **+ item for sale**. Leave the price empty for an automatic price (from the item's **Shop value** in the Items tab, or its gold, heal and attack numbers). Leave stock empty for unlimited. The shop pays a share of an item's value when the player sells (default one half; keys and quest items are never bought). Talk to the person with E: a person with a shop but no conversation opens the shop at once; with a conversation, an extra answer *Show me your wares* appears at the end.

### 8.11 Variables and on-screen counters
A **variable** is a named number the game keeps, such as `coins`, `lives` or `keys_found`. In the **Logic** tab, under **Game rules > Variables**, press **+ variable**. Each one has an **Id** (used by blocks), a **Name shown** to players, the number it **Starts at**, an optional **Lowest** and **Highest** (it never goes past them; empty means no limit), and **On screen**: hidden, *number* or *number / highest* (for example `Lives 3 / 5`). Shown variables appear in the player's corner panel. A game can have up to 30.

- Change one with the THEN block **Change a variable**: *add*, *subtract*, *set to*, or *random number from* (a whole number between the two you type, both included). The block also works in cutscenes, and a conversation answer can change a variable (`var`, `op`, `num` in the world file).
- Test one with an IF line (**a variable compares**: is equal to, is not, is less than, is at most, is more than, is at least) or with the WHEN **a variable reaches a value**.
- Variables are saved in the player's save slots and come back when they load. Renaming a variable's Id updates the blocks that use it.

### 8.12 More WHEN and IF lines
- **Every few seconds (repeats):** runs again and again while the level is played. It pauses while a menu, a conversation or a cutscene is open. At least half a second.
- **The player presses a key:** a letter, a digit, Space, Enter or an arrow key. Honest note: on phones and gamepads only the keys the game already uses (moving, Space, E, J and the rest) can be pressed, so pick one of those if the game must work everywhere.
- **A variable reaches a value:** fires when the test becomes true (for example `coins is at least 10`). With *Only once: every time* it fires again each time the test turns true after being false.
- **IF random chance (%):** the trigger only runs that percentage of the time, for a surprise or a rare drop.

---

## 9. The look

The 3D view is drawn with WebGL2, right in the browser (new renderer in 28.5): PBR materials (albedo, normal,
roughness/metal, occlusion), image-based lighting from HDRI skies, soft cascaded sun shadows, screen-space ambient
occlusion, bloom, ACES tone mapping and colour grading per mood, height fog that glows toward the sun, a textured
terrain that blends grass, dirt paths, rock, sand and snow, grass and trees that sway in the wind, water with
refraction, reflections and shore foam (ice in the Sub-Zero mood), point lights from lamps and crystals, and FXAA
(TAA on High). Levels with a day-night cycle blend between a day, a sunset and a night HDRI sky and move the sun.

**Graphics presets** (Esc menu > Graphics, or the menu on the Build performance meter): **Low** turns off shadows
(and so soft-shadow filtering), ambient occlusion, bloom, TAA, normal maps and the image-based lighting bake (it uses a
simple sky/ground light instead), draws fewer plants, renders at half resolution and loads textures lazily: small
copies, after the first frames, one per frame. It is meant for older laptops and software rendering. **Medium** is
the everyday preset; **High** adds a third shadow cascade, softer shadows, screen-space reflections on water and TAA.
**Auto** starts from a hardware check made on first start (software rendering or an old integrated chip: Low; a
GeForce RTX or similar card: High; anything else: Medium) and steps down after the world has loaded if it runs slowly.
Picking Low, Medium or High always overrides the check.

**Performance meter** (Build mode, top right of the 3D view): the measured time to draw one frame over the last
second on your computer, for the preset in use. Green is smooth (45+ fps), amber is playable (25-45 fps), red is slow:
remove some objects or pick a lower preset.

**The art.** Characters, houses, trees, plants and rocks are real glTF 2.0 models made by artists and released as CC0:
Quaternius (Universal Base Characters, Universal Animation Library, Medieval Village MegaKit, Stylized Nature MegaKit)
and Poly Haven (HDRI skies, ground and brick textures). The characters' outfits were painted onto the Quaternius base
bodies by our own tool. All of it is listed in `assets/library.json` and `assets/CREDITS.txt`, and every export credits
the art it uses.

**Your own art: the Assets tab.** Drop a `.glb` (or a `.gltf` with its `.bin` and textures), PBR textures (`.png` /
`.jpg`; albedo, normal, ORM/roughness, metallic and AO are recognised by their file names) or an `.hdr` sky. Give it a
name, a size, the licence and who made it, then press **Import**. It is stored in `My_Assets/` next to `My_Games/`, and
**Place** puts it in your world (an `.hdr` becomes the level's sky). Files that need Draco or meshopt compression are
refused with a note: export them again without compression.

**Honest note:** this is a stylized look designed to run in a browser. It is not a replacement for a full engine; for
complex physics or console builds, export to Godot or UE5 and continue there.

---

## 10. Playing your game

Click **▶ Play** (or press **Ctrl+Enter**) at any time to test. Click **Build** (or **Ctrl+Enter** again) to go back to editing; your changes are kept.

| | 3D | 2D |
|---|---|---|
| Move | WASD or arrow keys | A/D or arrow keys |
| Jump | Space | Space |
| Attack | J or mouse click | J, or stomp enemies by landing on them |
| Talk, open, unlock | E | E |
| Pick an answer | 1-4 | 1-4 |
| Camera | drag with the mouse, or Z / C | – |
| P | drone camera | postcard picture |

| In both | |
|---|---|
| **L** | quest log |
| **I** | bag (inventory and equipment) |
| **H** | drink a potion |
| **Shift / K / F** | Dash, Whirl (3D only), Star Bolt (once unlocked) |
| **Esc** | menu: save, load and options |

The HUD shows level, health, gold, attack and defence, your abilities and the current quest. Press **?** in the studio to see all shortcuts on one card.

### The in-game menu (Esc)
- **Save** and **Load** in **three save slots**, plus **Autosave (checkpoints)**, which is written at every checkpoint.
- **Difficulty:** easy, normal or hard.
- **Graphics:** auto, high, medium or low, and **Sound: on / off**.
- **More:** **Accessibility & controls**, **Achievements** and **High scores** (see 10.1).

Saves are kept in the browser on that computer (per game). A player of your exported cartridge can save, close the browser and continue later in the same browser.

### 10.1 Options your players get (nothing to set up)
Every exported game has these on the title screen and in the Esc menu:
- **Remappable keys:** click an action, press the new key. Swapping two keys works; Esc, Tab, Enter and the number keys stay fixed. Arrow keys keep working as extra movement keys.
- **Colour-blind palette:** red-weak, green-weak or blue-weak. It shifts the colours the player has trouble telling apart across the whole picture and the HUD.
- **Subtitles:** on, large or off. They show cutscene and event lines and name the sounds in square brackets, for example [door opens] (the caption you typed on the sound block). Conversations always show their text.
- **Slow mode:** 100%, 75% or 50% game speed.
- **Achievements:** 10 built in (first defeat, 25 defeats, a boss, 100 and 1000 gold, level 5, one and five quests, two levels, the finish) plus your own from the Logic tab (**Game rules**: unlock after N defeats, gold, a level, quests, a flag, an item, winning, levels visited, or winning within N seconds; or from a trigger). Unlocked achievements are remembered per game in that browser.
- **High scores:** the top 10 for the game, kept in that browser, shown after winning (the player can type a name) and from the menu. In **Game rules** choose *Fastest time first* for a time trial and **Show a timer** to put the clock on screen. The clock runs while the game is not paused.

Settings are stored in the player's browser. They need no account and are never sent anywhere.

### 10.2 Touch controls, gamepads and reading aloud (nothing to set up)
- **Touch:** on a phone, tablet or touch laptop an on-screen joystick (left) and **Jump**, **Hit** and **Use** buttons (right) appear as soon as the screen is touched, with **Potion**, **Bag**, **Quests**, **Menu** and the abilities the player has unlocked above the joystick. They hide in menus and conversations (tap the answers), and a **Skip** button shows during cutscenes that can be skipped. Players can set them to Auto, On or Off in **Accessibility & controls**. Dragging on the picture turns the camera in 3D.
- **Gamepads** (standard layout, for example Xbox or PlayStation pads in Chrome, Edge or Firefox): left stick or D-pad moves, **A** jump, **X** attack, **B** talk or back, **Y** potion, **LT** dash, **RT** whirl, right-stick click Star Bolt, **LB / RB** or the right stick turn the camera, **Back** bag, left-stick click quests, **Start** menu. In menus, conversations and the title screen the D-pad picks a button and **A** presses it.
- **Read text aloud:** in **Accessibility & controls**, *Read text aloud: On* reads conversations and subtitles (Slow, Normal or Fast). Only voices built into the player's computer are used; online voices are never used, so on a computer without an offline voice nothing is read (the menu says so).

### 10.3 The Watch panel (studio only)
Tick **Watch panel** in the Tools tab. While you play in the studio, a small panel shows the player's level, HP, gold and XP, every variable, the flags that are set, each quest's state and the triggers that have fired. Use it to find out why a trigger does not run. It never appears in exported games.

---

## 11. Saving, undo and backups

- **Save** (or **Ctrl+S**) writes your game to `My_Games/<game name>/world.json`. The studio also **autosaves** about 15 seconds after your last change, and when you switch to another game.
- **Undo / Redo** (Ctrl+Z / Ctrl+Y) go back and forth through your recent changes.
- Every save keeps the three most recent older versions in `My_Games/.backups/`. **World** tab > **Restore an earlier version...** lists them; **Restore this one** opens that version, and **Save** makes it current again.
- Your pictures and sounds are saved in your game's folder too (`assets/` and `postcards/`).
- To back up everything, copy the `My_Games` folder. To move to a new computer, copy it into the new studio folder.
- If you try to close the tab with unsaved changes, the browser asks first.

---

## 12. Shortcuts while building

| Key | Does |
|---|---|
| Click | place the selected thing |
| Drag | turn the camera (or move a selected thing) |
| Wheel | zoom |
| WASD | move the camera |
| Q / E | turn the camera |
| Ctrl+Z / Ctrl+Y | undo / redo |
| Ctrl+S | save |
| Ctrl+Enter | play / back to building |
| Delete | remove the selected thing |
| Ctrl+C / Ctrl+V / Ctrl+D | copy / paste / duplicate the selected thing |
| ? | the shortcuts card (with **Fix my setup**) |

---

## 13. Templates and starter games

The studio comes with three starter games, each with several levels, quests and people to talk to:

- **Firefly Fen** (3D, 3 levels): a glowing swamp.
- **Summit Snack Run** (3D, 2 levels): a sunset mountain.
- **Lantern Leap** (2D, 3 levels): a platformer through sunken ruins.

**World** tab > **Start from a template** also offers **Classic Crystal Hunt** and **Classic Platformer**: the simplest games (collect every crystal, or reach the flag) without quests.

Three more starters show off the Logic tab:
- **The Missing Bell** (3D, one village): a mystery. The festival bell has vanished; question the baker, the weaver and the fisher in turn, find the miller's key, and a cutscene reveals who took it. A watchman patrols, a pedlar runs a shop, and the well heals.
- **Cinder Crypt** (3D, one dungeon): buy supplies from the Quartermaster, take Mara the Sellsword along as a companion, find the crypt key past archers and guards, defeat the Crypt Captain to open the sealed warden gate (a trigger opens it), and wake the Cinder Warden in a cutscene.
- **Pennant Run** (3D, a time trial on foot in Swift Boots): nine gates in order, a timer on screen, patrolling slimes crossing the course, and a fastest-first high-score table with *Quick Feet* and *Blazing* achievements.

### 13.1 Guided tutorials (Learn tab)
The **Learn** tab starts with six short tutorials: *Your first world*, *Someone to talk to*, *A quest with a reward*, *A door that opens by magic*, *Count coins with a variable* and *Music and sharing*. Press one and a card appears on the stage with the steps. Each step ticks itself off when the studio sees you have done it (for example, three trees placed), and **Show me** opens the right tab or tool. Finished tutorials get a tick. Progress is kept in this browser only. Below them are the teacher lesson packs (see the Teacher Guide).

### 13.2 Remix: open a game someone shared
In the **World** tab, **Open a game someone shared (remix)** takes a game file (`.html`) exported from Stark World Engine, or a `world.json`. It is saved as a **new** game called *<name> (remix)* that you can play, look inside and change; the file you picked is never changed and nothing is uploaded. The World tab then shows where the remix came from.

Honest notes: only the game data is read from the file (nothing in it runs). A game exported with the Free edition holds only its first level, so that is all a remix gets. Imported models or skies that lived only on the other person's computer are left out (the studio says how many), and the other person's soundtrack file is not inside a game file.

---

## 14. Pixel Art Canvas

A 16 × 16 sprite editor in the **Pixel Art** tab.

- Pick a colour from the palette (the last swatch is the transparent "eraser") and click or drag on the canvas.
- **Clear** wipes the canvas.
- **Use as player sprite** makes your drawing the hero of your **2D** levels. Save the game to keep it.
- **Save PNG** saves a crisp 128 × 128 PNG into `My_Games/<game>/assets/`.

---

## 15. SFX Forge

Make retro sound effects without any audio software, in the **SFX Forge** tab.

1. Pick a **Preset**: jump, coin, hit, laser, boom, power, quest, levelup, door, portal, talk or magic.
2. Choose a **Wave** (square, saw, sine or noise) and move the sliders: **freq** (pitch), **slide** (pitch bend), **dur** (length), **decay**, **noise** and **vol**.
3. Click **Play** to hear it.
4. Click **Save WAV**. The sound is saved to `My_Games/<game>/assets/` **and** becomes your game's in-game sound for that action. Save the game to keep it.

---

## 16. Music and sound

**Soundtrack:** in the **World** tab, click **Make a soundtrack**. The studio composes an original looping tune (about 40 seconds) that matches the mood of the level you're editing. It's made from scratch on your computer, so it's yours to use. It takes a few seconds (up to about 20 seconds on a slower PC), is saved as `My_Games/<game>/assets/soundtrack.wav` and plays in **▶ Play** mode. **Listen** plays it, **Make a new soundtrack** replaces it and **Remove** takes it off the game.

**Honest note:** the soundtrack plays in the studio. Exported HTML5 cartridges stay small and do not carry the WAV file; it stays in your game's `assets/` folder for videos or for a Godot/UE5 project. For music inside the exported game, use the Tune Maker.

**Tune Maker (Tune tab):** a grid of 16 or 32 steps. The top 12 rows are notes (Cheerful pentatonic, Major or Minor, in a low, middle or high octave), the bottom rows are **Kick**, **Snare** and **Hat** drums. Click squares to place notes, **Listen** to loop it, change the speed (60 to 200 beats per minute), the sound (Retro, Soft, Flute or Bright) and the volume, or start from **Example tune**. The tune is stored in the game itself (a few hundred bytes), loops in **▶ Play** mode and **inside the exported HTML5 game**, and stops when the player turns sound off in the menu. While a tune is set it replaces the soundtrack file in Play mode. If you also built a game audio pack for the export, the pack's music plays in the exported game instead of the tune.

**Sound effects** for jumping, coins, hits, quests, level-ups, doors, portals, talking and magic are built in, and you can reshape each of them in the [SFX Forge](#15-sfx-forge).

---

## 17. Exporting

**Unreal Engine 5 export is a preview** in 28.7: the project is generated and checked, but it has not yet been opened
in a real Unreal Engine 5 install before release.

Open the **Export** tab. Every export is written to `My_Games/<game>/export/`. The studio saves your game first, then lists the files it wrote.

### HTML5 cartridge (all editions)
**What you get:** one self-contained `.html` file with the engine and your whole game inside: all levels, quests, dialogue, items, XP, enemies and bosses, save slots, a title screen and credits. No internet, no install: double-click and play in a modern browser.

- Share it by email, USB stick or any file host.
- **Upload to itch.io:** press **itch.io zip**. It writes `<game>_itch.zip` (the game as `index.html` plus `CREDITS.txt`) next to the cartridge. On itch.io, upload that zip as an HTML game and tick "This file will be played in the browser". The zip follows the same edition rules as the cartridge (Free: the first level with the closable note). Nothing is uploaded for you.
- Exported games work with touch screens and gamepads, keep variables in their save slots and play your Tune.
- In a cartridge, **P** toggles the drone camera and **O** saves a postcard PNG.

**The gameplay lives in the cartridge.** Quests, dialogue, combat, XP, saves, Logic-tab triggers, cutscenes, behaviours, shops, achievements, high scores and the player's accessibility options run in the HTML5 cartridge (and in the studio's Play mode). The Godot and UE5 exports below carry this data along in `world.json`, but do not re-create these systems in the engine.

### Godot 4 project (Pro and Studio)
**What you get:** a ready-to-open Godot 4 project in `My_Games/<game>/export/godot/`:

- **3D:** a lit scene for the Forward+ renderer: procedural sky, global illumination (SDFGI), ambient occlusion, volumetric and distance fog, glow, ACES tone mapping and auto exposure, a sun with soft shadows in your mood's colours, hilly terrain with collision, and every object (trees, rocks, ruins, crystals, lamps, enemies, houses, people, chests, items, checkpoints, portals) as simple shapes, with lights on lamps, crystals and portals and a water plane when your level has water. You walk around with a third-person camera (WASD/arrows, Space to jump).
- **2D:** your tiles as a scene with a player (A/D or arrows, Space to jump) and glow.
- `world.json` (all your game data) and `README_GODOT.md` (these steps and how to swap shapes for real art).

**How to open:** in Godot 4.2 or newer: **Import** > select `project.godot` > **Import & Edit** > press **F5** to run. The first open imports and compiles shaders. The 3D look needs a graphics card that supports Vulkan or Direct3D 12.

**Tested:** we opened and rendered exports of the starter games and several "Make me a game" games in Godot 4.7.

**Honest note:** this is a **starting scene**, not a finished port. It builds the **first level** of your game. Objects are simple placeholder shapes (select the `Props` node to drop in your own meshes; see the README), weather particles aren't re-created (weather changes fog and grading only), and quests, dialogue and combat are not carried over: the player can walk and jump. You add real models and gameplay in Godot.

### UE5 project (Pro and Studio, 3D worlds only)
**What you get:** an Unreal Engine 5 project in `My_Games/<game>/export/ue5/` that builds your level inside the Unreal Editor with a high-end setup already switched on:

- Lumen global illumination and reflections, Nanite, Virtual Shadow Maps and Temporal Super Resolution (in `Config/DefaultEngine.ini`).
- A sky with a sun matched to your mood, a sky light, volumetric clouds, height fog with volumetric fog, and a post-process volume with per-mood colour grading, bloom, exposure, vignette and a little film grain.
- Terrain shaped from your world (plus a heightmap PNG if you want to turn it into a Landscape), every object as a clearly named placeholder, lights for lamps and crystals, a PlayerStart, three cinematic camera shots, and markers for people, items, portals and quests.
- A **Stark World Engine** menu in the editor: **Build level from world.json**, **Swap proxies for real assets** and **Take hero screenshots**.

**How to open:**
1. Install **Unreal Engine 5.4 or newer** from the Epic Games Launcher (free Epic account). Stark World Engine never downloads it for you.
2. Open the `.uproject` file. If your engine is newer than 5.4, allow the conversion. The first open compiles shaders, which can take a while.
3. Menu **Stark World Engine > Build level from world.json** (or **Tools > Execute Python Script...** > `Content/Python/stark_build_level.py`). Running it again rebuilds only what it made and keeps your own work.
4. **Real art:** open **Fab** inside Unreal (Window > Fab; on 5.4, Quixel Bridge) with **your own Epic account** and add free Quixel Megascans or other assets to the project. Paste each asset's path into `Content/Python/proxy_swap_map.csv` (tree, rock, ruin, crystal, lamp, enemy; `ground` takes a material) and click **Stark World Engine > Swap proxies for real assets**. Stark World Engine doesn't include or download any Epic, Fab or Megascans content: those assets are licensed to you through your own account.

The full step list, including a headless build (`BUILD_LEVEL_HEADLESS.bat`) and optional hardware ray tracing, is in `README_UE5.md` inside the export.

**Honest notes:**
- UE5 export is for **3D worlds only** (use Godot or the cartridge for 2D). It builds the **first level** of your game.
- It is a starting point toward high-end visuals, not finished art: the look you end up with depends on the assets you swap in. Gameplay is not included; pressing Play in Unreal flies a default camera, and your quest and dialogue data stays in `Content/Python/world.json` for your own Blueprints.
- We have checked the generated project files and scripts, but at release we **have not yet run this export in a real Unreal Engine 5 install** ourselves. If something doesn't build, please tell us.

### Exporting without opening the studio
Advanced users can export from a terminal in the studio folder:

```
python studio_app.py --export "My First Island" --target cartridge
```

(`--target` can be `cartridge`, `godot` or `ue5`. On Windows, use `runtime\python\python.exe` instead of `python` if Python isn't installed separately.)

---

## 18. Activation and your license

- Your copy works the moment you unzip it. Without a key it runs as **unregistered**: everything except the Godot/UE5 exporters works.
- Paste your `GS1-...` key in **Export > Activate** and click **Activate**, or save it as `key.txt` next to `studio_app.py`.
- The key is checked **offline** on your computer. No server, no account, nothing is ever sent.
- A mistyped key never breaks anything: the studio just stays unregistered. Paste it again in one piece.
- **Your games are yours.** Stark World Engine itself is licensed for personal use: please don't resell or redistribute the program. The full text is in `PERSONAL_LICENSE.txt` (also linked from the Export tab).
- Every download includes `GIFT_CERTIFICATE.html`, a printable card you can fill in if you're giving Stark World Engine as a present.

---

## 19. The creator suite (Studio edition, Windows)

The creator suite helps you film, edit and publish videos about your games. It needs **Windows 10/11**, **OBS Studio 30+** and **FFmpeg** (both free).

Nothing in the creator suite uses a paid service or posts anything without you.

### 19.1 Setup Wizard (start here)
Double-click **`SETUP_STARK_WORLD_ENGINE.bat`**. Your browser opens a setup page with a checklist that turns green step by step (about 5 minutes; close it any time and it picks up where you left off next time):

1. **Check your tools:** Python, FFmpeg, OBS Studio and a browser. Missing FFmpeg or OBS? **Install** runs the official Windows installer (winget) in a window you can watch, or open the official download page.
2. **Camera and microphone:** pick your devices, **Show me** takes a test picture, **Test (3 seconds)** checks your mic, **Use these** sets up OBS with them.
3. **Your phone remote:** scan the QR code with your phone (same Wi-Fi). If the phone can't connect, click **Allow on my home network**: Windows asks you to click **Yes**, and one firewall rule lets devices on your home network (not the internet) reach the hub on port 8510. The wizard shows the exact command under "What exactly runs?".
4. **Your first test clip:** **Record a 5-second test** and watch it get edited automatically.
5. **YouTube (optional):** step-by-step help to make your own free Google "Desktop app" key and connect your channel.

At the end you get buttons to **Open Studio** and to open the phone hub.

After setup, **`START_CREATOR_HUB.bat`** starts everything at once: it prepares OBS, starts the auto editor in the background and starts the phone hub.

### 19.2 OBS bridge
`python obs_bridge.py` (run for you by the Setup Wizard and `START_CREATOR_HUB.bat`) writes a **new** OBS profile called **Stark_Profile** and a scene collection **Stark_Canon_R50V** with a scene named **Stark World Engine Studio** (camera, phone, screen and microphone sources). It also turns on OBS's built-in websocket (port 4455) so the phone hub can start and stop recording.

- Recordings are saved straight into the `raw_clips/` folder, where the auto editor finds them.
- Your existing OBS profiles and scenes are **never changed**. Anything it touches is backed up first.
- Want to see what it would do first? Run `python obs_bridge.py --dry-run`.
- In OBS, choose **Profile > Stark_Profile** and **Scene Collection > Stark_Canon_R50V**.

### 19.3 Auto editor
Drop recordings into `raw_clips/` (OBS does this for you) and the auto editor turns each recording session into:

- `edits/<session>/FINAL.mp4`: one edited video, and
- `edits/<session>/Social_Clips/clip_1..3.mp4`: three vertical 1080 × 1920 clips for Shorts, Reels and TikTok.

What it does for you: trims silences (jump cuts), evens out loudness to a YouTube-friendly level, adds captions, colour grading, subtle punch-in zooms, transitions and beat-snapped cuts when there's music. Then it opens a **review page** in your browser.

- `python auto_editor.py --watch` keeps watching in the background; `--once` edits what's ready and exits.
- **Captions:** if a `.srt` file with the same name sits next to a clip, it's used. Otherwise, if you've installed the free `faster-whisper` package, speech is transcribed on your PC. Otherwise you get a title caption.
- A file is edited once it stops growing, and the same recording is never edited twice.
- Look and feel are adjustable in `config.json` (created on first run): `grade_preset` (`teal_orange`, `warm_film`, `cold_cinematic`, `punch`, `neutral_clean`), `caption_preset` (`bold_pop`, `clean_lower`, `hype`), `transition_name` (`crossfade`, `whip_pan`, `dip_to_black`, `zoom_blur`, `glitch` and more), `target_lufs`, silence settings and intensity knobs.
- Uses your graphics card's video encoder when available (NVIDIA or Intel), otherwise a standard software encoder.

### 19.4 Phone hub (Creator Command Center)
A web app for your phone, served from your PC on your home network (port **8510**), protected by a **PIN**.

- Start it with `START_CREATOR_HUB.bat` (or `python creator_hub.py --phone`). It prints the address to open on your phone. Started without `--phone`, the hub only answers on this PC (`http://127.0.0.1:8510/`).
- **Log in once:** scan the QR code in the Setup Wizard (or make one with `python creator_hub.py --qr`, which writes `.claude/iphone_qr.html`). The QR logs your phone in one time and is valid for 15 minutes. Or open the address on your phone and type the 6-digit PIN on the login page. Your PIN is shown in the Setup Wizard; it is `hub_pin` in `config.json` (made for you on first run) unless you moved it out of the studio folder with `python gs_common.py --move-secrets` (add `--rotate` for a new PIN), which keeps it in your private app-data folder instead. The PIN is never put in a link; old links ending in `?pin=` no longer log you in.
- After logging in, your phone stays logged in until the hub is restarted.
- Five wrong PINs within 10 minutes lock out that device's PIN logins for 15 minutes.
- Use it on your trusted home Wi-Fi only: it is plain HTTP on your local network.
- Can't connect? See [My phone can't reach the hub](TROUBLESHOOTING.md#my-phone-cant-reach-the-hub).

**Tabs:**
- **Shoot Scheduler:** plan recording sessions and get calendar reminders. Five minutes before a shoot, OBS is armed and the teleprompter opens on your PC. Auto-record at shoot time is optional.
- **Camera:** use your phone as a camera through a VDO.Ninja link (a free browser-to-browser video service; this one tab uses the internet).
- **Teleprompter:** read your script; tap (or press Space) to start/stop OBS recording.
- **Review & Approve:** watch the finished edit, optionally pick a publish date and time, and **Approve**. Nothing is published without your approval.
- **Stats:** your own free YouTube / itch.io numbers, only if you've set up your own credentials.

### 19.5 Review & approve, then publishing drafts
After you approve an edit in the hub, the publishing helper takes over:

- **YouTube:** uploads the video to **your** channel using your own free Google Cloud "Desktop app" credentials (`client_secrets.json` in the studio folder; the Setup Wizard walks you through it). Videos are uploaded as **private**, and scheduled when you picked a publish time. No credentials? It explains how to make them and does nothing else.
- **X, Instagram, Substack:** the post text and media paths are written to `drafts/<session>/`, and the site's own compose page is opened. **You** paste and click Post. Nothing logs in or posts automatically.
- **Optional local AI captions:** if you run a local Ollama server, set `ollama_model` in `config.json` to draft post text on your own PC.
- `python publishers.py --setup-wizard` opens the official sign-up pages with a checklist; `--dry-run` checks everything and writes drafts without uploading.

---

## 20. Where things live

| Folder / file | What's in it |
|---|---|
| `My_Games/<game>/world.json` | your game (all levels, quests, dialogue, items) |
| `My_Games/<game>/assets/` | sprites (PNG), sounds and the soundtrack (WAV) |
| `My_Games/<game>/postcards/` | postcard screenshots |
| `My_Games/<game>/export/` | cartridges, `godot/`, `ue5/` |
| `My_Games/.backups/` | the 3 previous versions of every saved file |
| `key.txt` | your license key (optional) |
| `runtime/python/` | the built-in Python for Windows |
| `launcher.log`, `doctor.log` | what the launcher and the setup check did |
| `raw_clips/` | recordings for the auto editor (Studio) |
| `edits/` | finished videos and social clips (Studio) |
| `drafts/` | post drafts for X / Instagram / Substack (Studio) |
| `config.json` | creator suite settings and hub PIN (Studio, made on first run) |

---

## 21. Privacy in one paragraph

The studio only answers requests from your own computer and only reads and writes inside `My_Games/`. License keys are checked offline. Nothing calls home, nothing is tracked, no paid AI is used. On Windows, the launcher only goes online if no working Python is found at all and you press **Y** to fetch the official one from python.org. The only other things that touch the internet are ones you start on purpose in the Studio edition: the Setup Wizard's installs and official pages, the phone camera link (VDO.Ninja), YouTube uploads with your own credentials, and opening a social site's compose page for you.
