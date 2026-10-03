#!/bin/sh
# Installs the KDB-X license into $QLIC, then runs the command (q ...).
# Sources, first match wins:
#   KDB_LICENSE_B64     base64 kc.lic (Community Edition, from the KX welcome email)
#   KDB_LICENSE_K4B64   base64 k4.lic (commercial)
#   /license/kc.lic or /license/k4.lic (kdb-x/license/, mounted read-only, gitignored)
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
	echo "kdb-x: no license. Set KDB_LICENSE_B64 (base64 kc.lic) or put kc.lic in kdb-x/license/;" >&2
	echo "kdb-x: a free Community Edition license comes from https://developer.kx.com/ (see kdb-x/README.md)" >&2
	exit 64
fi
exec "$@"
