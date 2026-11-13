"""Shared client-access budgets independent of invitation replacement."""

import json
from contextlib import contextmanager
from datetime import timedelta
from ipaddress import IPv6Address, ip_address

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import CompanyAccount, LoginAttempt


class ClientThrottleExceeded(Exception):
    pass


class ClientAccessThrottle:
    @staticmethod
    def _key(purpose, *parts):
        value = json.dumps([purpose, *parts], separators=(",", ":"))
        return salted_hmac("login.client-throttle", value, algorithm="sha256").hexdigest()

    @classmethod
    def _scope_key(cls, purpose, invitation):
        return cls._key(
            purpose,
            str(invitation.company_id),
            CompanyAccount.objects.normalize_email(invitation.client_email),
            invitation.request_ref.strip(),
        )

    @classmethod
    def reserve_network(cls, request):
        # Forwarding headers are not trusted by the current direct-serving setup.
        try:
            address = ip_address(request.META.get("REMOTE_ADDR", ""))
            if isinstance(address, IPv6Address):
                address = address.ipv4_mapped or IPv6Address(address.packed)
            source = str(address)
        except (ValueError, TypeError):
            source = "unknown"
        with cls._reservation(
            cls._key("client-verify-network", source),
            settings.CLIENT_VERIFY_NETWORK_MAX_ATTEMPTS,
            settings.CLIENT_VERIFY_WINDOW,
        ):
            pass

    @classmethod
    def verification(cls, invitation):
        return cls._reservation(
            cls._scope_key("client-verify-scope", invitation),
            settings.CLIENT_VERIFY_SCOPE_MAX_ATTEMPTS,
            settings.CLIENT_VERIFY_WINDOW,
        )

    @classmethod
    def generation(cls, invitation):
        return cls._reservation(
            cls._scope_key("client-generate-scope", invitation),
            settings.CLIENT_GENERATION_MAX_CODES,
            settings.CLIENT_GENERATION_WINDOW,
            settings.CLIENT_GENERATION_COOLDOWN,
        )

    @staticmethod
    @contextmanager
    def _reservation(key, limit, window_seconds, cooldown_seconds=0):
        with transaction.atomic():
            attempts = LoginAttempt.objects.filter(key=key)
            # Acquire SQLite's writer before reading, including for a new key.
            attempts.update(failures=F("failures"))
            now = timezone.now()
            LoginAttempt.objects.get_or_create(
                key=key, defaults={"window_started_at": now}
            )
            # A window rollover must preserve any outstanding generation cooldown.
            attempts.filter(
                window_started_at__lte=now - timedelta(seconds=window_seconds)
            ).update(failures=0, window_started_at=now)
            reserved = attempts.filter(failures__lt=limit).filter(
                Q(locked_until__isnull=True) | Q(locked_until__lte=now)
            ).update(
                failures=F("failures") + 1,
            )
            if not reserved:
                raise ClientThrottleExceeded
            yield
            if cooldown_seconds:
                attempts.update(
                    locked_until=timezone.now() + timedelta(seconds=cooldown_seconds)
                )
