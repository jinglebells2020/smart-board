"""The parts tray (free build): which parts the student says they used, and
what the circuit should do. Plain Python checks it; the AI reads it in
phase 2 (it helps tell apart circuits the board reads the same way)."""

TRAY_PARTS = ["wire", "100 ohm resistor", "220 ohm resistor", "330 ohm resistor",
              "1k ohm resistor", "10k ohm resistor", "red LED", "yellow LED", "green LED",
              "blue LED", "push button"]
MAX_COUNT = 20
MAX_OTHER = 80
MAX_GOAL = 200


def clean_tray(parts, goal=""):
    """{"parts": [{"name", "count"}...], "other": text, "goal": text}, or
    ValueError. parts: [{"name": "220 ohm resistor", "count": 2},
    {"name": "other", "text": "a buzzer"}, ...], or plain names
    ["push button", "330 ohm resistor", "green LED"] (one each; a name
    that is not a tray part counts as "other"). Both kinds can be mixed."""
    if not isinstance(parts, list):
        raise ValueError("parts must be a list")
    counts, other = {}, []
    for p in parts:
        if isinstance(p, str):
            text = " ".join(p.split())
            match = next((n for n in TRAY_PARTS if n.lower() == text.lower()), None)
            p = {"name": match, "count": 1} if match else {"name": "other", "text": text}
        if not isinstance(p, dict):
            raise ValueError("each part must be a name or an object")
        name = str(p.get("name", "")).strip()
        if name == "other":
            text = " ".join(str(p.get("text", "")).split())[:MAX_OTHER]
            if text:
                other.append(text)
            continue
        if name not in TRAY_PARTS:
            raise ValueError(f"unknown part {name!r}; use one of {', '.join(TRAY_PARTS)} or other")
        try:
            count = int(p.get("count", 1))
        except (TypeError, ValueError):
            raise ValueError(f"count for {name} must be a number") from None
        if not 0 <= count <= MAX_COUNT:
            raise ValueError(f"count for {name} must be 0 to {MAX_COUNT}")
        if count:
            counts[name] = counts.get(name, 0) + count
    goal = " ".join(str(goal or "").split())[:MAX_GOAL]
    return {"parts": [{"name": n, "count": counts[n]} for n in TRAY_PARTS if n in counts],
            "other": "; ".join(other)[:MAX_OTHER], "goal": goal}
