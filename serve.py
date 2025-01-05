import os
from waitress import serve
from django.core.wsgi import get_wsgi_application
from searchix import settings
from dj_static import Cling

if __name__ == '__main__':
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "searchix.settings")
    application = Cling(get_wsgi_application())
    serve(application, port=settings.LISTEN_PORT, host=settings.LISTEN_ADDRESS, _quiet=True)

