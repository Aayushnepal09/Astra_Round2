"""Company sign-in using Django's password and session authentication."""

from datetime import timedelta
from hashlib import sha256

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.base_user import BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Case, DateTimeField, F, Q, When
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.debug import sensitive_variables

from client_files.workspace import CompanyFileWorkspace

from .common import (
    auth_error, auth_result, company_authenticated, company_context, read_json,
    render_login,
)
from .models import LoginAttempt


def _attempt_keys(request, email):
    # REMOTE_ADDR comes from the server; forwarded headers are not trusted.
    return [
        sha256(value.encode("utf-8")).hexdigest()
        for value in (
            f"company-email:{email}",
            f"company-address:{request.META.get('REMOTE_ADDR', '')}",
        )
    ]


def _reserve_attempts(keys):
    """Reserve before hashing a password so concurrent requests share the limit."""
    now = timezone.now()
    window = timedelta(seconds=settings.COMPANY_LOGIN_WINDOW)
    limit = settings.COMPANY_LOGIN_MAX_ATTEMPTS
    allowed = True
    for key in keys:
        attempts = LoginAttempt.objects.filter(key=key)
        LoginAttempt.objects.get_or_create(key=key)
        attempts.filter(window_started_at__lte=now - window).update(
            failures=0, locked_until=None, window_started_at=now
        )
        reserved = attempts.filter(failures__lt=limit).filter(
            Q(locked_until__isnull=True) | Q(locked_until__lte=now)
        ).update(
            failures=F("failures") + 1,
            locked_until=Case(
                When(failures__gte=limit - 1, then=F("window_started_at") + window),
                default=None,
                output_field=DateTimeField(),
            ),
        )
        allowed = bool(reserved) and allowed
    return allowed


class CompanyLogin(View):
    def get(self, request):
        if company_authenticated(request):
            return redirect("company_dashboard")
        return render_login(request, "company")

    @sensitive_variables("payload", "password")
    def post(self, request):
        try:
            payload = read_json(request)
        except ValueError as error:
            return auth_error(str(error))
        email, password = payload.get("email"), payload.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            return auth_error("Email or password is incorrect.", status=401)
        email = BaseUserManager.normalize_email(email.strip()).lower()
        if len(email) > 254 or not 1 <= len(password) <= 4096:
            return auth_error("Email or password is incorrect.", status=401)
        try:
            validate_email(email)
        except ValidationError:
            return auth_error("Email or password is incorrect.", status=401)
        keys = _attempt_keys(request, email)
        if not _reserve_attempts(keys):
            return auth_error("Unable to sign in. Try again later.", status=429)
        user = authenticate(request, username=email, password=password)
        if user is None:
            return auth_error("Email or password is incorrect.", status=401)
        request.session.flush()
        login(request, user)
        request.session.set_expiry(0)
        LoginAttempt.objects.filter(key=keys[0]).delete()
        return auth_result(request, "company")


class CompanyDashboard(View):
    def get(self, request):
        if not company_authenticated(request):
            return redirect("company_login")
        response = render(request, "dashboard.html", {
            **company_context(request),
            "workspace_data": CompanyFileWorkspace(request.user).snapshot(),
            "files_url": reverse("company_file_list"),
            "create_file_url": reverse("company_file_create"),
            "agreements_url": reverse("company_agreement_list"),
        })
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "same-origin"
        return response


class CompanySession(View):
    def get(self, request):
        if not company_authenticated(request):
            return auth_error("Company sign-in is required.", status=401)
        return auth_result(request, "company")


class CompanyLogout(View):
    def post(self, request):
        if not company_authenticated(request):
            return auth_error("Company sign-in is required.", status=401)
        logout(request)
        return JsonResponse({"authenticated": False, "csrfToken": get_token(request)})
