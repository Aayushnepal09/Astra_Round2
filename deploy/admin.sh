#!/usr/bin/env bash
# Use the same identity and environment as the service for maintenance commands.
set -euo pipefail
if [[ $EUID -ne 0 || $# -lt 1 ]]; then
    echo 'Usage: sudo bash deploy/admin.sh manage.py|provision.py [arguments...]' >&2
    exit 1
fi
case "$1" in
    manage.py|provision.py) ;;
    *) echo 'Only Django management and company provisioning are supported.' >&2; exit 1 ;;
esac
exec systemd-run --unit="astra-admin-$$" --wait --pty --collect --uid=astra --gid=astra \
    --working-directory=/opt/astra/app/backend \
    --property=EnvironmentFile=/etc/astra/astra.env \
    --property=SupplementaryGroups=docker \
    /opt/astra/venv/bin/python "$@"
