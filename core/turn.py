"""One reply in progress: everything the pipeline and modules know about it."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Turn:
    msgs: list                    # the burst being answered (last one is replied to)
    reason: str                   # main | pinged | name | woke | awake | sleep_cmd | dm
    is_owner: bool
    stranger: bool
    known: bool                   # owner or friend
    trusted: bool = False         # owner, or a friend marked trusted (see core/access.py)
    mode: str = "reply"           # reply | ignore | react | gif  (core/mix.py, for chatter not aimed at her)
    user_line: str = ""           # what goes into history for their side
    llm_line: str = ""            # what the model sees (user_line + system notes)
    joined: str = ""              # their text, all messages joined (for memory search / intent checks)
    images: list = field(default_factory=list)
    raw: str = ""                 # model output, untouched
    calls: list = field(default_factory=list)    # TagCalls parsed from the reply
    dropped: list = field(default_factory=list)  # unknown tags that were removed
    text: str = ""                # reply text being built
    has_output: bool = False      # something besides text will happen (reaction, gif...): [[SKIP]] doesn't apply
    blank_text: bool = False      # send no words (gif mode)
    done: list = field(default_factory=list)     # results of actions, e.g. "reacted 😂", "mute failed"
    history_text: str | None = None  # what she "said" for history (defaults to the sent text)
    data: dict = field(default_factory=dict)     # per-module scratch space: turn.data["gifs"] = {...}

    @property
    def msg(self):
        return self.msgs[-1]

    @property
    def cid(self) -> int:
        return self.msg.channel.id

    def tags(self, *names: str) -> list:
        want = {n.upper() for n in names}
        return [c for c in self.calls if c.name in want]
