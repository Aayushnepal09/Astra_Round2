"""Create local demo company accounts and request-bound client invitations."""

import argparse
import getpass
import os
import secrets
from datetime import timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

import django

django.setup()

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from login.models import ClientInvitation, CompanyAccount


def create_company(args):
    email = CompanyAccount.objects.normalize_email(args.email)
    company = CompanyAccount(email=email, company_name=args.name.strip())
    company.full_clean(exclude=["password"])
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if not password:
        raise ValidationError("Password must not be empty.")
    if password != confirmation:
        raise ValidationError("Passwords do not match.")
    validate_password(password, company)
    CompanyAccount.objects.create_user(
        email=email, password=password, company_name=company.company_name
    )
    print(f"Created company account: {email}")


def create_invitation(args):
    company = CompanyAccount.objects.get(
        email=CompanyAccount.objects.normalize_email(args.company), is_active=True
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    invitation = ClientInvitation(
        company=company,
        client_email=CompanyAccount.objects.normalize_email(args.email),
        request_ref=args.request_ref.strip(),
        code_hash=make_password(code),
        expires_at=timezone.now() + timedelta(seconds=settings.CLIENT_CODE_TTL),
    )
    invitation.full_clean()
    with transaction.atomic():
        ClientInvitation.objects.filter(
            company=company, client_email=invitation.client_email,
            request_ref=invitation.request_ref, used_at__isnull=True,
            revoked_at__isnull=True,
        ).update(revoked_at=timezone.now())
        invitation.save()
    print(f"Invitation URL: {args.base_url.rstrip('/')}/client/login/{invitation.id}/")
    print(f"Access code: {code}")
    print(f"Expires at: {invitation.expires_at.isoformat()}")
    print("Demo mode: no email is sent. Use this link and code to continue as the client.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    company = commands.add_parser("company", help="Create a company account")
    company.add_argument("--email", required=True)
    company.add_argument("--name", required=True)
    company.set_defaults(handler=create_company)
    invite = commands.add_parser("invite", help="Create a client invitation")
    invite.add_argument("--company", required=True, help="Company account email")
    invite.add_argument("--email", required=True, help="Client email")
    invite.add_argument("--request-ref", required=True)
    invite.add_argument("--base-url", default="http://127.0.0.1:8000")
    invite.set_defaults(handler=create_invitation)
    args = parser.parse_args()
    try:
        args.handler(args)
    except CompanyAccount.DoesNotExist:
        parser.error("No active company account matches that email.")
    except IntegrityError:
        parser.error("The account or invitation already exists.")
    except ValidationError as exc:
        parser.error("; ".join(exc.messages))


if __name__ == "__main__":
    main()
