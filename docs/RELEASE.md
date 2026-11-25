# NoxPy Release Procedure (Phase-by-Phase)

This document defines the normative CI and release workflow for [`https://github.com/mburakmmm/noxPY`](https://github.com/mburakmmm/noxPY), implementing [`AGENTS.md`](../AGENTS.md) §19–§20.

---

## 1. Phase-to-Version Mapping

| Phase | Milestone | Tag / PEP 440 Version Pattern | GitHub Release | PyPI (`noxpy`) |
|---|---|---|---|---|
| **Phase 0** | Compiler/Runtime Feasibility & `abi3t` Shim | `v0.0.1a0`, `v0.0.x*` | **Pre-release** (`prerelease: true`) | **Blocked** (`pypi_allowed: false`) |
| **Phase 1** | `0.1 alpha`: Functions & Wheel | `v0.1.0a1` .. `v0.1.0` | Pre-release / Release | Allowed via OIDC after Phase 0 exit |
| **Phase 2** | `0.2`: Types, Stubs & Packaging | `v0.2.0a1` .. `v0.2.0` | Pre-release / Release | Allowed via OIDC |
| **Phase 3** | `0.3`: Native Objects & Classes | `v0.3.0a1` .. `v0.3.0` | Pre-release / Release | Allowed via OIDC |
| **Phase 4** | `0.4`: Callbacks, Async & Isolation | `v0.4.0a1` .. `v0.4.0` | Pre-release / Release | Allowed via OIDC |
| **Phase 5** | `0.5`: Native Data, Buffers & Atlas | `v0.5.0a1` .. `v0.5.0` | Pre-release / Release | Allowed via OIDC |
| **Phase 6** | `0.6`: Arrow, Noxen & Optional Embed | `v0.6.0a1` .. `v0.6.0` | Pre-release / Release | Allowed via OIDC |
| **Phase 7** | `1.0` Stable | `v1.0.0` | Stable Release | Allowed via OIDC |

`scripts/release/check_release_gate.py` enforces this mapping automatically:
- Any `v0.0.*` tag is forced to `prerelease = true` and `pypi_allowed = false`.
- Any `v0.1.0+` tag is **blocked** if `TODO.md` section `0.4 Mandatory Phase 0 Exit Gates` still contains unchecked `[ ]` items.

---

## 2. Immutable Artifact & Security Rules

1. **SHA-pinned actions:** Every `uses:` in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) and [`.github/workflows/release.yml`](../.github/workflows/release.yml) is pinned to a 40-hex commit SHA (verified by `tests/unit/test_workflows.py`).
2. **Never rebuild between audit and publish:** `build-artifacts` builds once with `SOURCE_DATE_EPOCH`, records `SHA256SUMS.txt` + `release-manifest.json`, and uploads `dist-artifacts`. `verify-immutable-artifacts`, `publish-github-release`, and `publish-pypi` download and verify those exact hashes.
3. **Zero PR credential exposure:** `release.yml` triggers ONLY on `push.tags: ["v*"]` or manual `workflow_dispatch` (defaulting `dry_run: true`). Protected GitHub Environments (`github-release`, `pypi`) isolate publication credentials.
4. **Never reuse a tag:** If a published release has a defect, cut a new patch tag (`vX.Y.Z+1`); never delete or force-move a published tag.

---

## 3. GitHub Repository Setup (One-Time Maintainer Action)

1. In GitHub repository **Settings -> Environments**:
   - Create environment `github-release` (recommended: require maintainer review).
   - Create environment `pypi` (required: maintainer review + OIDC Trusted Publisher configured for `mburakmmm/noxPY`, workflow `release.yml`, environment `pypi` once Phase 1+ PyPI distribution name ownership is confirmed).
2. Enable **Branch protection** on `main` requiring the `CI Gate Summary` check.

---

## 4. Step-by-Step Phase Release Checklist

1. Confirm all phase exit criteria in [`TODO.md`](../TODO.md) and [`AGENTS.md`](../AGENTS.md) §19 are genuinely satisfied.
2. Record exact `noxc` version/commit, SHA-256 digests, Python `3.15` & `3.15t` test outputs, and ABI audit results in `docs/evidence/<phase>-YYYY-MM-DD.md`.
3. Update version identically in [`pyproject.toml`](../pyproject.toml) and [`python/noxpy/__init__.py`](../python/noxpy/__init__.py), and add `## [<version>]` notes to [`CHANGELOG.md`](../CHANGELOG.md).
4. Run the local dry-run release gate:
   ```sh
   python3.15 scripts/release/prepare_phase_release.py --tag v0.1.0a1 --dry-run
   ```
5. Optionally trigger **Release (`workflow_dispatch`, `dry_run: true`)** in GitHub Actions to validate wheel/sdist build + clean `3.15` & `3.15t` installation.
6. With maintainer approval, tag and push:
   ```sh
   git tag -a v0.1.0a1 -m "NoxPy v0.1.0a1"
   git push origin main --follow-tags
   ```
