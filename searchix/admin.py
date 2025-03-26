import inspect
from django.contrib.admin.views.main import ChangeList as ChangeListDefault
from django.urls import reverse
from django.db.models import *
from django.contrib import admin
from django.db.models import Max, Prefetch, F, FilteredRelation, Value, Q, F, OuterRef
from django.contrib.auth.models import User
from django.db import connection
from django.utils.html import escape, format_html
from django.contrib.postgres.search import SearchVector, SearchRank, SearchQuery, TrigramSimilarity
from . import models, settings
from enum import Enum
from datetime import datetime

searchable_types = [TextField, CharField, FloatField, URLField, DateTimeField, IntegerField]

def get_search_fields(obj) -> list:
    return  [e.name for e in obj._meta.get_fields() if type(e) in searchable_types]

def get_id_fields(obj) -> list:
    return [e.name for e in obj._meta.get_fields() if type(e) in [models.ManyToManyField, models.ForeignKey]]

for name, obj in {name: obj for (name, obj) in inspect.getmembers(models)}.items():
    if inspect.isclass(obj) and not obj is Model and issubclass(obj, Model) and name not in ['Email', 'IndexEntry', 'EmailAttachment']:
        class AdminClass(admin.ModelAdmin):
            raw_id_fields = get_id_fields(obj)
            search_fields = get_search_fields(obj)

        admin.site.register(obj, AdminClass)

def highlight_search_term(content: str, search_term: str, max_size: int, link: str = None):
    match_position = content.casefold().find(search_term.casefold())
    if match_position < 0 :
        prefix = content[:max_size] # No match
        suffix = ''
    else:

        # If we have a match, verify that it's not too long to include it
        context_size = int((max_size - len(search_term)) / 2)

        if context_size > 0:
            prefix = content[max(0, match_position - context_size):max(0, match_position)]
            suffix = content[match_position + len(search_term):min(len(content), match_position + context_size + len(search_term))]

        else:
            prefix = ''
            suffix = ''

    if link:
        return format_html('<a href="' + link + '">{}<b>{}</b>{}</a>', prefix, content[match_position:match_position + len(search_term)], suffix)
    else:
        return format_html('{}<b>{}</b>{}', prefix, content[match_position:match_position + len(search_term)], suffix)

def make_multiline_html(text: str):
    return format_html(escape(text).replace('\n', '<br/>'))

def make_link(entry, text: str):
    if entry is None:
        return '<null>'

    return format_html('<a href="' + entry.admin_link() + '">{}</a>', text)

def make_list_link(entries, text_method) -> str:
    return format_html(', '.join(f'<a href="{e.admin_link()}">{escape(text_method(e))} </a>' for e in entries))


class EmailAttachment(admin.ModelAdmin):
    raw_id_fields = get_id_fields(models.EmailAttachment)

    readonly_fields = ('source_email', 'file_name', 'content_type', 'download')

    def download(self, entry):
        return format_html(f'<a href="{entry.download_link()}">{escape(entry.file_name or "unnamed")} </a>')

class Email(admin.ModelAdmin):
    class FuzzyFilter(admin.SimpleListFilter):
        title = 'Fuzzy search'
        parameter_name = 'fuzzy'

        def lookups(self, request, model_admin):
            return [('disable', 'enable')]

        def queryset(self, request, queryset):
            value = self.value()

            if value is None:
                return queryset
            else:
                request.environ['fuzzy_search' ] = True
                return queryset

    class AddressFilter(admin.SimpleListFilter):
        title = 'author'
        parameter_name = 'address'
        template = 'admin_input_filter.html'

        def lookups(self, request, model_admin):
            return ((None, None),)

        def choices(self, changelist):
            query_params = changelist.get_filters_params()
            query_params.pop(self.parameter_name, None)
            all_choice = next(super().choices(changelist))
            all_choice['query_params'] = query_params
            yield all_choice

        def queryset(self, request, queryset):
            value = self.value()
            if value:
                return queryset.filter(author__address__trigram_similar=value)

    class AttachmentFilter(admin.SimpleListFilter):
        title = 'Attachments'
        parameter_name = 'attachment'

        def lookups(self, request, model_admin):
            return [('all', 'attachment only')]

        def queryset(self, request, queryset):
            value = self.value()

            if value is None:
                return queryset
            else:
                attachment_query = models.EmailAttachment.objects.filter(source_email=OuterRef('pk'))
                return queryset.annotate(attachment=Exists(attachment_query)).filter(attachment=True)


    list_filter = [FuzzyFilter, AttachmentFilter, AddressFilter]
    raw_id_fields = get_id_fields(models.Email)
    search_fields = ['id']

    list_display = ('_rank', '_subject', '_author', 'content_list')

    readonly_fields = ('subject', 'date', '_from', 'message_id', '_in_reply_to', 'date', '_to', '_cc', 'content', 'attachments', '_indexing_log', 'original_path')
    #link_fields = ('latest', )

    def _from(self, entry):
        return make_link(entry.author, entry.author.to_string())

    def _to(self, entry):
        return make_list_link(entry.to.all(), lambda e: e.to_string())

    def _cc(self, entry):
        return make_list_link(entry.cc.all(), lambda e: e.to_string())

    def _indexing_log(self, entry):
        return make_multiline_html(entry.indexing_log or '')

    def _subject(self, entry):
        value = entry.subject
        if hasattr(entry, 'search_term') and entry.search_term:
            return highlight_search_term(value, entry.search_term, settings.RESULT_PAGE_MAX_EMAIL_SUJECT_SIZE, link=entry.admin_link())
        else:
            return format_html(f'<a href="/searchix/email/{entry.id}/change"> {entry.subject} </a>')

    def content_list(self, entry):
        value = entry.content_text or entry.content_html or '<null>'
        if hasattr(entry, 'search_term') and entry.search_term:
            return highlight_search_term(value, entry.search_term, settings.RESULT_PAGE_MAX_EMAIL_BODY_SIZE)
        else:
            return value[:settings.RESULT_PAGE_MAX_EMAIL_BODY_SIZE]

    def content(self, entry):
        value = entry.content_text or entry.content_html or '<null>'
        return make_multiline_html(value)

    def _author(self, entry):
        if entry.author is None:
            return '<None>'
        value = entry.author.to_string()
        if hasattr(entry, 'search_term') and entry.search_term:
            return highlight_search_term(value, entry.search_term, 80, link = entry.author.admin_link())
        else:
            return format_html(f'<a href="/searchix/emailaddress/{entry.author.id}/change"> {value} </a>')

    def _rank(self, entry):
        if hasattr(entry, 'rank'):
            return f'{entry.rank:.2f}'
        else:
            return None

    def _in_reply_to(self, entry):
        if entry.in_reply_to is None:
            return None

        matches = models.Email.objects.filter(message_id=entry.in_reply_to)
        if matches.count() == 0:
            return f'Not found: {entry.in_reply_to}'
        elif matches.count() == 1:
            return make_link(matches[0], entry.in_reply_to)
        else:
            return format_html('Found multiple: ' + ','.join(make_link(e, e.id) for e in matches))

    def attachments(self, entry):
        attachments = models.EmailAttachment.objects.filter(source_email=entry).all()
        return format_html(', '.join(f'<a href="{e.download_link()}">{escape(e.file_name or "unnamed")} </a> <a href="{e.admin_link()}">(object)</a>' for e in attachments))

    def get_search_results(self, request, queryset, search_term):
        if not search_term:
            return queryset, False

        if False: # sqlite
            return self.model.objects.filter(
                    Q(subject__icontains=search_term) |
                    Q(content_text__icontains=search_term) |
                    Q(content_html__icontains=search_term)).annotate(search_term=Value(search_term)), False
        else:
            query = SearchQuery(search_term, search_type='websearch')

            search_vectors = (SearchVector('subject', weight='A', config='english')
                              + SearchVector('content_text', weight='B', config='english')
                              + SearchVector('content_html', weight='C', config='english'))

            rank = SearchRank(search_vectors, query=query)

            # Search the text index first
            query = queryset.filter(search=query).annotate(rank=rank).annotate(search_term=Value(search_term))

            # Then add trigrams if requested
            if request.environ.get('fuzzy_search', False):
                trigrams = TrigramSimilarity('subject', search_term) + TrigramSimilarity('content_text', search_term) + TrigramSimilarity('content_html', search_term)
                augmented_query = query.union(self.model.objects.exclude(id__in=query.values_list('id', flat=True)).annotate(rank=trigrams - 1).filter(rank__gte=-0.9).annotate(search_term=Value(search_term)))
                query = augmented_query

            return query.order_by('-rank'),False

admin.site.register(models.Email, Email)
admin.site.register(models.EmailAttachment, EmailAttachment)

class IndexEntry(admin.ModelAdmin):
    def get_changelist(self, request, **kwargs):
        class ChangeList(ChangeListDefault):
            def url_for_result(self, result):
                model_name = models.IndexEntry.ClassType(result.type).name.lower()
                return reverse(f'admin:{result._meta.app_label.lower()}_{model_name}_change', args=(result.id,))


        return ChangeList

admin.site.register(models.IndexEntry, IndexEntry)

def get_admin(): # Trick to allow admin panel access without authentication
    user, created = User.objects.get_or_create(username='admin')

    if created:
        user.is_superuser = True
        user.save()

    return user


admin.site.has_permission = lambda r: setattr(r, 'user', get_admin()) or True

