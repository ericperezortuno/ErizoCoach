"""Genera los iconos de Erizo Coach a partir del dibujo original (docs/img/ErizoCoach.png).

Uso:  uv run python scripts/make_icon.py
Crea:
* assets/erizocoach.ico   (16-256 px) icono del .exe, la barra de tareas y la bandeja.
* assets/erizocoach.png   (512 px) el dibujo entero, recortado y cuadrado.
* src/lolcoach/ui/web/static/logo.png        (256 px) el erizo grande del centro de la ventana.
* src/lolcoach/ui/web/static/logo-mini.png   (96 px) el de la barra superior (recorte).

En los tamaños pequeños (16-32 px: barra de tareas, bandeja y barra superior) el dibujo entero no se
distingue, así que se usa un recorte del erizo con su sombrero.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "img" / "ErizoCoach.png"
ASSETS = ROOT / "assets"
WEB_STATIC = ROOT / "src" / "lolcoach" / "ui" / "web" / "static"
LARGE_SIZES = (48, 64, 128, 256)
SMALL_SIZES = (16, 24, 32)
# Recorte del erizo (sombrero + cara) en coordenadas relativas al dibujo cuadrado.
HEAD_BOX = (0.04, 0.12, 0.56, 0.64)


def square(image: Image.Image, margin: float = 0.02) -> Image.Image:
    """Recorta lo transparente y centra el dibujo en un cuadrado con un pequeño margen."""
    image = image.crop(image.getbbox())
    side = round(max(image.size) * (1 + 2 * margin))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((side - image.width) // 2, (side - image.height) // 2))
    return canvas


def head(image: Image.Image) -> Image.Image:
    w, h = image.size
    left, top, right, bottom = HEAD_BOX
    box = (round(left * w), round(top * h), round(right * w), round(bottom * h))
    return square(image.crop(box))


def resized(image: Image.Image, size: int) -> Image.Image:
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    drawing = square(Image.open(SOURCE).convert("RGBA"))
    small = head(drawing)
    ASSETS.mkdir(exist_ok=True)
    resized(drawing, 512).save(ASSETS / "erizocoach.png", optimize=True)
    resized(drawing, 256).save(WEB_STATIC / "logo.png", optimize=True)
    resized(small, 96).save(WEB_STATIC / "logo-mini.png", optimize=True)
    frames = [resized(small, s) for s in SMALL_SIZES] + [resized(drawing, s) for s in LARGE_SIZES]
    frames[-1].save(
        ASSETS / "erizocoach.ico",
        format="ICO",
        sizes=[f.size for f in frames],
        append_images=frames[:-1],
    )
    print(f"Iconos creados en {ASSETS} y {WEB_STATIC}")


if __name__ == "__main__":
    main()
