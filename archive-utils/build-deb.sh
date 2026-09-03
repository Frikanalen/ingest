#!/usr/bin/env bash
#
# Build the fk-archive-utils .deb. Used by CI and by anyone who wants to try
# the package on a Debian box; the same script either way, so what the release
# contains is what a developer can reproduce.
#
#   ./build-deb.sh                 -> fk-archive-utils_0.0.0~dev_all.deb
#   ./build-deb.sh 1.4.0           -> fk-archive-utils_1.4.0_all.deb
#
# debian/changelog is generated rather than committed. Debian's version comes
# from the release tag, and a changelog kept by hand alongside a tag decided by
# release-please is a changelog that will eventually claim the wrong version.
set -euo pipefail

cd "$(dirname "$0")"

version="${1:-0.0.0~dev}"
distribution="${DEB_DISTRIBUTION:-trixie}"

# The leading `v` release-please puts on its tags is not part of a Debian
# version, and dpkg would read it as an epoch-less oddity rather than an error.
version="${version#v}"

mkdir -p dist
cat > debian/changelog <<EOF
fk-archive-utils (${version}) ${distribution}; urgency=medium

  * Built from $(git rev-parse --short HEAD 2>/dev/null || echo "an unversioned tree").

 -- Frikanalen <post@frikanalen.no>  $(date -R)
EOF

dpkg-buildpackage -us -uc -b

# dpkg-buildpackage writes to the parent directory, which here is the ingest
# repository root. Collect the artefacts somewhere that is ignored instead.
mv ../fk-archive-utils_"${version}"_*.deb dist/
mv ../fk-archive-utils_"${version}"_*.buildinfo ../fk-archive-utils_"${version}"_*.changes dist/ 2>/dev/null || true

ls -l dist/
