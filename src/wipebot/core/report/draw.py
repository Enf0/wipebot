"""A scene (maps.Scene) drawn as a WebP picture, for Discord and clients without the replay viewer.

Drawn at SS times the size and scaled down, for smooth edges. Players are spec icons in a ring of their class colour,
nudged apart in a stack as the viewer does (pull_map_app.html), each named beside their marker.
"""
import functools
import io
import math
from importlib import resources

from PIL import Image, ImageDraw, ImageFont

W, H, SS = 1200, 800, 2
BG = (11, 16, 30)
INK = (236, 240, 248)
GOLD = (245, 197, 66)
RED = (255, 92, 92)
GREY = (120, 124, 134)
MIN_VIEW_YD, MARGIN_YD = 60, 12  # the view shows at least this much of the room, this far around what's in it
R = 16 * SS  # a player's marker's radius
BR = 30 * SS  # a boss's
IMAGE_FORMATS = ["PNG", "JPEG", "WEBP"]  # Warcraft Logs' maps and icons: no other decoder opens what's fetched


@functools.cache
def font(size: float, bold: bool = True) -> ImageFont.FreeTypeFont:
    name = "Inter-Bold.ttf" if bold else "Inter-Medium.ttf"
    data = resources.files("wipebot.core").joinpath("fonts", name).read_bytes()
    return ImageFont.truetype(io.BytesIO(data), round(size * SS))


def nudge(points: list[tuple[float, float]], gap: float, keep_out: list[tuple[float, float, float]]
          ) -> list[tuple[float, float]]:
    """Markers placed one by one; one that would cover another (or a `keep_out` circle, x y radius) moves out along
    a spiral (as the viewer's spread())."""
    placed = [(x, y, r) for x, y, r in keep_out]
    out = []
    for x0, y0 in points:
        x, y = x0, y0
        for step in range(60):
            if not any(math.hypot(px - x, py - y) < max(pr, gap) for px, py, pr in placed):
                break
            a, d = step * 2.4, gap * 0.35 * math.sqrt(step + 1)
            x, y = x0 + math.cos(a) * d, y0 + math.sin(a) * d
        placed.append((x, y, 0))
        out.append((x, y))
    return out


def _view(sc) -> tuple[float, float, float]:
    """The map's left and top edges in the view (world) and pixels per hundredth of a yard: what's in the scene (the
    living, the bosses, the spots and marks, the dead only when nothing else is) with a margin, in the image's shape."""
    pts = [(d.x, d.y) for d in sc.dots if not d.dead or d.focus] + [(b.x, b.y) for b in sc.bosses]
    pts += [(s.x, s.y) for s in sc.spots] + [(m.x, m.y) for m in sc.marks]
    pts = pts or [(d.x, d.y) for d in sc.dots]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    span = max(max(xs) - min(xs) + 2 * MARGIN_YD * 100, (max(ys) - min(ys) + 2 * MARGIN_YD * 100) * W / H,
               MIN_VIEW_YD * 100)
    per = W * SS / span
    return (min(xs) + max(xs)) / 2 - span / 2, (min(ys) + max(ys)) / 2 + span * H / W / 2, per


def in_view(sc, x: float, y: float) -> bool:
    x0, y1, per = _view(sc)
    return 0 <= (x - x0) * per <= W * SS and 0 <= (y1 - y) * per <= H * SS


def _icon(data: bytes | None, d: int, grey: bool) -> Image.Image | None:
    if not data:
        return None
    icon = Image.open(io.BytesIO(data), formats=IMAGE_FORMATS).convert("L" if grey else "RGB").convert("RGB")
    icon = icon.resize((d, d))
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    icon.putalpha(mask)
    return icon


def _text(draw: ImageDraw.ImageDraw, xy, text: str, f, fill, anchor: str = "lm") -> None:
    draw.text(xy, text, font=f, fill=fill, anchor=anchor, stroke_width=3 * SS, stroke_fill=(0, 0, 0))


def render(sc, map_png: bytes, icons: dict[str, bytes]) -> bytes:  # noqa: C901, PLR0915
    """The scene as a WebP image; `icons` has the image of each icon path in it."""
    w, h = W * SS, H * SS
    x0, y1, per = _view(sc)

    def px(x: float, y: float) -> tuple[float, float]:
        return (x - x0) * per, (y1 - y) * per

    # The room, cut to the view (beyond the map's image stays dark) and dimmed so the markers stand out.
    m = Image.open(io.BytesIO(map_png), formats=IMAGE_FORMATS).convert("RGB")
    min_x, max_x, min_y, max_y = sc.bounds
    kx, ky = m.width / (max_x - min_x), m.height / (max_y - min_y)
    box = ((x0 - min_x) * kx, (max_y - y1) * ky, (x0 + w / per - min_x) * kx, (max_y - y1 + h / per) * ky)
    room = m.transform((w, h), Image.Transform.EXTENT, box, Image.Resampling.BICUBIC, fillcolor=BG)
    img = Image.blend(Image.new("RGB", (w, h), BG), room, 0.75).convert("RGBA")
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))  # what's translucent: spot rings, facing cones
    od, draw = ImageDraw.Draw(overlay), ImageDraw.Draw(img)

    bosses = [px(b.x, b.y) for b in sc.bosses]
    where = dict(zip([d.name for d in sc.dots],
                     nudge([px(d.x, d.y) for d in sc.dots], 2.05 * R, [(x, y, BR + R) for x, y in bosses]),
                     strict=True))

    rr = sc.ring_yd * 100 * per
    for s in sc.spots:  # a plan's spots: a ring where its player should be, a line to where they stood
        x, y = px(s.x, s.y)
        if s.ring or s.fill:
            od.ellipse((x - rr, y - rr, x + rr, y + rr), outline=(*(RED if s.ring else s.color), 220), width=2 * SS,
                       fill=(*s.color, 60) if s.fill else None)
        if s.to and s.to in where:
            od.line((x, y, *where[s.to]), fill=(*RED, 220), width=2 * SS)
    for b, (x, y) in zip(sc.bosses, bosses, strict=True):  # which way each boss faces: a gold cone
        if b.facing is not None:
            deg, reach = -math.degrees(b.facing), BR * 3.2
            od.pieslice((x - reach, y - reach, x + reach, y + reach), deg - 45, deg + 45, fill=(*GOLD, 70))
    img.alpha_composite(overlay)
    for s in sc.spots:
        x, y = px(s.x, s.y)
        draw.ellipse((x - 4 * SS, y - 4 * SS, x + 4 * SS, y + 4 * SS), fill=s.color, outline=(10, 10, 10), width=SS)
        if s.label:
            _text(draw, (x, y + 7 * SS), s.label, font(13), s.color, "mt")

    for b, (x, y) in zip(sc.bosses, bosses, strict=True):
        if ic := _icon(icons.get(b.icon), 2 * BR, False):
            img.alpha_composite(ic, (round(x - BR), round(y - BR)))
        draw.ellipse((x - BR, y - BR, x + BR, y + BR), outline=GOLD, width=4 * SS)
    for d in sorted(sc.dots, key=lambda d: (not d.dead, d.focus)):  # the living over the dead, those asked about on top
        x, y = where[d.name]
        if d.marked:
            draw.ellipse((x - R - 5 * SS, y - R - 5 * SS, x + R + 5 * SS, y + R + 5 * SS), outline=RED, width=3 * SS)
        draw.ellipse((x - R, y - R, x + R, y + R), fill=GREY if d.dead else d.color)
        inner = R - 4 * SS
        if ic := _icon(icons.get(d.icon), round(2 * inner), d.dead):
            img.alpha_composite(ic, (round(x - inner), round(y - inner)))
        if d.dead:
            k = inner * 0.55
            draw.line((x - k, y - k, x + k, y + k), fill=RED, width=R // 4)
            draw.line((x - k, y + k, x + k, y - k), fill=RED, width=R // 4)
    for a, b in sc.arrows:  # from whose debuff to whom it hit
        if a in where and b in where:
            _arrow(draw, where[a], where[b])
    labels: list[tuple[float, float, float, float]] = []
    for mk in sc.marks:
        x, y = px(mk.x, mk.y)
        draw.ellipse((x - 6 * SS, y - 6 * SS, x + 6 * SS, y + 6 * SS), fill=mk.color, outline=(20, 20, 20), width=SS)
        box = draw.textbbox((x + 9 * SS, y), mk.label, font=font(11, bold=False), anchor="lm")
        if mk.label and not any(box[2] > b[0] and box[0] < b[2] and box[3] > b[1] and box[1] < b[3] for b in labels):
            labels.append(box)  # one that would cover another is left out
            _text(draw, (x + 9 * SS, y), mk.label, font(11, bold=False), INK)

    _names(draw, sc, where, bosses)
    _text(draw, (24 * SS, 34 * SS), sc.at, font(34), INK)
    _text(draw, (24 * SS, 66 * SS), sc.title, font(16, bold=False), (200, 206, 220))
    yd = next(n for n in (5, 10, 20, 50, 100) if n * 100 * per >= 70 * SS)  # a scale bar, bottom right
    bx1, by = w - 24 * SS, h - 26 * SS
    draw.line((bx1 - yd * 100 * per, by, bx1, by), fill=INK, width=3 * SS)
    _text(draw, (bx1 - yd * 50 * per, by - 10 * SS), f"{yd} yd", font(14, bold=False), INK, "mb")
    buf = io.BytesIO()
    img.convert("RGB").resize((W, H), Image.Resampling.LANCZOS).save(buf, "WEBP", quality=85)  # a PNG is ~1 MB
    return buf.getvalue()


def _names(draw: ImageDraw.ImageDraw, sc, where: dict[str, tuple[float, float]],
           bosses: list[tuple[float, float]]) -> None:
    """Each name beside its marker: on the first side that covers no marker or name, farther out (with a thin line
    back) when those are taken; left out when nothing is free (the text lists everyone)."""
    taken = [(x - R, y - R, x + R, y + R) for x, y in where.values()]
    taken += [(x - BR, y - BR, x + BR, y + BR) for x, y in bosses]

    def place(text: str, x: float, y: float, color, f, gap: float, far: bool = True) -> None:
        tw, th = f.getlength(text), f.size * 1.1
        for step in range(4 if far else 1):
            g = gap + step * 14 * SS
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)):
                lx = x + dx * g - (tw if dx < 0 else tw / 2 if dx == 0 else 0)
                ly = y + dy * g - (th if dy < 0 else th / 2 if dy == 0 else 0)
                b = (lx, ly, lx + tw, ly + th)
                if b[0] < 0 or b[1] < 0 or b[2] > W * SS or b[3] > H * SS or any(
                        b[2] > t[0] and b[0] < t[2] and b[3] > t[1] and b[1] < t[3] for t in taken):
                    continue
                taken.append(b)
                if step:
                    draw.line((x, y, min(max(x, b[0]), b[2]), min(max(y, b[1]), b[3])), fill=color, width=SS)
                _text(draw, (b[0], (b[1] + b[3]) / 2), text, f, color)
                return
    for b, (x, y) in zip(sc.bosses, bosses, strict=True):
        place(b.name, x, y, GOLD, font(17), BR + 6 * SS, far=False)
    for d in sorted(sc.dots, key=lambda d: (not d.focus, d.dead)):
        x, y = where[d.name]
        if 0 <= x <= W * SS and 0 <= y <= H * SS:
            place(d.name, x, y, (170, 174, 184) if d.dead else d.color, font(20 if d.focus else 17), R + 6 * SS)


def _arrow(draw: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float]) -> None:
    length = math.dist(a, b)
    if length <= 2 * R:
        return
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    end = (b[0] - ux * (R + 2 * SS), b[1] - uy * (R + 2 * SS))
    draw.line((a[0] + ux * R, a[1] + uy * R, *end), fill=GOLD, width=4 * SS)
    draw.polygon([end, (end[0] - ux * 14 * SS - uy * 7 * SS, end[1] - uy * 14 * SS + ux * 7 * SS),
                  (end[0] - ux * 14 * SS + uy * 7 * SS, end[1] - uy * 14 * SS - ux * 7 * SS)], fill=GOLD)
