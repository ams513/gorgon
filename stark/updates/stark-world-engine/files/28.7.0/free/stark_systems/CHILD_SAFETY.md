# Stark World Engine child-safety rules (policy `stark-child-safety`, version 1)

**Hard rule: nothing involving the abuse of children, ever.** These rules cannot be switched off by a creator,
a world field, a script or a setting. There is deliberately no flag, argument or option that relaxes a blocking rule;
a test (`tests/test_stark_systems_safety.py::test_cannot_be_switched_off`) keeps it that way.

This file is the spec. Every implementation must behave the same:

| Where | Implementation |
|---|---|
| Studio export gate (Python) | `stark_systems/child_safety.py` (`enforce_export`, `scan_world`) |
| Cartridge / studio Play runtime (JS) | `stark_systems/js/05_safety.js` (+ guards in combat, AI, dialogue, scripts, save) |
| UE5 / Godot bridges | to be mirrored by the bridge agent (see "Bridge mirror" below) |

The word lists live in ONE file, `safety/lexicon.json`. The golden cases every implementation must pass live in
`safety/test_vectors.json` (57 texts, 15 worlds, benign and planted-bad; the bad fixtures are abstract and non-explicit).

## 1. Age groups

* Every character has an age group: `child`, `teen` or `adult`. If none is set, the default is `adult`.
  Characters are studio `npc` / `enemy` objects, `systems.characters` entries, and character-like `systems.entities`.
* Accepted input: the three words; a number of years (under 13 → child, 13-17 → teen, 18 or more → adult); synonyms
  (`kid`, `minor`, `toddler` → child; `teenager`, `adolescent`, `youth` → teen; `grown-up`, `elder` → adult).
  Any other value blocks export (CS015). Until it is fixed, the character is treated as a child.
* **Inference:** if a character is tagged adult or not tagged at all, but its own text describes it as a minor, it is
  treated as a minor and the creator is warned (CS012). Examples: "A ten-year-old girl who…", "She is only 12",
  "A shy young boy", "Age: 15", "12歳の少女". Inference only ever makes the protection stronger.
  Words that only mean "child" in another language (German "Kind") never trigger inference.
* Make-me-a-game output: characters generated as children carry `role: 'child'` and are tagged `child`
  (`tag_world(..., generated=True)`, `integration.tag_generated`). `procgen.npcs` creates families whose children are
  tagged `child`; children are given fetch quests, never combat roles.
* Ages are **locked at runtime**. A script that assigns `entity.age` gets a SafetyError. Save files never store or
  restore ages. A loaded save cannot make a child hostile.

## 2. What the runtime guarantees for child and teen characters

| Guarantee | How |
|---|---|
| Immune to all damage | `combat.applyDamage` returns 0; status ticks, environment, falls and script `hp =` cannot lower HP; the safety system restores HP and clears `dead` every frame |
| Cannot die | `combat.kill` throws; HP is clamped to at least 1 |
| Never a combat target | AI perception filters them out; `ai.setTarget` throws; melee sweeps, hitscan rays and projectiles pass through them; factions never make them hostile |
| Never an enemy | spawned from data: turned into a friendly villager (team neutral); spawned from a script: throws |
| Never captured | `ai.capture` and dialogue `capture` / `kidnap` / `imprison` / `enslave` throw |
| No romance / intimacy | `romance.start`, romantic affinity and dialogue `romance` / `date` / `marry` / `kiss` / `seduce` / `flirt` throw (friendship affinity is allowed) |
| No nudity / undressing | `appearance.undress`, and `setOutfit` with none / nude / underwear / lingerie, throw |
| No "harm" scripted actions | dialogue `damage` / `harm` / `hurt` / `kill` / `attack` and every harm node in a graph throw |
| Text shown at runtime is checked | `ui.say`, `ui.toast`, entity names, `say`, HUD values, subtitles and radiant-quest text go through `assertText` / `safeText` |

Every refused call throws `SafetyError` with a plain message, for example:
`Child safety: "Pim" is a child or teen character: it cannot be damaged, killed or attacked. (damage refused)`.
Scripts can catch it, but the action never happens.

A child or teen *player character* is protected in the same way. Games with child heroes therefore need non-violent
fail states: time, puzzles, hunger, or caught-and-restart without capture.

The studio's existing engine is protected by `GSYS.install(GS)`: Play mode runs on a sanitised copy of the world
(ages locked, child or teen enemies turned into villagers), a blocked world refuses to start, and the `damage()` /
`hitE()` guards (patch P7) are a second layer.

## 3. The content checker

It scans every authored string:
* world, level and quest text, dialogue speakers, lines and choices, item names and descriptions, object names, labels and lines;
* every `systems.*` string, plus user-chosen ids and map keys (with `_ . -` turned into spaces);
* the string literals and comments of scripts (code identifiers are not treated as text, so `node.children` is fine);
* node-graph parameters.

**Normalisation (both passes):** NFKC; zero-width characters removed; curly apostrophes unified; exception phrases
masked (replaced by spaces). Examples: "naked eye", "comic strip", "child's play", "children of the night", the
biological "sex of the foal", "drug abuse", "abuse of power", "sleep with the light on", "Billy the Kid",
"pieds nus", "a occhio nudo", 裸足.

**Evasion pass ("skeleton"):** accents removed; look-alike Cyrillic and Greek letters mapped (only inside mixed-script
words); leetspeak mapped (only in runs that mix letters and digits); s.p.a.c.e.d letters joined; letters repeated
3 or more times collapsed. It runs whenever it changes the text.

**Matching:** whole words, by token lookup, so "kidding", "Sussex", "therapist" and "grapes" never match.
Lexicon term syntax:
* `stem*` matches a word starting with the stem;
* spaces make a phrase;
* a leading `~` marks a term used only when its language is active: declared in `systems.locale.languages`, at least
  2 stopwords present, or an unambiguous term of that language present. This keeps German *Kind* (child) away from
  English *kind*.

CJK terms match as substrings. Japanese katakana-only terms need non-katakana neighbours, so カロリー (calorie) is not
ロリ. Korean terms must start a word, so 해야한다 is not 야한.

**Languages:** English (deepest), Spanish, French, German, Italian, Portuguese, Dutch, Polish, Russian, Japanese,
Chinese, Korean.

Categories:
* `always_block`
* `minor`, `teen`, `minor_weak` (girl/boy: ambiguous), `minor_time` (childhood)
* `sex_strong`, `sex_weak` (nudity / romance / suggestive)
* `abuse`, `protective`

Plus age-number patterns ("12 years old", "aged 12", "under 18", "5th grader", "tiene 12 años", "12-jährige", "12歳")
and the names of every child or teen character in the world.

**Rules** (block = export refused; warn = shown, export allowed):

| Code | Sev. | Fires when |
|---|---|---|
| CS001 | block | an always-block term (terms that only exist to refer to sexual content involving minors) |
| CS002 | block | a strong sexual term and a minor reference in one sentence, or a weak term (nudity / undress / romance) within 8 tokens (16 CJK characters) of one |
| CS003 | block | a strong sexual term and a minor reference anywhere in the same string |
| CS004 | block | a strong sexual term anywhere in a dialogue, quest, script or graph that involves a child or teen character (speaker, owner, giver, step target, attached entity) |
| CS005 | block | any sexual or nudity term in a child or teen character's own record (name, description, backstory, lines) |
| CS006 | block | a minor in romance data (`romanceable`, `systems.romance`, romantic `relationships`, romance dialogue actions) |
| CS007 | block | a minor as an enemy, hostile, combatant, squad member or quest defeat target, or a harm dialogue action on one |
| CS008 | block | abuse wording next to a minor reference; passive abuse ("the young boy was beaten"); abuse + childhood |
| CS009 | block | active violence aimed at a minor ("kill the child"), unless the text frames it as protection (save, rescue, protect, stop, never, don't, ...) |
| CS010 | block | nudity or undressing data for a minor (outfit none / underwear / lingerie, coverage partial, undress actions) |
| CS011 | block | a minor set up to be captured (`capturable`, `systems.ai.capture`, capture dialogue actions) |
| CS014 | block | a script or graph that calls damage / kill / setTarget / capture / setHostile / romance / undress on a literal minor id |
| CS015 | block | an age value that is not child / teen / adult / a number |
| CS012 | warn | described as a minor but tagged adult: treated as a minor |
| CS013 | warn | strong sexual wording next to "girl" / "boy" only (could be adults: say "woman" / "man") |
| CS104 | warn | romance / nudity wording in a conversation or quest that involves a minor |
| CS109 | warn | past violence against a minor mentioned as backstory ("the child was killed by the plague long ago") |

Deliberate choices, approved under the hard rule:
* Abuse of children is blocked even when the story frames it as a rescue ("rescue the abused children"). Violence
  verbs get the protective exemption; abuse words do not.
* Sexual content between adults is not this checker's business. "The courtesan seduces the duke" passes.
* "girl" / "boy" alone are not treated as minors (adult usage is common). Only "little girl", "young boy",
  "schoolgirl", ages and similar count.

Performance: 41 starter and generated games (9,084 strings) scan in about 0.8 s with zero findings. The scan has no
network access and no model: it is deterministic.

## 4. Export gate

`enforce_export(world)` returns a deep copy with an explicit `age` on every character (`age_inferred: true` when
inferred) and a `child_safety` stamp (`policy`, `policy_version`, `lexicon_version`, `minors`, `warnings`). It raises
`ChildSafetyError` (a `ValueError`) when anything blocks. `str(error)` is at most 300 characters, so the studio can
show it as-is, and `error.report` holds every finding.

Studio wiring (INTEGRATION_PATCHES.md):
* P2: every export target (cartridge, Godot, UE5) goes through the gate.
* P3: so does Open in Unreal.
* P9: `POST /api/safety` returns the report for a Safety panel.

It fails closed: without `stark_systems/`, export refuses.

The `child_safety` stamp is informational. No runtime ever reads it to relax a rule. Every runtime re-scans.

## 5. Cartridge runtime

Before a cartridge starts, `GSYS.cartridgeBoot` / `GSYS.install` run the same world scan in JS. If it blocks, the
game shows "This game cannot run" with the reasons, so a world edited by hand after export still cannot run. Python
and JS give identical verdicts: `tests/test_stark_systems_runtime.py` compares rule and path sets on starter worlds,
generated worlds, the samples and the golden worlds.

## 6. Bridge mirror (UE5 / Godot)

The exported world already carries explicit ages and `child_safety.minors`. The bridges must:
1. Call `stark_systems.integration.gate(world)` at their own entry points (patch P11).
2. Give minors no damage path: no damage component, or an `ApplyDamage` override that returns 0. Exclude minors from
   AI perception targets, auto-aim and projectile hits. Never assign them a hostile team, faction or behaviour tree.
3. Reject romance, undress, capture and harm on minors in exported dialogue actions and graph nodes, with a log message.
4. If the engine scans or edits text at runtime, embed `safety/lexicon.json` and implement section 3. Parity is
   proven by passing `safety/test_vectors.json`.
5. Keep ages read-only: no Blueprint or GDScript setter.

## 7. Honest limits

* It reads **text and data only**. It cannot see images, 3D models, textures, animations or audio.
* It is a deterministic rule set. It catches direct wording, common evasions and the structural cases above. It does
  **not** understand euphemism, implication spread across far-apart strings, or languages outside the 12 listed.
* An exported HTML file can be edited by someone who wants to. The boot check refuses a hand-edited world, but
  someone who also rewrites the JS can remove it. The licence clause (TOS_CHILD_SAFETY_CLAUSE.md) covers that case.
* The non-English lists were written without native-speaker review. Have each language reviewed before release.
  Adding terms means editing `lexicon.json` and adding a test vector.
* False positives are possible. They block with an exact explanation, and the creator rewrites the sentence.
  Report recurring ones as test vectors (benign) together with a lexicon exception.
