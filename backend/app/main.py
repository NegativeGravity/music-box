"""Django WSGI factory with isolated repositories for integration tests."""
import os
from types import ModuleType, SimpleNamespace
import uuid
import django
from django.core.handlers.wsgi import WSGIHandler
from .service import Service

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

def create_app(data_dir=None, start_worker=False):
    django.setup()
    from django.conf import settings
    from .views import create_routes
    service = Service(data_dir or settings.DATA_DIR, os.getenv('DATABASE_URL') if data_dir is None else None)
    urlconf = ModuleType('musicbox_urls_' + uuid.uuid4().hex)
    urlconf.urlpatterns = create_routes(service)
    class MusicboxHandler(WSGIHandler):
        def get_response(self, request):
            request.urlconf = urlconf
            return super().get_response(request)
    application = MusicboxHandler()
    application.state = SimpleNamespace(service=service)
    return application
