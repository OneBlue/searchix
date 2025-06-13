from searchix.models import *
from searchix import settings

import email
import traceback
import os
import dateutil
from email.utils import parseaddr, getaddresses, parsedate_to_datetime
from django.db import transaction
from django.db.utils import OperationalError
from psycopg2.errors import ProgramLimitExceeded

import email.header
import logging
import re
from html2text import HTML2Text

logger = logging.Logger(__name__)


def get_or_create_address(value: str) -> EmailAddress:
    name, address = parseaddr(value)
    return get_or_create_address_impl(name, address)

def get_or_create_address_impl(name: str, address: str) -> EmailAddress:
    if name is not None and ',' in name:
        logger.warning(f'Found comma in name "{name}" for email: {address}')
        name = name.replace(',', '')

    try:
        entry = EmailAddress.objects.get(address__iexact=address.lower())
    except EmailAddress.DoesNotExist:
        entry = EmailAddress(display_names=name if name else None, address=address)
        entry.save()
        logger.debug(f'Creating new entry for email address: {address}. id={entry.id}')
        return entry

    if name and name not in entry.names():
        entry.display_names = ','.join(entry.names() + [name])

        if len(entry.display_names) > 1024:
            logger.warning(f'Dropping name "{name}" from address {address}. Maximum size reached')
            return entry
        else:
            logger.debug(f'Added name: "{name}" to email address: {entry}')

        entry.save()

    return entry

def get_or_create_addresses(value: str) -> list:
    if value is None:
        return []
    elif ',' in value:
        return [get_or_create_address(e) for e in value.split(',') if e]
    else:
        return [get_or_create_address(value)]

def utf8_decode(value: bytes) -> str:
    # postgres doesn't accept null bytes in strings
    return value.decode('utf8', errors='replace').replace("\x00", "\uFFFD")

def decode_header(header: str, entry: Email, max_size: int) -> str:
    if header is None:
        return None

    def decode_value(value) -> str:
        if isinstance(value, bytes):
            # remove any BOM header found
            return utf8_decode(value)
        else:
            return value
    try:
        decoded = [decode_value(value) for value, _ in email.header.decode_header(header)]
        result = ''.join(decoded)

        if max_size is not None and len(result) > max_size:
            entry.add_indexing_note('Header is too long ({len(result)}) truncating to {max_size}')
            result = result[:max_size]

        return result

    except:
        message = f'Failed to parse header "{header}" from email {entry.message_id}: {traceback.format_exc()}'
        logger.warning(message)
        entry.add_indexing_note(message)

        return f'<decode-error>'

def decode_date(value: str, entry: Email) -> datetime:
    date = decode_header(value, entry, max_size=None)
    if date is None:
        message = f'Missing date header'
        entry.add_indexing_note(message)
        return None

    try:
        return  parsedate_to_datetime(date)
    except ValueError:
        message = f'Non standard "{date}", {traceback.format_exc()}'
        entry.add_indexing_note(message)

        try:
            return dateutil.parser.parse(date)
        except:
            message = f'Failed to parse "{date}", {traceback.format_exc()}'
            entry.add_indexing_note(message)

            return None


def extract_text_from_html(content: str):
    convert = HTML2Text()
    convert.ignore_images = True
    convert.ignore_links = True
    convert.ignore_emphasis = True
    convert.ignore_tables = True
    convert.single_line_break = True
    convert.wrap_links = True
    convert.wrap_lists = True

    return convert.handle(re.sub('<img .*?>', 'removed-image', content)) # Remove potential images

def process_text_content(content: str):
    # Try to guess if content is html
    if any(e in content.casefold() for e in ['<html', '<head', '<meta', '<img']):

        # Note: re-encoding needed because surrogates will break the sql statement
        return extract_text_from_html(content).encode(errors='replace').decode(errors='replace')
    else:
        return content

def reduce_body_size(content: str) -> str:
    # Remove http links
    return re.sub(r'http\S+', '<removed-link>', content)

@transaction.atomic
def visit_email(fd, path: str) -> bool:
    if Email.objects.filter(original_path=path).exists():
        return False

    content = email.message_from_string(utf8_decode(fd.read()))
    message_id = content.get('Message-id')
    if not message_id:
        message_id = f'<none>:{os.path.basename(path)}'

    if Email.objects.filter(message_id=message_id).exists():
        return False

    new_entry = Email(message_id=message_id, original_path=path)
    new_entry.subject = decode_header(content.get('Subject'), new_entry, 1024)
    new_entry.in_reply_to = decode_header(content.get('In-Reply-To'), new_entry, 1024)
    author = decode_header(content.get('From'), new_entry, 1024)
    new_entry.author = get_or_create_address(author) if author and author != '<decode-error>' else None
    new_entry.date = decode_date(content.get('Date'), new_entry)

    new_entry.save()

    if content.is_multipart():
        for entry in content.walk():
            type = entry.get_content_type()
            disposition = entry.get_content_disposition()

            if disposition is not None and 'attachment' in disposition:
                attachment = EmailAttachment(source_email=new_entry,
                                             file_name = decode_header(entry.get_filename(), new_entry, max_size = 1024),
                                             content_type = type,
                                             content = entry.get_payload(decode=True))
                attachment.save()
                logger.debug(f'Created attachment {attachment} for email {new_entry}. Filename = {attachment.file_name}, ContentType = {attachment.content_type}')
                continue

            if type == 'text/plain':
                new_entry.content_text = process_text_content(utf8_decode(entry.get_payload(decode=True))[:settings.MAX_EMAIL_CONTENT_SIZE])
            elif type == 'text/html':
                new_entry.content_html= utf8_decode(entry.get_payload(decode=True))[:settings.MAX_EMAIL_CONTENT_SIZE]
            elif type == 'text/calendar':
                pass # TODO
            elif type not in ['multipart/alternative', 'multipart/mixed', 'multipart/signed', 'multipart/report', 'message/delivery-status', 'message/rfc822']  and disposition != 'inline':
                new_entry.add_indexing_note(f'Unknown part content type while reading {path}. Content-Type={type}, disposition={disposition}')
                logger.warning(f'Unknown part content type while reading {path}. Content-Type={type}, disposition={disposition}')
    else:
        if 'Content-Type' in content and 'html' in decode_header(content['Content-Type'], new_entry, 1024).casefold():
            new_entry.content_html = process_text_content(utf8_decode(content.get_payload(decode=True))[:settings.MAX_EMAIL_CONTENT_SIZE])
        else:
            new_entry.content_text = process_text_content(utf8_decode(content.get_payload(decode=True))[:settings.MAX_EMAIL_CONTENT_SIZE])

    # Generate a text content field for easier search if none was available
    if new_entry.content_text is None and new_entry.content_html:
        new_entry.content_text = extract_text_from_html(new_entry.content_html)

    def attempt_save() -> bool:
        try:
            with transaction.atomic():
                new_entry.save()
                return True

        except OperationalError as e:
            if isinstance(e.__cause__, ProgramLimitExceeded):  # Hit when the index row is too big
                return False
            else:
                raise

    if not attempt_save():
        previous_body = new_entry.content_text
        new_entry.content_text = reduce_body_size(previous_body)
        new_entry.add_indexing_note(f'Exceeded index size, reducing email content (original size: {len(previous_body)}, reduced: {len(new_entry.content_text)})')

        note = None
        while True:
            if attempt_save():
                break

            if len(new_entry.content_text) <= 100:
                raise RuntimeError('Failed to save context_text for email: {path}')

            new_entry.content_text = new_entry.content_text[:len(new_entry.content_text) - 100]
            note = f'Entry still too big. Reduced size to: {len(new_entry.content_text)}'

        if note is not None:
            new_entry.add_indexing_note(note)
            new_entry.save()


    for e in get_or_create_addresses(decode_header(content.get('To', None), new_entry, max_size=None)):
        new_entry.to.add(e)

    for e in get_or_create_addresses(decode_header(content.get('CC', None), new_entry, max_size=None)):
        new_entry.cc.add(e)

    created_headers = 0
    for header, content in content.items():
        if header.lower() in ['date', 'subject', 'in-reply-to', 'from', 'to', 'cc', 'message-id']:
            continue

        content = decode_header(content, new_entry, 1024)

        new_header = EmailHeader(source_email=new_entry, name=header, value=content)
        new_header.save()

        created_headers += 1

    logger.debug(f'Created new entry from {path}: {new_entry} ({created_headers} headers)')

    return True


def visit_folder(path: str, stop: bool, pdb: bool):
    logger.debug(f'Visting: {path}')

    created = 0
    existing = 0
    failed = 0

    for e in os.listdir(path):
        item_path = os.path.join(path, e)

        if os.path.isdir(item_path):
            dir_created, dir_existing, dir_failed = visit_folder(item_path, stop, pdb)

            created += dir_created
            existing += dir_existing
            failed += dir_failed

        elif os.path.isfile(item_path):
            try:
                with open(item_path, 'rb') as fd:
                    if visit_email(fd, item_path):
                        created += 1
                    else:
                        existing += 1
            except:
                logging.error(f'Failed to parse email: {item_path}, {traceback.format_exc()}')

                if pdb:
                    import pdb
                    pdb.post_mortem()

                failed += 1

                if stop:
                    raise

    return created, existing, failed
