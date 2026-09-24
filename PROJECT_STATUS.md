# PROJECT_STATUS

**Milestone: FULLY COMPLETE AND FROZEN. Deployed, publicly verified, read-only.**

Live at <https://bordereaux-reconciler.onrender.com>. Last updated at commit `36df3a8`.

---

## Verified

Everything in this section was produced by running a command, not by reading code.

| | check | result |
|---|---|---|
| lint | `ruff check src tests scripts alembic` | clean |
| format | `ruff format --check` | 52 files already formatted |
| types | `mypy --strict src` | clean, 32 source files |
| tests | `pytest tests -q` | **206 passed** |
| kill criteria | `pytest tests/test_kill_criteria.py` | all pass against artifacts built this session |
| falsifiability | `python scripts/plant_breaches.py` | **19/19 caught** |
| corpus determinism | `generate_corpus.py --verify-determinism` | 2 builds, 37 files, byte-identical |
| migrations | `alembic upgrade head` then `test_migrations.py` | schema matches `create_all`; money is `NUMERIC(18,4)` |
| terraform | `validate` in `envs/{dev,bench,prod}` under 1.16.4 | all three valid |
| image | `docker build` then `curl /healthz` | 503 without a database, 200 with one, read-only |
| console | six screens served locally | all 200; lineage renders 7 cells for one row |
| **public deploy** | six screens over HTTPS | all 200; `/healthz` `read_only: true`, `writes_enabled: false` |
| **read-only in public** | POST with no / guessed / empty token | **403** every time; ledger counters unchanged to the audit event |
| **live money** | a held-out row's lineage | `2.768,84 GBP` read as `2768.8400`, less `276.88`, equals stored net `2491.9600` |

### The measured results

```
hold-out      715 rows,  90 injected discrepancies,  0 false MATCHED,  0 status disagreements
              mapping 42/46 = 0.9130   best baseline (curated synonyms) 0.6522
development  1616 rows, 195 injected,                0 false MATCHED, 272 status disagreements
              mapping 84/92 = 0.9130   best baseline (curated synonyms) 0.8370
```

13,718 canonical cells carry complete lineage; 0 do not. 16 files ingested 3× each into PostgreSQL
produced 0 second canonical versions.

---

## Known issues

These are real, they are in the repository's own documentation, and none of them is hidden behind a
passing test.

1. **`ins_04_split_commission_usd` and `ins_05_tax_basis_ambiguous` return `REVIEW` on every row**
   (272 of 1,616 development rows). Both carry two columns competing for one canonical field — two
   commissions, and a premium quoted both including and excluding tax — so `net` never equals
   `gross` minus deductions under either mapping and the engine refuses to compare. Correct
   behaviour, and a real product gap: the adapters model one commission and one tax.
2. **`Seller ID` is mapped to `seller_name`** on two development variants. The fuzzy header
   component treats `Seller ID` as a match for the `seller` synonym, and no declared shape separates
   an identifier from a name.
3. **Hold-out independence is slightly weaker than ideal.** ADR-002 records that hold-out numbers
   were visible in debug output before the scoring run, at 0.7826, and lists every change made in
   between with the development evidence each was diagnosed from. No threshold was lowered.
4. **Unmappable variants are counted in the mapping denominator.** `ins_12` and `mkt_06` have no
   correct mapping to find — abstention is what is correct, and kill condition F measures that — so
   including them in condition E arguably measures the same thing twice. The metric was **not**
   redefined after seeing the number, which is why it still is.

---

## Blockers

None. Two things remain unmeasured because they need something this build does not have, and both
are reported as unmeasured rather than estimated:

- **A live inference arm** needs an API key. The port, the validation boundary and the per-request
  residency record are complete and tested; the HTTP call is what is missing, and **no cost or
  latency figure is published for an arm that has never been called**.
- **Applying the Terraform** needs an Azure subscription. All three environment roots validate under
  1.16.4 against the real azurerm 5.6.0 schema; nothing has been applied, there are zero `.tfstate`
  files, and `terraform output` has never run.

---

## Deployment state

| | state |
|---|---|
| local | `docker compose up -d postgres` + `make console` → http://127.0.0.1:8061 |
| image | builds; serves; migrates and seeds a schema-less database; answers `/healthz` with and without one |
| **public demo** | **LIVE** — <https://bordereaux-reconciler.onrender.com>, Render Free, Frankfurt, Docker, read-only, no approver token, no model key, no payment method |
| **demo database** | Render Free PostgreSQL 16, Frankfurt. **Expires 24 October 2026**; `/evidence` survives it, the other screens will report an empty ledger |
| Azure | **not deployed.** Terraform validated under 1.16.4 in all three roots, never applied, zero `.tfstate`, no subscription |

---

## Next

1. An adapter that models split commission and a two-basis tax, which removes known issue 1.
2. A live inference arm, when a key exists — and only then may a cost or latency figure appear
   anywhere.
3. When the free database expires on 24 October 2026, either re-provision it or let the console
   serve `/evidence` alone. Both are acceptable; what is not is leaving the README claiming a
   working ledger that is no longer there.
