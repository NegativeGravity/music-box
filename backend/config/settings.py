import os
from pathlib import Path
import secrets

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv('MUSICBOX_DATA_DIR', str(BASE_DIR / '.data')))
DATA_DIR.mkdir(parents=True, exist_ok=True)
secret_file = DATA_DIR / 'django.secret'
if not os.getenv('DJANGO_SECRET_KEY') and not secret_file.exists():
    try:
        with secret_file.open('x', encoding='utf-8') as output:
            output.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY') or secret_file.read_text(encoding='utf-8')
DEBUG = os.getenv('DJANGO_DEBUG') == '1'
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost,testserver').split(',')
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
INSTALLED_APPS = []
MIDDLEWARE = ['app.http.ApiHeadersMiddleware']
DATABASES = {}  # The durable domain repository uses SQLAlchemy; see backend/README.md.
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_TZ = True
APPEND_SLASH = False
DATA_UPLOAD_MAX_MEMORY_SIZE = 101 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
SECURE_CONTENT_TYPE_NOSNIFF = True
