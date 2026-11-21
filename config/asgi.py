"""ASGI entry point for the WebSocket service (deploy/nitsdate-live.service, uvicorn on :8011).

Regular pages are still served by gunicorn (config/wsgi.py); nginx sends only /ws/ here. This
process also runs the background matchmaker (live/matcher.py) while it's up."""
import asyncio
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_app = get_asgi_application()   # sets Django up before anything below imports models

from channels.routing import ProtocolTypeRouter  # noqa: E402
from django.conf import settings  # noqa: E402

from live.matcher import run_forever  # noqa: E402
from live.routing import websocket_app  # noqa: E402

router = ProtocolTypeRouter({"http": django_app, "websocket": websocket_app})


async def lifespan(scope, receive, send):
    task = None
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            if settings.LIVE_MATCHER:
                task = asyncio.ensure_future(run_forever())
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            if task:
                task.cancel()
            await send({"type": "lifespan.shutdown.complete"})
            return


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        return await lifespan(scope, receive, send)
    return await router(scope, receive, send)
