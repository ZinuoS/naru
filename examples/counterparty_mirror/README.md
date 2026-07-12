# counterparty_mirror

A synthetic, self-contained demo of the full §2.7 loop (docs/spec.md):
a "client statement" whose column names and units deliberately differ
from the warehouse's ("Cpn (%)" holds a plain percentage number; the
warehouse's `Coupon Rate` column expects a decimal fraction), mirrored
into a formula-laden Excel warehouse workbook without disturbing any of
its existing cells, formulas, formatting, named ranges, or other sheets.

This directory *is* a Mapping Artifact: `mapping.yaml`, `fingerprint.json`
(source-side drift detection), `warehouse_fingerprint.json`
(warehouse-side drift detection, since `mapping.yaml` declares an
`excel_target`), and `schema.py`. `warehouse_workbook.xlsx` is the
mirror target itself; `client_statement.xlsx` is the file being mirrored
in. `client_statement_renamed_column.xlsx` is a deliberately drifted
variant (its "Deal ID" column is renamed) for exercising the halt.

Everything here was reached with `--help` alone -- no prior knowledge of
naru's internals required.
