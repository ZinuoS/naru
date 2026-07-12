"""naru: deterministic pipeline artifacts for messy financial data."""

from importlib.metadata import PackageNotFoundError, version

try:
    # "naru-data" is the PyPI distribution name (pyproject.toml's own
    # comment explains why) -- importlib.metadata resolves by distribution
    # name, not import name, so this must match [project] name exactly.
    __version__ = version("naru-data")
except PackageNotFoundError:  # pragma: no cover -- only when naru isn't installed at all
    __version__ = "0.0.0+unknown"
