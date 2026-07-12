"""Design-time crosswalk suggestion: `naru map suggest`, per docs/spec.md
§2.7.

`suggest(source_profile, target_schema, ...)` proposes a draft Mapping via
a tiered cascade, each match tagged with the tier that proposed it so a
human reviewer sees *why*:

1. `exact`   -- case/whitespace/punctuation-folded name equality
2. `synonym` -- hit in the shared synonym dictionary (~/.naru/synonyms.yaml)
3. `profile` -- type + distribution similarity (STUB in v0.1)
4. `llm`     -- design-time suggestion from profiles only (STUB in v0.1)

Tiers 3-4 are not implemented this session: suggest_tier3_profile_
similarity and suggest_tier4_llm exist as real, wired-in functions with
the right signature -- called from suggest()'s cascade for every column
tiers 1-2 couldn't place -- but always return a ProposedMatch with
target=None and evidence="", i.e. "this tier ran, found nothing". This is
the interface tier 3/4's real logic will fill in later; a column tier 3/4
"resolves" this way stays unmapped in the draft, surfacing later as an
unmapped_source_column at mirror time rather than as a fabricated,
low-confidence mapping line.

Every ColumnMapping suggest() actually produces (tiers 1-2 only, since
3-4 never resolve a target in v0.1) has approved: false -- per spec.md
§2.7, nothing here is auto-approved; a human (or, once implemented,
review of an LLM proposal) must flip that bit before naru mirror will
run it (see naru.mapping.load_mapping_for_execution).

The synonym dictionary (~/.naru/synonyms.yaml, or the path in the
NARU_SYNONYMS_PATH env var, override used in tests to avoid touching a
real home directory) is plain, diffable YAML: {normalized_source_text:
target_field_name}. map_learn() promotes human-approved synonym/llm
matches into it -- idempotently (re-learning the same match twice is a
no-op) and never overwriting a conflicting existing entry without an
explicit force=True (spec.md §2.7: "the accumulating asset... over time
it is worth more than the code").
"""

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from naru.mapping import ColumnMapping, Mapping
from naru.profiler import ColumnProfile, SheetProfile

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")

_SYNONYMS_PATH_ENV_VAR = "NARU_SYNONYMS_PATH"
_DEFAULT_SYNONYMS_PATH = Path.home() / ".naru" / "synonyms.yaml"


def normalize_column_name(text: str) -> str:
    """Case/whitespace/punctuation-folded form, used for both tier-1 exact
    matching and synonym-dictionary keys: lowercase, then collapse any run
    of non-alphanumeric characters (spaces, underscores, punctuation) to a
    single space, trimmed.

    >>> normalize_column_name("Deal ID")
    'deal id'
    >>> normalize_column_name("deal_id")
    'deal id'
    >>> normalize_column_name("  Coupon-Rate!! ")
    'coupon rate'
    """
    return _NORMALIZE_RE.sub(" ", text.lower()).strip()


def default_synonyms_path() -> Path:
    """~/.naru/synonyms.yaml, or the NARU_SYNONYMS_PATH env var if set
    (tests use this to avoid touching a real home directory).
    """
    override = os.environ.get(_SYNONYMS_PATH_ENV_VAR)
    return Path(override) if override else _DEFAULT_SYNONYMS_PATH


def load_synonyms(path: Path | None = None) -> dict[str, str]:
    """Load the synonym dictionary: {normalized_source_text: target_field}.
    Returns an empty dict if the file doesn't exist yet -- there is no
    synonym dictionary until the first naru map learn.
    """
    resolved = path if path is not None else default_synonyms_path()
    if not resolved.exists():
        return {}
    raw = yaml.safe_load(resolved.read_text()) or {}
    return {str(k): str(v) for k, v in raw.items()}


def save_synonyms(synonyms: dict[str, str], path: Path | None = None) -> None:
    """Write the synonym dictionary back out as plain, sorted-key,
    diffable YAML.
    """
    resolved = path if path is not None else default_synonyms_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(yaml.safe_dump(synonyms, sort_keys=True, default_flow_style=False))


class ProposedMatch(BaseModel):
    """A structured proposal from tier 3 or 4 -- deliberately NOT a
    ColumnMapping, since ColumnMapping requires non-empty evidence for
    basis in (profile, llm) and these stub tiers have none to give yet.
    `target` is always None in v0.1: a stub tier cannot actually name a
    candidate target field, only record that it was consulted.
    """

    source: str
    target: str | None
    basis: Literal["profile", "llm"]
    evidence: str
    confidence: float | None = None


def suggest_tier3_profile_similarity(
    column: ColumnProfile, target_schema: type[BaseModel]
) -> ProposedMatch:
    """STUB: tier 3 (type + distribution similarity against target_schema's
    fields) is not implemented in v0.1. This is the real, wired interface
    suggest() calls for every column tiers 1-2 couldn't place -- it always
    returns a no-op proposal (target=None, evidence="") rather than
    fabricating a match. See docs/spec.md §2.7.

    >>> from pydantic import BaseModel
    >>> class TargetRow(BaseModel):
    ...     coupon_rate: float
    >>> col = ColumnProfile(
    ...     position=1, header_text="Notes",
    ...     inferred_type="string", null_rate=0.0, cardinality=1,
    ...     samples=["x"], smells=[],
    ... )
    >>> suggest_tier3_profile_similarity(col, TargetRow)
    ProposedMatch(source='Notes', target=None, basis='profile', evidence='', confidence=None)
    """
    del target_schema  # unused in the stub; part of the real tier's future signature
    return ProposedMatch(
        source=column.header_text or "",
        target=None,
        basis="profile",
        evidence="",
        confidence=None,
    )


def suggest_tier4_llm(column: ColumnProfile, target_schema: type[BaseModel]) -> ProposedMatch:
    """STUB: tier 4 (LLM design-time suggestion from profiles only) is not
    implemented in v0.1. Same no-op-proposal contract as
    suggest_tier3_profile_similarity -- see that function's docstring.

    >>> from pydantic import BaseModel
    >>> class TargetRow(BaseModel):
    ...     coupon_rate: float
    >>> col = ColumnProfile(
    ...     position=1, header_text="Notes",
    ...     inferred_type="string", null_rate=0.0, cardinality=1,
    ...     samples=["x"], smells=[],
    ... )
    >>> suggest_tier4_llm(col, TargetRow)
    ProposedMatch(source='Notes', target=None, basis='llm', evidence='', confidence=None)
    """
    del target_schema  # unused in the stub; part of the real tier's future signature
    return ProposedMatch(
        source=column.header_text or "", target=None, basis="llm", evidence="", confidence=None
    )


def suggest(
    source_profile: SheetProfile,
    target_schema: type[BaseModel],
    *,
    target: str,
    key: list[str],
    unmapped_source_columns: Literal["warn", "fail"] = "warn",
    synonyms_path: Path | None = None,
) -> tuple[Mapping, list[ProposedMatch]]:
    """Propose a draft crosswalk from a profiled source sheet to a
    target_schema pydantic model's fields, via the tiered cascade
    described in this module's docstring.

    Every field name and header is matched at most once (first tier to
    claim a target field wins it). `on_duplicate` isn't a parameter:
    v0.1 only ever proposes "fail" (naru.mapping.Mapping rejects "skip"
    outright), so there is nothing to suggest.

    Returns (draft_mapping, tier_3_4_proposals) -- the second element is
    diagnostic only, for a human/log to see which columns tiers 3-4 were
    consulted for and came up empty; it plays no role in the draft
    mapping's `columns` (a stub proposal never has a target to map to).
    """
    target_fields = list(target_schema.model_fields.keys())
    used_targets: set[str] = set()
    columns: list[ColumnMapping] = []

    named_columns = [c for c in source_profile.columns if c.header_text]

    # Tier 1: exact normalized match.
    for column in named_columns:
        assert column.header_text is not None
        normalized_source = normalize_column_name(column.header_text)
        for field in target_fields:
            if field in used_targets:
                continue
            if normalize_column_name(field) == normalized_source:
                columns.append(
                    ColumnMapping(
                        source=column.header_text,
                        target=field,
                        transform="",
                        basis="exact",
                        approved=False,
                    )
                )
                used_targets.add(field)
                break

    matched_sources = {c.source for c in columns}
    unmatched_columns = [c for c in named_columns if c.header_text not in matched_sources]

    # Tier 2: synonym dictionary.
    synonyms = load_synonyms(synonyms_path)
    still_unmatched: list[ColumnProfile] = []
    for column in unmatched_columns:
        assert column.header_text is not None
        candidate = synonyms.get(normalize_column_name(column.header_text))
        if candidate and candidate in target_fields and candidate not in used_targets:
            columns.append(
                ColumnMapping(
                    source=column.header_text,
                    target=candidate,
                    transform="",
                    basis="synonym",
                    approved=False,
                )
            )
            used_targets.add(candidate)
        else:
            still_unmatched.append(column)

    # Tiers 3-4: stub interfaces, consulted but never resolve a target.
    proposals: list[ProposedMatch] = [
        proposal
        for column in still_unmatched
        for proposal in (
            suggest_tier3_profile_similarity(column, target_schema),
            suggest_tier4_llm(column, target_schema),
        )
    ]

    mapping = Mapping(
        target=target,
        key=key,
        on_duplicate="fail",
        columns=columns,
        unmapped_source_columns=unmapped_source_columns,
    )
    return mapping, proposals


class LearnReport(BaseModel):
    """What naru map learn actually did, for the transcript/CLI to show."""

    added: dict[str, str]
    skipped_conflicts: dict[str, str]


def map_learn(
    mapping: Mapping,
    *,
    synonyms_path: Path | None = None,
    force: bool = False,
) -> LearnReport:
    """Promote every approved synonym/llm-basis match in `mapping` into the
    synonym dictionary, per spec.md §2.7's "flywheel": exact-basis matches
    are skipped (they need no synonym; the name already matches), and
    unapproved lines are skipped (a human hasn't endorsed them yet).

    Idempotent: re-learning the same (source, target) pair twice is a
    no-op. A key that already maps to a *different* target is left
    untouched and reported in skipped_conflicts, unless force=True.
    """
    synonyms = load_synonyms(synonyms_path)
    added: dict[str, str] = {}
    skipped_conflicts: dict[str, str] = {}

    for column in mapping.columns:
        if not column.approved or column.basis not in ("synonym", "llm"):
            continue
        key = normalize_column_name(column.source)
        existing = synonyms.get(key)
        if existing == column.target:
            continue  # already known, exactly this way -- idempotent no-op
        if existing is not None and not force:
            skipped_conflicts[key] = existing
            continue
        synonyms[key] = column.target
        added[key] = column.target

    if added:
        save_synonyms(synonyms, synonyms_path)

    return LearnReport(added=added, skipped_conflicts=skipped_conflicts)
