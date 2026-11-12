import uuid

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class CompanyAccountManager(BaseUserManager):
    @classmethod
    def normalize_email(cls, email):
        return super().normalize_email((email or "").strip()).lower()

    def get_by_natural_key(self, email):
        return self.get(email=self.normalize_email(email))

    def create_user(self, email, password=None, **extra_fields):
        email = self.normalize_email(email)
        if not email:
            raise ValueError("An email address is required.")
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("Superusers must have is_staff and is_superuser enabled.")
        return self.create_user(email, password, **extra_fields)


class CompanyAccount(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    company_name = models.CharField(max_length=200)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["company_name"]
    objects = CompanyAccountManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="company_email_unique_ci")
        ]

    def save(self, *args, **kwargs):
        self.email = CompanyAccountManager.normalize_email(self.email)
        super().save(*args, **kwargs)


class ClientInvitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client_email = models.EmailField()
    request_ref = models.CharField(max_length=128)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    window_started_at = models.DateTimeField(default=timezone.now)
