"""Keep company and client session lifecycles independent in one browser."""

import time

from django.conf import settings
from django.contrib.sessions.backends.base import UpdateError
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils.cache import patch_vary_headers
from django.utils.http import http_date


class RoleSessionMiddleware(SessionMiddleware):
    @staticmethod
    def _cookie_settings(request):
        if request.path_info.startswith("/client/"):
            return settings.CLIENT_SESSION_COOKIE_NAME, settings.CLIENT_SESSION_COOKIE_PATH
        return settings.COMPANY_SESSION_COOKIE_NAME, settings.COMPANY_SESSION_COOKIE_PATH

    def process_request(self, request):
        cookie_name, _ = self._cookie_settings(request)
        request.session = self.SessionStore(request.COOKIES.get(cookie_name))

    def process_response(self, request, response):
        # Follow Django's session persistence rules with a role-specific cookie.
        try:
            accessed = request.session.accessed
            modified = request.session.modified
            empty = request.session.is_empty()
        except AttributeError:
            return response

        cookie_name, cookie_path = self._cookie_settings(request)
        if cookie_name in request.COOKIES and empty:
            response.delete_cookie(
                cookie_name,
                path=cookie_path,
                domain=settings.SESSION_COOKIE_DOMAIN,
                samesite=settings.SESSION_COOKIE_SAMESITE,
            )
            patch_vary_headers(response, ("Cookie",))
            return response

        if accessed:
            patch_vary_headers(response, ("Cookie",))
        if empty or not (modified or settings.SESSION_SAVE_EVERY_REQUEST):
            return response
        if response.status_code >= 500:
            return response

        if request.session.get_expire_at_browser_close():
            max_age = expires = None
        else:
            max_age = request.session.get_expiry_age()
            expires = http_date(time.time() + max_age)
        try:
            request.session.save()
        except UpdateError as error:
            raise SessionInterrupted(
                "The request's session was deleted before the request completed. "
                "The user may have logged out in a concurrent request, for example."
            ) from error

        response.set_cookie(
            cookie_name,
            request.session.session_key,
            max_age=max_age,
            expires=expires,
            domain=settings.SESSION_COOKIE_DOMAIN,
            path=cookie_path,
            secure=settings.SESSION_COOKIE_SECURE or None,
            httponly=settings.SESSION_COOKIE_HTTPONLY or None,
            samesite=settings.SESSION_COOKIE_SAMESITE,
        )
        patch_vary_headers(response, ("Cookie",))
        return response
