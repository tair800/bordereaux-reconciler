# CLAUDE.md — bordereaux-reconciler

Operating rules for this repository. Read before changing anything.

---

## 1. What this is

Reconciles delegated-authority insurance bordereaux against a carrier ledger. Exact decimal money,
cell-level lineage, a model confined to proposing a column mapping.

The three claims the project stands or falls on are in `DECISIONS.md` ADR-001, together with eight
kill conditions. `tests/test_kill_criteria.py` grades them and was committed at `29240ef` before any
implementation existed.

---

## 2. Rules that are not negotiable

**Money is never a float.** Not in parsing, not in comparison, not in storage, not in JSON. The ban
is enforced at runtime by validators, not just by type annotations. PostgreSQL columns are `NUMERIC`;
deductions are stored as strings inside JSON because a JSON number is a double.

**`MATCHED` is constructed in exactly one place in `reconcile.py`.** A test asserts this over the
module's AST. If a second one is ever needed, the guard in front of it must be identical and a test
must say so.

**No model, heuristic or scoring function may choose or influence a tolerance.** A tolerance is
configuration, it names the fields it covers, and a field it does not name requires exact agreement.

**A row that cannot be read exactly is quarantined, never coerced.** No "treat unparseable as zero",
no "assume the missing tax was nil", no fallback convention. A ledger that balances while being
wrong is worse than one that refuses to exist.

**The decimal convention and the date order are declared by the coverholder, never inferred.**
`1.234` and `03/04/2026` are genuinely ambiguous, and a system that guessed would be right most of
the time — which is the worst possible failure rate.

**No family vocabulary in the engine.** `reconcile.py`, `money.py`, `domain.py`, `ingest/*` and
`store/*` must not name a family or a canonical field in code they run. Kill condition G's leak scan
enforces it; it has already caught two real leaks.

**Never publish an unmeasured number.** No cost, latency or accuracy figure appears anywhere unless
a build produced it. No live model arm runs in this build, so no cost or latency for one is
published.

**Never describe the corpus as real data.** It is synthetic, generated from a committed seed, and
every generated file says so in its own body.

---

## 3. The hold-out is frozen

Membership is fixed by `blake2b(variant_id) % 100 < 30` plus the unseen-vocabulary flag. **Do not
change that rule.** Do not change anything in `adapters/`, `ingest/` or `reconcile.py` on the
strength of a hold-out result.

ADR-002 records that hold-out numbers were visible before the scoring run and lists every change made
in between. From that commit the rules are frozen. If a future change is wanted, add variants to the
corpus and let the unchanged rule re-draw the split.

Thresholds in `tests/test_kill_criteria.py` may go **up**, never down.

---

## 4. Commands

```bash
make setup        # uv sync
make db           # PostgreSQL on 127.0.0.1:15437
make corpus       # generate the synthetic fixtures
make determinism  # build it twice, diff every byte
make artifacts    # the eight evidence files (needs the database)
make test         # the whole suite
make fast         # lint, types, no-infrastructure tests
make breaches     # plant nineteen defects, check each is caught
make console      # http://127.0.0.1:8061
make residency    # regenerate docs/residency/*.json
```

Terraform lives in `infra/terraform/`. `terraform validate` runs in CI against all three environment
roots. **Nothing is applied** and nothing in this repository may imply otherwise.

---

## 5. Before every commit

1. `make lint types` — ruff at 100 columns, mypy `--strict`, both clean.
2. `make test` — the whole suite including the kill criteria.
3. If anything under `adapters/`, `ingest/`, `reconcile.py` or `store/` changed:
   `make artifacts` then `make test` again, because the evidence is now stale.
4. If anything under `infra/terraform/` changed: `make residency` and commit the result.
5. Scan the staged diff for secrets. `.env` is gitignored; `.env.example` carries placeholders only.

---

## 6. Conventions

- Python 3.12, `uv`, ruff at 100 columns, mypy `--strict`. Pydantic models are frozen with
  `extra="forbid"`.
- Comments explain **why**, especially where a simpler thing was rejected and for what reason. A
  comment restating the code is worse than none.
- Tests are named as sentences about behaviour, not after the function under test.
- Structural tests — over the AST, the import graph, the model schema — are preferred wherever the
  property is structural. A behavioural test only covers the paths somebody thought to write.
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`.
- **No AI attribution anywhere**: not in commits, not in code headers, not in documentation.

---

## 7. Adding an adapter pack

The measure of whether the adapter boundary is real. It should take a single new file under
`adapters/` and nothing else.

1. Declare canonical fields with roles, synonyms and **declared shapes** — the shapes are what let
   the mapper work on vocabulary it has never seen.
2. Implement `identity()`. Think about whether the key alone is unique, or whether it needs the
   period: the same policy recurs monthly with a different premium.
3. Add corpus variants for it in `corpus/variants.py`, including at least one with an unseen
   vocabulary and one that is deliberately unmappable.
4. Run `make evidence`. If any engine module needed a change, kill condition G will say so, and the
   change belongs in the adapter instead.
