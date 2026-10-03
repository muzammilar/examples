#!/bin/sh
# Installs the kdb+ license into $QLIC, then runs the command (q ...).
# Sources, first match wins:
#   KDB_LICENSE_B64     base64 kc.lic (e.g. the KDB-X Community Edition key from the KX welcome email)
#   KDB_LICENSE_K4B64   base64 k4.lic (commercial kdb+)
#   /license/kc.lic or /license/k4.lic (kdb/license/, mounted read-only, gitignored)
set -eu
mkdir -p "$QLIC"
if [ -n "${KDB_LICENSE_B64:-}" ]; then
	printf '%s' "$KDB_LICENSE_B64" | base64 -d >"$QLIC/kc.lic"
elif [ -n "${KDB_LICENSE_K4B64:-}" ]; then
	printf '%s' "$KDB_LICENSE_K4B64" | base64 -d >"$QLIC/k4.lic"
elif [ -f /license/kc.lic ]; then
	cp /license/kc.lic "$QLIC/kc.lic"
elif [ -f /license/k4.lic ]; then
	cp /license/k4.lic "$QLIC/k4.lic"
else
	echo "kdb+: no license. Set KDB_LICENSE_B64 (base64 kc.lic) / KDB_LICENSE_K4B64 (base64 k4.lic)" >&2
	echo "kdb+: or put kc.lic / k4.lic in kdb/license/; how to get a free key: kdb/README.md#license-required-before-running" >&2
	exit 64
fi
exec "$@"
