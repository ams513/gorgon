# Credits: animation + motion capture (stark_anim)

## MediaPipe (webcam motion capture): Apache License 2.0

Copyright 2023 The MediaPipe Authors / Google LLC. These are unmodified files in `models/`. The full list, with sizes, SHA-256 hashes and source URLs, is in `models/CREDITS.md` and `models/manifest.json`. The licence text is `models/LICENSE-Apache-2.0.txt` and the attribution notice is `models/NOTICE.txt`.

- **Pose Landmarker Lite** (`pose_landmarker_lite.task`): 33 body landmarks.
- **Face Landmarker** (`face_landmarker.task`): 52 blendshape scores.
- **Tasks Vision web runtime 1.2.3**: `vision_bundle.mjs` and the `wasm/vision_wasm_internal.*` files.

The owner approved these files on 2026-11-19 ("body + face"). They were fetched with `python -m stark_anim models fetch --approved`, and the pinned hashes were verified.

## Quaternius (Universal Base Characters, Universal Animation Library): CC0 1.0

Quaternius (quaternius.com) published these under the public-domain dedication. Credit is not required, but it is given.

- `data/stark_humanoid.rig.json`: joint names, hierarchy, rest pose and inverse-bind numbers derived from the Superhero base character.
- `library/quaternius_ual/*.gclip.json`: the 43 Universal Animation Library clips, retargeted onto the Stark World Engine rig, with root motion extracted, feet locked, and footstep and hit events added.
- `fixtures/*.bvh`, `fixtures/*.glb` and `fixtures/take_*.json`: those clips re-expressed on other rig layouts, plus landmark takes produced from them.
- Previews render the asset library's Quaternius characters (`assets/models/characters`).

## Everything else in this folder

Stark World Engine: original code and data, including the test rigs in `testrigs.py`, whose offsets were rebuilt by hand with no third-party data copied.
