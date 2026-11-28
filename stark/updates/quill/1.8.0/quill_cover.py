"""Print cover planner: page count, spine width and the full-wrap cover size for a paperback, from the book's words.

Pure arithmetic on published print-on-demand figures (inches per page for white, cream and colour paper; 0.125 in
bleed). Printers change their numbers: the result says to check the printer's own calculator before ordering.
"""
from __future__ import annotations

from html import escape

BLEED = 0.125          # inches, every edge of a full-wrap cover
SAFE = 0.25            # keep text this far inside the trim line
DPI = 300
MIN_PAGES, MAX_PAGES = 24, 828
SPINE_TEXT_MIN_PAGES = 80   # below this most printers ask for a blank spine
FRONT_MATTER = 6       # title, copyright, dedication, contents and blanks
PAPER = {"white": 0.002252, "cream": 0.0025, "color": 0.002347}   # inches of spine per page
TRIMS = {             # inches (w, h) and words a typeset page holds at a normal novel size
    "5x8": (5.0, 8.0, 250),
    "5.25x8": (5.25, 8.0, 265),
    "5.5x8.5": (5.5, 8.5, 290),
    "6x9": (6.0, 9.0, 330),
    "8.5x11": (8.5, 11.0, 550),
}


def pages_for(words, trim="6x9", chapters=0):
    """Estimated printed pages: text pages, a fresh page per chapter opening, front matter; even, within limits."""
    per = TRIMS[trim][2]
    words = max(0, int(words or 0))
    pages = -(-words // per) + max(0, int(chapters or 0)) // 2 + FRONT_MATTER
    pages += pages % 2
    return max(MIN_PAGES, min(MAX_PAGES, pages))


def plan(words, trim="6x9", paper="cream", chapters=0, pages=None):
    """-> dict with pages, spine and full cover sizes in inches, millimetres and pixels at 300 dpi."""
    if trim not in TRIMS:
        raise ValueError("unknown trim size")
    if paper not in PAPER:
        raise ValueError("unknown paper")
    w, h, _ = TRIMS[trim]
    estimated = pages is None
    asked = None if estimated else int(pages)
    p = pages_for(words, trim, chapters) if estimated else max(MIN_PAGES, min(MAX_PAGES, asked + asked % 2))
    spine = round(p * PAPER[paper], 4)
    full_w, full_h = round(2 * w + spine + 2 * BLEED, 4), round(h + 2 * BLEED, 4)
    notes = []
    if asked is not None and not MIN_PAGES <= asked <= MAX_PAGES:
        notes.append("Paperbacks here run %d to %d pages, so these figures use %d pages, not %d."
                     % (MIN_PAGES, MAX_PAGES, p, asked))
    elif asked is not None and asked % 2:
        notes.append("Printed pages come in pairs, so %d pages is counted as %d." % (asked, p))
    if p < SPINE_TEXT_MIN_PAGES:
        notes.append("Under %d pages: leave the spine blank; it is too thin for text." % SPINE_TEXT_MIN_PAGES)
    if estimated:
        notes.append("Pages are an estimate from the word count; the final PDF's page count is the one to use.")
    notes.append("Printers update their figures: check the printer's own cover calculator before you order.")
    mm = lambda x: round(x * 25.4, 1)  # noqa: E731
    return {"trim": trim, "paper": paper, "pages": p, "estimated": estimated, "words": int(words or 0),
            "spine_in": spine, "spine_mm": mm(spine), "spine_text": p >= SPINE_TEXT_MIN_PAGES,
            "width_in": full_w, "height_in": full_h, "width_mm": mm(full_w), "height_mm": mm(full_h),
            "width_px": round(full_w * DPI), "height_px": round(full_h * DPI), "bleed_in": BLEED, "safe_in": SAFE,
            "notes": notes}


def template_svg(p, title=""):
    """A to-scale layout guide (1 in = 100 units): bleed, trim, safe areas, back, spine and front."""
    k = 100.0
    w, h = TRIMS[p["trim"]][0], TRIMS[p["trim"]][1]
    W, H, b, s, sp = p["width_in"] * k, p["height_in"] * k, BLEED * k, SAFE * k, p["spine_in"] * k
    back_x, spine_x, front_x = b, b + w * k, b + w * k + sp
    t = (title or "Your book")[:80]

    def rect(x, y, rw, rh, cls):
        return '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="%s"/>' % (x, y, rw, rh, cls)

    def label(x, y, text, size=14):
        return '<text x="%.1f" y="%.1f" font-size="%d" text-anchor="middle">%s</text>' % (x, y, size, escape(text))

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %.1f %.1f" width="%.2fin" height="%.2fin">' % (W, H, W / k, H / k),
        "<style>.bleed{fill:#fdecea;stroke:none}.trim{fill:#fffdf8;stroke:#1b1b1b;stroke-width:1}"
        ".safe{fill:none;stroke:#1d6fb8;stroke-width:1;stroke-dasharray:6 4}.spine{fill:#f1eadb;stroke:#1b1b1b}"
        "text{font-family:Georgia,serif;fill:#1b1b1b}</style>",
        rect(0, 0, W, H, "bleed"),
        rect(back_x, b, w * k, h * k, "trim"), rect(front_x, b, w * k, h * k, "trim"),
        rect(spine_x, b, sp, h * k, "spine"),
        rect(back_x + s, b + s, w * k - 2 * s, h * k - 2 * s, "safe"),
        rect(front_x + s, b + s, w * k - 2 * s, h * k - 2 * s, "safe"),
        label(back_x + w * k / 2, b + h * k / 2, "BACK"), label(front_x + w * k / 2, b + h * k / 2, "FRONT"),
        label(front_x + w * k / 2, b + h * k / 2 + 24, t, 12),
        label(W / 2, H - 6, "%s in x %s in, spine %s in, %d pages (%s), 0.125 in bleed"
              % (p["width_in"], p["height_in"], p["spine_in"], p["pages"], p["paper"]), 11),
        "</svg>",
    ]
    return "".join(parts)
