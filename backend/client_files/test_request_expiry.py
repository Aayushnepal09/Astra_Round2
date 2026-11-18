"""Request deadlines are independent of code expiry and preserve signing history."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .assistant_access import AssistantAccess, AssistantAccessError
from .countersigning import CompanySigning
from .finalization import AgreementFinalizer
from .lifecycle import refresh_file_expiry
from .models import ClientFile, SigningRequest
from .signing import signing_recipient
from .submissions import SigningError
from .test_request_lifecycle import LifecycleFixture
from .workspace import CompanyFileWorkspace


class RequestExpiryChecks(LifecycleFixture, TestCase):
    def after_deadline(self, issued):
        return patch("django.utils.timezone.now", return_value=issued.expires_at + timedelta(seconds=1))

    def test_new_request_has_fourteen_days_and_invitation_preview_uses_frozen_recipient(self):
        now = timezone.now()
        with patch("django.utils.timezone.now", return_value=now):
            issued = self.issue()
        self.assertEqual(issued.expires_at, now + timedelta(days=14))
        self.assertLess(self.invitation(issued).expires_at, issued.expires_at)
        self.file.signer_name = "Changed local name"
        state = self.issuer.snapshot(issued)
        self.assertEqual(state["recipient"], {"name": "Demo Client", "email": "demo-client@example.test"})
        self.assertEqual(state["requestExpiresAt"], issued.expires_at.isoformat())
        page = self.client.get(reverse("company_file_detail", args=[self.file.pk]))
        self.assertContains(page, "Normally this invitation is emailed to the client. In this demo")
        self.assertContains(page, "Invitation ready")

    def test_code_expiry_and_replacement_do_not_expire_or_extend_request(self):
        issued = self.issue()
        old = self.invitation(issued)
        with patch("django.utils.timezone.now", return_value=old.expires_at + timedelta(seconds=1)):
            snapshot = self.issuer.snapshot(issued)
            self.assertEqual(snapshot["state"], "expired")
            self.assertEqual(snapshot["progress"], "awaiting_client")
            self.issuer.replace_code(self.file.pk, str(old.pk))
        issued.refresh_from_db()
        self.assertEqual(issued.expires_at.isoformat(), snapshot["requestExpiresAt"])
        self.assertEqual(issued.status, "awaiting_client")
        self.assertEqual(SigningRequest.objects.count(), 1)

    def test_code_is_reusable_until_it_expires_and_success_clears_wrong_guesses(self):
        from .signing import demo_code
        issued = self.issue()
        invitation = self.invitation(issued)
        url = reverse("client_invitation", args=[invitation.pk])
        post = lambda code: Client().post(url, {"code": code}, content_type="application/json")
        wrong = "000000" if demo_code(invitation) != "000000" else "111111"
        self.assertEqual(post(wrong).status_code, 401)
        self.assertEqual(post(demo_code(invitation)).status_code, 200)
        invitation.refresh_from_db()
        first_use, attempts_after_success = invitation.used_at, invitation.attempts
        self.assertEqual(attempts_after_success, 0)
        for _ in range(3):
            self.assertEqual(post(demo_code(invitation)).status_code, 200)
        invitation.refresh_from_db()
        self.assertEqual(invitation.used_at, first_use)
        self.assertEqual(self.issuer.snapshot(issued)["code"], demo_code(invitation))
        self.assertFalse(self.issuer.snapshot(issued)["canReplace"])
        with patch("django.utils.timezone.now", return_value=invitation.expires_at + timedelta(seconds=1)):
            self.assertEqual(post(demo_code(invitation)).status_code, 410)

    def test_new_code_is_clamped_to_request_deadline(self):
        issued = self.issue()
        old = self.invitation(issued)
        with patch("django.utils.timezone.now", return_value=issued.expires_at - timedelta(minutes=2)):
            self.issuer.replace_code(self.file.pk, str(old.pk))
            self.assertEqual(self.issuer.snapshot(issued)["expiresAt"], issued.expires_at.isoformat())

    def test_expiry_revokes_verified_access_signing_and_assistant_without_model_call(self):
        issued = self.issue()
        client = self.verify(issued)
        invitation = self.invitation(issued)
        session = self.client_request(client)
        with self.after_deadline(issued):
            self.assertIsNone(signing_recipient(invitation))
            with self.assertRaises(AssistantAccessError):
                AssistantAccess.resolve(session, invitation.pk)
            self.assertEqual(client.get(reverse("client_agreement_signing", args=[invitation.pk])).status_code, 401)
            self.assertEqual(Client().post(reverse("client_invitation", args=[invitation.pk]),
                            {"code": "000000"}, content_type="application/json").status_code, 401)
            with self.assertRaises(ValidationError):
                self.issuer.replace_code(self.file.pk, str(invitation.pk))
        issued.refresh_from_db()
        invitation.refresh_from_db()
        self.assertEqual(issued.status, "expired")
        self.assertIsNotNone(invitation.revoked_at)
        self.assertFalse(issued.submissions.exists())

    def test_dashboard_attention_and_explicit_replacement_keep_expired_history(self):
        issued = self.issue()
        old_values = dict(issued.preparation_values)
        with self.after_deadline(issued):
            workspace = CompanyFileWorkspace(self.company).snapshot()
            self.assertEqual(workspace["counts"]["attention"], 1)
            self.assertEqual(workspace["counts"]["waiting"], 0)
            self.assertEqual(workspace["files"][0]["status"], "expired")
            # Moving the clock also expires the company's session; sign in at
            # the simulated future time before exercising the company API.
            self.client.force_login(self.company)
            from django.conf import settings
            self.client.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = self.client.cookies[settings.SESSION_COOKIE_NAME].value
            response = self.client.get(reverse("company_file_request", args=[self.file.pk]))
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(response.json()["file"]["canPrepareReplacement"])
            self.assertFalse(response.json()["file"]["canCancelRequest"])
            self.assertEqual(response.json()["invitation"]["state"], "request_expired")
            self.assertEqual(self.cancel_post(issued).json()["outcome"], "already_expired")
            self.lifecycle.prepare_replacement(self.file.pk, issued.pk)
            self.assertEqual(SigningRequest.objects.count(), 1)
            replacement = self.issue()
            self.assertEqual(replacement.replaces_id, issued.pk)
            self.assertFalse(replacement.submissions.exists())
            self.assertGreater(replacement.expires_at, issued.expires_at)
        issued.refresh_from_db()
        self.assertEqual(issued.status, "expired")
        self.assertEqual(issued.preparation_values, old_values)
        history = self.client.get(reverse("company_request_history", args=[self.file.pk]))
        self.assertContains(history, "Signing deadline passed")

    def test_partial_signatures_preserved_but_company_stage_blocked(self):
        issued = self.countersigned()
        self.sign_client(issued)
        before = list(issued.submissions.values())
        with self.after_deadline(issued):
            with self.assertRaises(SigningError):
                CompanySigning.recipient(SimpleNamespace(user=self.company, session={}), self.file.pk)
            self.file.refresh_from_db()
            self.assertEqual(self.file.status, "expired")
            self.lifecycle.prepare_replacement(self.file.pk, issued.pk)
            replacement = self.issue()
        self.assertEqual(list(issued.submissions.values()), before)
        self.assertFalse(replacement.submissions.exists())

    def test_accepted_signatures_before_deadline_can_finalize_later_even_with_stale_stage(self):
        issued = self.issue()
        self.sign_client(issued, direct=True)
        QuerySet(model=SigningRequest, using="default").filter(pk=issued.pk).update(status="awaiting_client")
        ClientFile.objects.filter(pk=self.file.pk).update(status="awaiting_client")
        self.file.refresh_from_db()
        with self.after_deadline(issued):
            file = refresh_file_expiry(self.file)
            self.assertEqual(file.status, "finalizing")
            result = AgreementFinalizer(self.company.pk).run(file.pk)
            self.assertEqual(result["state"], "completed")
            self.file.refresh_from_db()
            self.assertEqual(refresh_file_expiry(self.file).status, "completed")

    def test_preexisting_request_without_deadline_is_not_backfilled(self):
        issued = self.issue()
        QuerySet(model=SigningRequest, using="default").filter(pk=issued.pk).update(expires_at=None)
        self.file.refresh_from_db()
        with patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(days=30)):
            self.assertEqual(refresh_file_expiry(self.file).status, "awaiting_client")
        issued.refresh_from_db()
        self.assertIsNone(issued.expires_at)
