import json

from django.conf import settings
from django.http import JsonResponse
from django.middleware.csrf import get_token, rotate_token
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .models import ClientInvitation


def company_authenticated(request):
    return request.user.is_authenticated and not request.session.get("client_invitation")


def company_context(request):
    account = request.user
    account_name = account.get_short_name() or "Your account"
    company_name = account.company_name or "Company workspace"
    return {
        "company_id": str(account.pk),
        "company_name": company_name,
        "company_initial": company_name[:1].upper(),
        "account_name": account_name,
        "account_initial": account_name[:1].upper(),
        "account_email": account.email,
        "session_url": reverse("company_session"),
        "logout_url": reverse("company_logout"),
        "login_url": reverse("company_login"),
        "dashboard_url": reverse("company_dashboard"),
        "static_prefix": settings.STATIC_URL,
    }


def read_json(request):
    if request.content_type != "application/json" or len(request.body) > 8192:
        raise ValueError("Send a valid JSON request.")
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("Send a valid JSON request.") from error
    if not isinstance(data, dict):
        raise ValueError("Send a valid JSON request.")
    return data


def render_login(request, role, authenticated=False):
    response = render(request, "login.html", {
        "login_role": role,
        "login_url": request.path,
        "logout_url": reverse(f"{role}_logout"),
        "authenticated": "true" if authenticated else "false",
        "dashboard_url": reverse("company_dashboard") if role == "company" else "",
        "static_prefix": settings.STATIC_URL,
    })
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"
    return response


def auth_result(request, role):
    response = JsonResponse({
        "authenticated": True, "role": role,
        "message": "You’re logged in.",
        "dashboardUrl": reverse("company_dashboard") if role == "company" else None,
        "csrfToken": get_token(request),
        **({"companyId": str(request.user.pk)} if role == "company" else {}),
    })
    response["Cache-Control"] = "no-store"
    return response


def auth_error(message, status=400):
    response = JsonResponse({"error": message}, status=status)
    response["Cache-Control"] = "no-store"
    return response


def establish_client_session(request, invitation):
    request.session.flush()
    rotate_token(request)
    now = timezone.now().timestamp()
    request.session.update({
        "client_invitation": str(invitation.pk),
        "client_started": now, "client_last_seen": now,
    })
    request.session.set_expiry(settings.CLIENT_SESSION_IDLE)


def client_identity(request, touch=True):
    invitation_id = request.session.get("client_invitation")
    if not invitation_id or request.user.is_authenticated:
        return None
    now = timezone.now().timestamp()
    started = request.session.get("client_started", 0)
    last_seen = request.session.get("client_last_seen", 0)
    if now - started >= settings.CLIENT_SESSION_MAX or now - last_seen >= settings.CLIENT_SESSION_IDLE:
        request.session.flush()
        return None
    invitation = ClientInvitation.objects.filter(
        pk=invitation_id, used_at__isnull=False,
        revoked_at__isnull=True, company__is_active=True,
    ).first()
    if invitation is None:
        request.session.flush()
        return None
    if touch:
        request.session["client_last_seen"] = now
        request.session.set_expiry(max(1, min(settings.CLIENT_SESSION_IDLE, int(settings.CLIENT_SESSION_MAX - (now - started)))))
    return invitation
