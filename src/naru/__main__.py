"""Entry point for `python -m naru <command> ...`.

The real CLI lives in naru.cli (typer-based); this module just makes
`python -m naru` an alternative to the installed `naru` console script
(see pyproject.toml's [project.scripts]). See docs/exit_codes.md for the
exit-code scheme.
"""

from naru.cli import app

__all__ = ["app"]

if __name__ == "__main__":  # pragma: no cover
    app()
