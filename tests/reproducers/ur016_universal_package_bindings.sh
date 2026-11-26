#!/usr/bin/env bash
set -euo pipefail

# Reproducer for UR-016:
#   UR-016.1: Package-relative intra-package import resolution (`import errors` inside dependency)
#   UR-016.2: Compiler-owned public API discovery (`noxc expand --public-api --json`)
#
# Exits 0 only once upstream `noxc` satisfies both UR-016.1 and UR-016.2.

NOXC="${NOXC:-$(command -v noxc || echo "$HOME/.nox-lang/bin/noxc")}"
if [[ ! -x "$NOXC" ]]; then
  echo "[UR-016] ERROR: noxc not found ($NOXC)" >&2
  exit 2
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PKG_A="$TMP/pkg_a"
PKG_B="$TMP/pkg_b"
mkdir -p "$PKG_A" "$PKG_B/src"

cat > "$PKG_A/nox.json" <<'EOF'
{
  "name": "pkg_a",
  "version": "0.1.0",
  "source": "nox",
  "entry": "main.nox"
}
EOF

cat > "$PKG_A/errors.nox" <<'EOF'
class PkgError(Exception):
    pass

def fail_msg(tag: str) -> str:
    return "err:" + tag
EOF

cat > "$PKG_A/values.nox" <<'EOF'
import errors
from errors import fail_msg

def checked_label(tag: str) -> str:
    if len(tag) == 0:
        raise errors.PkgError("empty")
    return fail_msg(tag)
EOF

cat > "$PKG_A/main.nox" <<'EOF'
import values
print(values.checked_label("ok"))
EOF

git -C "$PKG_A" init -q
git -C "$PKG_A" config user.email "repro@noxpy.dev"
git -C "$PKG_A" config user.name "NoxPy Repro"
git -C "$PKG_A" add -A
git -C "$PKG_A" commit -q -m "pkg_a initial"
REV_A="$(git -C "$PKG_A" rev-parse HEAD)"

cat > "$PKG_B/nox.json" <<EOF
{
  "name": "pkg_b",
  "version": "0.1.0",
  "source": "nox",
  "entry": "src/lib.nox",
  "requires": [
    {
      "alias": "pkg_a",
      "repo": "$PKG_A",
      "ref": "$REV_A",
      "require_signed_commit": false
    }
  ]
}
EOF

cat > "$PKG_B/src/lib.nox" <<'EOF'
import pkg_a.values

@export
def wrap_label(tag: str) -> str:
    return pkg_a.values.checked_label(tag)
EOF

(cd "$PKG_B" && "$NOXC" fetch >/dev/null 2>&1)

FAIL=0

echo "[UR-016.1] Checking package-relative import resolution across dependency boundary..."
if OUT_16_1="$(cd "$PKG_B" && "$NOXC" check src/lib.nox 2>&1)"; then
  echo "[UR-016.1] PASS: package-relative intra-package import resolved cleanly."
else
  echo "[UR-016.1] OPEN: consumer failed resolving intra-package import inside dependency:"
  echo "$OUT_16_1" | sed 's/^/  /'
  FAIL=1
fi

echo "[UR-016.2] Checking compiler-owned public API discovery (noxc expand --public-api --json)..."
if OUT_16_2="$(cd "$PKG_A" && "$NOXC" expand --public-api --json values.nox 2>&1)"; then
  echo "[UR-016.2] PASS: noxc expand --public-api --json succeeded."
else
  echo "[UR-016.2] OPEN: noxc expand --public-api --json not yet supported:"
  echo "$OUT_16_2" | sed 's/^/  /'
  FAIL=1
fi

exit "$FAIL"
