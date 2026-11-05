"""Sass and swearing dials: a gentle push added to each message (the persona file itself never changes)."""
from core.module import Module as Base


class Module(Base):
    HELP = {}
    ORDER = 40

    async def context(self, turn) -> str:
        return mood_note(self.lato.S)


def mood_note(S) -> str:
    bits, sass, sw = [], S("sass"), S("swearing")
    if sass <= 2:
        bits.append("be noticeably softer and sweeter than usual, hardly any teasing.")
    elif sass <= 4:
        bits.append("tone the sass down a little.")
    elif sass >= 9:
        bits.append("be extra sassy and teasing.")
    elif sass >= 7:
        bits.append("turn the sass up a notch.")
    if sw == "off":
        bits.append("no swearing at all.")
    elif sw == "more":
        bits.append("swearing is fine now and then when it fits, keep it casual.")
    return ("(mood dial: " + " ".join(bits) + ")") if bits else ""
