"""Genera el icono de LoL Coach (diseño propio: rombo dorado sobre azul oscuro).

Uso:  uv run python scripts/make_icon.py
Crea assets/lolcoach.ico (16-256 px, para Windows) y assets/lolcoach.png (512 px).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 1024  # se dibuja grande y se reduce: bordes suaves en todos los tamaños
NAVY_TOP, NAVY_BOTTOM = (16, 34, 62), (6, 14, 30)
GOLD, GOLD_LIGHT, GOLD_DARK = (200, 170, 110), (240, 222, 170), (120, 90, 40)
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def diamond(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]


def draw_icon() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # Fondo: cuadrado redondeado con degradado vertical
    gradient = Image.new("RGBA", (SIZE, SIZE))
    pixels = gradient.load()
    for y in range(SIZE):
        t = y / (SIZE - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(NAVY_TOP, NAVY_BOTTOM, strict=True))
        for x in range(SIZE):
            pixels[x, y] = (*color, 255)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((32, 32, SIZE - 32, SIZE - 32), radius=200, fill=255)
    image.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(image)
    # Marco dorado fino del fondo
    draw.rounded_rectangle((32, 32, SIZE - 32, SIZE - 32), radius=200, outline=GOLD_DARK, width=14)

    c = SIZE / 2
    # Resplandor suave detrás del rombo
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(diamond(c, c, 330), fill=(200, 170, 110, 90))
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(40)))

    draw = ImageDraw.Draw(image)
    # El interior del rombo vuelve a azul oscuro (el resplandor solo se ve por fuera)
    draw.polygon(diamond(c, c, 330), fill=(10, 22, 42))
    # Rombo exterior: contorno dorado grueso
    draw.polygon(diamond(c, c, 330), outline=GOLD, width=58)
    # Rombo interior relleno, con un brillo en la mitad superior
    draw.polygon(diamond(c, c, 170), fill=GOLD)
    draw.polygon([(c, c - 170), (c + 170, c), (c, c), (c - 170, c)], fill=GOLD_LIGHT)
    return image


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    icon = draw_icon()
    icon.resize((512, 512), Image.Resampling.LANCZOS).save(ASSETS / "lolcoach.png")
    icon.resize((256, 256), Image.Resampling.LANCZOS).save(ASSETS / "lolcoach.ico", sizes=ICO_SIZES)
    print(f"Icono creado en {ASSETS}")


if __name__ == "__main__":
    main()
