from app.views import create_routes
from app.service import Service
from django.conf import settings
import os

service = Service(settings.DATA_DIR, os.getenv('DATABASE_URL'))
urlpatterns = create_routes(service)
