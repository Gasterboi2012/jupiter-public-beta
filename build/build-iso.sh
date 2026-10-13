#!/usr/bin/env bash
# Build the Jupiter live installer ISO.
#
# Must run on a Fedora host (or a Fedora container/VM) as root, with
# lorax-lmc-novirt and pykickstart installed:
#   sudo dnf install lorax-lmc-novirt pykickstart
#   sudo ./build/build-iso.sh
# Output: build/out/Jupiter-<version>-x86_64.iso
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/out"
WORK="$(mktemp -d /var/tmp/jupiter-build.XXXX)"
trap 'rm -rf "$WORK"' EXIT

if [[ $EUID -ne 0 ]]; then
  echo "build-iso.sh must run as root" >&2
  exit 1
fi

for tool in livemedia-creator ksflatten ksvalidator base64; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

VERSION="${JUPITER_VERSION:-0.1.0}"
KS="$WORK/jupiter-live.ks"

# Fill the kickstart template with the installer and service unit.
b64() { base64 -w0 "$1"; }
sed -e "s|@INSTALLER_B64@|$(b64 "$ROOT/installer/jupiter-installer.py")|" \
    -e "s|@SERVICE_B64@|$(b64 "$ROOT/installer/jupiter-installer.service")|" \
    "$ROOT/kickstart/jupiter-live.ks.in" > "$KS"

ksvalidator -v F44 "$KS"

# livemedia-creator refuses to run if --resultdir already exists, so let it
# create a fresh directory inside the work dir and move the ISO out afterwards.
RESULT="$WORK/result"
livemedia-creator \
  --make-iso \
  --no-virt \
  --ks "$KS" \
  --project "Jupiter" \
  --releasever 44 \
  --volid "JUPITER-44" \
  --iso-only \
  --iso-name "Jupiter-${VERSION}-x86_64.iso" \
  --tmp "$WORK/lmc" \
  --resultdir "$RESULT"

mkdir -p "$OUT"
mv "$RESULT/Jupiter-${VERSION}-x86_64.iso" "$OUT/"
echo "ISO written to $OUT/Jupiter-${VERSION}-x86_64.iso"
