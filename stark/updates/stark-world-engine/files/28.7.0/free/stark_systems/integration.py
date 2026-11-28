"""stark_systems/integration.py: the few functions studio_app.py (and the UE5 / Godot exporters) call.
See INTEGRATION_PATCHES.md. Everything here fails CLOSED: if the checker cannot run, nothing is exported.

  gate(world)          -> the world to export (explicit ages + child_safety stamp) or raises ChildSafetyError
                          (a ValueError whose text is a short, human explanation)
  report(world)        -> the full child-safety report (for a Safety panel / the /api/safety route)
  tag_generated(world) -> Make-me-a-game output with child characters tagged child
  runtime_js()         -> the Stark World Engine Systems runtime (child-safety guards + systems) to inline after the engine
"""
import threading

from . import bundle, child_safety

_JS = {}
_LOCK = threading.Lock()


def gate(world):
    return child_safety.enforce_export(world)


def report(world):
    return child_safety.scan_world(world)


def tag_generated(world):
    return child_safety.tag_world(world, generated=True)


def runtime_js():
    """Cached by the js/ + lexicon mtimes, so a studio session picks up an update without restarting."""
    import os
    files = bundle.js_files() + [bundle.LEXICON, bundle.NODES]
    key = tuple((p, os.stat(p).st_mtime_ns) for p in files)
    with _LOCK:
        if _JS.get('key') != key:
            _JS['key'], _JS['js'] = key, bundle.bundle()
        return _JS['js']
