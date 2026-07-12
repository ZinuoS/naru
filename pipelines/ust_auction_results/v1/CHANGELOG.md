# Changelog

## v1

Initial artifact, ported from the hand-written tracer bullet (tracer.py,
now retired). No format drift yet -- this version exists to prove the
artifact format itself works end to end against the same fixture the
tracer was built and tested against (tests/fixtures/ust_lite.xlsx).

See docs/adr/0001-lineage-carrier.md (provenance carrier),
docs/adr/0002-header-detection.md (unrelated to this pipeline's own
header handling, which hardcodes position rather than detecting it --
see transform.py), and docs/adr/0003-transform-loading.md (how this
transform.py gets loaded and executed).
