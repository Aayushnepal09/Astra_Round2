"""Disposable, offline DOCX conversion. Only stdin/stdout cross the boundary."""

import io
import json
import os
import re
import signal
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET


MAX_BYTES = 10 * 1024 * 1024
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


class InvalidDocx(Exception):
    pass


def validate(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            entries = package.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 1000 or len(set(names)) != len(names):
                raise InvalidDocx("complex_docx")
            if sum(entry.file_size for entry in entries) > 64 * 1024 * 1024:
                raise InvalidDocx("complex_docx")
            roots = {}
            for entry in entries:
                name = entry.filename
                if (entry.flag_bits & 1 or entry.file_size > 16 * 1024 * 1024
                        or entry.file_size > max(1, entry.compress_size) * 200):
                    raise InvalidDocx("complex_docx")
                if (name.startswith("/") or "\\" in name or ":" in name
                        or ".." in PurePosixPath(name).parts):
                    raise InvalidDocx("invalid_docx")
                if any(part in name.lower() for part in (
                    "vbaproject", "vba", "activex", "embeddings/", "customui/",
                )):
                    raise InvalidDocx("active_content")
                content = package.read(entry)  # Enforce CRC as well as declared size.
                if name.endswith((".xml", ".rels")):
                    xml = content.decode("utf-8-sig")
                    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
                        raise InvalidDocx("active_content")
                    root = ET.fromstring(xml)
                    roots[name] = root
                    if name.endswith(".rels"):
                        if root.tag != f"{{{REL}}}Relationships":
                            raise InvalidDocx("invalid_docx")
                        for link in root:
                            if link.get("TargetMode", "Internal") != "Internal":
                                raise InvalidDocx("external_content")
                            target = link.get("Target", "")
                            if ":" in target or target.startswith(("//", "\\")):
                                raise InvalidDocx("external_content")
                    for node in root.iter():
                        local = node.tag.rsplit("}", 1)[-1]
                        if local in ("altChunk", "object", "control", "attachedTemplate"):
                            raise InvalidDocx("active_content")
                    instructions = " ".join(
                        (node.text or "") if node.tag == f"{{{WORD}}}instrText"
                        else node.get(f"{{{WORD}}}instr", "")
                        for node in root.iter()
                    )
                    if re.search(r"\b(DDEAUTO|DDE|INCLUDETEXT|INCLUDEPICTURE|LINK|DATABASE)\b", instructions, re.I):
                        raise InvalidDocx("external_content")
            types = roots.get("[Content_Types].xml")
            document = roots.get("word/document.xml")
            relationships = roots.get("_rels/.rels")
            if types is None or document is None or relationships is None:
                raise InvalidDocx("invalid_docx")
            if (document.tag != f"{{{WORD}}}document"
                    or document.find(f"{{{WORD}}}body") is None
                    or not any(node.get("PartName") == "/word/document.xml"
                               and node.get("ContentType") == MAIN_TYPE for node in types)
                    or not any(node.get("Type", "").endswith("/officeDocument")
                               and node.get("Target", "").lstrip("/") == "word/document.xml"
                               for node in relationships)):
                raise InvalidDocx("invalid_docx")
            if any(re.search(r"macro|vba|activex|oleobject", node.get("ContentType", ""), re.I) for node in types):
                raise InvalidDocx("active_content")
    except (zipfile.BadZipFile, KeyError, UnicodeError, ET.ParseError, RuntimeError, NotImplementedError, ValueError) as error:
        raise InvalidDocx("invalid_docx") from error


def main():
    # Stop the whole container even if the caller disappears.
    signal.signal(signal.SIGALRM, lambda *_: os._exit(3))
    signal.alarm(48)
    try:
        data = sys.stdin.buffer.read(MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            raise InvalidDocx("complex_docx")
        validate(data)
        work = Path("/tmp/work")
        work.mkdir(mode=0o700)
        profile = Path("/tmp/profile/user")
        profile.mkdir(parents=True, mode=0o700)
        (profile / "registrymodifications.xcu").write_text('''<?xml version="1.0"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
</oor:items>''', encoding="utf-8")
        source = work / "agreement.docx"
        source.write_bytes(data)
        version = subprocess.check_output(["libreoffice", "--version"], timeout=5).decode().strip()
        result = subprocess.run([
            "libreoffice", "-env:UserInstallation=file:///tmp/profile", "--headless",
            "--nologo", "--nodefault", "--norestore", "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", str(work), str(source),
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
        pdf = work / "agreement.pdf"
        if result.returncode or not pdf.is_file():
            raise InvalidDocx("conversion_failed")
        if not 0 < pdf.stat().st_size <= MAX_BYTES:
            raise InvalidDocx("complex_docx")
        sys.stdout.buffer.write(json.dumps({"version": version[:100]}).encode() + b"\n")
        sys.stdout.buffer.write(pdf.read_bytes())
    except InvalidDocx as error:
        print(json.dumps({"error": str(error)}))
        return 2
    except Exception:
        print(json.dumps({"error": "conversion_failed"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
