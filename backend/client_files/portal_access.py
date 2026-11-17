"""Client portal identity: one company/client record and one verified signer email.

An issued request freezes its client binding. Matching an email alone never grants
access across clients. Invitation IDs locate documents; the verified session is
the authorization source, and every document is rechecked independently.
"""
from django.db.models import F, Q
from django.utils import timezone

from .models import ClientFile, SigningAccess, SigningRecipient
from .signing import signing_recipient


def verification_recipient(invitation):
    access = SigningAccess.objects.select_related('recipient__signing_request__client_file',
        'recipient__signing_request__client', 'recipient__signing_request__template_version__agreement').filter(invitation=invitation).first()
    if not access:
        return None
    recipient, issued = access.recipient, access.recipient.signing_request
    if (recipient.role != 'client' or invitation.company_id != issued.client_file.company_id
            or invitation.company_id != issued.template_version.agreement.company_id
            or invitation.request_ref != str(issued.pk) or invitation.client_email != recipient.email
            or (issued.client_id and issued.client.company_id != invitation.company_id)):
        return None
    return recipient


def scope_for(invitation):
    recipient = signing_recipient(invitation)
    if recipient and recipient.signing_request.client_id:
        return {'client_id': str(recipient.signing_request.client_id), 'company_id': str(invitation.company_id),
                'email': recipient.email.casefold(), 'verification_id': str(invitation.pk)}
    return None


def scope_matches(invitation, scope):
    if not isinstance(scope, dict) or set(scope) != {'client_id','company_id','email','verification_id'}:
        return False
    recipient = verification_recipient(invitation)
    if not recipient or not invitation.used_at:
        return False
    issued = recipient.signing_request
    # Cancelling one request revokes its document access, not unrelated shared requests.
    if invitation.revoked_at and issued.status not in (ClientFile.Status.CANCELLED, ClientFile.Status.EXPIRED):
        return False
    return scope == {'client_id': str(issued.client_id), 'company_id': str(invitation.company_id),
                     'email': recipient.email.casefold(), 'verification_id': str(invitation.pk)}


def shared_recipients(scope):
    return SigningRecipient.objects.filter(role='client', email__iexact=scope['email'],
        signing_request__client_id=scope['client_id'], signing_request__client__company_id=scope['company_id'],
        signing_request__client_file__company_id=scope['company_id'],
        signing_request__template_version__agreement__company_id=scope['company_id'],
        signing_request__client_file__current_request_id=F('signing_request_id')).exclude(
        signing_request__client_file__status='draft')


def available_recipients(scope):
    return shared_recipients(scope).filter(
        signing_request__status=F('signing_request__client_file__status'),
        signing_request__template_version_id=F('signing_request__client_file__template_version_id')).filter(
        Q(signing_request__status__in=('finalizing','completed')) |
        (Q(signing_request__status__in=('awaiting_client','awaiting_company')) &
         (Q(signing_request__expires_at__isnull=True) | Q(signing_request__expires_at__gt=timezone.now()))))


def client_access(request, *, invitation_id=None, recipient_id=None, touch=True):
    from login.common import client_identity
    identity = client_identity(request, touch=touch)
    if not identity:
        return None, None
    scope = request.session.get('client_portal')
    if not scope:
        recipient = signing_recipient(identity)
        if recipient and ((recipient_id is not None and str(recipient.pk) == str(recipient_id)) or
                (invitation_id is not None and SigningAccess.objects.filter(invitation_id=invitation_id, recipient=recipient).exists())):
            return identity, recipient
        return identity, None
    candidates = available_recipients(scope)
    if recipient_id is not None:
        candidates = candidates.filter(pk=recipient_id)
    elif invitation_id is not None:
        candidates = candidates.filter(access_codes__invitation_id=invitation_id)
    else:
        return identity, None
    recipient = candidates.first()
    if not recipient:
        return identity, None
    access = recipient.access_codes.select_related('invitation').order_by('-invitation__created_at').first()
    # Reuse the existing exact document/version/current-request validation.
    checked = signing_recipient(access.invitation) if access else None
    return identity, checked if checked and checked.pk == recipient.pk else None


def invitation_authorizes(invitation, recipient):
    source = verification_recipient(invitation)
    if not source:
        return False
    if source.pk == recipient.pk:
        return True  # Preserve validation of existing per-invitation signing records.
    target, origin = recipient.signing_request, source.signing_request
    return bool(invitation.used_at and target.client_id and target.client_id == origin.client_id
                and target.client_file.company_id == invitation.company_id
                and target.template_version.agreement.company_id == invitation.company_id
                and target.client.company_id == invitation.company_id
                and recipient.role == 'client' and recipient.email.casefold() == source.email.casefold())
