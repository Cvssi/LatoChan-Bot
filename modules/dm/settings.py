GROUP = {"id": "dm", "label": "DMs", "help": "Direct messages: she can message friends for you, and answer friends who DM her."}
SETTINGS = [
    {"key": "mod_dm", "group": "modules", "label": "DMs", "kind": "bool", "default": False,
     "help": "She can send DMs when asked and reply to DMs from friends."},
    {"key": "dm_who", "label": "Who can ask her to DM someone", "kind": "choice", "default": "owner",
     "choices": ["owner", "trusted", "friends"],
     "help": "owner = only you; trusted = you + trusted friends; friends = anyone on the friends list."},
    {"key": "dm_reply", "label": "Answers DMs from friends", "kind": "bool", "default": True,
     "help": "Off = she only sends DMs when asked and ignores DMs sent to her. Strangers' DMs are always ignored."},
    {"key": "dm_daily_cap", "label": "DMs per person per day", "kind": "int", "default": 5, "min": 1, "max": 50, "step": 1,
     "help": "Most DMs she'll send to one person in a day (server-local day), so nobody gets spammed."},
]
