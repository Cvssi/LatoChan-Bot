GROUP = {"id": "activity", "label": "Activity watcher",
         "help": "She notices what friends play or listen to on Discord and pings them about it. Needs the Presence Intent (see MODULES.md)."}
SETTINGS = [
    {"key": "mod_activity", "group": "modules", "label": "Activity watcher", "kind": "bool", "default": False,
     "help": "She comments on games and Spotify. Needs PRESENCE_INTENT=1 and the Presence Intent switched on in the Discord portal."},
    {"key": "activity_chance", "label": "Chance she says something", "kind": "int", "default": 35, "min": 0, "max": 100, "step": 5, "unit": "%",
     "help": "Each time someone starts a new game or artist, after the cooldowns."},
    {"key": "activity_user_cooldown_min", "label": "Per-person cooldown", "kind": "int", "default": 240, "min": 10, "max": 1440, "step": 10, "unit": "min"},
    {"key": "activity_global_cooldown_min", "label": "Cooldown between any two comments", "kind": "int", "default": 30, "min": 1, "max": 600, "step": 1, "unit": "min"},
    {"key": "activity_games", "label": "Games", "kind": "bool", "default": True, "help": ""},
    {"key": "activity_music", "label": "Spotify", "kind": "bool", "default": True, "help": ""},
    {"key": "activity_ping", "label": "Ping them", "kind": "bool", "default": True, "help": "Off = she mentions their name without a ping."},
    {"key": "activity_strangers", "label": "Strangers too", "kind": "bool", "default": False, "help": "Off = only you and the friends list."},
    {"key": "activity_quiet", "label": "Respect quiet hours", "kind": "bool", "default": True, "help": "Uses the quiet hours from Sleep & wake."},
]
