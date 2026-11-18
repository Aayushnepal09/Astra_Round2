"""Build a source-only release, including current uncommitted application work."""

import hashlib
import json
import os
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "dist" / "astra-release.zip"
EXTENSIONS = {
    "backend": {".py", ".txt"},
    "frontend": {".html", ".css", ".js", ".svg", ".png", ".ico", ".woff", ".woff2"},
    "deploy": {".py", ".sh", ".service", ".md", ".example"},
}


def source_files():
    yield ROOT / "README.md"
    for folder, extensions in EXTENSIONS.items():
        for directory, children, names in os.walk(ROOT / folder, followlinks=False):
            children[:] = sorted(name for name in children if not name.startswith(".")
                                 and name not in {"__pycache__", "tests"}
                                 and not (Path(directory) / name).is_symlink())
            for name in sorted(names):
                path = Path(directory) / name
                if path.is_symlink():
                    raise ValueError(f"Release source cannot be a symlink: {path.relative_to(ROOT)}")
                if name.startswith((".", "test_")) or ".test." in name:
                    continue
                is_converter_build = path == ROOT / "backend" / "agreements" / "converter" / "Dockerfile"
                if path.suffix in extensions or is_converter_build or (folder == "deploy" and name == "Caddyfile"):
                    yield path


def main():
    OUTPUT.parent.mkdir(exist_ok=True)
    temporary = OUTPUT.with_suffix(".zip.partial")
    manifest = {}
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in source_files():
                data = path.read_bytes()
                # A second guard against accidentally hard-coded provider/private keys.
                if re.search(rb"gsk_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----", data):
                    raise ValueError(f"Possible secret in {path.relative_to(ROOT)}; release not written.")
                name = path.relative_to(ROOT).as_posix()
                archive.writestr(name, data.replace(b"\r\n", b"\n") if path.suffix in {
                    ".py", ".sh", ".txt", ".md", ".service", ".example", ".html", ".css", ".js"
                } else data)
                manifest[name] = hashlib.sha256(archive.read(name)).hexdigest()
            archive.writestr("release-manifest.json", json.dumps(manifest, indent=2) + "\n")
        temporary.replace(OUTPUT)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Created {OUTPUT} ({len(manifest)} source files; no local data or instructions).")


if __name__ == "__main__":
    main()
