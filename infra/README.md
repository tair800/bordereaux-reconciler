# Infrastructure

Terraform for the three environments this project defines, plus the generator that turns the
configuration into a committed residency manifest.

---

## Nothing here has been applied

Read this before anything else in this directory.

**There is no Azure subscription attached to this build and no Azure credentials.** No resource
described in `infra/terraform/` exists. There has been no `terraform plan`, no `terraform apply`, no
state file, and the storage backend in each root has never been initialised.

What *has* been run, on 2026-09-24, against Terraform v1.16.4 and azurerm v5.6.0:

| command | where | result |
|---|---|---|
| `terraform fmt -recursive -check` | `infra/` | clean |
| `terraform init -backend=false` | each of `envs/dev`, `envs/bench`, `envs/prod` | succeeded; provider downloaded, `.terraform.lock.hcl` written |
| `terraform validate` | each of the three roots | `Success! The configuration is valid.` |
| `python scripts/residency_manifest.py --check` | repo root | passed |

What that establishes and what it does not is worth being exact about, because "the Terraform
validates" is often read as more than it is:

- `validate` checks syntax, the provider **schema** (every argument exists and has the right type),
  module wiring, and variable type constraints. Every resource here is checked against azurerm
  5.6.0's real schema, which is how several v5 breaking changes were caught rather than guessed.
- `validate` does **not** contact Azure. It cannot know whether a SKU is available in a region,
  whether a quota allows a deployment, whether a GPU workload profile type exists in Sweden Central,
  or whether a name is already taken in the tenant. Several values here — the model version, the
  deployment SKU, the GPU workload profile type and its CPU/memory allocation — are **plausible but
  unverified**, and are flagged as such at the variable that carries them.
- A clean `validate` says nothing about whether a second `plan` would be clean. The perpetual-diff
  traps that were anticipated are handled explicitly (`ignore_changes` on container images, on the
  budget's time period, on the PostgreSQL zone and on diagnostic metric categories) and each carries
  a comment saying why — but they are *anticipated*, not observed.

The demo that is actually reachable is deployed on a free tier, not on this. `DECISIONS.md` records
that distinction in the same words.

---

## Layout

```
infra/
├── bootstrap/README.md          the state storage account, created by hand — and why
└── terraform/
    ├── region_jurisdictions.json   region -> place. Read by Terraform AND by the manifest generator
    ├── residency_statement.md      the caveats, single-sourced into every manifest
    ├── envs/{dev,bench,prod}/      one root per environment, own state, own lock file
    └── modules/
        ├── platform/                  everything an environment has regardless of topology
        ├── inference_azure_openai/    a cognitive account and a deployment
        ├── inference_hosted_api/      no resources at all, on purpose
        └── inference_selfhost_gpu/    a GPU container app
```

### Directory-per-environment, not CLI workspaces

The three inference topologies provision materially different resource sets: the Azure OpenAI arm
provisions an account plus a deployment, the hosted-API arm provisions nothing whatsoever, and the
self-hosted arm provisions a GPU container app and needs a workload profile and a VNet to put it on.
A single configuration serving all three through workspaces would carry a conditional `count` on
nearly every resource it contains — the workspace anti-pattern in its most literal form, where the
`.tf` files stop describing any one environment and start describing the union of all of them.

Instead: three roots, each composing the modules it needs, driven by a typed `enabled_topologies`
variable, with `count` on the **module** rather than on the resources inside it.

---

## What Terraform controls

- **Resource group**, and a **user-assigned managed identity** that is the single application
  identity. There is no client secret, no storage account key and no database password anywhere in
  this configuration.
- **Log Analytics workspace** with `daily_quota_gb = 1`. The provider default is unlimited, which on
  a workspace collecting diagnostics from every data resource is the ingest-cost trap.
- **Key Vault** with RBAC authorisation (required in azurerm 5.x; it was optional and defaulted to
  *off* in 3.x and 4.x). Purge protection in prod only — it is irreversible and blocks 90-day name
  reuse.
- **Scoped role assignments**: Key Vault Secrets User, Storage Blob Data Contributor and AcrPull on
  the platform's own resources; Cognitive Services OpenAI User on the Azure OpenAI account, declared
  in that module because declaring it in `platform` would make the module graph a cycle. Nothing is
  scoped at resource-group or subscription level.
- **Storage account** with `shared_access_key_enabled = false` and blob versioning, and three
  containers — `raw-bordereaux`, `quarantine`, `replay-snapshots` — with lifecycle tiering. Raw
  spreadsheets are immutable evidence for cell-level lineage, so nothing in the raw container's rule
  deletes anything.
- **PostgreSQL Flexible Server** with Entra authentication and `password_auth_enabled = false`, so
  no database password exists to rotate or leak.
- **Container App Environment**, two **Container Apps** (api with `min_replicas = 0`, worker) and two
  **Container App Jobs** (Alembic migration on a manual trigger, nightly replay on a schedule).
- **Container Registry**, Premium only where private endpoints force it.
- **Private networking and private endpoints in prod only.**
- **Diagnostic settings on every data-holding resource** — the audit-trail requirement expressed as
  infrastructure rather than as a paragraph.
- **Consumption budget** with an alert action group.

## What the platform controls, not Terraform

Azure owns everything inside the services: the Container Apps managed environment's internal
resource group, blob replication mechanics, the Flexible Server's patching and failover, Entra
directory replication for the managed identity, and the actual placement of a `GlobalStandard`
inference request. That last one is why the manifest records a deployment SKU rather than inferring
a processing location from the account's region.

## Deliberately not in Terraform

| | why | where it lives |
|---|---|---|
| **State storage account** | Chicken-and-egg: it has to exist before the first apply can record anything, and a destroy would delete the record of what it was deleting. | `infra/bootstrap/README.md`, as `az` CLI commands |
| **Secret *values*** | `azurerm_key_vault_secret` writes its value into Terraform state in plaintext, moving every secret into a blob readable by a wider group than the vault. | Put in the vault by a human or a pipeline; Terraform manages only the vault and the identity's read access, and the apps consume Key Vault *references* |
| **Container image tags** | A deployment concern that changes several times a day, against a configuration that changes a few times a quarter. Owning tags here would make every deploy a Terraform apply and every drift check a false positive. | The deployment pipeline; the resources carry `ignore_changes` on the image |

Each of these is also commented at the place a reader would look for it — the bootstrap note in
`envs/*/versions.tf`, the secrets note in `modules/platform/keyvault.tf`, the image note in
`modules/platform/variables.tf`.

---

## Pinning

`required_version = "~> 1.16"` and `azurerm = "~> 5.3"`, both as specified. The azurerm 5.x line was
checked against the registry before being pinned rather than taken on trust: 5.0.0 through 5.6.0
exist, and `~> 5.3` resolves to **5.6.0**, which is what the committed `.terraform.lock.hcl` records
in all three roots.

The lock files carry hashes for `linux_amd64`, `windows_amd64` and `darwin_arm64`. A lock file
written on one platform only contains that platform's hashes, and `terraform init` on a CI runner
then fails with a checksum error for a provider it just downloaded correctly — so the hashes were
added with `terraform providers lock -platform=...` rather than left to be discovered.

Two v5 breaking changes bite this configuration and are commented where they land:
`azurerm_storage_container` takes `storage_account_id` rather than `storage_account_name` (the old
form issued a data-plane call, which returns 403 against an account with shared keys disabled), and
`azurerm_key_vault.rbac_authorization_enabled` is now **required** where 4.x had an optional
`enable_rbac_authorization` defaulting to false.

## Two provider settings that are load-bearing

Both are in `envs/*/providers.tf`:

- `cognitive_account { purge_soft_delete_on_destroy = true }`. Without it, destroying an Azure OpenAI
  account leaves a soft-deleted account holding its name at tenant scope, and the next apply fails on
  a conflict with something the portal does not show by default. This is the single most common
  reason a portfolio's Terraform provisions Azure OpenAI exactly once and is never run again. The
  `bench` environment is destroyed after every benchmark run, so it would meet the problem on its
  second use.
- `key_vault { purge_soft_delete_on_destroy = true }`, safe precisely because
  `purge_protection_enabled` is `false` outside prod.

## Cost safety

`min_replicas = 0` on the API app, a consumption budget with an alert action group in every
environment, and the GPU topology confined to `bench` — an environment that exists to be created for
a benchmark run and destroyed after it. A careless `apply` on a GPU workload profile is the largest
financial risk in the portfolio, and the answer is that the only environment which can do it is
short-lived by construction. A budget does not stop an apply; nothing in Azure does.

---

## The residency manifest

This is why Terraform is in this project at all, rather than being a skill checkbox.

Project 6's headline is a residency claim, and **a residency claim made about clicked infrastructure
is unfalsifiable** — there is nothing to diff it against. Terraform turns it into an artifact: the
deployment SKU, the account region and the model version live in version-controlled HCL; every
environment root emits a `residency_manifest` output; `scripts/residency_manifest.py` renders the
same document from the same committed bytes and writes `docs/residency/<env>.json`; and
`DECISIONS.md` kill condition **H** fails the project if the two disagree.

The generator reads the committed `residency.auto.tfvars.json` that Terraform itself auto-loads, and
the `region_jurisdictions.json` that three modules read with `jsondecode(file(...))`. Same bytes,
two readers — so the manifest cannot describe different infrastructure from the one that would be
applied. It also refuses to render at all when the configuration and the manifest could not both be
right: a topology enabled in the declaration but not instantiated in `main.tf`, a module whose
`count` is gated on something other than `enabled_topologies`, an environment whose `locals` name
disagrees with its directory, a self-hosted GPU region that differs from the environment its
container app would inherit from, or a constant that has drifted from the module file it mirrors.
Each of those was tested by planting it and confirming the render fails.

See `docs/residency/README.md` for the document's structure and for what it does and does not claim.

---

## What a reviewer should check to decide whether this is real

The honest list, including the parts that cannot be checked yet.

**Checkable today, from a clone:**

1. `terraform fmt -recursive -check` in `infra/` — clean.
2. `terraform init -backend=false && terraform validate` in each of the three roots — valid against
   the real azurerm 5.6.0 schema.
3. `python scripts/residency_manifest.py --check` — the committed manifests match the configuration.
4. Break something and watch the guard fire: change `envs/bench/residency.auto.tfvars.json` so the
   self-hosted arm declares `eastus`, or repoint a topology module's `source`, and re-run `--check`.
   A guard that cannot fail is decoration.
5. Grep for diagnostic settings: `modules/platform/diagnostics.tf` should name every data-holding
   resource, and `manifest.platform.diagnostic_settings` in each committed manifest should list the
   same set.
6. Confirm no secret value, no image tag and no bootstrap account appears anywhere in the
   configuration.

**Requires a subscription, and has therefore not been done:**

7. **Is the second `plan` clean?** A configuration with a perpetual diff is one nobody can use to
   detect drift. The known traps are handled; whether any remain is unknown.
8. **Does the committed manifest equal `terraform output -json residency_manifest`?** This is the
   strong form of check 3 and the one kill condition H is really about. The command is in
   `docs/residency/README.md`.
9. **Does destroy-then-apply work?** The `cognitive_account` purge setting exists for exactly this,
   and `bench` is designed around it, but it has not been observed.
10. **Do the unverified values hold?** The model version, the deployment SKU's availability in Sweden
    Central, the GPU workload profile type and its allocatable CPU and memory. Each is flagged at its
    variable.

Nothing in this directory should be read as claiming 7 through 10 have been done.
