"""Bus de eventos interno.

Los módulos no se llaman entre sí directamente: publican eventos y otros se
suscriben. Así, añadir una funcionalidad nueva (runas, builds, ventana en el
segundo monitor...) es suscribirse a los eventos que ya existen, sin tocar el
resto del programa.
"""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[[Any], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)

    def subscribe[E](
        self, event_type: type[E], handler: Callable[[E], Awaitable[None] | None]
    ) -> Callable[[], None]:
        """Registra ``handler`` para ``event_type``. Devuelve una función para darse de baja."""
        self._handlers[event_type].append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def publish(self, event: object) -> None:
        """Entrega el evento a sus suscriptores, en orden de suscripción.

        Un error en un suscriptor se registra y no impide que el resto reciba el evento.
        """
        for handler in list(self._handlers.get(type(event), ())):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception(
                    "Error al procesar el evento %s en %s", type(event).__name__, _name(handler)
                )


def _name(handler: Handler) -> str:
    return getattr(handler, "__qualname__", repr(handler))
