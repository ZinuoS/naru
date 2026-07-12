"""naru: deterministic pipeline artifacts for messy financial data."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("naru")
except PackageNotFoundError:  # pragma: no cover -- only when naru isn't installed at all
    __version__ = "0.0.0+unknown"
