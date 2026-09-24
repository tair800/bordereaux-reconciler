# Architecture

Four boundaries do most of the work here, and each exists to make a specific claim checkable rather
than merely stated.

---

## The pipeline

```mermaid
flowchart TB
    file["Coverholder file<br/><i>CSV or XLSX</i>"]

    subgraph ingest["ingest/ — no family vocabulary"]
        read["read.py<br/>grid + content hash"]
        profile["profile.py<br/>value-shape per column"]
        map["mapping.py<br/>header ∨ shape → canonical field"]
        canon["canonical.py<br/>exact decimals + lineage"]
    end

    subgraph adapters["adapters/ — the only place a domain lives"]
        ins["insurance<br/>8 fields, 2 deductions"]
        mkt["marketplace<br/>7 fields, 1 deduction"]
    end

    subgraph engine["the engine — knows no domain"]
        money["money.py<br/>Decimal, declared conventions"]
        domain["domain.py<br/>CanonicalRow, Lineage, 6 statuses"]
        rec["reconcile.py<br/>the comparison"]
    end

    ledger[("PostgreSQL<br/>NUMERIC, content-hash PK")]
    quar["Quarantine<br/><i>never coerced</i>"]
    carrier["Carrier ledger"]

    file --> read --> profile --> map --> canon
    adapters -. "canonical fields,<br/>synonyms, declared shapes" .-> map
    adapters -. "identity()" .-> canon
    canon -->|"row readable exactly"| ledger
    canon -->|"row not readable exactly"| quar
    map -->|"a required field has no column"| quar

    ledger --> rec
    carrier --> rec
    money --> rec
    domain --> rec
    rec --> report["ReconciliationReport<br/>MATCHED · MISMATCH · MISSING<br/>DUPLICATE · AMBIGUOUS · REVIEW"]

    provider["providers.py<br/><i>the single model call site</i>"]
    provider -. "proposes a mapping,<br/>for a person to confirm" .-> map
    provider -.-x rec

    style rec fill:#2b5f4a,color:#fff
    style provider fill:#8a5a00,color:#fff
    style quar fill:#a11b1b,color:#fff
```

The dashed line from `providers.py` to `reconcile.py` is crossed out because it does not exist and
`test_providers.py` asserts it over the import graph. `reconcile.py` imports exactly two modules of
this package — `domain` and `money` — and neither knows what insurance is.

---

## Boundary 1 — the domain lives in an adapter pack

`reconcile.py`, `money.py`, `domain.py` and everything under `ingest/` contain no insurance
vocabulary. `gross_premium`, `commission` and `policy_reference` are names an adapter declares;
the engine sees roles (`GROSS`, `DEDUCTION`, `KEY`) and nothing else.

The falsifier is not "a second family also runs". It is a scan that parses every engine module,
drops its docstrings, and searches the identifiers and string literals of code it actually runs for
any registered family name or canonical field name. That scan caught two real leaks — a
`family="insurance"` default in a store helper, and `Literal["insurance", "marketplace"]` in the
domain model, which would have made a third adapter pack impossible to add without editing the
engine.

The marketplace pack is deliberately **not** a mirror of the insurance one: seven canonical fields
against eight, one deduction against two, a different identity function. A second adapter shaped
like the first would test nothing.

---

## Boundary 2 — the model proposes, and cannot do anything else

```mermaid
flowchart LR
    headers["Headers + ≤5 sample<br/>values per column"]
    prov["MappingProvider<br/><i>the port</i>"]
    val["validate_proposals()"]
    human["A person confirms"]
    contract[("mapping_contract<br/>confirmed_by NOT NULL")]
    rec["reconcile.py"]

    headers --> prov --> val --> human --> contract --> rec
    prov -.-x rec

    style prov fill:#8a5a00,color:#fff
    style val fill:#2b5f4a,color:#fff
```

Three enforcement points, none of which is a convention:

| | mechanism | asserted by |
|---|---|---|
| A proposal cannot express money | `MappingProposal` has no numeric field | `test_providers.py`, over the model's own schema |
| A model cannot reach a status | no import path | `test_providers.py`, over the import graph |
| An invented field never reaches a human | `validate_proposals` runs first | `test_providers.py` |

A mapping is stored only with a person's name on it: `confirmed_by` is `NOT NULL` in the schema, so
a mapping that a model proposed and nobody accepted cannot exist in the table.

**PII minimisation is a property of the payload, not a promise.** `prompt_payload` is a separate
method precisely so a test can assert what would be sent: headers, at most five sample values per
column, the closed field set, and nothing else — no coverholder name, no totals.

---

## Boundary 3 — the guarantees are in the schema

```mermaid
erDiagram
    ingested_file ||--o{ ledger_row : "source_content_hash"
    ingested_file ||--o{ quarantine : "source_content_hash"

    ingested_file {
        string content_hash PK "the file's bytes ARE its identity"
        string coverholder
        int    mapping_version
        string status "accepted | quarantined"
    }
    ledger_row {
        int     id PK
        string  key UK "unique with (content_hash, mapping_version)"
        numeric gross "NUMERIC(18,4), never a float"
        json    deductions "amounts as STRINGS"
        json    lineage "one record per canonical value"
    }
    quarantine {
        int    spreadsheet_row "as a person would scroll to it"
        text   reason "a sentence, not a code"
        text   resolved_by "a queue, not a bin"
    }
    mapping_contract {
        string coverholder PK
        int    version PK "replay is additive"
        string confirmed_by "NOT NULL"
    }
    audit_event {
        string event "append-only: no update, no delete"
    }
```

Three decisions worth the space:

- **The content hash is the primary key of `ingested_file`.** A repeat ingestion is a constraint
  violation the database refuses, not a check the caller has to remember. A `SELECT` then `INSERT`
  would leave a window two workers can both pass through.
- **`ledger_row` is unique on (content hash, mapping version, key).** Replaying a period under a new
  mapping *inserts* rather than overwrites, so last month's numbers stay attributable to the mapping
  that produced them. Correcting history in place is how a reconciliation system loses the ability
  to explain what it said.
- **A file whose key repeats has those rows quarantined, not written.** The ledger is the accounting
  record and has to be unambiguous; the reconciliation report still reports them as `DUPLICATE`, so
  the row is visible — it simply is not yet money.

---

## Boundary 4 — what is exact, and what may round

```mermaid
flowchart LR
    raw["'1.234,56'"] -->|"declared convention"| dec["Decimal('1234.56')"]
    dec --> arith["+ − at full precision"]
    arith --> compare{"compare"}
    compare -->|"exact, or inside a<br/>tolerance that NAMES the field"| matched["MATCHED"]
    compare -->|"otherwise"| mismatch["MISMATCH"]
    arith --> total["declared total"]
    total -->|"ROUND_HALF_UP, once, at the end"| out["reported figure"]

    style matched fill:#2b5f4a,color:#fff
    style dec fill:#2b5f4a,color:#fff
```

Rounding happens in exactly two places, both declared in ADR-001: when parsing a source value to the
currency's minor unit, and once at the end when computing a declared total. Never per row — rounding
per row is how a total drifts a penny from its own detail and somebody loses an afternoon to it.

A tolerance is configuration. It names the fields it covers, a field it does not name requires exact
agreement whatever `absolute` says, and no model or heuristic can choose one.

---

## The reconciliation decision

```mermaid
flowchart TB
    start(["a key present on at least one side"]) --> dup{"key repeats<br/>on either side?"}
    dup -->|yes| DUPLICATE["DUPLICATE<br/><i>no further question is asked</i>"]
    dup -->|no| both{"present on<br/>both sides?"}
    both -->|no| MISSING["MISSING"]
    both -->|yes| ccy{"same currency?"}
    ccy -->|no| REVIEW1["REVIEW<br/><i>no FX source exists here</i>"]
    ccy -->|yes| arith{"does each row<br/>agree with itself?"}
    arith -->|no| REVIEW2["REVIEW<br/><i>comparing a known-wrong figure<br/>gives a confident wrong answer</i>"]
    arith -->|yes| onesided{"a field on<br/>one side only?"}
    onesided -->|yes| REVIEW3["REVIEW<br/><i>absent ≠ zero</i>"]
    onesided -->|no| outside{"any field outside<br/>the declared tolerance?"}
    outside -->|yes| MISMATCH["MISMATCH"]
    outside -->|no| MATCHED["MATCHED"]

    style MATCHED fill:#2b5f4a,color:#fff
    style MISMATCH fill:#8a5a00,color:#fff
    style DUPLICATE fill:#8a5a00,color:#fff
    style MISSING fill:#a11b1b,color:#fff
```

**`MATCHED` is constructed at exactly one place in `reconcile.py`**, and `test_reconcile.py` asserts
that over the module's AST rather than trusting a reading of it. Kill condition D is a property of
the single guard in front of that statement; a second one would have to be guarded identically, and
nothing would make sure it was.

`REVIEW` is not a hedge. It is the engine saying a person must decide, and nothing is written to the
ledger while a row sits in it. A reconciler that must answer on every row answers wrongly on some,
and in this domain a wrong answer is money.

---

## The evaluation, and why the carrier ledger is synthesised

The corpus supplies one side. `evaluation/corpusio.py` builds the other from ground truth and then
**injects a declared set of discrepancies into it**:

| injection | must produce |
|---|---|
| gross out by one minor unit | `MISMATCH` |
| gross out materially | `MISMATCH` |
| a deduction changed while gross agrees to the penny | `MISMATCH` |
| a row absent from the carrier | `MISSING` |
| a row only in the carrier | `MISSING` |
| a carrier row whose own net no longer closes | `REVIEW` |

The first five keep the carrier's row internally consistent — moving a gross moves the net with it,
moving a deduction moves the net the other way — because an inconsistent row is one the reconciler
is right to refuse, so injecting one by accident would measure the arithmetic guard while appearing
to measure the comparison. The sixth breaks that consistency on purpose and expects exactly that
refusal.

Without the injections the carrier side would equal the truth, every row would legitimately be
`MATCHED`, and "zero false MATCHED" would be a statement about a test that could not fail.

---

## Deployment topology

```mermaid
flowchart TB
    subgraph declared["infra/terraform — DECLARED, NOT APPLIED"]
        direction TB
        plat["modules/platform<br/>container app · postgres · blob<br/>key vault · log analytics"]
        aoai["modules/inference_azure_openai"]
        hosted["modules/inference_hosted_api"]
        gpu["modules/inference_selfhost_gpu"]
        dev["envs/dev"] --> plat
        bench["envs/bench"] --> plat
        prod["envs/prod"] --> plat
        dev --> aoai
        bench --> aoai & hosted & gpu
        prod --> aoai & hosted
    end

    plat --> manifest["residency_manifest<br/><i>terraform output</i>"]
    manifest -.->|"must equal"| committed["docs/residency/*.json<br/><i>rendered from the same<br/>committed inputs</i>"]
    committed --> killH["kill condition H"]

    style declared stroke-dasharray: 6 4
    style killH fill:#2b5f4a,color:#fff
```

Three environment roots composing one platform module, rather than CLI workspaces: the three
inference arms provision materially different things — an account and a deployment, nothing at all,
a GPU container app — and a single configuration serving all three would carry a conditional on
almost every resource.

`terraform validate` passes in all three roots against the real azurerm 5.6.0 schema. **Nothing has
been applied**, `terraform output` has never run, and what kill condition H actually proves is that
the committed manifest was rendered from the same committed inputs Terraform reads — so a region
changed in one place and not the other fails the build.
