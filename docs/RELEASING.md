# Release checklist

The v1.1.1 candidate contains packaging and documentation changes. Its numerical
solver, bundled circuits and frozen reference fixtures match v1.1.0. Existing
experimental features keep their experimental status. See [supported scope](SUPPORTED_SCOPE.md).

## Build and check

Use an isolated Python environment and a clean release checkout:

```sh
python -m pip install build
python -m build
```

This produces `dist/dpspice-1.1.1-py3-none-any.whl` and
`dist/dpspice-1.1.1.tar.gz`. In a **second clean environment**, install the wheel:

```sh
python -m pip install "./dist/dpspice-1.1.1-py3-none-any.whl[cli]"
python scripts/release_smoke.py --expected-version 1.1.1 --out smoke-wheel
```

Repeat with the source distribution in another clean environment. The smoke
script checks the installed entry point, bundled examples, waveform exports,
all three Table V rows against the unchanged golden tolerances, and a clean
unsupported-device refusal. Each output directory must be new. This is not
proof of arbitrary SPICE or vendor-model compatibility.

The existing CI tests the numerical regression suite on Ubuntu. The additional
`Release package checks` workflow tests both artifacts on Linux, macOS and
Windows using Python 3.13. A workflow definition is not a passing run: check the
actual results on the release commit before claiming platform verification.

## Publish only after review

1. Review the exact diff against v1.1.0. Do not import a research working tree.
2. Check license, citation, version consistency, README commands, package contents
   and file hashes. Keep the paper baseline and existing release tags immutable.
3. Run regression and installed-artifact checks. Preserve failures and skips.
4. Obtain maintainer approval for pushing the release branch and opening its PR.
5. After approved integration and green checks on the selected commit, create
   tag `v1.1.1` and a **draft** GitHub release. Attach the wheel, source archive and
   `SHA256SUMS.txt`; use the reviewed release notes.
6. Verify draft assets and checksums against that same commit, then publish with
   maintainer approval. Confirm the pinned quickstart command works anonymously.
7. Verify the archival record separately before claiming the new version is on
   Zenodo. The concept DOI alone does not establish that this version is archived.

The workflow does not publish releases, push tags, deploy the website or upload
to PyPI. PyPI publication is a separate maintainer decision. Browser engine and
CLI versions are tracked separately; do not replace the live website as a side
effect of a package release.
