"""Invitation-bound client access; invitation identity always comes from the URL."""

import re

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import DatabaseError
from django.db.models import F
from django.http import JsonResponse
from django.middleware.csrf import get_token, rotate_token
from django.utils import timezone
from django.views import View
from django.views.decorators.debug import sensitive_variables

from .client_throttle import ClientAccessThrottle, ClientThrottleExceeded
from .common import (
    auth_error,
    auth_result,
    client_identity,
    establish_client_session,
    read_json,
    render_login,
)
from .models import ClientInvitation


class _InvitationUnavailable(Exception):
    pass


def _invalid_code():
    return auth_error("Unable to verify this code. Check your invitation or contact the company.", status=401)


def _available(invitation_id, code_hash):
    return ClientInvitation.objects.filter(
        pk=invitation_id,
        code_hash=code_hash,
        used_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
        company__is_active=True,
    )


class ClientLogin(View):
    def get(self, request, invitation_id=None):
        invitation = client_identity(request)
        authenticated = invitation is not None and (
            invitation_id is None or str(invitation.pk) == str(invitation_id)
        )
        return render_login(request, "client", authenticated=authenticated)

    @sensitive_variables("payload", "code")
    def post(self, request, invitation_id=None):
        try:
            # Commit network admission even when the body or invitation is invalid.
            ClientAccessThrottle.reserve_network(request)
            try:
                payload = read_json(request)
            except ValueError as error:
                return auth_error(str(error))
            if invitation_id is None:
                return _invalid_code()
            invitation = ClientInvitation.objects.filter(pk=invitation_id).only(
                "company_id", "client_email", "request_ref", "code_hash"
            ).first()
            if invitation is None:
                return _invalid_code()
            with ClientAccessThrottle.verification(invitation):
                reserved = _available(invitation_id, invitation.code_hash).filter(
                    attempts__lt=settings.CLIENT_MAX_ATTEMPTS
                ).update(attempts=F("attempts") + 1)
                if not reserved:
                    # Roll back the tuple budget for stale or exhausted invitations.
                    raise _InvitationUnavailable
            code = payload.get("code")
            if set(payload) != {"code"} or not isinstance(code, str) or re.fullmatch(r"[0-9]{6}", code) is None:
                return _invalid_code()
            if not check_password(code, invitation.code_hash):
                return _invalid_code()
            redeemed = _available(invitation_id, invitation.code_hash).filter(
                attempts__lte=settings.CLIENT_MAX_ATTEMPTS
            ).update(used_at=timezone.now())
            if not redeemed:
                return _invalid_code()
            establish_client_session(request, invitation)
            return auth_result(request, "client")
        except _InvitationUnavailable:
            return _invalid_code()
        except ClientThrottleExceeded:
            return auth_error("Unable to verify this code right now. Try again later.", status=429)
        except DatabaseError:
            return auth_error("Unable to verify this code right now. Try again later.", status=503)


class ClientSession(View):
    def get(self, request):
        if client_identity(request) is None:
            return auth_error("Client sign-in is required.", status=401)
        return auth_result(request, "client")


class ClientLogout(View):
    def post(self, request):
        if client_identity(request) is None:
            return auth_error("Client sign-in is required.", status=401)
        request.session.flush()
        rotate_token(request)
        return JsonResponse({"authenticated": False, "csrfToken": get_token(request)})
