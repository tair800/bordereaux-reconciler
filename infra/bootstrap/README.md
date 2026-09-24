# Bootstrap — the one thing Terraform deliberately does not manage

Terraform's state for this project lives in an Azure Storage blob. That storage account cannot itself
be created by the Terraform that stores its state there, because the state has to exist before the
first `apply` can record anything — and if the state *were* recorded there, a `terraform destroy`
would delete the account holding the record of what it was deleting, midway through deleting it.
That is the chicken-and-egg, and the honest answer to it is a short, boring, out-of-band script
rather than a clever `terraform_remote_state` arrangement.

So the backend storage account is created once, by hand, with the commands below, and then never
touched again by anything in `infra/terraform/`.

> **Nothing in this file has been run.** This build has no Azure subscription and no Azure
> credentials, so the commands below are written from the documented `az` CLI surface and have **not
> been executed against a real tenant**. Treat them as the intended procedure, not as a transcript.
> See `infra/README.md` for the full statement of what has and has not been applied.

---

## What gets created

| resource | why it is here and not in Terraform |
|---|---|
| Resource group `rg-brdx-tfstate` | Holds only state. Separate from every environment so that destroying an environment cannot reach it. |
| Storage account `stbrdxtfstate<suffix>` | Holds the state blobs. Globally unique name, hence the suffix. |
| Container `tfstate` | One blob per environment: `dev.tfstate`, `bench.tfstate`, `prod.tfstate`. |

Locking needs no extra resource. The `azurerm` backend takes a **native blob lease** on the state
blob for the duration of an operation, which is why this project has no DynamoDB analogue, no
Terraform Cloud account and no third locking service to keep alive. That is the single largest
operational advantage the Azure backend has over the AWS one, and it is worth knowing it is free.

---

## Commands

Substitute a suffix of your own; storage account names are globally unique across all of Azure and
`stbrdxtfstate` alone will already be taken.

```bash
SUFFIX="a7k2"                       # must match name_suffix in the env declarations
LOCATION="swedencentral"
RG="rg-brdx-tfstate"
SA="stbrdxtfstate${SUFFIX}"

az group create \
  --name "$RG" \
  --location "$LOCATION"

# shared-key access is disabled here for the same reason it is disabled on the application
# storage account: the backend authenticates with Entra ID (use_azuread_auth = true), so an
# account key would be a credential that exists only to be leaked.
az storage account create \
  --name "$SA" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --sku Standard_LRS \
  --kind StorageV2 \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false \
  --allow-shared-key-access false \
  --https-only true

# versioning is not optional on a state container. A corrupted or truncated state file is
# recoverable from a previous blob version and is otherwise recoverable from nothing.
az storage account blob-service-properties update \
  --account-name "$SA" \
  --resource-group "$RG" \
  --enable-versioning true \
  --enable-delete-retention true \
  --delete-retention-days 30

az storage container create \
  --name tfstate \
  --account-name "$SA" \
  --auth-mode login

# the identity that runs terraform needs data-plane access; Owner on the subscription does not
# grant it, which is the most common reason a first `terraform init` returns 403.
az role assignment create \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "Storage Blob Data Contributor" \
  --scope "$(az storage account show --name "$SA" --resource-group "$RG" --query id -o tsv)"
```

---

## Wiring an environment to it

The backend block in each `envs/<env>/versions.tf` is deliberately **partial** — it declares that
state lives in an `azurerm` blob and nothing else, because the account name contains a suffix that
differs per operator and a committed value would be wrong for everyone but its author.

```bash
cd infra/terraform/envs/dev

terraform init \
  -backend-config="resource_group_name=rg-brdx-tfstate" \
  -backend-config="storage_account_name=stbrdxtfstate${SUFFIX}" \
  -backend-config="container_name=tfstate" \
  -backend-config="key=dev.tfstate" \
  -backend-config="use_azuread_auth=true"
```

`use_azuread_auth=true` matters: without it the backend looks for an account key, and the account
above has none.

---

## Deleting it

Don't, while any environment still has state. If you must, copy every `*.tfstate` blob somewhere
first. There is no recovery path for a lost state file other than importing every resource by hand,
and on a configuration this size that is a day's work with a real chance of orphaning something.
