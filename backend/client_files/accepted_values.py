"""Read-only document overlays from accepted submissions, never editable drafts."""
from .models import SigningSubmission
from .preparation import PreparationValues
from .submissions import ClientSigning, CONSENT_TEXT, SigningError


def accepted_fields(issued, *, role=None):
    submissions = SigningSubmission.objects.filter(signing_request=issued).select_related('recipient','invitation').order_by('submitted_at','pk')
    if role:
        submissions = submissions.filter(recipient__role=role)
    fields = []
    for submission in submissions:
        submission.full_clean()
        if (submission.template_version_id != issued.template_version_id
                or submission.pdf_sha256 != issued.template_version.pdf_sha256
                or submission.consent_text != CONSENT_TEXT
                or submission.submitted_at < issued.created_at
                or (issued.expires_at and submission.submitted_at > issued.expires_at)):
            raise SigningError('The recorded signing values could not be validated for this agreement.',status=409)
        values = ClientSigning.validate_values(submission.recipient,submission.values,complete=True)
        if values != submission.values:
            raise SigningError('The recorded signing values do not match their accepted submission.',status=409)
        fields.extend({**field,'value':values.get(field['id'],False if field['type']=='acknowledgement' else '')}
                      for field in issued.template_version.fields if field['role']==submission.recipient.role)
    return fields


def preview_fields(issued):
    return PreparationValues.displayed(issued) + accepted_fields(issued)
