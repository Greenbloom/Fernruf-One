"""The blur region matches the rounded window and leaves the square corners out."""

from __future__ import annotations

from fernruf.desktop.shape import rounded_blur_rects


def _inside(x: int, y: int, width: int, height: int, radius: int) -> bool:
    px = x + 0.5
    py = y + 0.5
    if px < 0 or py < 0 or px >= width or py >= height:
        return False
    if radius <= 0:
        return True
    if px < radius and py < radius:
        return (px - radius) ** 2 + (py - radius) ** 2 <= radius * radius
    if px > width - radius and py < radius:
        return (px - (width - radius)) ** 2 + (py - radius) ** 2 <= radius * radius
    if px < radius and py > height - radius:
        return (px - radius) ** 2 + (py - (height - radius)) ** 2 <= radius * radius
    if px > width - radius and py > height - radius:
        return (px - (width - radius)) ** 2 + (py - (height - radius)) ** 2 <= radius * radius
    return True


def _covers(rects: list[tuple[int, int, int, int]], x: int, y: int) -> bool:
    return any(rx <= x < rx + rw and ry <= y < ry + rh for rx, ry, rw, rh in rects)


def test_blur_region_matches_the_rounded_rectangle() -> None:
    width, height, radius = 100, 80, 24
    rects = rounded_blur_rects(width, height, radius)
    assert rects
    assert not _covers(rects, 0, 0)
    assert not _covers(rects, width - 1, 0)
    assert not _covers(rects, 0, height - 1)
    assert not _covers(rects, width - 1, height - 1)
    assert _covers(rects, width // 2, height // 2)
    for y in range(height):
        for x in range(width):
            assert _covers(rects, x, y) is _inside(x, y, width, height, radius)


def test_blur_region_is_empty_until_the_window_has_a_size() -> None:
    assert rounded_blur_rects(0, 720, 24) == []
    assert rounded_blur_rects(1080, 720, 0) == [(0, 0, 1080, 720)]
