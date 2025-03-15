from django.http import HttpResponse, HttpResponseNotFound
from searchix.models import EmailAttachment

def attachment_download(request, id):

    try:
        attachment = EmailAttachment.objects.get(id=id)
    except EmailAttachment.DoesNotExist:
        return HttpResponseNotFound()

    # TODO: This won't work for big files
    response = HttpResponse(attachment.content, content_type=attachment.content_type)

    response['Content-Disposition'] = 'inline; filename=' + attachment.file_name or 'unnamed'

    return response
