"""Request-bound source passages; editable preparation and signer drafts are excluded."""

import hashlib
import json
import re
import tempfile
from pathlib import Path

from django.conf import settings

from agreements.processing import PdfProcessor
from agreements.storage import PrivateDocumentStorage
from .assistant_access import AssistantAccessError
from .preparation import PreparationValues


class AssistantSources:
    def __init__(self, recipient):
        issued = recipient.signing_request
        version, agreement = issued.template_version, issued.template_version.agreement
        storage = PrivateDocumentStorage(issued.client_file.company_id, agreement.pk)
        # Extract a private, verified snapshot rather than reopening a mutable source path.
        with tempfile.TemporaryDirectory(prefix="astra-assistant-", dir=storage.root) as directory:
            snapshot = Path(directory) / "issued.pdf"
            with storage.open(agreement.pdf_key) as source, snapshot.open("xb") as destination:
                digest, size = hashlib.sha256(), 0
                for chunk in iter(lambda: source.read(64 * 1024), b""):
                    size += len(chunk)
                    if size > settings.AGREEMENT_MAX_BYTES:
                        raise self.invalid_source()
                    digest.update(chunk)
                    destination.write(chunk)
            if size != agreement.pdf_size_bytes or digest.hexdigest() != version.pdf_sha256:
                raise self.invalid_source()
            pages = PdfProcessor().extract(snapshot)
        if len(pages) != agreement.page_count:
            raise self.invalid_source()
        self.limited_pages = [page["page"] for page in pages if page["status"] != "text"]
        self.readable_pages = [page["page"] for page in pages if page["status"] != "unreadable"]
        self.mixed_pages = [page["page"] for page in pages if page["status"] == "mixed"]
        self.unreadable_pages = [page["page"] for page in pages if page["status"] == "unreadable"]
        self.page_count = len(pages)
        prepared = PreparationValues.displayed(issued)
        identity = json.dumps({"request": str(issued.pk), "version": str(version.pk),
                               "pdf": version.pdf_sha256, "prepared": prepared, "extractor": 2},
                              sort_keys=True, ensure_ascii=False)
        self.identity = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.passages = {}
        for page in pages:
            if page["status"] == "unreadable":
                continue
            text = " ".join(page["text"].split())
            # Small passages keep page citations precise and allow bounded local retrieval.
            width = 1000
            for offset in range(0, len(text), width):
                self.add(page["page"], "Agreement text", text[offset:offset + width])
        for field in prepared:
            self.add(field["page"], "Company-prepared " + (field["label"] or field["type"]),
                     field["value"] or "Not provided")

    @staticmethod
    def invalid_source():
        return AssistantAccessError("The issued PDF failed its integrity check. Contact the company.",
                                    status=409, code="issued_agreement_unavailable")

    def add(self, page, label, text):
        key = hashlib.sha256(f"{self.identity}:{len(self.passages)}:{page}:{label}:{text}".encode("utf-8")).hexdigest()[:20]
        self.passages[key] = {"id": key, "page": page, "label": label, "text": text}

    def context(self, question, *, summary, fits):
        complete = not self.limited_pages
        all_passages = list(self.passages.values())
        if fits(all_passages, complete):
            return all_passages, complete
        prepared = [p for p in all_passages if p["label"].startswith("Company-prepared ")]
        if not fits(prepared, False):
            return [], False
        text = [p for p in all_passages if p not in prepared]
        if summary:
            # Spread whole passages across pages, then add another round when it
            # fits. Coverage explicitly reports this as a selected-text summary.
            by_page = {page: [p for p in text if p["page"] == page] for page in self.readable_pages}
            ranked = [group[index] for index in range(max((len(g) for g in by_page.values()), default=0))
                      for group in by_page.values() if index < len(group)]
        else:
            terms = set(re.findall(r"\w+", question.casefold()))
            ranked = sorted(text, key=lambda passage: len(
                terms & set(re.findall(r"\w+", (passage["label"] + " " + passage["text"]).casefold()))), reverse=True)
        selected = list(prepared)
        for passage in ranked:
            if fits(selected + [passage], False):
                selected.append(passage)
        return selected, False
