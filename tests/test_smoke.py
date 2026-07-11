"""Smoke test confirming the package imports and the test harness runs."""

import naru


def test_package_has_version() -> None:
    """The package exposes a version string.

    >>> naru.__version__
    '0.0.1.dev0'
    """
    assert naru.__version__ == "0.0.1.dev0"
