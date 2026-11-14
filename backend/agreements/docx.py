import json
import subprocess
import tempfile
import uuid

from django.conf import settings

from .processing import DocumentError


class DocxConverter:
    errors = {
        "invalid_docx": "This is not a valid, supported DOCX. Save a fresh .docx in Word or LibreOffice, or export a PDF.",
        "active_content": "This document contains macros, embedded objects, or active content. Remove them or export a PDF.",
        "external_content": "This document contains external links or linked resources. Embed resources and remove links, or export a PDF.",
        "complex_docx": "This DOCX exceeds conversion limits. Simplify its images/content or export a PDF up to 10 MiB.",
        "conversion_failed": "DOCX conversion failed. Retry, or export a PDF from Word or LibreOffice and upload it.",
    }

    def convert(self, source, destination):
        name = f"astra-docx-{uuid.uuid4()}"
        command = [
            settings.AGREEMENT_DOCKER_COMMAND, "run", "--rm", "--pull=never", "--name", name,
            "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--memory=512m", "--memory-swap=512m", "--cpus=1", "--pids-limit=64",
            "--ulimit", "nofile=256:256", "--ulimit", "fsize=16777216:16777216",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=201326592,mode=1777",
            "--user=65534:65534", "--log-driver=none", "-i", settings.AGREEMENT_DOCX_IMAGE,
        ]
        try:
            with source.open("rb") as original, tempfile.TemporaryFile() as output:
                result = subprocess.run(
                    command, stdin=original, stdout=output, stderr=subprocess.DEVNULL,
                    timeout=settings.AGREEMENT_DOCX_TIMEOUT, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                output.seek(0)
                header = output.readline(1024)
                try:
                    metadata = json.loads(header)
                    if not isinstance(metadata, dict):
                        raise ValueError
                except (ValueError, UnicodeError) as error:
                    raise DocumentError(
                        "DOCX conversion is unavailable. Ask your administrator to build/start the conversion runtime, then retry; or upload a PDF.",
                        "conversion_unavailable",
                    ) from error
                if result.returncode or "error" in metadata:
                    code = metadata.get("error", "conversion_failed")
                    raise DocumentError(self.errors.get(code, self.errors["conversion_failed"]), code)
                version = metadata.get("version")
                if not isinstance(version, str) or not version.startswith("LibreOffice ") or len(version) > 100:
                    raise DocumentError(self.errors["conversion_failed"], "conversion_failed")
                pdf = output.read(settings.AGREEMENT_MAX_BYTES + 1)
                if not pdf or len(pdf) > settings.AGREEMENT_MAX_BYTES:
                    raise DocumentError(self.errors["complex_docx"], "complex_docx")
                destination.write_bytes(pdf)
                return "LibreOffice", version
        except subprocess.TimeoutExpired as error:
            raise DocumentError("DOCX conversion took too long. Retry or export a simpler PDF.", "conversion_timeout") from error
        except OSError as error:
            raise DocumentError("DOCX conversion is unavailable. Start the conversion runtime and retry, or upload a PDF.", "conversion_unavailable") from error
        finally:
            # Also kill the worker after a disconnected/timed-out Docker client.
            try:
                subprocess.run(
                    [settings.AGREEMENT_DOCKER_COMMAND, "rm", "-f", name],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
