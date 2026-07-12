# Releasing

`.github/workflows/release.yml` builds, tests, and publishes on any tag
matching `v*` — a tag containing `rc` (e.g. `v0.1.0rc1`) goes to
TestPyPI only; anything else (e.g. `v0.1.0`) goes to real PyPI. Both
publish steps use [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC) — no API token is stored in this repo, but a Trusted Publisher
has to be configured on each index first, for this exact repo
(`ZinuoS/naru-data`), workflow filename (`release.yml`), and environment
name (`pypi` / `testpypi`), before the corresponding job can succeed.
That's a one-time, account-holder-only action on pypi.org and
test.pypi.org (Account settings → Publishing) — not something this repo
can configure for itself.

## Tagging a release candidate

Once the Trusted Publisher is configured on both PyPI and TestPyPI:

```bash
git checkout main
git pull
git tag v0.1.0rc1
git push origin v0.1.0rc1
```

Watch the "Release" workflow run under this repo's Actions tab. If
`publish-testpypi` succeeds, verify the install works from TestPyPI
before tagging the real release (TestPyPI doesn't mirror PyPI's other
packages, so dependencies have to come from the real index):

```bash
pip install -i https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ naru-data
naru --version
```

## Tagging the real release

Only after the release-candidate install has been verified:

```bash
git checkout main
git pull
git tag v0.1.0
git push origin v0.1.0
```

This triggers `publish-pypi` instead of `publish-testpypi` (no `rc` in
the tag name). Verify a cold `pip install naru-data` works on a second
machine, then create the GitHub Release with hand-written notes
(Releases → Draft a new release → choose the `v0.1.0` tag) — this last
step is manual on purpose; nobody should read auto-generated release
notes for a v0.1.0.
