"""The calendar colour scheme — the single source of truth for it.

Google's Calendar API takes an opaque numeric ``colorId``. Naming them is the
whole point of this module: ``--color teaching`` still means something when it
is read back in six months, ``--color 3`` does not.
"""

# Google's eleven event colours, by their names in the Calendar UI.
COLORS = {
    "lavender": "1",
    "sage": "2",
    "grape": "3",
    "flamingo": "4",
    "banana": "5",
    "tangerine": "6",
    "peacock": "7",
    "graphite": "8",
    "blueberry": "9",
    "basil": "10",
    "tomato": "11",
}

# What each colour MEANS.
#
# The three that earn their keep are teaching / student / deadline. On the
# calendar of someone who both takes courses and staffs them, those read
# identically in text and mean three different things: a course you teach, a
# date that predicts your support load, and your own work.
CATEGORIES = {
    "lecture": "9",     # blueberry — a class he attends
    "teaching": "3",    # grape     — a course he staffs
    "deadline": "11",   # tomato    — his own due dates
    "student": "6",     # tangerine — student deadlines (support load, not homework)
    "exam": "4",        # flamingo
    "esports": "10",    # basil     — Rocket League
    "meeting": "7",     # peacock   — standing meetings
    "personal": "2",    # sage
}

_BY_ID = {}
for _name, _id in CATEGORIES.items():
    _BY_ID.setdefault(_id, []).append(_name)


class UnknownColor(ValueError):
    """Raised for a colour that is neither a category, a name, nor 1-11."""


def resolve(value):
    """Category name, Google colour name, or raw id -> a Google colorId string."""
    key = str(value).strip().lower()
    if key in CATEGORIES:
        return CATEGORIES[key]
    if key in COLORS:
        return COLORS[key]
    if key.isdigit() and 1 <= int(key) <= 11:
        return key
    raise UnknownColor(
        f"unknown color {value!r}. categories: {', '.join(sorted(CATEGORIES))}; "
        f"colors: {', '.join(COLORS)}"
    )


def category_of(color_id):
    """colorId -> the category name, or None when it maps to no category."""
    if not color_id:
        return None
    names = _BY_ID.get(str(color_id))
    return names[0] if names else None


def infer(spec):
    """Guess a category for a spec entry that does not state one.

    A fallback, not the design. Inference reads words; it cannot know that a
    course hackathon is teaching load rather than coursework. Spec entries
    should say ``"color": "teaching"`` and only fall through to here when
    nobody has decided yet.
    """
    if spec.get("color"):
        return resolve(spec["color"])
    text = f"{spec.get('title', '')} {spec.get('notes', '')}".lower()
    if "student deadline" in text or spec.get("audience") == "students":
        return CATEGORIES["student"]
    if "exam" in text or "midterm" in text or "final" in text:
        return CATEGORIES["exam"]
    if "lecture" in text or "lab" in text or "discussion" in text:
        return CATEGORIES["lecture"]
    return CATEGORIES["deadline"]


def table():
    """Rows of (id, google_name, categories) sorted by id, for display."""
    rows = []
    for name, cid in sorted(COLORS.items(), key=lambda kv: int(kv[1])):
        rows.append((cid, name, "/".join(_BY_ID.get(cid, []))))
    return rows
