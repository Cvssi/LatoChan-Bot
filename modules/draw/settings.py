GROUP = {"id": "draw", "label": "Drawing", "help": "Doodle mode: she draws simple pictures (SVG, rendered on the CPU)."}
SETTINGS = [
    {"key": "mod_draw", "group": "modules", "label": "Drawing", "kind": "bool", "default": True,
     "help": "Ask her to draw something; she posts a doodle a minute later."},
    {"key": "draw_friends", "label": "Friends can ask for drawings", "kind": "bool", "default": True, "help": "Off = only you."},
    {"key": "draw_daily_cap", "label": "Drawings per day", "kind": "int", "default": 20, "min": 1, "max": 200, "step": 1,
     "help": "All people together. You're not limited."},
    {"key": "draw_max_tokens", "label": "Drawing detail budget", "kind": "int", "default": 1600, "min": 400, "max": 4000, "step": 100,
     "unit": "tokens", "help": "More = more detailed but slower (about 20 tokens a second on the current GPU)."},
]
