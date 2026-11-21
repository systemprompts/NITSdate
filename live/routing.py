from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.urls import path

from .consumers import LiveConsumer

websocket_urlpatterns = [path("ws/live/", LiveConsumer.as_asgi())]

# signed in with the normal session cookie; pages on other sites can't open it (Origin check)
websocket_app = AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns)))
