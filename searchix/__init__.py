import logging
from . import settings
TIME_FORMAT = '%Y-%m-%d %H:%M:%S'

def setup_logging():

    handlers = [logging.StreamHandler()]
    logging.basicConfig(format=settings.LOG_FORMAT,
                        datefmt = TIME_FORMAT,
                        handlers = handlers,
                        level = logging.DEBUG)

