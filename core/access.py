"""Who may ask her for what. Four levels, highest first:

    owner    Owner
    trusted  friends marked trusted (LatoDash Friends card, or `lato-friends trust ID`)
    friends  everyone on the friends list
    (anyone else is a stranger and never gets past chat)

A module that does things (lights, and later plugs, devices...) gives itself a "<feature>_who" setting:
    {"key": "lights_who", "kind": "choice", "choices": ["owner", "trusted", "friends"], "default": "trusted", ...}
and checks it with allows(turn, S("lights_who")). The requester's own words still have to ask for the thing
(each module's intent check), so other people's text can't steer her."""
from __future__ import annotations

WHO = ("owner", "trusted", "friends")


def level_of(is_owner: bool, trusted: bool, known: bool) -> str:
    if is_owner:
        return "owner"
    if trusted:
        return "trusted"
    return "friends" if known else "stranger"


def level(turn) -> str:
    return level_of(turn.is_owner, turn.trusted, turn.known)


def allows_level(lv: str, who: str) -> bool:
    """True if someone at level `lv` may use a feature set to `who` (unknown values fall back to owner only)."""
    if lv == "owner":
        return True
    if who == "trusted":
        return lv == "trusted"
    if who == "friends":
        return lv in ("trusted", "friends")
    return False


def allows(turn, who: str) -> bool:
    return allows_level(level(turn), who)
