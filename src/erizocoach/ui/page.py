"""Páginas autocontenidas para las ventanas de escritorio (WebView2).

Los estilos y el código van incrustados y marcados con un *nonce* aleatorio en cada
apertura, con una política de seguridad estricta: solo su propio código, imágenes
incrustadas (``data:``) y ninguna conexión de red. Así no hace falta servidor ni puerto.
"""

from __future__ import annotations

import base64
import secrets
from importlib.resources import files


def content_security_policy(nonce: str) -> str:
    return (
        f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        "img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"
    )


def inline_page(
    page: str,
    *,
    css: str,
    script: str,
    images: tuple[str, ...] = (),
    replacements: dict[str, str] | None = None,
    nonce: str | None = None,
) -> str:
    """``page`` de ``ui/web/static`` con su CSS y su JS dentro y las imágenes como data:."""
    static = files("erizocoach.ui.web").joinpath("static")
    html = static.joinpath(page).read_text(encoding="utf-8")
    style = static.joinpath(css).read_text(encoding="utf-8")
    for image in images:  # la CSP solo deja imágenes data:
        data = base64.b64encode(static.joinpath(image).read_bytes()).decode("ascii")
        style = style.replace(f"/static/{image}", f"data:image/png;base64,{data}")
    code = static.joinpath(script).read_text(encoding="utf-8")
    nonce = nonce or secrets.token_urlsafe(18)
    changes = {
        **(replacements or {}),
        '<meta charset="utf-8">': (
            '<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" '
            f'content="{content_security_policy(nonce)}">'
        ),
        f'<link rel="stylesheet" href="/static/{css}">': f'<style nonce="{nonce}">{style}</style>',
        f'<script src="/static/{script}"></script>': f'<script nonce="{nonce}">{code}</script>',
    }
    for old, new in changes.items():
        if old not in html:
            raise RuntimeError(f"{page} ha cambiado: falta {old!r}")
        html = html.replace(old, new, 1)
    return html
