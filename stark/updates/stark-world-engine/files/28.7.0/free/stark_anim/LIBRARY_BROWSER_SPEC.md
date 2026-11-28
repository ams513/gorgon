# Animation library browser: spec

This is the studio panel where creators find, preview, import, record, clean up and assign animations. The data and every action already exist in `stark_anim` (`studio_api.py` routes, `library.py`, `cleanup.py`, `export_map.py`). This spec describes the UI the v28.5 studio should build on top of them.

## Data

- **List.** `GET /api/anim/library?q=&category=&child=` returns `{clips: [...]}`. Each entry has:
  - identity and file: `id` (`lib:...` built-in, `mine:...` the creator's own), `name`, `origin` (`built-in` or `mine`), `file`;
  - classification: `category` (locomotion, traversal, combat, reaction, stance, interaction, idle, mocap, other), `tags`;
  - timing: `loop`, `duration`, `frames`, `fps`;
  - content: `events` (counts by type, such as `{footstep: 2}` or `{hit: 1}`) and `root_motion` (`{distance, speed, turn}` or null);
  - safety and provenance: `safety` (`neutral`, `harm` or `intimacy`), `source`, `licence`, `credit`;
  - `preview` (a GIF/MP4 path when one has been rendered).
- **One clip.** `GET /api/anim/clip?id=` returns the `.gclip.json`. Previews play it with `preview/preview.html` or `StarkSkinView`.
- **Starter library.** 43 Quaternius CC0 clips, retargeted, with root motion extracted, feet locked, and footstep/hit events.
- **The creator's clips.** Everything from `My_Games/_animations/clips` (mocap takes, imports, edits).

## Layout

```
+-----------------------------------------------------------------------------------+
| [search........................]  [Child-safe: ON]  Character: [Mara (adult) v]    |
| Categories: All  Locomotion  Combat  Reaction  Idle  Interaction  Traversal  Mine  |
| Filters:   [ ] loops only   [ ] root motion   [ ] has events                       |
+-----------------------------------------------------------------------------------+
|  +-------+ +-------+ +-------+ +-------+ +-------+      | Preview (selected)        |
|  | card  | | card  | | card  | | card  | | card  |      | [3D view, orbit, speed]   |
|  +-------+ +-------+ +-------+ +-------+ +-------+      | Walk_Loop  1.33 s  loop   |
|  ...virtualised grid (only visible cards rendered)...   | footsteps x2 - 0.89 m/s   |
|                                                         | [Assign to state v][Use]  |
|  [+ Import file]  [Record with webcam]                  | [Clean up...] [Export v]  |
+-----------------------------------------------------------------------------------+
```

**Card**

- **Image.** A poster frame, playing the GIF or MP4 on hover or keyboard focus. If no preview exists yet, it shows a skeleton silhouette and the "Render preview" action (`POST /api/anim/preview`).
- **Text.** Name, duration, a loop badge, a root-motion badge (arrow with m/s), event chips (footstep, hit), and an origin badge ("Mine").
- **Safety badge.** Only `harm` and `intimacy` clips get one (sword or heart icon, plus the word). Neutral clips get no badge.

**Preview pane.** The selected clip plays on the selected character, using the same runtime as cartridges. Controls:

- play/pause, scrub, 0.25x-2x speed;
- a root-motion toggle (walk forward vs in place);
- a mirror preview toggle;
- a ground/IK toggle.

## Actions

| Action | How | Backend |
|---|---|---|
| Search | words match name, tags and category (all words must match) | `?q=` |
| Filter by category / loops / root motion / events | chips and checkboxes | client side, or `?category=` |
| Import | drag and drop or a file picker: `.bvh .glb .gltf .fbx`. FBX needs Blender or FBX2glTF already installed; otherwise the message names the options. | `POST /api/anim/import` |
| Record | opens `/anim/webcam/mocap_studio.html`; a saved take shows up under "Mine" | `POST /api/anim/take` |
| Assign to state | idle / walk / run / sprint / jump / attack / hit / die / talk, or a custom state, for the selected character or a character type. Saved into `world.animations` (`{character_id: {state: clip_id}}`) and `world.anim_clips` (the clip ids to embed). | world JSON + INTEGRATION_PATCHES #6 |
| Clean up | panel: smooth (strength), lock feet, loop (blend s), trim (in/out), mirror, speed (x), footstep events, hit event. Writes a new clip named `<name>_edit`; the original is kept. | `python -m stark_anim clean` (route to add: `POST /api/anim/clean`, same options) |
| Export | Godot AnimationLibrary (`.tres`) or the UE5 package | `POST /api/anim/export` |
| Delete / rename | "Mine" only; built-in clips are read-only | (route to add, inside `My_Games/_animations/clips`) |

## Child safety (hard rule)

- The **Character** picker drives the filter. When the selected character is a minor (child or teen per `stark_systems`), "Child-safe" is forced ON and greyed out. While it is on, the list comes from `?child=1`: harm and intimacy clips are not listed and cannot be assigned or previewed on that character.
- "Assign to state" double-checks on the server (`child_safety.check_apply`). It refuses with the reason: "'Sword_Attack' is a harm animation; it can never be applied to a child character".
- With an adult selected, the toggle is the creator's choice and defaults to ON for new projects.
- A clip's class can be raised (for example marking a dance as intimacy) but never lowered below what its name and tags imply.

## States and messages

- **Empty "Mine".** "Nothing here yet: import a BVH / glTF / FBX file or record yourself with the webcam."
- **Mocap models missing.** "Webcam capture needs the pose model (not installed)." The Record button is disabled; replaying take files still works.
- **Import report.** After an import, show what the retargeter detected (from `report`). For example: "Mixamo rig, T-pose, scale 0.0108 (centimetres), 52 bones mapped, foot locking on". If `missing_core` is not empty, list the missing body parts in plain words.

## Accessibility and performance

- **Keyboard and screen readers.**
  - Arrows move between cards, Enter previews, Space plays or pauses, `/` focuses search.
  - Every badge has text. Previews start on focus as well as on hover. Reduced-motion users get poster frames only.
- **Speed.**
  - The grid is virtualised: only visible cards load media.
  - At most 3 previews play at a time, and a clip's JSON loads on first preview.
