GROUP = {"id": "recall", "label": "Long-term memory", "help": "She looks back over older conversations and daily summaries (memory v2)."}
SETTINGS = [
    {"key": "mod_recall", "group": "modules", "label": "Long-term memory", "kind": "bool", "default": False,
     "help": "Index her main channel and recall older conversations + nightly summaries when they're relevant."},
    {"key": "recall_budget", "label": "Prompt budget (characters)", "kind": "int", "default": 1500, "min": 300, "max": 4000, "step": 100,
     "help": "Most recalled text added to one message (~4 characters per token)."},
    {"key": "recall_min_words", "label": "Look back only for messages with at least (words)", "kind": "int", "default": 3,
     "min": 1, "max": 20, "step": 1, "help": "Skips 'lol', 'ok' and the like."},
    {"key": "recall_max_distance", "label": "Match strictness (cosine distance, %)", "kind": "int", "default": 40,
     "min": 10, "max": 90, "step": 5, "help": "Lower = only very close matches. 40 = 0.40 (tuned 4 Nov: real questions matched 0.26-0.42, unrelated chat 0.43+)."},
    {"key": "recall_skip_recent_min", "label": "Ignore the last (min)", "kind": "int", "default": 90, "min": 0, "max": 1440, "step": 15,
     "help": "Recent chat is already in her short-term history, so don't recall it twice."},
    {"key": "recall_episodes", "label": "Nightly summaries", "kind": "bool", "default": True,
     "help": "Each night she writes a few lines about the day in each main channel; kept forever."},
    {"key": "recall_episode_hour", "label": "Write summaries at (server hour)", "kind": "int", "default": 4, "min": 0, "max": 9, "step": 1,
     "help": "Pick a quiet hour; it uses the chat model for a minute."},
]
