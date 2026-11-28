# Troubleshooting

Find your problem below. Most fixes take under a minute.

**First stop:** the setup check. It runs by itself every time you start the studio, and inside the studio you can run it any time: press **?** and click **Fix my setup**. It names the problem and the fix in plain words. Its last report is saved in `doctor.log` in the studio folder.

---

**On Steam?** Use Steam > Stark World Engine > Properties > Installed Files > Verify integrity of game files instead of
downloading or extracting a zip again, and keep your games in `Documents\Stark World Engine\My_Games`. Refunds go through
Steam.

**The 3D view is slow on an older laptop?** Press Esc in play mode and set Graphics to **Low**: it turns off shadows,
ambient occlusion, bloom and normal maps, draws fewer plants and renders at half resolution.

## Starting the studio

### What the setup check's messages mean
When you start Stark World Engine, the black window (Terminal on Mac/Linux) prints **"Setup check: N of N OK."** and lists anything that needs attention:

- **[STOP]** must be fixed before the studio starts. The window says exactly how.
- **[TIP ]** is advice. The studio starts anyway.
- **[FIXED]** means it repaired something by itself (for example the built-in Python's settings file; a backup is kept as `.bak`).
- **[NOTE]** is just information, for example "Stark World Engine is already running - opening it in your browser".

The sections below cover each message.

### "Stark World Engine has to be extracted from the zip first"
You double-clicked the launcher inside the zip preview, so Windows only gave it a temporary copy and your games would be lost. Press **Y** to open the folder with the zip, right-click the zip > **Extract All...** > **Extract**, then double-click `START_STARK_WORLD_ENGINE.bat` in the new folder.

On a Mac, the same check catches macOS running the app from a hidden read-only copy: move the Stark World Engine folder out of Downloads (for example into Documents) with Finder and start again.

### "Stark World Engine is in Program Files..." / "This folder is read-only..."
The studio needs to save your games next to itself. Press **Y** when asked: it copies itself to `Documents\StarkWorldEngine` (or your user folder if Documents is synced) and starts from there. The original folder isn't changed; delete it later if you like. Or move the folder yourself to somewhere you own, such as Documents or the Desktop.

### "The folder path is very long" / "...inside a syncing folder"
Both are tips. Windows has trouble with very long paths, and OneDrive, Dropbox, iCloud or Google Drive can lock or pause files while you save. It works, but for a smooth ride move the folder to `Documents\StarkWorldEngine` (a folder that isn't synced).

### "Stark World Engine files are missing or damaged"
Usually an antivirus quarantined a file right after unzipping. Restore it (Windows: **Windows Security > Virus & threat protection > Protection history**, pick the Stark World Engine item > **Restore** or **Allow on device**), or download the zip again from your purchase page and extract it into a **new** folder. Copy your `My_Games` folder across. See also [My antivirus flagged or removed a file](#my-antivirus-flagged-or-removed-a-file).

### "Stark World Engine is already running - opening it in your browser"
Nothing is wrong: a studio from this folder is already open, so the launcher just opens your browser to it. To restart the studio, close its other black window first.

### "All ports 8505-8520 are in use by ..."
The studio needs one free port between 8505 and 8520. The message names the programs using them. Close them (often several old studio windows), or restart the PC, then start again. Advanced: `python studio_app.py --port 8600` picks a port yourself.

### "No Chrome, Edge or Firefox found"
The studio opens in your web browser and works best in a current Chrome, Edge or Firefox (all free). Install one and start again.

### "Low disk space" / "This computer's clock looks wrong"
Free up at least 1 GB for saves and exports. A wrong clock doesn't break anything, but save dates and backups look odd: turn on **Set time automatically** in Windows Settings > Time & language (Mac: System Settings > General > Date & Time).

### "Your key (key.txt) is not valid, so the studio runs as Free"
Nothing is lost. See [License keys](#license-keys) below.

### Windows: "This PC doesn't have it yet" (Python)
The download includes its own Python in the `runtime\python` folder, so this only appears if that folder is missing or blocked (usually by an antivirus, or because the zip wasn't fully extracted).

- Press **Y**: the launcher fetches the official Python 3.12.10 from python.org (about 11 MB), checks it, and keeps it inside the Stark World Engine folder only. No admin rights needed; nothing else on your PC changes.
- Or press **N** and install Python yourself: python.org > Downloads, run the installer, **tick "Add python.exe to PATH"** on the first screen, then double-click `START_STARK_WORLD_ENGINE.bat` again.
- Or extract the zip again into a new folder, so the built-in Python is complete.

### "The bundled Python could not start"
The launcher then tries an installed Python instead. To fix the built-in one, extract the zip again into a new folder (and check your antivirus's quarantine).

### "The studio stopped unexpectedly"
The black window then shows the last lines of `launcher.log`. Closing other Stark World Engine windows and starting again usually fixes it. If not, send those lines to the seller (see [Still stuck?](#still-stuck)).

### macOS: the launcher won't open, or says Python isn't installed
- "Cannot be opened because it is from an unidentified developer": right-click `start_stark_world_engine.command` > **Open** > **Open**. You only need to do this once.
- "isn't installed on this Mac yet": the launcher opens python.org for you. Download the macOS installer, click through it, then double-click the launcher again. (Optional: in the Python folder in Applications, double-click **Install Certificates.command**; the studio itself never goes online.)

### Linux: "Stark World Engine runs on Python 3.10 or newer"
Install it with your package manager (for example `sudo apt install python3`, `sudo dnf install python3` or `sudo pacman -S python`), then run `bash start_stark_world_engine.sh` again. Check your version with `python3 --version`.

### My Python is too old (macOS / Linux)
Run `python3 --version`. If it's below 3.10, install a newer version from python.org or your package manager. You can keep the old one; the launcher looks for 3.10 and newer.

### The browser didn't open
Copy the address printed in the black/terminal window (like `http://127.0.0.1:8505`) into your browser.

### I opened the studio from another device and it doesn't load
By design, the studio only answers on `127.0.0.1` / `localhost` on the same computer. That keeps your files private. (The **phone hub** is the one part made for other devices; see below.)

### I closed the black window and the studio stopped
The black window *is* the studio's engine. Keep it open while you work; close it when you're done.

---

## Browser and graphics (WebGL2)

### The stage is black or shows a WebGL error
The 3D builder needs **WebGL2**. When it isn't available, the studio opens a help card by itself ("3D graphics are switched off in this browser"). 2D worlds still work.

1. Use a current **Chrome**, **Edge** or **Firefox**.
2. Turn hardware acceleration on: Chrome/Edge **Settings > System > "Use graphics acceleration when available"**, then restart the browser. Firefox: **Settings > General > Performance**, untick "Use recommended performance settings" and tick "Use hardware acceleration when available".
3. Check WebGL2: visit `chrome://gpu` (Chrome/Edge) and look for "WebGL2: Hardware accelerated", or `about:support` in Firefox.
4. Update your graphics driver (NVIDIA, AMD or Intel website, or Windows Update > Advanced options > Optional updates).
5. Remote desktop sessions and some virtual machines don't provide WebGL2. Run the studio on the machine directly.

### It's slow or stuttery
- While playing, press **Esc** and set **Graphics** to medium or low.
- Close other heavy tabs and apps.
- On laptops, plug in power and choose the high-performance graphics: **Windows Settings > System > Display > Graphics**, add your browser, choose **High performance**.
- Very dense worlds: thin out trees and rocks a little. Fewer, well-placed objects often look better anyway.

### No sound
Browsers block sound until you click the page. Click once on the stage, then play. Also check the tab isn't muted (right-click the tab), and that **Sound: on** is set in the in-game menu (**Esc**).

### My soundtrack doesn't play
- It plays in **▶ Play** mode in the studio. Click the page once first (browsers block sound until you do).
- Exported HTML5 cartridges don't include the soundtrack WAV. A tune made in the **Tune** tab does play inside exported games.
- While a Tune is set, it replaces the soundtrack in Play mode. Clear the Tune to hear the soundtrack again.
- "The soundtrack maker is not available in this copy": `music_engine.py` is missing from the folder. Extract the zip again into a new folder.
- Making a soundtrack can take up to about 20 seconds on a slower PC. Wait for "Soundtrack ready".

### The interface is in the wrong language
Use the language drop-down at the top (EN, ES, FR, ...). Your choice is remembered.

---

## Saving and files

### "Saved" doesn't appear / "Not saved: ..." shows
- Make sure the game has a **name**.
- Don't run the studio from inside the zip: unzip it first, to a folder you can write to (Desktop or Documents, not `C:\Program Files`). The setup check warns about this.
- Cloud-sync folders (OneDrive, Dropbox) can briefly lock files. If saves fail there, move the studio folder somewhere local.

### Where are my games?
In the `My_Games` folder next to `studio_app.py`, one folder per game.

### I need an older version of my game
**World** tab > **Restore an earlier version...** > **Restore this one**, then **Save**. The three previous versions of each saved file are kept in `My_Games/.backups/`.

### My players' saves are gone
In-game saves (the three slots and the autosave) are kept in the browser on that computer. They disappear if the browser's site data is cleared, and they don't move to another browser or computer.

---

## Playing

### The portal or exit won't let me through
- "Finish your quests first!": the game has quests still in progress. Press **L** to see what's left.
- A portal can be locked until a quest is done (**Opens after quest** in its settings).

### I can't use Dash, Whirl or Star Bolt
They unlock at player levels 2, 3 and 4. Earn XP by defeating enemies and finishing quests.

### A person won't talk
Walk right up to them until the **E** prompt appears, then press **E**.

---

## License keys

### "Activate" shows an error, or the copy stays "unregistered"
- Paste the **whole key in one piece**. It's one long line starting with `GS1-`. Email apps sometimes break long lines: copy it from a plain-text view if you can.
- No spaces or extra characters at the start or end.
- Using `key.txt`? It must sit in the same folder as `studio_app.py` and contain only the key. Restart the studio after creating it.
- A wrong key never breaks anything: the studio keeps working as unregistered.

### Godot / UE5 buttons are greyed out
They need a valid **Pro** or **Studio** key. Check the lines under the export buttons and next to **Activate**: they show your tier and whether you're registered. An older Standard key doesn't include these exporters (it exports every level with no note).

### UE5 export says it supports 3D worlds only
That's correct: export 2D worlds to Godot 4 or as an HTML5 cartridge.

### The Godot project looks dark, flat or won't run
- Use Godot **4.2 or newer**. The 3D scene uses the Forward+ renderer, which needs a graphics card that supports Vulkan or Direct3D 12.
- The first open imports and compiles shaders; give it a moment.
- Only your first level is exported, and objects are simple shapes by design. See [Godot 4 project](USER_GUIDE.md#godot-4-project-pro-and-studio).

### Unreal Engine: the Stark World Engine menu is missing or the level is empty
- Use Unreal Engine **5.4 or newer**, and let it convert the project if your engine is newer.
- The project turns on Unreal's Python Editor Script Plugin; if Unreal asks to enable plugins or restart, say yes.
- The level isn't built until you choose **Stark World Engine > Build level from world.json** (or **Tools > Execute Python Script...** > `Content/Python/stark_build_level.py`). What it did, with any warnings, is in `Saved/Stark/build_report.json`.
- Honest note: we had not yet run this export in a real Unreal Engine 5 install at release. If it fails, please send the seller `build_report.json` and the Output Log.

---

## Antivirus and security warnings

### Windows says "Windows protected your PC" (SmartScreen)
SmartScreen warns about any new download that hasn't built up a reputation yet. The `.bat` files are short plain-text scripts: right-click > **Edit** (or open in Notepad) to read exactly what they do. If you're comfortable, click **More info > Run anyway**.

### My antivirus flagged or removed a file
Some antivirus products are suspicious of new script files and zips from small sellers. Stark World Engine is plain Python and HTML you can read in any text editor. The only programs in the download are the official Python from python.org (in `runtime\python`, Windows) and its numpy library, both unchanged.
1. Re-download from your purchase page to make sure the file is complete.
2. Restore the file from quarantine and add the studio folder as an exception, if you're comfortable doing that.
3. Still stuck? Contact the seller and mention which antivirus and which file.

### Firewall popup when starting the studio
The studio itself only listens on `127.0.0.1`, which normally doesn't need firewall access. If Windows asks anyway, choosing **Cancel** is fine for the studio. (For the phone hub, see below.)

---

## Creator suite (Studio edition)

### My phone can't reach the hub
1. Phone and PC must be on the **same Wi-Fi / home network**. Guest networks often block devices from seeing each other.
2. Run the **Setup Wizard** (`SETUP_STARK_WORLD_ENGINE.bat`) and go to **Your phone remote**. If the hub isn't running, click **Start it**. If the phone says it can't connect, click **Allow on my home network** and click **Yes** on the Windows prompt (it may blink in the taskbar). This adds one firewall rule for port 8510, for devices on your home network only, not the internet. The wizard shows "Allowed on your home network" when it's done.
3. Scan the QR code shown in the wizard, or open the address it shows and type the PIN.
4. VPN on the PC or phone? Turn it off while using the hub.

**Fallback (if the wizard can't add the rule):** open **PowerShell as administrator** and run this one line:

```
New-NetFirewallRule -DisplayName "Stark World Engine Creator Hub 8510" -Direction Inbound -Protocol TCP -LocalPort 8510 -RemoteAddress LocalSubnet -Action Allow -Profile Any
```

It opens port 8510 to your local network only, not the internet.

### The QR code says it "was already used or has expired"
Each QR code logs in one phone, once, within 15 minutes. Type the PIN on the login page instead, or make a new QR code (open the wizard's **Your phone remote** step again, or run `python creator_hub.py --qr`).

### An old link with `?pin=` in it doesn't log me in
That's on purpose: the PIN is never accepted from a link any more. Scan a fresh QR code or type the PIN on the login page.

### The hub asks for the PIN again
Your phone stays logged in until the hub is restarted. After a restart, scan a new QR code or type the PIN.

### The hub says the PIN is wrong / I'm locked out
The PIN is shown in the Setup Wizard. It is `hub_pin` in `config.json` in the studio folder, unless you moved it to your private app-data folder with `python gs_common.py --move-secrets`. After 5 wrong tries within 10 minutes from one device, that device's PIN logins are locked for 15 minutes; wait, then try again with the right PIN.

### The phone camera doesn't work
The Camera tab needs the HTTPS VDO.Ninja link the hub provides and an internet connection. Allow camera access when Safari asks.

### OBS doesn't start or stop recording from the phone
- OBS must be open, using **Profile > Stark_Profile**.
- The websocket must be on: in OBS, **Tools > WebSocket Server Settings** > "Enable WebSocket server" on port 4455. Running the Setup Wizard's camera step (or `python obs_bridge.py`) with OBS closed sets this up for you; restart OBS afterwards.

### The auto editor does nothing
- Is **FFmpeg** installed? The Setup Wizard's **Check your tools** step shows it and can install it. Or run `ffmpeg -version` in a new terminal; if it's not found, `winget install Gyan.FFmpeg`, then open a new terminal. You can also set the full path to `ffmpeg.exe` as `"ffmpeg"` in `config.json`.
- Are your recordings in `raw_clips/`? The editor reads only that folder.
- It waits until a file stops growing. Stop the recording, then give it a moment.
- Each recording is edited only once (even if renamed). To get a new edit, record a new take.
- Only one watcher runs at a time; check `.claude/editor.log` in the studio folder for details.
- Run `python auto_editor.py --probe` to see which video encoder it will use.

### Captions are only a title, not my words
Add a `.srt` subtitle file with the same name next to the clip, or install the free local speech-to-text: `pip install faster-whisper`.

### YouTube upload doesn't happen
- You need your own free Google Cloud **Desktop app** OAuth client saved as `client_secrets.json` in the studio folder. The Setup Wizard's **YouTube** step walks you through it, or run `python publishers.py --setup-wizard` for a checklist.
- Uploads only happen after you **Approve** in the hub.
- Uploaded videos are **private** (and scheduled if you picked a time). Check YouTube Studio > Content.

---

## Still stuck?
Contact the seller through the store page. Include your operating system, browser, edition, what the black window (or terminal) printed, and `doctor.log` / `launcher.log` from the studio folder if you can. Please never send your license key in public comments.
