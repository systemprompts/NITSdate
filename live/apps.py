from django.apps import AppConfig


class LiveConfig(AppConfig):
    name = "live"
    verbose_name = "Live updates"

    def ready(self):
        from . import signals  # noqa: F401  (connects the receivers)
