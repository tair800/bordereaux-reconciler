# Environment roots

One directory per environment, each a Terraform root of its own with its own state blob. Not CLI
workspaces — `infra/README.md` gives the full argument, and the short version is that the three
inference topologies provision materially different resource sets, so one configuration serving all
of them would need a conditional on nearly every resource it contains.

The three roots are deliberately near-identical in shape. `versions.tf`, `providers.tf`,
`variables.tf` and `outputs.tf` are the same file in each; `main.tf` differs only in the `locals`
block at the top, which is where an environment says what it is. That symmetry is the point: a
reviewer comparing two environments should be reading a diff of about twenty lines, not two
configurations.

## The two kinds of input

| | where it lives | committed |
|---|---|---|
| **Declared** — region, topologies, sizing, retention, budget shape | `residency.auto.tfvars.json` | yes |
| **Operator** — subscription, tenant, database admin, alert recipients, firewall openings | `TF_VAR_*` or a gitignored `*.tfvars` | no |

`residency.auto.tfvars.json` is a Terraform *variable definitions file in JSON* — Terraform
auto-loads any `*.auto.tfvars.json` in the working directory. That format is chosen over HCL for one
reason: `scripts/residency_manifest.py` has to read the same values in order to render the committed
residency manifest, and with no Azure subscription it cannot obtain them by running a plan. JSON
means both readers parse the same bytes with a standard-library parser, so the manifest and the
configuration cannot describe different infrastructure. An HCL `.tfvars` would have needed a Python
HCL parser, which is a dependency and a second implementation of somebody's grammar.

JSON has no comments, so nothing here is annotated. What each field means is documented at the
variable in `variables.tf` and at the resource that consumes it.

## What each environment is for

| | `dev` | `bench` | `prod` |
|---|---|---|---|
| topologies | Azure OpenAI, hosted API | all three | Azure OpenAI, hosted API |
| VNet | none | for workload profiles | for private endpoints |
| private endpoints | no | no | yes |
| Key Vault purge protection | no | no | **yes, irreversible** |
| API min replicas | 0 | 0 | 1 |
| lifetime | long-lived, broken often | **created for a run, destroyed after it** | long-lived |

`bench` is the only environment with a GPU workload profile, and it is the reason the budget and the
teardown discipline exist at all: a GPU profile bills for allocated nodes rather than for requests.

## Running one

```bash
cd infra/terraform/envs/dev

# see infra/bootstrap/README.md for the backend arguments
terraform init -backend-config=...

export TF_VAR_subscription_id=...
export TF_VAR_tenant_id=...
terraform plan
```

Nothing in this repository has been applied — there is no subscription attached to this build. What
has been run is `terraform fmt -check`, `terraform init -backend=false` and `terraform validate`, in
each of the three roots. `infra/README.md` says exactly what that does and does not establish.
