# Linux evaluation deployment

Prepared configuration for one Ubuntu 24.04 Arm64 or x86-64 VM, PostgreSQL on
localhost, Caddy HTTPS, Gunicorn, and the existing isolated DOCX/PDF processing.
Oracle Always Free and an unupgraded Google Cloud Free Trial are supported hosting
arrangements. Nothing in this folder creates cloud resources or upgrades a cloud
or AI account. Live deployment state is maintained separately from these installation instructions.

## Local release preparation

From the project root on Windows:

```powershell
.venv/Scripts/python.exe -m pip install -r backend/requirements-production.txt
.venv/Scripts/python.exe deploy/package.py
```

Gunicorn is Linux-only and is skipped on Windows. The PostgreSQL driver can be
installed on either platform. Development continues to use SQLite and the normal
local server; installing these packages does not migrate or replace local data.

The packager includes the current backend/frontend sources, migrations and
deployment files even when uncommitted. Its fixed source directories and file
types exclude private instructions, Git metadata, environment files, virtual
environments, database files, documents and tests. The example environment has
placeholders only. `dist/astra-release.zip` is ignored by Git. Review its manifest
before transfer; the secret-pattern guard is an extra check, not a secret scanner
that can prove every possible credential absent.

## Server prerequisites

- Use an unupgraded Free Tier account and Always Free resources in the home region.
  Confirm existing boot/block volumes plus the new volume remain within the free
  allocation. Trial credits and paid fallback are not part of this setup.
- Alternatively, use the explicitly agreed Google Cloud Free Trial with available
  trial credit and no paid-account upgrade. The VM, disk, IPv4 address and network
  traffic consume that credit; billing alerts are not a spending cap. A required
  verification prepayment is handled separately under Google's refund conditions.
- The prepared VCN needs its enabled internet gateway, `0.0.0.0/0` route, and
  regional public subnet. Assign the instance a public IPv4 address.
- Restrict ingress TCP 22 to the administrator's public IP (`/32` for IPv4).
  Permit ingress TCP 80 and 443 publicly. Keep 8000, 5432, Docker and Caddy's admin
  API closed publicly, in both OCI security rules and the host firewall.
- A real DNS hostname must point directly to the VM; a free DuckDNS subdomain is
  suitable. An IP-based sslip.io hostname can also be used without creating an
  account. That hostname changes if the VM's external address changes. Do not put
  a CDN or another proxy in front of this configuration.
- Keep the SSH private key outside the repository. Transfer files through SSH/SCP,
  never a public bucket, repository or anonymous download URL.

## First installation, after a VM becomes available

The following commands are for the Linux VM, not Windows. Transfer the release
archive to the SSH user's home first. Extract into a fresh application directory:

```bash
sudo install -d -m 0755 /opt/astra/app
sudo unzip -n "$HOME/astra-release.zip" -d /opt/astra/app
sudo bash /opt/astra/app/deploy/install.sh YOUR_REAL_HOSTNAME
```

If `unzip` is missing, install it with `sudo apt-get update` and
`sudo apt-get install -y unzip` first. Do not extract a new release over a running
installation; see updates below.

The installer targets Ubuntu 24.04 Arm64 or x86-64 and installs OS packages, the official
stable Caddy package, PostgreSQL, Docker Engine, Python packages and fonts. It
creates the `astra` service user and private directories, builds the converter,
and records its immutable local `sha256` image ID. The existing converter uses
`--pull=never`; document uploads do not download images. There is no paid
conversion or signing service.

It generates a persistent Django secret and database password on the server;
neither is printed, passed in command-line arguments, nor included in the source
archive. It creates a fresh `astra` PostgreSQL database, applies migrations and
collects static files. It refuses to overwrite an existing `/etc/astra/astra.env`.
This installer is for a dedicated VM; it is not a general upgrade or recovery tool.
If interrupted after configuration creation, inspect that state and finish the
remaining steps rather than deleting credentials and rerunning it blindly.

The service user has access to Docker to preserve per-upload container isolation.
That access is effectively host-administrator power, despite the unprivileged
converter containers. Keep the daemon local and the VM dedicated to this app;
never enable a public Docker TCP endpoint. Docker Engine on Linux does not require
Docker Desktop or a paid Docker account.

### Persistent paths

| Path | Contents / access |
| --- | --- |
| `/opt/astra/app` | Application source, root-owned, read-only to the service |
| `/opt/astra/venv` | Linux Python environment |
| `/etc/astra/astra.env` | Server secrets/settings, root:astra, 0640 |
| `/var/lib/astra/agreements` | Private originals, PDFs and artifacts; service only |
| `/var/lib/astra/agreement-processing` | Temporary upload/conversion files |
| `/var/www/astra/static` | Public collected assets only; Caddy may read |
| PostgreSQL database `astra` | Sessions, signing state/history, templates and AI budgets |

The filesystem adapter is retained, including atomic artifact publication. Do not
move private artifacts into the public static tree. Temporary source paths also
use persistent storage outside the application checkout.

## Configure access before activation

The installer does not start Astra. Edit `/etc/astra/astra.env` through a private
administrator session and set `GROQ_API_KEY` from a Free-plan account to enable the
existing document assistant. Keep the key out of terminal output, shell history,
public files and release packages. Never upgrade AI billing or configure a paid
fallback. An empty key leaves the assistant unavailable, while signing remains
independent; an empty key is not completion of the required AI setup.

Create the fictional company account using the existing password prompt:

```bash
sudo bash /opt/astra/app/deploy/admin.sh provision.py company \
    --email company@example.test --name "Demo Company"
```

This creates a fresh evaluation database. It does not automatically transfer the
developer's SQLite data or documents. If those must be retained on the server,
plan a matched database/document migration before issuing requests. Keep the
existing local database and documents intact.

For each release, verify Django deployment checks, PostgreSQL migrations,
DOCX conversion, native PDFium/font behavior, signing order, completed downloads,
and proxy/rate-limit behavior. Verify real AI access separately when authorized.
Migrations in the installer do not establish that these checks passed, and earlier
verification does not cover later source changes. After verification, activation uses:

```bash
sudo systemctl enable --now astra.service
sudo systemctl enable --now caddy.service
```

Caddy obtains and renews public certificates when DNS and ports are reachable.
Gunicorn binds only to `127.0.0.1:8000`, with two synchronous workers and a
150-second timeout for sequential document operations. At most two requests run
in this pool; a long operation can delay other requests. It does not introduce a
paid queue or simulate background success. PDF/DOCX process limits remain intact.
Systemd's 3 GB application limit covers application children, not Docker-managed
containers, which retain their separate limits. Suitability for 1 OCPU / 6 GB is
subject to verification on the target host.

`DJANGO_BEHIND_PROXY=1` requires loopback requests with a valid client IP and HTTPS
metadata. Caddy overwrites that metadata using the connected client address.
Django applies it before SecurityMiddleware and existing IP throttles. Do not
expose Gunicorn directly, trust arbitrary client-supplied forwarding headers, or
reuse this proxy policy behind a CDN without revisiting the trust boundary.
Development defaults leave proxy handling disabled.

Request access logging is disabled to avoid routine invitation URL logging.
Operational failures go to the service journal. Review logs privately and avoid
enabling debug output containing codes, document text, questions or keys.

## Updates, backup and recovery

Before updating source, migrations, secrets, or the converter, make a paired backup:

```bash
sudo bash /opt/astra/app/deploy/backup.sh
```

The backup briefly stops Astra, dumps PostgreSQL and archives private agreement
files plus `/etc/astra`, then restores the prior service running state. It does
not delete old backups or document history. Snapshots contain secrets and client
data: copy them off the VM to private local storage and never commit/upload them
to GitHub. A backup on the same VM does not protect against VM/disk loss. Keep
only the agreed small evaluation set of backups within available disk capacity;
no object-storage account or automatic paid expansion is configured here.

For an update: back up, stop Astra, preserve the old source directory, extract a
fresh release, install its production requirements, apply migrations and
collectstatic using the service environment, then restart. Never overwrite
`/etc/astra`, `/var/lib/astra`, or PostgreSQL data with a source release. Keep the
existing converter image referenced by configuration until an explicit image
update is ready. Rollback after a migration requires considering both database
and source versions; replacing source alone may be unsafe.

Recovery requires a compatible application release and converter, a fresh
PostgreSQL database restored using `pg_restore`, and the *matching* agreement tree
and server secret from the same snapshot. Restore directory ownership and 0700
private directory / 0600 artifact permissions, set the actual hostname and
available converter image ID, and perform the later agreed runtime checks before
activation. Do not combine the latest database with unrelated older document
files. Recovery has not been exercised during deferred testing.

For the end of the 7–10-day demo: take and download the private snapshot first.
Stop Astra/Caddy and the VM. Permanent removal of the instance, retained volumes,
backups, DNS and other resources is a separate explicit action. Free-tier
capacity and idle reclamation can affect availability during the demo; these
files do not guarantee uptime or arrange an automatic shutdown.

## Official references

- [Oracle Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Gunicorn settings](https://gunicorn.org/reference/settings/)
- [Caddy proxy headers](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- [Caddy installation](https://caddyserver.com/docs/install)
- [Psycopg installation](https://www.psycopg.org/psycopg3/docs/basic/install.html)
