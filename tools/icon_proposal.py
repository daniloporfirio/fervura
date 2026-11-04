"""Generates the icon PROPOSAL in assets/proposta-icone/. Nothing here is used by the build.

Concept "fervura" (boiling): an orange-to-red flame with rising bubbles on a dark rounded
square. Sizes up to 32 px use a simplified version (bigger flame,
no bubbles). The tray variant keeps the live number (legibility at
16 px wins over artwork) on the same gradient, gray when disconnected.

  fervura.ico                  app icon (16, 24, 32, 48, 64, 128, 256 px)
  fervura-256.png              app icon, large
  bandeja-conectado.png        tray variant, connected (64 px)
  bandeja-desconectado.png     tray variant, disconnected (64 px)
  previa.png                   preview sheet: sizes on light/dark backgrounds + tray variants
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "assets" / "proposta-icone"
SS = 1024  # supersampled canvas; downscaled for every size

BG = (28, 28, 28)            # dark square behind the flame
FLAME_TOP = (255, 196, 61)   # amber
FLAME_BOTTOM = (226, 43, 43) # red
CORE = (255, 236, 170)
BUBBLE = (255, 214, 140)
BORDER = (70, 70, 70)
GRAY_TOP, GRAY_BOTTOM = (120, 120, 120), (70, 70, 70)


def _font(size: int):
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _gradient(size: int, top, bottom) -> Image.Image:
    g = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / (size - 1)
        g.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    return g.resize((size, size))


def _teardrop(cx: float, cy: float, r: float, stretch: float, m: float = 1.6) -> list[tuple[float, float]]:
    """Pointed top, round bottom; (cx, cy) is the center of the round part."""
    pts = []
    for i in range(240):
        t = 2 * math.pi * i / 240
        x = r * math.sin(t) * abs(math.sin(t / 2)) ** m
        y = -r * math.cos(t) * (stretch if math.cos(t) > 0 else 1)
        pts.append((cx + x, cy + y))
    return pts


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def app_icon(small: bool = False) -> Image.Image:
    """`small`: simplified art for 16-32 px (bigger flame, no bubbles)."""
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (SS, SS), BORDER + (255,)), (0, 0), _rounded_mask(SS, 200))
    inset = Image.new("L", (SS, SS), 0)
    b = 14 if not small else 40  # subtle border so the square stands out on dark taskbars
    ImageDraw.Draw(inset).rounded_rectangle((b, b, SS - 1 - b, SS - 1 - b), radius=200 - b, fill=255)
    img.paste(Image.new("RGBA", (SS, SS), BG + (255,)), (0, 0), inset)

    # flame: gradient through a teardrop mask, soft glow behind it
    cx, cy, r, stretch = (512, 600, 350, 1.3) if small else (512, 615, 300, 1.45)
    flame_mask = Image.new("L", (SS, SS), 0)
    ImageDraw.Draw(flame_mask).polygon(_teardrop(cx, cy, r, stretch), fill=255)
    glow = flame_mask.filter(ImageFilter.GaussianBlur(60))
    img.paste(Image.new("RGBA", (SS, SS), FLAME_BOTTOM + (110,)), (0, 0), glow)
    img.paste(_gradient(SS, FLAME_TOP, FLAME_BOTTOM), (0, 0), flame_mask)

    # bright core
    core_mask = Image.new("L", (SS, SS), 0)
    ImageDraw.Draw(core_mask).polygon(_teardrop(cx, cy + r * 0.38, r * 0.42, 1.5), fill=255)
    img.paste(Image.new("RGBA", (SS, SS), CORE + (255,)), (0, 0), core_mask.filter(ImageFilter.GaussianBlur(6)))

    if not small:  # rising bubbles (the "boiling"); noise below 48 px
        d = ImageDraw.Draw(img)
        for x, y, br in ((250, 290, 58), (790, 230, 44), (775, 430, 30)):
            d.ellipse((x - br, y - br, x + br, y + br), outline=BUBBLE + (255,), width=max(14, br // 3))
    return img


def tray_icon(text: str, connected: bool, size: int = 64) -> Image.Image:
    top, bottom = (FLAME_TOP, FLAME_BOTTOM) if connected else (GRAY_TOP, GRAY_BOTTOM)
    big = 512
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    img.paste(_gradient(big, top, bottom), (0, 0), _rounded_mask(big, 96))
    d = ImageDraw.Draw(img)
    d.text((big // 2, big // 2 + 12), text, font=_font(350), fill="white", anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


def sized(size: int) -> Image.Image:
    return app_icon(small=size <= 32).resize((size, size), Image.LANCZOS)


def preview(icon: Image.Image) -> Image.Image:
    sizes = (256, 64, 48, 32, 16)
    sheet = Image.new("RGB", (1200, 760), (240, 240, 240))
    d = ImageDraw.Draw(sheet)
    d.rectangle((0, 380, 1200, 760), fill=(28, 28, 28))
    label = _font(22)
    for row, (y, fg) in enumerate(((40, (40, 40, 40)), (420, (230, 230, 230)))):
        x = 40
        for s in sizes:
            small = sized(s)
            sheet.paste(small, (x, y + (256 - s) // 2), small)
            d.text((x, y + 270), f"{s}px", font=label, fill=fg)
            x += s + 60
        # tray variants shown at real 16/32 px and enlarged x4
        tx = 900
        for i, (txt, conn) in enumerate((("54", True), ("--", False))):
            t32 = tray_icon(txt, conn, 32)
            sheet.paste(t32, (tx, y + 20 + i * 120), t32)
            t16 = tray_icon(txt, conn, 16)
            sheet.paste(t16, (tx + 50, y + 28 + i * 120), t16)
            big = t32.resize((96, 96), Image.NEAREST)
            sheet.paste(big, (tx + 90, y + i * 120), big)
        d.text((tx, y + 250), "bandeja", font=label, fill=fg)
    return sheet


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon = app_icon()
    icon.resize((256, 256), Image.LANCZOS).save(OUT / "fervura-256.png")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    icon.resize((256, 256), Image.LANCZOS).save(
        OUT / "fervura.ico", sizes=[(s, s) for s in sizes],
        append_images=[sized(s) for s in sizes if s != 256])
    tray_icon("54", True).save(OUT / "bandeja-conectado.png")
    tray_icon("--", False).save(OUT / "bandeja-desconectado.png")
    preview(icon).save(OUT / "previa.png")
    print(f"Proposta gerada em {OUT}")


if __name__ == "__main__":
    main()
