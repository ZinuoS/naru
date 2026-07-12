"""Smoke test confirming the package imports and the test harness runs."""

import naru


def test_package_has_version() -> None:
    """The package exposes a version string, read from installed package
    metadata (importlib.metadata) rather than hand-duplicated here --
    otherwise this exact assertion goes stale on every version bump, as
    it already had (pyproject.toml said 0.1.0; this string still said
    0.0.1.dev0).
    """
    assert naru.__version__
    assert naru.__version__ != "0.0.0+unknown"
