"""System tray icon showing the number currently on the display."""

from __future__ import annotations

from typing import Callable

import pystray
from PIL import Image, ImageDraw, ImageFont

from .settings import SOURCES
from .system import fonts_dir


def _font(size: int):
    # absolute path: with a bare name Pillow looks in the current directory first
    fonts = fonts_dir()
    for name in ("segoeuib.ttf", "arialbd.ttf") if fonts is not None else ():
        try:
            return ImageFont.truetype(str(fonts / name), size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


_FONT = _font(44)


def render(value: int | None, connected: bool) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 63, 63), radius=12,
                        fill=(200, 30, 40, 255) if connected else (90, 90, 90, 255))
    text = "--" if value is None else str(value)
    d.text((32, 33), text, font=_FONT, fill="white", anchor="mm")
    return img


class Tray:
    def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None],
                 get_source: Callable[[], str], set_source: Callable[[str], None],
                 get_update: Callable[[], str | None] = lambda: None,
                 on_update: Callable[[], None] = lambda: None):
        def source_item(key: str):
            return pystray.MenuItem(
                SOURCES[key], lambda: set_source(key),
                checked=lambda _: get_source() == key, radio=True)

        menu = pystray.Menu(
            pystray.MenuItem("Abrir", lambda: on_open(), default=True),
            # "Nova versão X.Y.Z disponível": only while there is one; opens the release page
            pystray.MenuItem(lambda _: get_update() or "", lambda: on_update(),
                             visible=lambda _: get_update() is not None),
            pystray.Menu.SEPARATOR,
            *(source_item(k) for k in SOURCES),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda: on_quit()),
        )
        self.icon = pystray.Icon("Fervura", render(None, False), "Fervura", menu)
        self._last = None

    def start(self) -> None:
        self.icon.run_detached()

    def update(self, value: int | None, connected: bool, tooltip: str) -> None:
        if (value, connected) != self._last:
            self.icon.icon = render(value, connected)
            self._last = (value, connected)
        self.icon.title = tooltip
        self.icon.update_menu()

    def stop(self) -> None:
        self.icon.stop()
