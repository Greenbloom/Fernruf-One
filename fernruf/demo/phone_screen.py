"""Portrait phone-call frames for the demo video.

Frames are plain images. ffmpeg only stitches them to the call audio.
The contact is the fictional practice, never a real person or number.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from fernruf.demo.captions import FERNRUF_CAPTION, PRAXIS_CAPTION

WIDTH = 1080
HEIGHT = 1920
FPS = 12

CONTACT_NAME = "Praxis Beispiel"
DEMO_CALL_LABEL = "Both voices are AI · Demo call"

BG = (16, 18, 22)
AVATAR = (62, 74, 68)
AVATAR_MARK = (198, 206, 196)
NAME = (244, 244, 240)
TIMER = (196, 202, 194)
LABEL = (176, 184, 170)
FERNRUF_BUBBLE = (42, 88, 68)
PRAXIS_BUBBLE = (44, 68, 108)
BUBBLE_TEXT = (244, 244, 240)
BUBBLE_META = (186, 196, 184)
SPEAK = (232, 236, 214)

LABEL_TOP = 408
LABEL_BOTTOM = 468
CHAT_TOP = 520
CHAT_BOTTOM = 1840

_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


@dataclass(frozen=True)
class CaptionCue:
    start_ms: int
    end_ms: int
    side: str
    text: str


def format_timer(t_s: float) -> str:
    total = max(0, int(t_s))
    return f"{total // 60:02d}:{total % 60:02d}"


def cues_from_srt(text: str) -> list[CaptionCue]:
    cues: list[CaptionCue] = []
    blocks = [block.strip() for block in text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) < 2:
            continue
        stamp = lines[1] if "-->" in lines[1] else next((line for line in lines if "-->" in line), "")
        if "-->" not in stamp:
            continue
        start_s, end_s = (part.strip() for part in stamp.split("-->", 1))
        body = " ".join(line for line in lines if "-->" not in line and not line.isdigit())
        side, spoken = _split_caption(body)
        cues.append(
            CaptionCue(
                start_ms=_parse_stamp(start_s),
                end_ms=_parse_stamp(end_s),
                side=side,
                text=spoken,
            )
        )
    return cues


def active_side(cues: list[CaptionCue], t_ms: int) -> str | None:
    for cue in cues:
        if cue.start_ms <= t_ms < cue.end_ms:
            return cue.side
    return None


def render_phone_frame(t_s: float, cues: list[CaptionCue], *, frame_index: int = 0) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    name_font = _font(46, bold=True)
    timer_font = _font(34, bold=False)
    label_font = _font(26, bold=False)
    _draw_avatar(image)
    _centered(draw, CONTACT_NAME, 292, name_font, NAME)
    _centered(draw, format_timer(t_s), 352, timer_font, TIMER)
    _centered(draw, DEMO_CALL_LABEL, 428, label_font, LABEL)
    t_ms = max(0, int(t_s * 1000))
    _draw_bubbles(draw, cues, t_ms, frame_index)
    return image


def _draw_avatar(image: Image.Image) -> None:
    """Generic silhouette inside a circle. Not a photo and not a person."""
    size = 156
    tile = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(tile)
    draw.ellipse((0, 0, size - 1, size - 1), fill=AVATAR)
    draw.ellipse((54, 28, 102, 78), fill=AVATAR_MARK)
    draw.ellipse((28, 84, 128, 168), fill=AVATAR_MARK)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    image.paste(tile, ((WIDTH - size) // 2, 90), mask)


def _draw_bubbles(
    draw: ImageDraw.ImageDraw,
    cues: list[CaptionCue],
    t_ms: int,
    frame_index: int,
) -> None:
    shown = [cue for cue in cues if cue.start_ms <= t_ms and cue.text]
    if not shown:
        return
    body_font = _font(32, bold=False)
    meta_font = _font(22, bold=False)
    layouts: list[tuple[CaptionCue, int, int]] = []
    y = CHAT_BOTTOM
    for cue in reversed(shown):
        height = _bubble_height(cue.text, body_font)
        y -= height + 22
        if y < CHAT_TOP:
            break
        layouts.append((cue, y, height))
    pulse = 8 + (frame_index % 6) // 3
    for cue, top, height in reversed(layouts):
        live = cue.start_ms <= t_ms < cue.end_ms
        _draw_bubble(draw, cue, top, height, body_font, meta_font, live, pulse)


def _draw_bubble(
    draw: ImageDraw.ImageDraw,
    cue: CaptionCue,
    top: int,
    height: int,
    body_font,
    meta_font,
    speaking: bool,
    pulse: int,
) -> None:
    width = 760
    if cue.side == "fernruf":
        left = 56
        fill = FERNRUF_BUBBLE
        label = FERNRUF_CAPTION
    else:
        left = WIDTH - 56 - width
        fill = PRAXIS_BUBBLE
        label = PRAXIS_CAPTION
    box = (left, top, left + width, top + height)
    draw.rounded_rectangle(box, radius=28, fill=fill)
    draw.text((left + 28, top + 16), label, font=meta_font, fill=BUBBLE_META)
    text_top = top + 52
    for line in _wrap(cue.text, body_font, width - 56):
        draw.text((left + 28, text_top), line, font=body_font, fill=BUBBLE_TEXT)
        text_top += 42
    if speaking:
        dot = pulse
        if cue.side == "fernruf":
            cx, cy = left + 28, top + height - 18
        else:
            cx, cy = left + width - 28, top + height - 18
        draw.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=SPEAK)


def _bubble_height(text: str, font) -> int:
    lines = _wrap(text, font, 760 - 56)
    return 52 + 42 * max(1, len(lines)) + 28


def _wrap(text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if _length(font, trial) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _length(font, text: str) -> float:
    if hasattr(font, "getlength"):
        return float(font.getlength(text))
    return float(len(text) * 16)


def _centered(draw: ImageDraw.ImageDraw, text: str, y: int, font, fill: tuple[int, int, int]) -> None:
    width = _length(font, text)
    draw.text(((WIDTH - width) / 2, y), text, font=font, fill=fill)


def _font(size: int, *, bold: bool):
    path = _FONT_BOLD if bold else _FONT
    if path.is_file():
        return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


def _split_caption(body: str) -> tuple[str, str]:
    for label, side in ((FERNRUF_CAPTION, "fernruf"), (PRAXIS_CAPTION, "praxis")):
        prefix = f"{label}:"
        if body.startswith(prefix):
            return side, body[len(prefix) :].strip()
        if body.startswith(label):
            return side, body[len(label) :].lstrip(": ").strip()
    return "praxis", body.strip()


def _parse_stamp(stamp: str) -> int:
    clock = stamp.strip().replace(".", ",")
    hms, milli = clock.split(",")
    hours, minutes, seconds = (int(part) for part in hms.split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(milli)
