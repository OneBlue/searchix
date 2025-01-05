from datetime import datetime
from django.db.models import *
from django.contrib.postgres.search import SearchVectorField, SearchVector
from django.contrib.postgres.indexes import GinIndex
from django.db import transaction
from enum import Enum


class IndexEntry(Model):
    entry_type = None

    class ClassType(IntegerChoices):
        Unknown = 0, 'Unknown'
        Email = 1, 'Email'
        EmailAddress = 2, 'EmailAddress'
        EmailAttachment = 3, 'EmailAttachment'
        EmailHeader = 4, 'EmailHeader'

    type = PositiveIntegerField(null=False, choices=ClassType, editable=False)
    created_timestamp = DateTimeField(auto_now_add=True, blank=True, editable=False)
    indexing_log = TextField(null=True, blank=True, editable=False)

    def add_indexing_note(self, note: str):
        self.indexing_log = note if not self.indexing_log else self.indexing_log + '\n' + note

    def save(self, *args, **kwargs):
        if self.type is None:
            self.type = self.entry_type

        return super().save(*args, **kwargs)


class EmailAddress(IndexEntry):
    entry_type = IndexEntry.ClassType.EmailAddress

    address = EmailField(null=False, unique=True)
    display_names = TextField(null=True, blank=True) # Comma separated list for simlicity

    def names(self) -> list:
        if self.display_names is None:
            return []

        return self.display_names.split(',')

    def to_string(self) -> str:
        if self.display_names:
            return f'{self.names()[0]} <{self.address}>'
        else:
            return self.address

    def admin_link(self) -> str:
        return f'/searchix/emailaddress/{self.id}/change'


class Email(IndexEntry):
    entry_type = IndexEntry.ClassType.Email

    subject = TextField(null=True, blank=True, editable=False)
    message_id = TextField(null=False, blank=False, editable=False, unique=True)
    in_reply_to = TextField(null=True, blank=True, editable=False)
    date = DateField(null=True, blank=True, editable=False)
    author = ForeignKey(EmailAddress, on_delete=CASCADE, null=True, blank=True, editable=False)
    to = ManyToManyField(EmailAddress, related_name='to', editable=False)
    cc = ManyToManyField(EmailAddress, related_name='cc', editable=False)
    content_text = TextField(null=True, blank=True, editable=False)
    content_html = TextField(null=True, blank=True, editable=False)
    original_path = TextField(editable=False, unique=True)

    search = GeneratedField(db_persist=True,
                            expression=SearchVector('content_text', 'content_html', 'subject',  config='english'),
                            output_field=SearchVectorField())
    class Meta:
        indexes = [
                    GinIndex(fields=["search"]),
                    GinIndex(fields=['subject'], name='subject_trigram_index', opclasses=['gin_trgm_ops']),
                    GinIndex(fields=['content_text'], name='content_text_trigram_index', opclasses=['gin_trgm_ops']),
                    GinIndex(fields=['content_html'], name='context_html_trigram_index', opclasses=['gin_trgm_ops'])
                  ]

    def admin_link(self) -> str:
        return f'/searchix/email/{self.id}/change'

class EmailHeader(IndexEntry):
    entry_type = IndexEntry.ClassType.EmailHeader

    source_email = ForeignKey(Email, on_delete=CASCADE)
    name = TextField(blank=True)
    value = TextField(null=True, blank=True)


class EmailAttachment(IndexEntry):
    entry_type = IndexEntry.ClassType.EmailAttachment

    source_email = ForeignKey(Email, on_delete=CASCADE)
    file_name = TextField(null=True, blank=True)
    content_type = TextField(null=True, blank=True)
    content = BinaryField(null=True, blank=True)



