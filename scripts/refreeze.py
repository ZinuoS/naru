#!/usr/bin/env python3
"""Refreeze an artifact's golden/expected_output.parquet from its current
transform() output against golden/input_sample.xlsx.

Refuses to run unless the artifact's CHANGELOG.md has been modified in
the same working tree (relative to HEAD, staged or unstaged, or newly
untracked) -- golden changes must be justified by a changelog entry,
enforced mechanically, not by convention.

Usage: python scripts/refreeze.py pipelines/<name>/<version>
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openpyxl import load_workbook  # noqa: E402

from naru.artifact import load_artifact  # noqa: E402
from naru.runtime import read_raw_grid  # noqa: E402


class RefreezeRefusedError(Exception):
    """The mechanical CHANGELOG-modified gate rejected this refreeze."""


def _changelog_modified_in_working_tree(changelog_path: Path) -> bool:
    """True if changelog_path differs from HEAD in the working tree --
    staged, unstaged, or untracked. `git status --porcelain` covers all
    three uniformly, unlike `git diff HEAD` (which misses untracked files).
    """
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", changelog_path.name],
        cwd=changelog_path.parent,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def refreeze(artifact_path: Path) -> Path:
    """Regenerate golden/expected_output.parquet; returns the path written.

    Raises RefreezeRefusedError if CHANGELOG.md wasn't modified.
    """
    changelog_path = artifact_path / "CHANGELOG.md"
    if not _changelog_modified_in_working_tree(changelog_path):
        raise RefreezeRefusedError(
            f"refusing to refreeze {artifact_path}: {changelog_path} was not modified "
            "in this working tree. Golden changes must be justified by a changelog entry -- "
            "add one, then re-run."
        )

    artifact = load_artifact(artifact_path)
    wb = load_workbook(artifact_path / "golden" / "input_sample.xlsx", data_only=True)
    raw_grid = read_raw_grid(wb, artifact.manifest.sheet)
    actual = artifact.transform(raw_grid)

    output_path = artifact_path / "golden" / "expected_output.parquet"
    actual.to_parquet(output_path, index=False)
    return output_path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/refreeze.py <artifact_path>", file=sys.stderr)
        return 1
    try:
        output_path = refreeze(Path(sys.argv[1]))
    except RefreezeRefusedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"refrozen {output_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
