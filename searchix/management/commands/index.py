import logging
from django.core.management.base import BaseCommand, CommandError
from searchix.index import email
from searchix import setup_logging
import os
import sys

setup_logging()
class Command(BaseCommand):
    help = "Index emails"

    def add_arguments(self, parser):
        parser.add_argument('path', type=str)
        parser.add_argument('--stop', action='store_true')
        parser.add_argument('--pdb', action='store_true')
        parser.add_argument('--stdin', action='store_true')

    def handle(self, *args, **options):
        setup_logging()
        email.logger.addHandler(logging.StreamHandler())

        pdb = options.get('pdb', False)
        stop_on_error = options.get('stop', False)
        if options.get('stdin', False):
            if email.visit_email(sys.stdin.buffer, options['path']):
                print('Created new entry')
            else:
                print('Entry already indexed')
        else:
            created, existing, failed = email.visit_folder(os.path.realpath(options['path']), stop=stop_on_error, pdb=pdb)
            print(f'Created: {created}, existing; {existing}, failed: {failed}')
