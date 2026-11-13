import json
import subprocess
import sys
from pathlib import Path

from django.conf import settings


class DocumentError(Exception):
    def __init__(self, message, code="processing_failed"):
        super().__init__(message)
        self.code = code


class PdfProcessor:
    """Keep native PDF work outside Django's process and thread pool."""

    errors = {
        "empty": "This file is empty. Choose a PDF containing at least one page.",
        "oversized": "This PDF exceeds the upload size limit. Choose a smaller PDF.",
        "unsupported": "Only PDF documents are supported. Export your agreement as a PDF and try again.",
        "encrypted": "This PDF is encrypted or password-protected. Export an unprotected copy and upload it again.",
        "corrupt": "This PDF is damaged or incomplete. Open it in a PDF editor, export a fresh copy, and try again.",
        "no_pages": "This PDF contains no pages. Choose a PDF containing at least one page.",
        "too_many_pages": "This PDF exceeds the page limit. Choose a shorter PDF.",
        "unsupported_forms": "This PDF uses XFA forms that cannot be previewed. Export a standard, flattened PDF and try again.",
        "invalid_page": "A page in this PDF cannot be previewed. Export a standard PDF and try again.",
        "page_not_found": "This page does not exist. Choose another page.",
        "resource_limit": "This PDF needs more processing resources than are available. Export a simpler PDF and try again.",
        "processing_failed": "The PDF could not be processed. Please try again or export a fresh PDF.",
    }

    def validate(self, path):
        result = self._run("validate", path)
        try:
            pages = json.loads(result)
            if not isinstance(pages, list) or not pages:
                raise ValueError
            return pages
        except (ValueError, UnicodeDecodeError) as error:
            raise DocumentError(self.errors["processing_failed"]) from error

    def render(self, path, page_number):
        result = self._run("render", path, page_number)
        if not result.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DocumentError(self.errors["processing_failed"])
        return result

    def _run(self, operation, path, page_number=0):
        command = [
            sys.executable, "-I", str(Path(__file__).with_name("pdf_worker.py")),
            operation, str(Path(path).resolve()), str(settings.AGREEMENT_MAX_BYTES),
            str(settings.AGREEMENT_MAX_PAGES), str(settings.AGREEMENT_PREVIEW_MAX_EDGE),
            str(page_number), str(settings.AGREEMENT_PROCESSING_MAX_MEMORY),
        ]
        try:
            result = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=settings.AGREEMENT_PROCESSING_TIMEOUT,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as error:
            raise DocumentError(
                "This PDF took too long to process. Try again or export a simpler PDF.",
                "processing_timeout",
            ) from error
        except OSError as error:
            raise DocumentError(
                "PDF processing is temporarily unavailable. Please try again.",
                "processing_unavailable",
            ) from error
        if result.returncode:
            try:
                code = json.loads(result.stdout).get("error", "processing_failed")
            except (ValueError, UnicodeDecodeError, AttributeError):
                code = "processing_failed"
            if code not in self.errors:
                code = "processing_failed"
            message = self.errors[code]
            if code == "too_many_pages":
                message = f"This PDF exceeds the {settings.AGREEMENT_MAX_PAGES}-page limit. Choose a shorter PDF."
            elif code == "oversized":
                size = settings.AGREEMENT_MAX_BYTES / (1024 * 1024)
                message = f"This PDF exceeds the {size:g} MiB upload limit. Choose a smaller PDF."
            raise DocumentError(message, code)
        return result.stdout
