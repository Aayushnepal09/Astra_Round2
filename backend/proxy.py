"""Accept proxy metadata only from the deployment's local reverse proxy."""

from ipaddress import ip_address

from django.conf import settings
from django.http import HttpResponseBadRequest


class TrustedProxyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.BEHIND_PROXY:
            # Caddy overwrites both headers; Gunicorn listens on IPv4 localhost only.
            if request.META.get("REMOTE_ADDR") != "127.0.0.1":
                return HttpResponseBadRequest("Invalid proxy connection.")
            try:
                address = str(ip_address(request.META.get("HTTP_X_ASTRA_CLIENT_IP", "")))
            except ValueError:
                return HttpResponseBadRequest("Invalid proxy address.")
            if request.META.get("HTTP_X_FORWARDED_PROTO") != "https":
                return HttpResponseBadRequest("HTTPS is required.")
            request.META["REMOTE_ADDR"] = address
            request.META["wsgi.url_scheme"] = "https"
        return self.get_response(request)
