# Releasing

`.github/workflows/release.yml` builds, tests, and publishes on any tag
matching `v*` — a tag containing `rc` (e.g. `v0.1.0rc1`) goes to
TestPyPI only; anything else (e.g. `v0.1.0`) goes to real PyPI. Both
publish steps use [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC) — no API token is stored in this repo, but a Trusted Publisher
has to be configured on each index, for this exact repo and workflow
filename, before the corresponding job can succeed. That setup is a
one-time, account-holder-only action — the numbered steps below are for
whoever holds (or is creating) the PyPI/TestPyPI accounts for this
project.

## One-time setup (do this once, before the first release tag)

1. Create a PyPI account at <https://pypi.org/account/register/>, if you
   don't already have one. Enable two-factor authentication:
   Account settings → Add 2FA — PyPI requires 2FA for anyone who
   manages a project, so do this before step 3, not after.
2. Create a **separate** TestPyPI account at
   <https://test.pypi.org/account/register/> (TestPyPI accounts are
   entirely separate from PyPI's — a PyPI login does not carry over).
   Enable 2FA there too: Account settings → Add 2FA.
3. On PyPI, configure a Trusted Publisher for this project *before it
   exists there yet* (PyPI supports pre-registering a Trusted Publisher
   for a project name that hasn't been published under your account
   yet — this is exactly that case):
   - Go to <https://pypi.org/manage/account/publishing/>
   - Under "Add a new pending publisher," fill in:
     - PyPI Project Name: `naru`
     - Owner: `ZinuoS`
     - Repository name: `naru`
     - Workflow filename: `release.yml`
     - Environment name: `pypi`
   - Submit. This creates a *pending* publisher that activates the
     first time the workflow successfully publishes.
4. Repeat step 3 on TestPyPI at
   <https://test.pypi.org/manage/account/publishing/>, with the same
   values except:
     - Environment name: `testpypi`
5. Optional, recommended: on GitHub, go to this repo's
   Settings → Environments, open (or create) the `pypi` environment, and
   add yourself as a required reviewer. This makes the real-PyPI publish
   job pause for manual approval every time, even after the Trusted
   Publisher is fully configured — a deliberate pause point before
   anything reaches the real index. Do the same for `testpypi` if you
   want the same pause there too (lower stakes, so optional).

## Tagging a release candidate

Once step 3 above (the PyPI *pending* Trusted Publisher, step 3) and
step 4 (TestPyPI) are both done:

```bash
git checkout main
git pull
git tag v0.1.0rc1
git push origin v0.1.0rc1
```

Watch the "Release" workflow run under this repo's Actions tab. If
`publish-testpypi` succeeds, verify the install works from TestPyPI
(see PREFLIGHT.md's pre-flight checklist for the exact command) before
tagging the real release.

## Tagging the real release

Only after the release-candidate install has been verified:

```bash
git checkout main
git pull
git tag v0.1.0
git push origin v0.1.0
```

This triggers `publish-pypi` instead of `publish-testpypi` (no `rc` in
the tag name). Verify a cold `pip install naru` works on a second
machine, then create the GitHub Release with hand-written notes
(Releases → Draft a new release → choose the `v0.1.0` tag) — this last
step is manual on purpose; nobody should read auto-generated release
notes for a v0.1.0.
