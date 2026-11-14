"""Configuración de mensajes: consola (para ti) y archivo (para diagnosticar fallos).

Seguridad: la contraseña temporal del cliente nunca se escribe. ``REDACTOR`` la
sustituye por ``***`` en cualquier mensaje, tanto en consola como en el archivo.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import ClassVar

LOG_FILE_NAME = "lolcoach.log"
LOG_FILE_MAX_BYTES = 1_000_000
LOG_FILE_BACKUPS = 3


class RedactingFilter(logging.Filter):
    """Oculta secretos (contraseñas del cliente) en todos los mensajes."""

    def __init__(self) -> None:
        super().__init__()
        self._secrets: set[str] = set()

    def add_secret(self, *secrets: str) -> None:
        self._secrets.update(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        message = record.getMessage()
        redacted = self._redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        # También el texto del error (Traceback), que se escribe aparte del mensaje
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self._redact(record.stack_info)
        return True

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


REDACTOR = RedactingFilter()


class ConsoleFormatter(logging.Formatter):
    _MARKS: ClassVar[dict[int, str]] = {
        logging.WARNING: "AVISO: ",
        logging.ERROR: "ERROR: ",
        logging.CRITICAL: "ERROR: ",
    }

    def __init__(self, *, debug: bool) -> None:
        super().__init__()
        self._debug = debug

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if self._debug and not record.name.startswith("lolcoach.ui"):
            message = f"{record.name}: {message}"
        timestamp = self.formatTime(record, "%H:%M:%S")
        line = f"[{timestamp}] {self._MARKS.get(record.levelno, '')}{message}"
        if record.exc_info and self._debug:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(log_dir: Path, *, debug: bool = False) -> None:
    """Consola: mensajes para el usuario. Archivo: todo el detalle técnico (rotativo, 4 MB máx.)."""
    has_console = sys.stdout is not None  # el .exe de escritorio no tiene consola
    if has_console and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = logging.getLogger()
    root.setLevel(logging.WARNING)  # librerías externas: solo avisos y errores
    for handler in list(root.handlers):
        root.removeHandler(handler)
    logging.getLogger("lolcoach").setLevel(logging.DEBUG)

    if has_console:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.DEBUG if debug else logging.INFO)
        console.setFormatter(ConsoleFormatter(debug=debug))
        console.addFilter(REDACTOR)
        root.addHandler(console)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / LOG_FILE_NAME,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUPS,
            encoding="utf-8",
        )
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "No se puede escribir el registro en %s: %s", log_dir, exc
        )
        return
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    file_handler.addFilter(REDACTOR)
    root.addHandler(file_handler)
