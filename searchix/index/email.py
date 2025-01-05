from searchix.models import *
from searchix import settings

import email
import traceback
import os
import dateutil
from email.utils import parseaddr, getaddresses, parsedate_to_datetime
from django.db import transaction

import email.header
import logging
import re

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
        logger.debug(f'Added name: "{name}" to email address: {entry}')
        entry.display_names = ','.join(entry.names() + [name])
        entry.save()

    return entry

def get_or_create_addresses(value: str) -> list:
    if ',' in value:
        return [get_or_create_address_impl(name, address) for name, address in getaddresses(value)]
    else:
        return [get_or_create_address(value)]

def utf8_decode(value: bytes) -> str:
    # postgres doesn't accept null bytes in strings
    return value.decode('utf8', errors='replace').replace("\x00", "\uFFFD")

def decode_header(header: str, entry: Email, filter: str = None) -> str:
    if header is None:
        return None

    def decode_value(value) -> str:
        if isinstance(value, bytes):
            # remove any BOM header found
            return utf8_decode(value)
        else:
            return value
    try:
        decoded = [(decode_value(value), encoding) for value, encoding in email.header.decode_header(header)]

        if filter is not None:
            # prefer utf-8 values matching the filter
            result = next((value for value, encoding in decoded if encoding is not None and encoding.casefold() == 'utf-8' and filter in value), None)
            if result is not None:
                return result

            # Then look for any value matching the filter
            result = next((value for value, encoding in decoded if filter in value), None)
            if result is not None:
                return result

        # Prefer utf8-values
        result = next((value for value, encoding in decoded if encoding is not None and encoding.casefold() == 'utf-8'), None)
        if result is not None:
            return result

        # Otherwise look for any non-empty value, defaulting on the first one
        return next((value for value, encoding in decoded if value), None) or decoded[0][0]
    except:
        message = f'Failed to parse header "{header}" from email {entry.message_id}: {traceback.format_exc()}'
        logger.warning(message)
        entry.add_indexing_note(message)

        return f'<decode-error>'

def decode_date(value: str, entry: Email) -> datetime:
    date = decode_header(value, entry)
    if date is None:
        message = f'Missing date'
        logger.warning(message)
        entry.add_indexing_note(message)

        return None

    try:
        return  parsedate_to_datetime(date)
    except ValueError:
        message = f'Non standard "{date}", {traceback.format_exc()}'
        logger.warning(message)
        entry.add_indexing_note(message)

        try:
            return dateutil.parser.parse(date)
        except:
            message = f'Failed to parse "{date}", {traceback.format_exc()}'
            logger.warning(message)
            entry.add_indexing_note(message)

            return None

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
    new_entry.subject = decode_header(content.get('Subject'), new_entry)
    new_entry.in_reply_to = decode_header(content.get('In-Reply-To'), new_entry)
    author = decode_header(content.get('From'), new_entry)
    new_entry.author = get_or_create_address(author) if author and author != '<decode-error>' else None
    new_entry.date = decode_date(content.get('Date'), new_entry)

    new_entry.save()

    if content.is_multipart():
        for entry in content.walk():
            type = entry.get_content_type()
            disposition = entry.get_content_disposition()

            if disposition is not None and 'attachment' in disposition:
                attachment = EmailAttachment(source_email=new_entry,
                                             file_name = entry.get_filename(),
                                             content_type = type,
                                             content = entry.get_payload(decode=True))
                attachment.save()
                logger.debug(f'Created attachment {attachment} for email {new_entry}. Filename = {attachment.file_name}, ContentType = {attachment.content_type}')
                continue

            if type == 'text/plain':
                new_entry.content_text = utf8_decode(entry.get_payload(decode=True))[:settings.MAX_EMAIL_CONTENT_SIZE]
            elif type == 'text/html':
                new_entry.content_html= utf8_decode(entry.get_payload(decode=True))[:settings.MAX_EMAIL_CONTENT_SIZE]
            elif type == 'text/calendar':
                pass # TODO
            elif type != 'multipart/alternative' and type != 'multipart/mixed' and type != 'multipart/signed'  and disposition != 'inline':
                new_entry.add_indexing_note(f'Unknown part content type while reading {path}. Content-Type={type}, disposition={disposition}')
                logger.warning(f'Unknown part content type while reading {path}. Content-Type={type}, disposition={disposition}')
    else:
        new_entry.content_text = utf8_decode(content.get_payload(decode=True))[:settings.MAX_EMAIL_CONTENT_SIZE]

    new_entry.save()

    for e in get_or_create_addresses(decode_header(content.get('To', ''), new_entry)):
        new_entry.to.add(e)

    for e in get_or_create_addresses(decode_header(content.get('CC', ''), new_entry)):
        new_entry.cc.add(e)

    created_headers = 0
    for header, content in content.items():
        if header.lower() in ['date', 'subject', 'in-reply-to', 'from', 'to', 'cc', 'message-id']:
            continue

        content = decode_header(content, new_entry)

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
