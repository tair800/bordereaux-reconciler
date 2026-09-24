# Residency manifests

One JSON document per environment, generated from the Terraform configuration by
`scripts/residency_manifest.py` and committed. `dev.json`, `bench.json`, `prod.json`.

## What they say, and what they do not

Each manifest states **where the resources of one environment are declared to live**, and for each
enabled inference topology, where a model call is declared to be processed. The full wording is
carried inside every file, in its `manifest.statement` field, so a manifest that is copied out of
this repository takes its own caveats with it.

The short version, repeated here because it is the sentence most likely to be dropped:

> **This is not a compliance statement.** It does not assert that any arrangement described here
> satisfies the GDPR, DORA, the EU AI Act or anything else. It does not account for the jurisdiction
> of the operator or of anyone with administrative access, and it does not address lawful-access
> powers that reach a provider's parent company regardless of where a byte sits.

And **"declared" is meant literally.** Terraform sends a create request naming a region; Azure
decides what to do with it. Nothing here is a measurement of where data physically came to rest, and
no configuration file can produce one.

For the hosted-API topology even the declaration is second-hand. That arm provisions nothing, so
Terraform can attest to nothing about it; its entry carries `verified_by_terraform: false` and a
`declaration_source` naming who made the claim.

## Nothing here has been deployed

Every manifest carries `provenance.applied: false`. This build has no Azure subscription. The
configuration has been formatted, initialised with `-backend=false` and validated; it has never been
planned or applied, and **none of the resources described in these files exists.** See
`infra/README.md`.

## Why they are generated rather than written

`DECISIONS.md` kill condition **H** fails the project if "the committed residency manifest does not
equal what the Terraform module declares". A hand-written manifest could not fail that condition in
any useful way — it would be a second opinion about the same infrastructure, drifting quietly. A
generated one can, because it is rendered from the same committed bytes Terraform reads:
`infra/terraform/envs/<env>/residency.auto.tfvars.json` (a Terraform variable definitions file that
Terraform auto-loads) and `infra/terraform/region_jurisdictions.json` (read by
`jsondecode(file(...))` in three modules).

## Regenerating and checking

```bash
python scripts/residency_manifest.py --write          # rewrite all three
python scripts/residency_manifest.py --check          # exit non-zero if any is stale
python scripts/residency_manifest.py --env prod       # print one, write nothing
```

The render is deterministic: no clock, no environment, no network, and line endings are normalised
before hashing, so the same commit produces identical bytes on Windows and Linux. `--check` is what
belongs in CI.

Once a subscription exists, the stronger check becomes available and should replace this one as the
primary guard:

```bash
terraform -chdir=infra/terraform/envs/prod output -json residency_manifest \
  | jq .value > /tmp/live.json
jq .manifest docs/residency/prod.json > /tmp/committed.json
diff <(jq -S . /tmp/live.json) <(jq -S . /tmp/committed.json)
```

The two documents are compared **as parsed objects**, not as text — the committed file is written
with sorted keys and Terraform's is not.

## Structure

| key | |
|---|---|
| `manifest` | Exactly what `terraform output -json residency_manifest` emits. This is the part that must match. |
| `manifest.platform` | Region and region facts for the resource group, storage account, database, Container App Environment, Key Vault and workspace, plus which data resources have diagnostic settings. |
| `manifest.inference` | One entry per **enabled** topology. A disabled arm is absent rather than present-and-false: the manifest answers where a model call is processed, and an arm that cannot be called has no answer to give. |
| `provenance` | Facts about the render, not about the infrastructure: generator version, the `required_version` constraint, the azurerm version pinned in `.terraform.lock.hcl`, a digest of every file the manifest was derived from, and whether any of it has been applied. Deliberately outside `manifest`, because the Terraform output cannot produce it and including it would break the comparison. |

Resource *names* are deliberately absent. A name is not a residency fact, and including one would
force the generator to reimplement the platform module's naming rules in Python in order to render
the document without a plan — a duplication that would diverge and then be reported as a residency
discrepancy. Names are published by the `resource_names` Terraform output instead.
