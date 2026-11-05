GROUP = {"id": "voice", "label": "Voice notes", "help": "She listens to Discord voice notes and can answer with her own."}
SETTINGS = [
    {"key": "mod_voice", "group": "modules", "label": "Voice notes", "kind": "bool", "default": False,
     "help": "She understands voice notes (transcribed on the server, audio never saved) and can reply with one."},
    {"key": "voice_who", "label": "Whose voice notes she listens to", "kind": "choice", "default": "friends",
     "choices": ["owner", "trusted", "friends"],
     "help": "owner = only yours; trusted = you + trusted friends; friends = anyone on the friends list. Strangers never."},
    {"key": "voice_speak", "label": "She can send voice notes", "kind": "bool", "default": False,
     "help": "Off = she still listens to voice notes but only ever answers in text, even if asked for a voice note."},
    {"key": "voice_reply", "label": "How she answers a voice note", "kind": "choice", "default": "both",
     "choices": ["text", "both", "voice"],
     "help": "text = normal message; both = message + a voice note; voice = only a voice note (text if the voice fails)."},
    {"key": "voice_speed", "label": "Voice speed (%)", "kind": "int", "default": 105, "min": 70, "max": 150, "step": 5,
     "help": "100 = normal."},
    {"key": "voice_max_s", "label": "Longest voice note she listens to (s)", "kind": "int", "default": 120,
     "min": 10, "max": 600, "step": 10, "help": "Longer ones get a 'too long' note instead."},
]
