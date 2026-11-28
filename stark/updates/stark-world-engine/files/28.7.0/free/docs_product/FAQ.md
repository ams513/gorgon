# Frequently Asked Questions

## The basics

### Do I need an internet connection?
No. The studio, your games and license activation all work offline. On Windows, Python is built in, so nothing is downloaded. The only things that use the internet are ones you start yourself: fetching Python from python.org if the built-in one is missing and you say yes, and the optional Studio-edition features (the Setup Wizard's installs, the phone camera link, YouTube uploads with your own credentials, and opening social sites' compose pages).

### Do I need an account or subscription?
No. You buy once and it's yours. There is no account, login or subscription.

### Is it really running in my browser? Is my work uploaded?
The studio *displays* in your browser, but a small program on your computer does the saving. It only accepts connections from your own computer (`127.0.0.1`) and only writes inside the `My_Games` folder. Nothing is uploaded.

### Which computers does it run on?
The builder runs on **Windows 10/11** (Python included, just double-click `START_STARK_WORLD_ENGINE.bat`), **macOS** and **Linux** (both need Python 3.10+). You also need a WebGL2 browser (current Chrome, Edge or Firefox). The **creator suite** (Studio edition) is **Windows 10/11 only**.

### Do I need to install Python?
On Windows, no: the download includes its own Python in the `runtime` folder and the launcher uses it. On macOS and Linux, yes: Python 3.10 or newer, free from python.org or your package manager. See the [Quickstart](QUICKSTART.md).

### Do I need a powerful PC?
No. The worlds are stylized and designed for browsers, so a typical laptop from the last several years with a working graphics driver is fine. Very large worlds (thousands of objects) run smoother on a dedicated graphics card, and players can lower **Graphics** in the in-game menu (Esc). The auto editor (Studio) benefits from an NVIDIA or Intel GPU with a video encoder, but works without one, just slower.

### Do I need to know how to code?
No. Building, quests, dialogue, items, pixel art, sound effects, music and exporting are all point-and-click. **Make me a game** even builds a complete game for you. The Godot and UE5 exports are a starting point for people who *want* to go further in a full engine.

### Which languages does the interface support?
English, Spanish, French, German, Portuguese, Italian, Japanese and Chinese for the main buttons and tabs. Switch with the drop-down at the top (EN, ES, FR, DE, PT, IT, JA, ZH). The tool palette, editor panels and in-game text are in English.

---

## Editions and licensing

**Is there a free version?** Yes: Stark World Engine Free has every builder feature. Its HTML5 cartridges export the
first level of a game with a small "Made with Stark World Engine" note that players can close after a few seconds. A
Pro or Studio key (or an older Standard key) removes both limits in place (Export > Activate).

**Where does the art come from?** From CC0 packs by Quaternius and Poly Haven (see `assets/CREDITS.txt`), plus any
glTF models you import yourself in the Assets tab. Every export lists the art it uses.


### What's the difference between Free, Pro and Studio?
- **Free ($0):** the full 3D + 2D builder with quests, dialogue, items, multi-level games and "Make me a game", the Logic tab with variables, moods, starter games, guided tutorials, remix, pixel art, SFX Forge, Tune Maker, the soundtrack maker and HTML5 cartridge / itch.io zip export (first level, with a small closable note).
- **Pro ($30):** every level with no note, + Godot 4 and UE5 project export, and example cartridges.
- **Studio ($50):** + the Windows creator suite: Setup Wizard, OBS setup, auto video editor, phone hub, publishing helpers.

Standard is no longer sold. If you already have a Standard key it keeps working as before: every level, no note (Godot and UE5 export are part of Pro and Studio).

### Can I get a refund?
Studios: refunds within 2 days of purchase, on request. If you bought on itch.io, ask for the refund through itch.io. Your statutory rights in your country still apply (for example, EU and UK consumer rights on digital content depend on consent to immediate download). On Steam, refunds go through Steam.

### Do I get a key by e-mail?
No. The Pro and Studio downloads come activated: the key (`key.txt`) is already in the folder, so there is nothing to wait for. You only paste a key (Export > Activate) when you were given one as text, for example a gift or a re-issued key.

### Does it work without a key?
Yes. Without a key it runs as Free with every builder feature. A Pro or Studio key exports every level with no note and unlocks the Godot and UE5 exporters.

### Can I upgrade later?
Contact the seller through the store page with your order number.

### I lost my key.
Contact the seller with your order number; keys can be re-issued.

### Can I use it on more than one computer?
The license is personal: use it on your own computers for your own projects. Please don't share your download or key with others.

### Do I own the games I make?
Yes. **The games you make are yours**: sell them, share them, put them on itch.io. That includes the soundtracks and sounds the studio makes for you: they're generated from scratch on your computer. What you may not do is resell or redistribute Stark World Engine itself. See `PERSONAL_LICENSE.txt`.

### Is there DRM or an online check?
No online check, no anti-debugging, nothing that can lock you out. The key is checked with a digital signature on your computer.

---

## Building and playing

### Can it make a game for me?
Yes. Click **Make me a game**, pick a theme (Spooky Swamp, Sky Islands, Frozen Kingdom, Lava Fortress, Sunken City or Cozy Village) and a size (Snack: 1 level, Evening: 3 levels, Epic: 6 levels), and click **Make it and play!** You get villages, people with quests, monsters, treasure and a boss, and you can change everything afterwards. The same seed always makes the same game.

### Can my games have quests and characters?
Yes. Place a **Person**, then use the **Quests** and **Dialogue** tabs: quests with steps (collect, defeat, talk, go to a place, escort) and rewards, and conversations with answers that start quests or give items. Players also earn XP and level up, carry items and equipment, and fight different kinds of enemies and bosses. See [Quests, people, items](USER_GUIDE.md#8-quests-people-items-xp-enemies-and-campaigns).

### How does the player win?
In a game with quests, by finishing the quests marked **finishing it wins the game** (or all quests, if none is marked). In a 3D game without quests, by collecting every crystal. In 2D, by reaching the flag on the last level.

### Can a game have several levels?
Yes, up to 25. Add them in the **World** tab under **Levels**; portals (3D) and the flag or exit (2D) lead to the next one. XP, items and gold carry over.

### Can players save their progress?
Yes. Press **Esc** while playing for **three save slots** plus an autosave that's written at every checkpoint. Saves are kept in the browser on that computer.

### Can I import my own 3D models?
Yes, since 28.5. Open the **Assets** tab, drop a `.glb` (or a `.gltf` with its `.bin` and textures), PBR textures or an `.hdr` sky, fill in the name, size, licence and who made it, and press **Import**. **Place** puts it in your 3D world. Imported art is stored in `My_Assets/`, travels inside your HTML5 cartridge and is listed in its credits. Files that need Draco or meshopt compression are refused: export them again without compression.

### Can I use my own sounds and images?
You can make your own with the **Pixel Art** canvas, the **SFX Forge** and the soundtrack maker. Importing outside files into the builder isn't supported; in Godot/UE5 exports you can use anything you like.

### Does my game have music?
Two ways. **Tune** tab: place notes and drums on a grid; the tune plays in Play mode **and inside the exported HTML5 game**. **World** tab > **Make a soundtrack**: the studio composes a longer original tune for the level's mood; it plays in the studio's Play mode only (the WAV file is not put inside exported games, to keep them small).

### Can people play my game on a phone, a tablet or with a gamepad?
Yes. Exported games show an on-screen joystick and buttons when the screen is touched, and they work with standard gamepads. 3D games need a phone or tablet with WebGL2 (most from the last few years); big worlds run slower on phones than on computers.

### I made a mistake. Can I undo?
Yes: **Undo** (Ctrl+Z) and **Redo** (Ctrl+Y) work while you edit. And every save keeps the three previous versions: **World** tab > **Restore an earlier version...**.

### How big can a world be?
Up to 5,000 objects per level, and up to 25 levels per game.

---

## Exporting and sharing

### What does the HTML5 cartridge include?
Everything: the engine and your whole game (all levels, quests, dialogue, items, enemies and bosses, save slots, title screen and credits) in a single `.html` file. It runs offline in a modern browser. No install needed for your players.

### Can I put my game on itch.io?
Yes. Rename a copy of the cartridge to `index.html`, zip it, and upload it as an HTML game on itch.io with "This file will be played in the browser" ticked.

### Is the Godot / UE5 export a finished game?
No, and we want to be upfront about that. It's a **starting scene** for your game's first level: layout, terrain, mood lighting, sky and fog, with objects as simple placeholder shapes. The gameplay (quests, dialogue, combat, XP, saves) runs in the HTML5 cartridge and is **not** re-created in Godot or Unreal; your game data comes along in `world.json`. In Godot you get a walking player; in Unreal, Play flies a default camera.

### How good does the Godot export look?
It opens with Godot 4's Forward+ renderer set up: sky, global illumination, ambient occlusion, volumetric fog, glow and per-mood colour grading, plus terrain and lit placeholder objects. We opened and rendered exports of several games in Godot 4.7 before release. You make it look like *your* game by swapping in real models.

### What does the UE5 export set up?
Lumen lighting and reflections, Nanite, virtual shadow maps, a physical sky with sun, clouds and fog, and per-mood post-processing, plus a **Stark World Engine** menu that builds the level and swaps the placeholders for Fab / Quixel Megascans assets you add with your own Epic account. It needs Unreal Engine 5.4 or newer. Honest note: we checked the generated files, but at release we have not yet run it in a real Unreal Engine 5 install ourselves.

### Can I export 2D worlds to Unreal?
No. UE5 export is for 3D worlds. Export 2D worlds to Godot 4 or as an HTML5 cartridge.

---

## Creator suite (Studio)

### How do I set up the creator suite?
Double-click `SETUP_STARK_WORLD_ENGINE.bat`. A setup page walks you through tools, camera and mic, the phone remote (including the firewall, with one click and your "Yes"), a first test clip and, optionally, YouTube. About 5 minutes.

### Does the creator suite post to my social accounts automatically?
No. It uploads to YouTube only with **your own** credentials and only after you approve in the phone hub, as a private (optionally scheduled) video. For X, Instagram and Substack it writes drafts and opens the compose page: you click Post.

### Does it use paid AI services?
No. Captions use a local `.srt` file or the free `faster-whisper` running on your PC. Optional post-text drafting uses a local Ollama server if you have one. No paid cloud API is called anywhere.

### How does my phone log in to the hub?
Scan the QR code in the Setup Wizard: it logs your phone in once and is valid for 15 minutes. Or open the hub's address on your phone and type the 6-digit PIN (shown in the Setup Wizard). The PIN is never put in a link.

### Does the phone hub work with Android?
The hub is a web page, so it opens in any modern phone browser on the same network. It's designed with iPhone Safari in mind. The Camera tab relies on the phone browser's camera permission, which needs the HTTPS link the hub gives you.

### Will it mess up my OBS settings?
No. It creates a new profile (**Stark_Profile**) and a new scene collection (**Stark_Canon_R50V**) and leaves your existing ones alone. Anything it touches is backed up first. Try `python obs_bridge.py --dry-run` to see exactly what it would write.
