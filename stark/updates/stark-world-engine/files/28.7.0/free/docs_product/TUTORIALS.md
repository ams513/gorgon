# Tutorials

Five complete projects, from your very first world to a full engine export. Each one lists what you need and roughly how long it takes.

1. [Your first adventure in 10 minutes](#tutorial-1-your-first-adventure-in-10-minutes) (all editions)
2. [A 3-level campaign with a boss](#tutorial-2-a-3-level-campaign-with-a-boss) (all editions)
3. [A cozy collect-a-thon](#tutorial-3-a-cozy-collect-a-thon) (all editions)
4. [Make a trailer of your game with the auto editor](#tutorial-4-make-a-trailer-of-your-game-with-the-auto-editor) (Studio, Windows)
5. [Export to Godot](#tutorial-5-export-to-godot) (Pro and Studio)

---

## Tutorial 1: Your first adventure in 10 minutes

**You need:** the studio running (see the [Quickstart](QUICKSTART.md)). **Time:** about 10 minutes.

**The idea:** a lantern keeper's island at night. Talk to the old keeper, find the scattered crystals and avoid the swamp creatures.

### Part A: the world (4 minutes)
1. Click **New 3D world**. Type **Lantern Isle** in the Name box.
2. Click the **Bioluminescent Twilight** mood. Fireflies appear and the foliage starts to glow.
3. **Tools** tab > **Tree**: click around the edges to make a forest ring. About 20 trees is plenty.
4. **Ruin**: place 3 ruins in a triangle around the middle. They become landmarks the player can steer by.
5. **Lamp**: put a lamp next to each ruin, so the landmarks glow.
6. **Rock**: sprinkle a few rocks along paths between the ruins.
7. Click **Save**.

### Part B: the challenge (3 minutes)
1. **Crystal**: place 6 crystals: one next to each ruin, three hidden behind trees.
2. **Slime**: place 2 slimes near the hidden crystals. Not on top of the start point: give the player a moment to look around.
3. Click **Save**, then **▶ Play**. Walk with WASD, look by dragging the mouse, attack with J.
4. Too hard? Click **Build**, choose **Erase**, and remove a slime. Too easy? Add one.

### Part C: a story (3 minutes)
1. **Tools** tab > **Person**: place one near the start. Choose **Select / move** and click the person. In the panel, change **Name** to *Old Keeper*.
2. Still in the panel, click **Give them a quest**. The **Quests** tab opens with a new quest given by the Old Keeper, and the Keeper's dialogue is written for you.
3. Change the quest's **Title** to *Relight the Isle*. In **Step 1** (Collect items, Item: Crystal), set **How many** to **6**. Step 2 already says *Talk to someone: Old Keeper*, so the player brings the news back.
4. Open the **Dialogue** tab. Find the line that starts *"Could you help me?"* and change its text to: *"The lanterns went dark when the crystals scattered. Bring all six back and the isle will shine again."*
5. **Save** and **▶ Play**: walk to the Keeper (a gold **!** floats above them), press **E**, pick *I will help.* with **1**, then follow the gold beam. Press **L** to see the quest log.

Because the game now has a quest, finishing that quest is what wins it.

### Finish
- Press **P** while playing for a drone flyover, then click **Postcard PNG** for a cover picture.
- Optional: **World** tab > **Make a soundtrack** for a looping tune while you play in the studio.
- **Export > HTML5 cartridge** and double-click the file in `My_Games/Lantern_Isle/export/`. You've made a game you can send to anyone.

---

## Tutorial 2: A 3-level campaign with a boss

**You need:** the studio running. **Time:** about 30 minutes.

**The idea:** a short journey across three moods: a sunset meadow, a frozen pass and a burning citadel with a boss at the end. All three levels live in **one game**.

### Step 1: Level 1, "Meadow Gate" (Alpine Sunset)
1. **New 3D world**, name it **The Long Road**, mood **Alpine Sunset**. This first level is called after the game.
2. Lots of trees, a few rocks, 5 crystals out in the open, 1 slime. This level teaches the controls, so keep it gentle.
3. **Quests** tab > **Fetch**. Set the title to *Meadow Gate*, tick **starts when the level begins**, and make **Step 1** *Collect items: Crystal, 5*. Delete step 2 with its **X**.
4. **Tools** tab > **Portal**: place it at the far end. Select it and set **Opens after quest** to *Meadow Gate*. Now the way on opens once the crystals are found.
5. **Save**.

### Step 2: Level 2, "Frost Pass" (Sub-Zero Aurora)
1. **World** tab > **Levels** > **+ 3D level**. You're now editing level 2 (the level picker at the top shows it). Under **This level**, set **Level name** to *Frost Pass*. Click the **Sub-Zero Aurora** mood.
2. A new 3D level already has a portal to the next level. Fewer trees, more rocks and ruins: a narrow, rocky path. 6 crystals and 3 creatures guarding them (try an **Archer** and a **Brute**).
3. **Quests** tab > **Hunt**: title *Clear the Pass*, tick **starts when the level begins**, step *Defeat enemies: Any enemy, 3*. Lock the portal to this quest like in level 1.
4. **Save**.

### Step 3: Level 3, "Ember Citadel" (Crimson Citadel)
1. **World** tab > **+ 3D level**, name it *Ember Citadel*, mood **Crimson Citadel**. Use **Erase** to remove the portal it came with: this is the last level.
2. Build a ring of ruins as castle walls with lamps on the corners. 4 crystals inside, creatures at the gaps.
3. In the middle, place a **Boss**. Select it: set **Name** to *Warden of Embers*, **Id** to *warden*, and raise its **Health**.
4. **Quests** tab > **Hunt**: title *The Warden*, tick **starts when the level begins** and **finishing it wins the game**. In the step, set **How many** to 1 and **Or one named enemy (id)** to *warden*. Give it a big reward: **Reward XP** 200 and an item such as Moon Blade.
5. Add a **Person** at the gate, name them *Gatekeeper*, click **Write dialogue for this person**, and in the **Dialogue** tab change the greeting to *"The Warden of Embers waits within."* (Do this after step 4: a quest added while a person exists is handed to that person, with offer lines written for them. If that happens, set the quest's **Given by** back to *(starts by itself)*.)
6. **Save**.

### Step 4: Play the whole campaign
1. Pick level 1 in the level picker at the top and click **▶ Play**.
2. Finish each level's quest and walk through its portal. XP, items and gold carry over, so the player arrives at the boss stronger, maybe with a new ability or two (Dash at level 2, Whirl at level 3, Star Bolt at level 4).
3. **Esc** opens the menu with three save slots, so players can stop and continue later. Every checkpoint you place also writes the autosave.
4. Defeat the Warden to see the ending.

### Playtesting tips
- Use the level picker to play each level on its own first. It's much faster to tune.
- Each level should be a little harder than the one before: one more enemy, one less easy crystal.
- Give the player a quiet moment before the boss: a lamp-lit ruin with a **Checkpoint** and nothing hostile nearby.
- Want to see how a finished campaign is built? Open **Firefly Fen** (3 levels) and look at its **Quests** and **Dialogue** tabs.

---

## Tutorial 3: A cozy collect-a-thon

**You need:** the studio running. **Time:** about 15 minutes. No enemies, no stress, just a pleasant world full of things to find.

### Option A: a 2D seaside platformer
1. Click **New 2D world**, name it **Pearl Cove**. The mood starts on **Sunken Ruins** (bubbles!).
2. **Pixel Art** tab: draw a little hero in 16 × 16. A round body, two eyes and a hat is plenty. Click **Use as player sprite**.
3. **Tools** tab > **Ground**: paint a few floating platforms at different heights, like stepping stones.
4. **Pearl**: put one on top of each platform, and a few in the air between platforms, so jumps are rewarded.
5. Paint the **Flag** at the far end (you can **Erase** the one the new world started with). Skip spikes and creatures: this one is cozy.
6. **SFX Forge** tab: pick the **coin** preset, set the **Wave** to **sine** and nudge **freq** up for a softer chime. Click **Play** to hear it, then **Save WAV**: it becomes your collect sound.
7. **Save**, then **▶ Play**.

### Option B: a 3D garden stroll
1. **New 3D world**, name it **Firefly Garden**, mood **Bioluminescent Twilight**.
2. Plant trees in loose clusters, with lamps along winding paths. Add a **House** or two.
3. Hide 12 crystals: under trees, behind ruins, one on the far edge of the map.
4. No creatures. **World** tab: set **Day and night** to *Slow (10 min day)* and turn on **Water** for a little lake.
5. **Save** and **▶ Play**. Collect every crystal to finish.

### Make it feel cozy
- Gentle, rhythmic placement (paths, clusters) feels calmer than random scatter.
- Lamps along a route quietly guide the player without any signs.
- Try every mood: **Alpine Sunset** is warm and relaxed, **Sunken Ruins** is dreamy.
- **World** tab > **Make a soundtrack** adds a calm looping tune in the studio.

**Shortcut:** **Make me a game** > **Cozy Village** > **Snack** builds a gentle one-level adventure with villagers, easy quests and pumpkins in one click. Then hand-tune it.

---

## Tutorial 4: Make a trailer of your game with the auto editor

**You need:** the Studio edition and Windows 10/11. **Time:** about 20 minutes (plus editing time on your PC).

### Step 1: Run the Setup Wizard (one time, about 5 minutes)
1. Double-click **`SETUP_STARK_WORLD_ENGINE.bat`** in the studio folder. A setup page opens in your browser.
2. **Check your tools:** if FFmpeg or OBS Studio is missing, click **Install** and watch the window, or use the official download page.
3. **Camera and microphone:** pick yours and click **Use these**. This writes the Stark World Engine OBS profile and scene with those devices.
4. **Your phone remote:** scan the QR code with your phone. If it can't connect, click **Allow on my home network** and click **Yes** on the Windows prompt.
5. **Your first test clip:** click **Record a 5-second test** to see the auto editor at work.
6. Optional: for automatic speech captions, install the free `faster-whisper` package (`pip install faster-whisper` with an installed Python).

### Step 2: Get OBS ready
1. Double-click **`START_CREATOR_HUB.bat`**: it prepares OBS, starts the auto editor in the background, and starts the phone hub.
2. Open OBS and pick **Profile > Stark_Profile** and **Scene Collection > Stark_Canon_R50V**.
3. In the **Stark World Engine Studio** scene, make sure the screen capture shows the display where your browser is. Hide sources you don't need (camera, phone) by clicking their eye icons.

OBS now records straight into `raw_clips/`.

### Step 3: Film your gameplay
1. Open your game (in the studio, or the exported cartridge, full screen).
2. Click **Start Recording** in OBS (or use the phone hub's **Teleprompter** tab).
3. Record 3 or 4 short takes:
   - a **drone flyover** (press **P**), with no talking;
   - 30 seconds of exploring, talking to a villager and collecting;
   - a fight or a boss;
   - the ending screen.
4. Stop recording between takes. Talking over a take? The auto editor cuts the silences and captions your voice.

### Step 4: Let the auto editor cut it
If `START_CREATOR_HUB.bat` is running, the editor is already watching. Otherwise run:

```
python auto_editor.py --once
```

Once your recordings stop growing, it edits them and opens a **review page** in your browser. You'll find:
- `edits/<session>/FINAL.mp4`: your trailer;
- `edits/<session>/Social_Clips/clip_1.mp4` to `clip_3.mp4`: vertical versions for Shorts, Reels and TikTok.

### Step 5: Tune the look (optional)
Open `config.json` and try:
- `"grade_preset": "warm_film"` (or `cold_cinematic` for icy worlds);
- `"caption_preset": "hype"` for trailer energy;
- `"transition_name": "whip_pan"`.

The new look applies to the next recordings the editor processes. (Each recording is edited only once, even if you rename it, so record a fresh take to try a new style.)

### Step 6: Approve and publish
1. Open the **phone hub** on your phone (you logged in with the QR in the Setup Wizard; see [Phone hub](USER_GUIDE.md#194-phone-hub-creator-command-center)).
2. **Review & Approve** tab: watch the edit, optionally choose a publish date and time, tap **Approve**.
3. If you connected YouTube, the video uploads to your channel as private (scheduled if you picked a time). For X, Instagram and Substack you get ready-to-paste drafts in `drafts/<session>/`.

---

## Tutorial 5: Export to Godot

**You need:** the Pro or Studio edition, activated, and **Godot 4.2 or newer** (free, from godotengine.org). **Time:** about 10 minutes.

### Step 1: Activate (if you haven't)
**Export** tab > paste your `GS1-...` key > **Activate**. The **Godot 4 project** button becomes clickable.

### Step 2: Export
1. Open the game you want, for example **Firefly Fen**.
2. **Export** tab > **Godot 4 project**.
3. The file list shows what was written, in `My_Games/<game>/export/godot/`:
   - `project.godot`: the project file;
   - `main.tscn`: the scene with your first level;
   - `player.gd`: the player (WASD/arrows + Space, third-person camera);
   - `stark_terrain.gd` and `stark_scatter.gd`: build the terrain and place your objects (they also run in the editor, so you see the level right away);
   - `stark_plan.json` and `world.json`: your level layout and all your game data;
   - `README_GODOT.md`: these steps in short.

### Step 3: Open it in Godot
1. Start Godot. In the Project Manager click **Import**.
2. Browse to `My_Games/<game>/export/godot/` and select `project.godot`.
3. Click **Import & Edit**. The first open imports and compiles shaders. Then press **F5** to run and walk around your level in Godot.

### Step 4: Make it your own
What carries over: your first level's layout, terrain and water, mood lighting (sky, sun, fog, glow, colour grading), every object as a simple shape (lamps, crystals and portals glow), and a controllable player.

What doesn't: the other levels, quests, dialogue, combat and scoring, and the browser's weather particles. Those run in the HTML5 cartridge; in Godot you build your own gameplay. `world.json` still lists every object, quest and line of dialogue if you want to rebuild from data.

Next steps in Godot:
- Select the `Props` node. In the Inspector each kind of object has a `*_mesh` slot (fast, stays a MultiMesh) and a `*_scene` slot (for a `.glb` or `.tscn`, for example animated enemies). Drop in your own models; toggle `rebuild` to refresh. Free CC0 sources include Poly Haven, Kenney and ambientCG (check each licence).
- Set `ground_material` on the `Terrain` node for real ground textures.
- Script enemies and pickups with GDScript.

**Unreal Engine instead?** Use **UE5 project** (3D worlds only). Open the `.uproject` in UE 5.4 or newer and choose **Stark World Engine > Build level from world.json**. Your level is built with Lumen, Nanite, a real sky, clouds, fog and per-mood post-processing; swap the placeholders for Fab / Megascans assets with your own Epic account. See [UE5 project](USER_GUIDE.md#ue5-project-pro-and-studio-3d-worlds-only).
