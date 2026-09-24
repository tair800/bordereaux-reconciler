# PROJECT_STATUS

**Milestone: feature-complete against the fast-track scope, evidence built and graded, hold-out
scored once and frozen.**

Last updated after the evidence build at commit `cd5c1b1` plus the packaging and migrations work.

---

## Verified

Everything in this section was produced by running a command, not by reading code.

| | check | result |
|---|---|---|
| lint | `ruff check src tests scripts alembic` | clean |
| format | `ruff format --check` | 48 files already formatted |
| types | `mypy --strict src` | clean, 32 source files |
| tests | `pytest tests -q` | **171 passed** |
| kill criteria | `pytest tests/test_kill_criteria.py` | all pass against artifacts built this session |
| falsifiability | `python scripts/plant_breaches.py` | **19/19 caught** |
| corpus determinism | `generate_corpus.py --verify-determinism` | 2 builds, 37 files, byte-identical |
| migrations | `alembic upgrade head` then `test_migrations.py` | schema matches `create_all`; money is `NUMERIC(18,4)` |
| terraform | `validate` in `envs/{dev,bench,prod}` under 1.16.4 | all three valid |
| image | `docker build` then `curl /healthz` | 503 without a database, 200 with one, read-only |
| console | six screens served locally | all 200; lineage renders 7 cells for one row |

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

None for the scope as specified. Three things need something this build does not have:

- **A live inference arm** needs an API key. The port, the validation boundary and the per-request
  residency record are complete and tested; the HTTP call is what is missing, and no cost or latency
  figure is published for an arm that has never been called.
- **Applying the Terraform** needs an Azure subscription. All three environment roots validate;
  nothing has been applied and `terraform output` has never run.
- **The public deployment** needs the owner to connect the repository on Render. `render.yaml` is
  committed and describes a free-tier, read-only service with no approver token and no model key.

---

## Deployment state

| | state |
|---|---|
| local | `docker compose up -d postgres` + `make console` → http://127.0.0.1:8061 |
| image | builds; serves; answers `/healthz` with and without a database |
| public demo | **not deployed.** `render.yaml` committed, free tier, read-only, no payment method required |
| Azure | **not deployed.** Terraform validated, never applied, no subscription |

---

## Next

1. Deploy the Render blueprint and record the URL in the README.
2. An adapter that models split commission and a two-basis tax, which removes known issue 1.
3. A live inference arm, when a key exists — and only then may a cost or latency figure appear
   anywhere.
