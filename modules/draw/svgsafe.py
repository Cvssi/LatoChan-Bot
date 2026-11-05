"""Turn model output into a safe, renderable SVG, or refuse.

Allowlist: only plain drawing elements survive. No scripts, styles sheets, images, <use>, links, foreignObject,
animation, external url(...) references or XML entities, so rendering can never fetch a file/URL or run code.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

MAX_CHARS = 40_000
MAX_ELEMENTS = 800
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ALLOWED = {"svg", "g", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "tspan", "defs",
           "lineargradient", "radialgradient", "stop", "title", "desc", "clippath"}
BAD_VALUE = re.compile(r"url\(\s*['\"]?\s*(?!#)|@import|expression\s*\(|javascript:", re.I)


class Unsafe(ValueError):
    pass


def extract(text: str) -> str:
    m = re.search(r"<svg\b.*?</svg\s*>", text, re.S | re.I)
    if m:
        return m.group(0)
    start = text.lower().find("<svg")
    if start < 0:
        raise Unsafe("no <svg> in the reply")
    # cut off mid-drawing (ran out of tokens): keep the complete elements, close what's still open
    body = text[start:]
    cut = max(body.rfind("/>"), body.rfind("</"))
    if cut < 0:
        raise Unsafe("svg cut off before anything was drawn")
    body = body[:body.index(">", cut) + 1]
    opened = len(re.findall(r"<g[\s>]", body)) - len(re.findall(r"</g\s*>", body))
    return body + "</g>" * max(0, opened) + "</svg>"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def clean(text: str, size: int = 512) -> bytes:
    svg = extract(text)
    if len(svg) > MAX_CHARS:
        raise Unsafe("svg too big")
    if re.search(r"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet", svg, re.I):
        raise Unsafe("doctype/entities not allowed")
    if "xmlns=" not in svg[:300]:
        svg = svg.replace("<svg", f'<svg xmlns="{SVG_NS}"', 1)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        raise Unsafe(f"broken svg: {e}") from e
    if _local(root.tag) != "svg":
        raise Unsafe("root is not <svg>")
    count = 0

    def scrub(el):
        nonlocal count
        for child in list(el):
            if _local(child.tag) not in ALLOWED:
                el.remove(child)
                continue
            count += 1
            if count > MAX_ELEMENTS:
                raise Unsafe("too many elements")
            scrub(child)
        for k in list(el.attrib):
            name, v = _local(k), el.attrib[k]
            if name.startswith("on") or (name == "href" and not v.startswith("#")) or BAD_VALUE.search(v):
                del el.attrib[k]

    scrub(root)
    for k in list(root.attrib):
        if _local(k).startswith("on"):
            del root.attrib[k]
    if "viewBox" not in root.attrib:
        w, h = root.attrib.get("width", "256"), root.attrib.get("height", "256")
        try:
            root.set("viewBox", f"0 0 {float(re.sub('[^0-9.]', '', w) or 256):g} {float(re.sub('[^0-9.]', '', h) or 256):g}")
        except ValueError:
            root.set("viewBox", "0 0 256 256")
    root.set("width", str(size))
    root.set("height", str(size))
    return ET.tostring(root, encoding="utf-8")


def render_png(svg: bytes, size: int = 512) -> bytes:
    """SVG -> PNG on a white background (so black lines show up in Discord's dark mode)."""
    import io

    import cairosvg
    from PIL import Image
    png = cairosvg.svg2png(bytestring=svg, output_width=size, output_height=size, unsafe=False)
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    out = io.BytesIO()
    bg.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()
