#!/usr/bin/env bash
# First installation on the dedicated evaluation VM; never runs from Windows.
set -euo pipefail
if [[ $EUID -ne 0 || $# -ne 1 ]]; then
    echo 'Usage: sudo bash /opt/astra/app/deploy/install.sh PUBLIC_HOSTNAME' >&2
    exit 1
fi
source /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 || $(uname -m) != aarch64 ]]; then
    echo 'This installer targets Ubuntu 24.04 Arm64 only.' >&2
    exit 1
fi
if [[ $(realpath "$(dirname "$0")/..") != /opt/astra/app || -e /etc/astra/astra.env ]]; then
    echo 'Extract into /opt/astra/app first. Existing configuration requires a manual update.' >&2
    exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv python3-pip postgresql postgresql-client docker.io \
    fonts-dejavu-core fonts-liberation ca-certificates curl gpg \
    debian-keyring debian-archive-keyring apt-transport-https
# Official stable Caddy packages include the request-body limiter used here.
curl --fail --silent --show-error --location https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl --fail --silent --show-error --location https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    -o /etc/apt/sources.list.d/caddy-stable.list
chmod 0644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get install -y caddy
systemctl stop caddy
systemctl enable --now postgresql docker
id astra >/dev/null 2>&1 || useradd --system --home-dir /var/lib/astra --shell /usr/sbin/nologin astra
usermod -aG docker astra
install -d -o root -g astra -m 0750 /etc/astra
install -d -o astra -g astra -m 0700 /var/lib/astra /var/lib/astra/agreements
install -d -o astra -g astra -m 0755 /var/www/astra /var/www/astra/static
chown -R root:root /opt/astra/app
chmod -R go-w /opt/astra/app
python3 -m venv /opt/astra/venv
/opt/astra/venv/bin/python -m pip install --disable-pip-version-check -r /opt/astra/app/backend/requirements-production.txt
docker build -t astra-docx:deployment /opt/astra/app/backend/agreements/converter
image_id=$(docker image inspect --format '{{.Id}}' astra-docx:deployment)
python3 /opt/astra/app/deploy/configure.py "$1" "$image_id"
systemd-run --quiet --wait --pipe --collect --uid=astra --gid=astra \
    --working-directory=/opt/astra/app/backend --property=EnvironmentFile=/etc/astra/astra.env \
    /opt/astra/venv/bin/python manage.py migrate --noinput
systemd-run --quiet --wait --pipe --collect --uid=astra --gid=astra \
    --working-directory=/opt/astra/app/backend --property=EnvironmentFile=/etc/astra/astra.env \
    /opt/astra/venv/bin/python manage.py collectstatic --noinput
# Public static assets must be readable by Caddy, never the private data tree.
chmod -R a+rX /var/www/astra/static
install -m 0644 /opt/astra/app/deploy/astra.service /etc/systemd/system/astra.service
systemctl daemon-reload
echo 'Installation prepared. No Astra application service has been started.'
echo 'Set the Free-plan Groq key in /etc/astra/astra.env and provision the company account before activation.'
