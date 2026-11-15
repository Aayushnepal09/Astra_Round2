"""Fixed, tool-free Groq transport with shared free-plan admission."""

import json
import math
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import AssistantBudget


class AssistantError(Exception):
    def __init__(self, message, *, status=503, code="assistant_unavailable", retry_after=None):
        super().__init__(message)
        self.status, self.code, self.retry_after = status, code, retry_after


class AssistantAdmission:
    @staticmethod
    @transaction.atomic
    def reserve(tokens=1000):
        now = timezone.now()
        budget, _ = AssistantBudget.objects.get_or_create(
            provider="groq", defaults={"day": now.date(), "next_at": now},
        )
        AssistantBudget.objects.filter(pk=budget.pk, day__lt=now.date()).update(day=now.date(), calls=0)
        # Take a writer before reading admission state, including on SQLite.
        AssistantBudget.objects.filter(pk=budget.pk).update(calls=F("calls"))
        budget.refresh_from_db()
        if budget.calls >= settings.ASSISTANT_DAILY_CALLS:
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            raise AssistantError("The assistant's shared daily allowance is exhausted. Try after the daily reset; review and signing remain available.",
                                 status=429, code="assistant_quota", retry_after=math.ceil((midnight - now).total_seconds()))
        if budget.next_at > now:
            raise AssistantError("The provider asked us to pause. Retry after the indicated wait; signing remains available.",
                                 status=429, code="provider_quota", retry_after=math.ceil((budget.next_at - now).total_seconds()))
        active, _ = AssistantBudget.objects.get_or_create(provider="groq-active", defaults={"day": now.date(), "next_at": now})
        if active.next_at > now:
            raise AssistantError("Another assistant answer is in progress. Retry shortly; signing remains available.",
                                 status=429, code="assistant_busy", retry_after=max(1, math.ceil((active.next_at - now).total_seconds())))
        for key, cost, limit in [("groq-requests", 1, settings.ASSISTANT_REQUESTS_PER_MINUTE),
                                 ("groq-tokens", tokens, settings.ASSISTANT_TOKENS_PER_MINUTE)]:
            window, _ = AssistantBudget.objects.get_or_create(provider=key, defaults={"day": now.date(), "next_at": now})
            if window.next_at <= now:
                window.calls, window.next_at = 0, now + timedelta(seconds=60)
            if window.calls + cost > limit:
                raise AssistantError("The assistant's short-term shared allowance is in use. Retry after the indicated wait, or ask a narrower question; signing remains available.",
                                     status=429, code="assistant_quota", retry_after=max(1, math.ceil((window.next_at - now).total_seconds())))
            window.calls += cost
            window.save()
        lease = now + timedelta(seconds=settings.ASSISTANT_TIMEOUT + 5)
        active.next_at = lease
        active.save(update_fields=["next_at"])
        budget.calls += 1
        budget.save(update_fields=["calls"])
        return lease

    @staticmethod
    def release(lease):
        AssistantBudget.objects.filter(provider="groq-active", next_at=lease).update(next_at=timezone.now())

    @staticmethod
    def pause(seconds):
        until = timezone.now() + timedelta(seconds=seconds)
        AssistantBudget.objects.filter(provider="groq", next_at__lt=until).update(next_at=until)


class GroqAssistant:
    @staticmethod
    def available():
        return bool(settings.ASSISTANT_GROQ_API_KEY)

    def answer(self, messages, schema):
        if not self.available():
            raise AssistantError("The document assistant is not configured. You can still review and sign.",
                                 code="assistant_not_configured")
        encoded = json.dumps(messages, ensure_ascii=False).encode("utf-8")
        if len(encoded) > settings.ASSISTANT_INPUT_MAX_BYTES:
            raise AssistantError("This context exceeds the assistant's free-plan input allowance.",
                                 status=422, code="assistant_context_limit")
        body = {"model": settings.ASSISTANT_MODEL, "messages": messages,
                           "max_completion_tokens": settings.ASSISTANT_MAX_COMPLETION_TOKENS,
                           "reasoning_effort": "low", "stream": False,
                           "response_format": {"type": "json_schema", "json_schema": {
                               "name": "agreement_answer", "strict": True, "schema": schema,
                           }}}
        # Include schema/framing and reserved output; provider quotas remain the
        # authority if unusual text tokenizes above this byte-based estimate.
        tokens = math.ceil(len(json.dumps(body, ensure_ascii=False).encode("utf-8")) / 3) + settings.ASSISTANT_MAX_COMPLETION_TOKENS + 128
        if tokens > settings.ASSISTANT_TOKENS_PER_MINUTE:
            raise AssistantError("This context exceeds the assistant's bounded free-plan allowance. Ask a narrower question.", status=422, code="assistant_context_limit")
        lease = AssistantAdmission.reserve(tokens)
        try:
            # Kill the entire call at the deadline, including a slowly streaming response.
            process = subprocess.run(
                [sys.executable, "-I", str(Path(__file__).with_name("assistant_transport.py"))],
                input=json.dumps({"body": body, "key": settings.ASSISTANT_GROQ_API_KEY,
                                  "timeout": settings.ASSISTANT_TIMEOUT,
                                  "max_bytes": settings.AGREEMENT_MAX_BYTES}).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
                timeout=settings.ASSISTANT_TIMEOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if process.returncode:
                raise ValueError
            transport = json.loads(process.stdout)
            if transport["status"] == 429:
                retry = transport.get("retryAfter")
                retry = retry if type(retry) is int and 0 < retry <= 86400 else 60
                AssistantAdmission.pause(retry)
                raise AssistantError("The provider's free quota is exhausted. Try later; signing remains available.",
                                     status=429, code="provider_quota", retry_after=retry)
            if transport["status"] == 413:
                raise AssistantError("This context exceeds the provider's current free-plan allowance. Ask a narrower question or review the original pages; no complete summary was generated.",
                                     status=422, code="assistant_context_limit")
            if transport["status"] in (401, 403):
                raise AssistantError("Groq rejected the configured access. Check the server's Free Plan API key and model access, then restart Django. You can still review and sign.",
                                     code="provider_configuration")
            if transport["status"] == 504:
                raise subprocess.TimeoutExpired("assistant", settings.ASSISTANT_TIMEOUT)
            if transport["status"] != 200:
                raise AssistantError("The assistant provider is unavailable. Try later; signing remains available.")
            result = transport["body"]
            choice = result["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError
            answer = json.loads(choice["message"]["content"])
            if not isinstance(answer, dict):
                raise ValueError
            return answer
        except subprocess.TimeoutExpired:
            raise AssistantError("The assistant timed out. Try again later; signing remains available.",
                                 status=504, code="assistant_timeout") from None
        except OSError:
            raise AssistantError("The assistant could not be started. You can still review and sign.") from None
        except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
            raise AssistantError("The assistant returned an incomplete or invalid answer. No answer was shown; you can still sign.",
                                 code="invalid_assistant_response") from None
        finally:
            AssistantAdmission.release(lease)
