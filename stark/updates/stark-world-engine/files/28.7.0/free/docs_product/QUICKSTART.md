# Quickstart: your first playable game in 5 minutes

*Build a game. Share it as one file. One install, endless ideas.*

Stark World Engine is a game builder that runs in your web browser, but **everything stays on your own computer**. No account, no subscription, no internet needed. A small program on your computer serves the studio to your browser, and only your computer can reach it.

**On Steam?** Press Play in your Steam library; your games are in `Documents\Stark World Engine\My_Games`. Skip
Step 1 below. If something is missing or damaged, use Steam > Stark World Engine > Properties > Installed Files > Verify
integrity of game files.

**Stark World Engine Free** has the whole builder; its HTML5 cartridges export the first level and show a small closable
"Made with Stark World Engine" note. Pro and Studio come activated (the key is already in the folder; nothing is e-mailed) and export every level.

You need:

- **A current browser with WebGL2**: Chrome, Edge or Firefox (Safari 15+ also has WebGL2).
- **Windows 10 / 11:** nothing else. Python is built into the download.
- **macOS and Linux:** Python 3.10 or newer (free, from python.org or your package manager).

---

## Step 1: Unzip

Unzip the download (for example `Stark_World_Engine_Pro_v28.7.zip`) somewhere you own: your Desktop or Documents folder is fine. You get one folder. Keep everything in that folder together.

> Tip: don't run it from inside the zip preview. On Windows, right-click the zip > **Extract All...** first. Avoid `C:\Program Files` and cloud-synced folders (OneDrive, Dropbox): the studio needs to save your games there.

## Step 2: Start the studio

### Windows 10 / 11
Double-click **`START_STARK_WORLD_ENGINE.bat`** in the folder.

- A small black window opens. It finds the built-in Python, runs a quick **setup check**, then starts the studio and opens your browser. Leave the black window open while you work; close it when you're done.
- If Windows shows **"Windows protected your PC"**, click **More info > Run anyway** (see [Troubleshooting](TROUBLESHOOTING.md#windows-says-windows-protected-your-pc-smartscreen)).
- If the setup check finds a problem, it says exactly what to do. For example, if the folder can't be written to, press **Y** and it copies itself to your Documents folder and starts from there.

### macOS
1. If you don't have Python 3.10 or newer: go to **python.org > Downloads**, download the macOS installer and click through it.
2. Double-click **`start_stark_world_engine.command`** in the folder. The first time, macOS may refuse because it isn't from the App Store: right-click the file > **Open** > **Open**.
3. A Terminal window opens, runs the setup check and starts the studio. Leave it open while you work.

(If the launcher can't find Python, it opens python.org for you and tells you what to do.)

### Linux
Most distributions already have Python 3. Check in a terminal with `python3 --version`; if it's below 3.10, install a newer one (for example `sudo apt install python3`). Then, in a terminal in the folder:

```
bash start_stark_world_engine.sh
```

Your browser opens at an address like **http://127.0.0.1:8505** (or the next free number up to 8520). The address `127.0.0.1` means "this computer only".

> If the browser doesn't open by itself, copy the address printed in the black/terminal window into your browser. Something not right? Press **?** in the studio and click **Fix my setup**.

## Step 3: Play a starter game (1 minute)

The studio comes with three starter games in the drop-down at the top. On your first visit a short tour offers **Play a starter game now**.

- **Firefly Fen** (3D): a glowing swamp with villagers, quests and a boss.
- **Summit Snack Run** (3D): a sunset mountain adventure.
- **Lantern Leap** (2D): a platformer through sunken ruins.

Pick one, then click **▶ Play** at the top (or press **Ctrl+Enter**).

- **3D controls:** WASD to move, Space to jump, J or click to attack, E to talk or open chests, drag the mouse to look around.
- **2D controls:** A/D to move, Space to jump, stomp enemies, reach the flag.
- **Everywhere:** **L** quest log, **I** bag, **H** drink a potion, **Esc** menu (three save slots and options).

Click **Build** (or press **Ctrl+Enter** again) to go back to editing. Press **?** to see every shortcut.

## Step 4: Let the studio make you a game (1 minute)

1. Click **Make me a game** at the top left.
2. Pick a theme (for example **Frozen Kingdom**) and a size: **Snack** is one level, about 15 minutes of play.
3. Click **Make it and play!**

You're dropped straight into a complete little adventure with people to talk to, quests, monsters and a boss. It's saved in your games list, and you can change anything in it.

## Step 5: Make your own (3 minutes)

1. Click **New 3D world**.
2. Type a name in the **Name** box (for example "My First Island").
3. Click a **Living World Mood** along the top, for example **Sub-Zero Aurora**. The whole world relights instantly.
4. In the **Tools** tab on the right, pick **Tree** and click on the ground a few times. Then pick **Crystal** and place 5 crystals: these are what the player collects. Add one or two **Slime** creatures for a bit of danger.
5. Click **Save** (the studio also autosaves a few seconds after each change).
6. Click **▶ Play**. Collect every crystal to see "World complete!".

**You just made a playable game.** Ready for more? Place a **Person**, then open the **Quests** tab and click **Fetch**: your person now has a quest and dialogue. See [Quests, people, items](USER_GUIDE.md#8-quests-people-items-xp-enemies-and-campaigns).

## Step 6 (optional): Activate your key

Your copy works fully without a key. A key registers your copy and, on the **Pro** and **Studio** editions, unlocks the **Godot 4** and **UE5** project exporters.

1. After buying you receive one line of text that starts with `GS1-`.
2. In the studio, open the **Export** tab on the right.
3. Paste the whole key into the **Activate** box and click **Activate**.

Or: save the key as a file called `key.txt` in the same folder as `studio_app.py`. The studio reads it when it starts.

Activation is checked on your computer only. Nothing is sent anywhere.

## Step 7 (optional): Share your game

Open the **Export** tab and click **HTML5 cartridge**. Stark World Engine writes one `.html` file into
`My_Games/<your game>/export/`. Double-click it and your whole game (levels, quests, saves and all) runs in any modern browser, offline. Send it to a friend or upload it to itch.io (see [Exporting](USER_GUIDE.md#17-exporting)).

## Studio edition: the creator suite

Bought the Studio edition? Double-click **`SETUP_STARK_WORLD_ENGINE.bat`** for a 5-minute guided setup of OBS, your camera and mic, the phone remote and your first auto-edited clip. See [The creator suite](USER_GUIDE.md#19-the-creator-suite-studio-edition-windows).

---

**Next:** the [User Guide](USER_GUIDE.md) explains every feature, and [Tutorials](TUTORIALS.md) walks you through five complete projects. Something not working? See [Troubleshooting](TROUBLESHOOTING.md).
