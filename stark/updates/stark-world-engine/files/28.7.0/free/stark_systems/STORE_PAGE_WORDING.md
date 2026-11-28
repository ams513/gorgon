# Store-page wording proposal: Stark World Engine Systems

This follows the existing STORE_PAGE.md style: concrete, no "AAA", no unproven claims. Use it only after the
integration patches have landed and the samples ship. Until then, say "coming in the next update".

## Section: "Systems for any kind of game"

- **Nine genre kits, each with a small playable example:** action RPG, platformer, twin-stick shooter,
  survival / crafting, first-person horror, top-down racing, push-block puzzle, village-building strategy and a
  visual novel. Every example is a single offline HTML file.
- **Character controllers:** top-down, third-person, first-person, side-scrolling (coyote time, jump buffering,
  variable jump height), vehicles (grip, drift, boost) and point-and-click.
- **Combat:** melee arcs, projectiles, hitscan weapons with ammo and reloads, abilities with cooldowns and
  stamina / mana, critical hits, armour, stagger, knockback and status effects.
- **AI:** enemies that see (sight cones, line of sight) and hear you, behaviour trees and state machines, squads that
  flank or surround, and villagers that follow daily schedules.
- **Inventory, crafting and loot tables; XP, skill trees and perks; factions and reputation; shops whose prices
  react to reputation and stock.**
- **Quests:** your own hand-written quests, plus a radiant quest generator with variety rules, so repeated jobs do
  not come back to back. These are generated jobs: they reuse your places and enemies.
- **Day/night and weather that change play:** night shortens enemy sight, rain makes roads slippery, cold nights
  drain warmth.
- **Save slots, a HUD builder, camera rigs with cinematic timelines, and synthesised sound effects.**
- **Scripting:** write event-driven scripts in a safe subset of JavaScript, or connect visual nodes. Scripts run in
  a sandbox and cannot reach your files or the internet.
- **Big worlds:** content streams in chunks around the player, with budgets for visible objects and active enemies.
  Generators make dungeons, regions and villages full of named people with daily routines.
- **Play-time estimate:** shows how long your game might take to play and how it got there. It is an estimate from
  your content, not a measurement.

## Section: "Child safety (always on)"

> Stark World Engine has child-safety rules that cannot be turned off. Every character has an age group. Child
> characters cannot be harmed, targeted, captured or romanced. The studio refuses to export sexual or abusive content
> involving minors and tells you exactly which line to fix; exported games run the same checks. Using Stark World Engine
> to create such content ends your licence.

## Honest-details bullets to add

- The genre examples run in Stark World Engine Systems' own lightweight player (2D canvas and a first-person raycaster). The
  studio's 3D renderer does not draw them yet.
- Godot and UE5 exports carry the systems data (quests, AI, items, ages) in world.json. Gameplay is not re-created
  inside those engines yet.
- The child-safety checker reads text and game data in 12 languages. It cannot see images or 3D models.

Avoid: "AAA", "hundreds of hours" (unless a game's estimate and playtests actually show it), "any game imaginable",
"unbreakable" protection.
