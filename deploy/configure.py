"""One-time server configuration; never run against the Windows checkout."""

import grp
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys


def main():
    if os.geteuid() != 0 or len(sys.argv) != 3:
        raise SystemExit("Run as root: python3 deploy/configure.py HOSTNAME LOCAL_IMAGE_SHA256")
    hostname, image = sys.argv[1:]
    labels = hostname.split(".")
    if (len(labels) < 2 or len(hostname) > 253 or hostname.endswith(".invalid")
            or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
        raise SystemExit("Supply the real lowercase public DNS hostname.")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise SystemExit("Supply the immutable locally built converter image ID.")
    destination = Path("/etc/astra/astra.env")
    if destination.exists():
        raise SystemExit("Configuration already exists; refusing to replace credentials.")
    password = secrets.token_hex(32)
    template = Path(__file__).with_name("astra.env.example").read_text()
    content = (template.replace("REPLACE_WITH_RANDOM_SECRET", secrets.token_hex(64))
               .replace("REPLACE_WITH_RANDOM_DATABASE_PASSWORD", password)
               .replace("REPLACE_WITH_LOCAL_IMAGE_SHA256", image)
               .replace("astra.example.invalid", hostname))
    # Keep the new credentials recoverable if a subsequent provisioning step fails.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(destination, 0, grp.getgrnam("astra").gr_gid)
    sql = (
        "SELECT 'CREATE ROLE astra LOGIN' WHERE NOT EXISTS "
        "(SELECT FROM pg_roles WHERE rolname = 'astra')\\gexec\n"
        f"ALTER ROLE astra WITH PASSWORD '{password}';\n"
        "SELECT 'CREATE DATABASE astra OWNER astra' WHERE NOT EXISTS "
        "(SELECT FROM pg_database WHERE datname = 'astra')\\gexec\n"
    )
    result = subprocess.run(
        ["runuser", "-u", "postgres", "--", "psql", "-v", "ON_ERROR_STOP=1", "postgres"],
        input=sql, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise SystemExit("Database setup failed. Credentials are preserved in /etc/astra/astra.env; inspect PostgreSQL as administrator.")
    caddy = Path(__file__).with_name("Caddyfile").read_text().replace("astra.example.invalid", hostname)
    Path("/etc/caddy/Caddyfile").write_text(caddy)
    print("Created server configuration and database. No credentials were printed.")


if __name__ == "__main__":
    main()
