# Decisions

Architecture decision records. **ADR-001 is written before any implementation** and fixes the claim,
the money rules, the corpus contract, the baselines, the hold-out policy and the kill test. Nothing
in it may be weakened after a result is seen.

---

## ADR-001 — The claim, the money rules, the corpus, the baselines, and the kill test

**Status:** accepted, 2026-09-24, **before implementation**. Committed with zero source files in the
repository, which `git log --stat` shows.

### What the authoritative sources require, and what they do not

`PORTFOLIO_BLUEPRINT.md` §6 and `SKILL_MATRIX.md` are the authority; this file records what they say
rather than what a brief assumed. Two of those findings materially shrink the build:

| capability | column 6F says | so this project |
|---|---|---|
| **Terraform / IaC** | ● and **no other project has it** | **must ship it.** Sole home. |
| **Object storage** | ● and **no other project has it** | **must ship it.** Sole home. |
| Model routing / tier cascade | ● (also 10) | ships a provider port with more than one arm |
| Data residency / inference topology as config | ● (also 10) | ships a residency record per model call |
| **pgvector** | **blank** — it is 3, 7, 8 | **does not build it** |
| **RAG end-to-end through citation** | **blank** — it is 3, 7, 8 | **does not build it** |
| **Embeddings / hybrid search / reranking** | **blank** | **does not build them** |
| **Redis** | **blank** — it is 1, 4, 8, 9 | **does not build it** |

Skipping a sole-home row empties it portfolio-wide. That mistake has been made twice in this
portfolio already — project 4's rate limiting and project 5's expand/contract migration — and is not
repeated here: **Terraform and object storage are load-bearing deliverables, not decoration.**

Adding pgvector or a retrieval layer would be the opposite mistake: a capability three other projects
already carry, bolted on to tick a box the matrix does not tick. It is not built, and this table is
why.

### The claim

`PORTFOLIO_BLUEPRINT.md` states it in three independently checkable parts. They are reproduced here
verbatim in substance, because the kill test below is built to falsify each one:

1. **Re-ingesting the same bordereau any number of times cannot change the canonical ledger, and
   every canonical value points back to the source cell and mapping version that produced it.**
2. **The identical engine runs a committed non-insurance fixture set with adapter-level changes
   only.**
3. **The single model call site — first-time schema mapping — is executed across more than one
   inference topology, each with a published cost and accuracy and a per-request residency record.**

And the constraint that governs all three: **the model never touches a monetary value.** Not as a
policy somebody remembers — as a type boundary the tests attack.

### Money

Financial truth is exact or it is not truth. Binary floating point is banned from every monetary
path; the type system carries `Decimal` and the tests prove a float cannot reach a ledger value.

- **Representation.** Every monetary amount is a `Decimal` parsed from the source *string*. A value
  that cannot be parsed exactly is a quarantine reason, never a coerced zero.
- **Scale.** Amounts are held at the currency's minor-unit scale — 2 for GBP, EUR and USD — and
  rounded **ROUND_HALF_UP** only at declared boundaries, which are: parsing a source cell, and
  computing a declared total. Nowhere else.
- **Currency.** Reconciliation is **per currency**. There is no FX conversion in this build and no
  rate source is invented; a bordereau whose currency does not match the ledger's is an exception,
  not a conversion.
- **Tolerance.** A per-field absolute tolerance, declared in configuration and **defaulting to
  `0.00`** for premium, tax and commission. A non-zero tolerance is a deliberate act, is recorded in
  the evidence of every row it affects, and is tested. **No model and no heuristic may choose a
  tolerance.**
- **Totals.** A bordereau's declared total is reconciled against the sum of its own rows
  *independently* of the row-level reconciliation against the ledger, because the two can disagree
  in either direction and each direction is a different finding.

### Reconciliation statuses

Six, and no others. The blueprint does not define a set, so these are fixed here before any code:

| status | means |
|---|---|
| `MATCHED` | Every reconciled field agrees, exactly or within the declared tolerance. |
| `MISMATCH` | A reconciled field differs by more than the declared tolerance. |
| `MISSING` | The row exists on one side and not the other. The side is carried as evidence. |
| `DUPLICATE` | The row is a repeat of another within the same bordereau, by the declared key. |
| `AMBIGUOUS` | More than one candidate on the other side and no deterministic tie-break. |
| `REVIEW` | The engine declines. A person decides; nothing is written to the ledger. |

**A false `MATCHED` is the failure this project exists to prevent.** Defined precisely: a row
reported `MATCHED` whose reconciled fields differ by more than the declared tolerance. That
definition is what makes the threshold in kill condition D meaningful rather than rhetorical.

### The corpus

No public delegated-authority bordereaux corpus exists that is safely redistributable — these files
contain commercial terms and, routinely, personal data. So the corpus is **synthetic and openly
so**:

- A committed generator produces a fixed canonical truth and then applies **controlled schema
  perturbations** to render it as coverholder files.
- The generator, its seed and its version are committed, and every artifact records them.
- **It is never described as real-world labelled data.** Where project 5 could say its labels were
  a registrar's, this project cannot, and the README says so in those words.

Perturbations are the ones the domain actually produces: renamed and legacy column names, reordered
columns, absent optional columns, localised date formats, currency formatting and decimal
conventions, split versus combined premium and tax, net versus gross representations, duplicated
rows, and abbreviated headers.

**Two fixture families, one engine.** An insurance family and a **non-insurance** family
(marketplace seller settlement statements). Claim 2 says the same engine reconciles both with
adapter-level changes only, and kill condition G is how that is falsified.

### The baselines, chosen before any result

Schema mapping is the task with a model in it, so it is the task that gets baselines. Four, each
what a competent engineer actually reaches for first:

1. **Exact header match** — source header equals a canonical field name.
2. **Normalised header match** — lowercase, strip punctuation and spacing, then equality.
3. **Fuzzy header match** — best token-set ratio above a cut-off.
4. **Curated synonym dictionary** — a hand-written alias table, no model, no value inspection.

(4) is the strongest and is the one that matters: beating a fuzzy string match proves little, and
beating a maintained alias table is the honest bar. The system's own contribution is **value-shape
evidence** — what the column's *contents* look like — which is precisely what a header-only method
cannot see.

### Train / development / hold-out — fixed before scoring

Project 3 published a development F1 of 0.963 and a held-out F1 of 0.328. Project 5 built the split
before writing a rule because of it. This project does the same, with one difference that matters
here:

**The hold-out is whole schema variants, not rows.** Holding out rows from a layout the system has
already been tuned against measures nothing — the mapping is per layout. At least **four** variants,
including at least one whose headers use synonyms that appear nowhere in development, are assigned
to the hold-out, committed, and only then scored.

Assignment is by hash of the variant identifier, so the split is reproducible without a seed and
cannot be quietly re-drawn after a disappointing score.

**Once scored, no mapping rule, synonym, tolerance, threshold or normalisation changes.** If the
hold-out exposes a weakness it is published and the fix waits for a fresh corpus and a fresh split.

### The kill test — predeclared, and not to be weakened

The project **fails** if any of these is true.

| | condition | threshold |
|---|---|---|
| **A** | Reconciliation is not deterministic | two full runs over the same corpus differ in **any** canonical value or **any** row status |
| **B** | Re-ingestion is not idempotent | ingesting a file **three** times produces more than one canonical version of it, or changes any ledger row |
| **C** | Lineage is incomplete | **any** canonical cell lacking source content hash, sheet, row, column and mapping version |
| **D** | A false `MATCHED` | **more than 0** on the held-out set — a row reported `MATCHED` whose fields differ by more than the declared tolerance |
| **E** | Mapping does not beat the best baseline | system mapping accuracy **≤** the best of the four baselines on the **held-out** variants, or **< 0.90** absolute |
| **F** | It guesses instead of abstaining | a deliberately unmappable schema produces a ledger instead of a quarantine |
| **G** | The engine is not portable | the non-insurance fixture set requires a change outside the adapter package |
| **H** | The residency claim is unfalsifiable | the committed residency manifest does not equal what the Terraform module declares |

**D is zero and stays zero.** Everything else in a reconciler can be argued about; silently
reporting money as agreeing when it does not is the one outcome with no acceptable rate.

Every count is computed from committed artifacts by the test suite, not asserted in prose. A guard
that cannot fail is not a guard: `scripts/plant_breaches.py` plants a real defect into each one and
fails the build if any survives.

### Where AI is allowed

**Exactly one call site: first-time schema mapping.** The model proposes a mapping from source
columns to canonical fields, with a rationale and a confidence. A person confirms it once per
coverholder, and the confirmed mapping becomes deterministic, versioned configuration. Every later
file from that coverholder is reconciled with no model call at all, and the README publishes the
share of ingestions that needed one.

The model **may not**:

- produce, alter or round a monetary value;
- choose or relax a tolerance;
- decide a reconciliation status;
- mark a discrepancy acceptable;
- write to the ledger by any path.

This is enforced structurally rather than by instruction: the mapping proposer's return type cannot
express a monetary value, and the reconciliation engine does not import the provider package at all.

**Model output is untrusted input.** Bordereaux cells are attacker-authorable — a coverholder
supplies the file — so cell content reaching a prompt is treated as hostile, and a proposed mapping
is validated against the canonical field set before a human ever sees it.

### What this build does not include, against the blueprint

Recorded now so it cannot look like an omission discovered later. The blueprint scopes ~31 sessions;
the owner's instruction for this increment is a 1–2 day fast-track.

| blueprint item | status |
|---|---|
| Azure Container Apps / Azure Database for PostgreSQL / Azure Blob deployment | **Terraform is written and validated; nothing is applied.** No Azure subscription is available to this build, and `terraform apply` against a subscription that does not exist is not a deployment. The public demo is deployed on a free tier instead, and the README says which is which. |
| Azure OpenAI in the Sweden Central data zone | **Not called.** Declared in Terraform, unexecuted. No key exists. |
| Three live inference topologies with measured cost and p50/p95 | **Partially.** The provider port and the residency record are built and tested, and a deterministic arm plus a recorded-cassette arm run in CI. Live arms need keys this build does not have, so no live cost or latency figure is published — and an unmeasured number is not published as if it were measured. |
| Self-hosted open-weight arm (vLLM/Ollama) | **Not built.** |
| OpenTelemetry + self-hosted Langfuse | **Not built.** Structured logging only. |
| Next.js / TypeScript virtualised grid | **Server-rendered HTML.** A bundler and a virtualised grid are a day this increment does not have. |
| Quarantine + DLQ + period replay | **Built.** These carry claim 1 and are not optional. |

**Nothing in the README, the UI or this file may describe an unbuilt item as built, or publish a
number that was not measured.**
