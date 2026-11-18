#!/usr/bin/env bash
# Consistent maintenance snapshot, containing credentials: keep it private.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
    echo 'Run this script as root on the deployment VM.' >&2
    exit 1
fi
exec 9>/run/astra-backup.lock
flock -n 9 || { echo 'A backup is already running.' >&2; exit 1; }
umask 077
install -d -m 0700 /var/backups/astra
work=$(mktemp -d /var/backups/astra/.working.XXXXXX)
was_running=0
systemctl is-active --quiet astra.service && was_running=1
finish() {
    rm -f -- "$work/database.dump" "$work/snapshot.tar.gz"
    rmdir -- "$work"
    if [[ $was_running == 1 ]]; then systemctl start astra.service; fi
}
trap finish EXIT
systemctl stop astra.service
runuser -u postgres -- pg_dump --format=custom astra > "$work/database.dump"
tar -czf "$work/snapshot.tar.gz" -C "$work" database.dump \
    -C /var/lib/astra agreements -C /etc astra
destination="/var/backups/astra/astra-$(date -u +%Y%m%dT%H%M%S)-$$.tar.gz"
mv -- "$work/snapshot.tar.gz" "$destination"
echo "Saved private snapshot: $destination"
echo 'Copy it off the VM. No existing backups or agreement files were deleted.'
