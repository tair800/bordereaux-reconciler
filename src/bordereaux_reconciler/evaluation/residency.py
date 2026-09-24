"""Rendering the residency manifest from the same committed inputs Terraform reads.

`terraform output -json residency_manifest` is the authoritative producer of this document, and it
needs state, which needs an apply, which needs a subscription this build does not have. So the
manifest is also rendered here, in Python, from the same committed files the HCL expression reads:
each environment's `residency.auto.tfvars.json`, the shared `region_jurisdictions.json`, and
`residency_statement.md`.

That gives kill condition H something real to fail on. Change a region in an environment's tfvars,
forget to regenerate `docs/residency/<env>.json`, and the build stops. That is the drift that
actually happens — a configuration edited and a document not.

**What it cannot do.** It cannot prove Terraform's expression evaluates to the same object, because
nothing here executes HCL. The two are held together by reading the same bytes, by `terraform
validate` in CI, and by the mirror guard below. The gap between that and a real `terraform output`
is stated in `residency.json` rather than papered over: `matches_module: true` means "the committed
document and the committed configuration agree", and nothing more.

**The mirror guard.** A handful of values in the manifest live in HCL rather than in an input file —
the blob container names, the diagnostic-setting keys, two storage booleans, the self-hosted
processing scope. Those are genuinely duplicated here, and duplication that nobody checks is how two
sources of truth quietly diverge. :data:`_MIRRORED` names each one together with the module file it
came from, and :func:`_check_mirrors` refuses to render if the fragment has disappeared from that
file. Renaming a container in `storage.tf` therefore fails the render rather than producing a
manifest that describes a container that no longer exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

__all__ = ["ENVIRONMENTS", "MirrorDriftError", "render_manifest", "write_manifests"]

#: Kept in step with the directories under `infra/terraform/envs/`.
ENVIRONMENTS: Final = ("bench", "dev", "prod")

SCHEMA_VERSION: Final = 1

#: Values mirrored out of HCL, each with the module file that owns it and a fragment that must still
#: appear there. The fragment is what makes this a check rather than a comment.
_MIRRORED: Final[tuple[tuple[str, str, str], ...]] = (
    ("blob container", "modules/platform/storage.tf", '"raw-bordereaux"'),
    ("blob container", "modules/platform/storage.tf", '"quarantine"'),
    ("blob container", "modules/platform/storage.tf", '"replay-snapshots"'),
    ("storage versioning", "modules/platform/outputs.tf", "versioning_enabled  = true"),
    ("storage shared keys", "modules/platform/outputs.tf", "shared_keys_enabled = false"),
    ("database password auth", "modules/platform/outputs.tf", "password_auth_enabled = false"),
    ("diagnostic target", "modules/platform/diagnostics.tf", "blob = {"),
    ("diagnostic target", "modules/platform/diagnostics.tf", "keyvault = {"),
    ("diagnostic target", "modules/platform/diagnostics.tf", "postgres = {"),
    ("diagnostic target", "modules/platform/diagnostics.tf", "registry = {"),
    ("diagnostic target", "modules/platform/diagnostics.tf", "containerappenv = {"),
    ("diagnostic target", "modules/platform/diagnostics.tf", "storage = {"),
    (
        "self-host processing scope",
        "modules/inference_selfhost_gpu/outputs.tf",
        'processing_scope = "single-region"',
    ),
)

#: Mirrored from `local.blob_containers` in `modules/platform/storage.tf`, in the order Terraform's
#: `sort(keys(...))` produces.
_BLOB_CONTAINERS: Final = ("quarantine", "raw-bordereaux", "replay-snapshots")

#: Mirrored from `local.diagnostic_targets` in `modules/platform/diagnostics.tf`, likewise sorted.
_DIAGNOSTIC_TARGETS: Final = (
    "blob",
    "containerappenv",
    "keyvault",
    "postgres",
    "registry",
    "storage",
)


class MirrorDriftError(RuntimeError):
    """A value duplicated from HCL no longer appears in the file it was copied from."""


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_mirrors(infra: Path) -> None:
    missing = [
        f"{label}: {fragment!r} is no longer in infra/terraform/{relative}"
        for label, relative, fragment in _MIRRORED
        if fragment not in (infra / relative).read_text(encoding="utf-8")
    ]
    if missing:
        raise MirrorDriftError(
            "the residency renderer mirrors values out of the Terraform configuration, and these "
            "no longer match:\n  " + "\n  ".join(missing) + "\n\nUpdate _MIRRORED in "
            "evaluation/residency.py together with the module, or the manifest will describe "
            "infrastructure the configuration no longer declares."
        )


def _statement(infra: Path) -> str:
    r"""The statement text, newline-normalised exactly as `outputs.tf` normalises it.

    The HCL is `trimspace(replace(file(...), "\r\n", "\n"))`. Without matching that, a Windows
    checkout would make this field differ from the committed manifest byte for byte while saying the
    same thing, and kill condition H would fire on a line ending — a false alarm that teaches
    everyone to ignore the check. `core.autocrlf` is active on at least one machine this is
    developed on, so this is not hypothetical.
    """
    raw = (infra / "residency_statement.md").read_text(encoding="utf-8")
    return raw.replace("\r\n", "\n").strip()


def _region_facts(jurisdictions: dict[str, Any], region: str) -> dict[str, Any]:
    facts = jurisdictions.get("regions", {}).get(region)
    if facts is None:
        raise KeyError(
            f"{region!r} has no entry in region_jurisdictions.json. The manifest will not infer a "
            "jurisdiction from a region name; add the region to the lookup, with its source."
        )
    return dict(facts)


def render_manifest(repo_root: Path, environment: str) -> dict[str, Any]:
    """The manifest object for one environment, from committed inputs only.

    Mirrors the structure of `output "residency_manifest"` in `envs/<env>/outputs.tf`, which is
    `{schema_version, environment, statement, platform, inference}`.
    """
    infra = repo_root / "infra" / "terraform"
    _check_mirrors(infra)

    tfvars = _load(infra / "envs" / environment / "residency.auto.tfvars.json")
    jurisdictions = _load(infra / "region_jurisdictions.json")

    location = tfvars["location"]
    facts = _region_facts(jurisdictions, location)
    topologies = tfvars["enabled_topologies"]
    private_endpoints = environment == "prod"

    inference: dict[str, Any] = {}

    azure_openai = topologies.get("azure_openai", {})
    if azure_openai.get("enabled"):
        region = azure_openai["location"]
        openai_facts = _region_facts(jurisdictions, region)
        deployment = azure_openai["deployment"]
        inference["azure_openai"] = {
            "topology": "azure_openai",
            "provisioned": True,
            "declared_region": region,
            "geography": openai_facts["geography"],
            "country_iso": openai_facts["country_iso"],
            "residency_boundary": openai_facts["residency_boundary"],
            # The SKU, not the region, and the distinction is the point: a GlobalStandard or
            # DataZone deployment created in one region may be served from anywhere the provider
            # runs the model. Repeating declared_region here would make the manifest answer a
            # question it was not asked, with a value that is not true of the request.
            "processing_scope": deployment["sku_name"],
            "model": f"{deployment['model_name']}:{deployment['model_version']}",
        }

    hosted = topologies.get("hosted_api", {})
    if hosted.get("enabled"):
        inference["hosted_api"] = {
            "topology": "hosted_api",
            "provisioned": False,
            "provider": hosted["provider_label"],
            "endpoint_host": hosted["endpoint_host"],
            "declared_region": hosted["declared_region"],
            "country_iso": hosted["declared_country_iso"],
            "residency_boundary": hosted["declared_residency_boundary"],
            # Second-hand, and marked as such. The vendor states it; nothing here checks it, and a
            # manifest presenting a vendor's claim as its own finding would be the dishonest version
            # of this document.
            "verified_by_terraform": False,
            "region_table_used": hosted["declared_region"] in jurisdictions.get("regions", {}),
            "declaration_source": hosted["declaration_source"],
            "key_vault_secret_name": hosted["key_vault_secret_name"],
        }

    selfhost = topologies.get("selfhost_gpu", {})
    if selfhost.get("enabled"):
        region = selfhost["location"]
        gpu_facts = _region_facts(jurisdictions, region)
        inference["selfhost_gpu"] = {
            "topology": "selfhost_gpu",
            "provisioned": True,
            "declared_region": region,
            "geography": gpu_facts["geography"],
            "country_iso": gpu_facts["country_iso"],
            "residency_boundary": gpu_facts["residency_boundary"],
            "processing_scope": "single-region",
            "model": selfhost["model_id"],
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "environment": environment,
        "statement": _statement(infra),
        "platform": {
            "region": location,
            "region_facts": facts,
            "resources": {
                "resource_group": {"declared_region": location},
                "storage_account": {
                    "declared_region": location,
                    "containers": list(_BLOB_CONTAINERS),
                    "versioning_enabled": True,
                    "shared_keys_enabled": False,
                },
                "database": {"declared_region": location, "password_auth_enabled": False},
                "container_app_environment": {"declared_region": location},
                "key_vault": {"declared_region": location},
                "log_analytics_workspace": {"declared_region": location},
            },
            "private_endpoints_enabled": private_endpoints,
            "diagnostic_settings": list(_DIAGNOSTIC_TARGETS),
        },
        "inference": inference,
    }


def write_manifests(repo_root: Path) -> list[Path]:
    """Regenerate every committed manifest. Checked by kill condition H and by CI's fast lane."""
    out = repo_root / "docs" / "residency"
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for environment in ENVIRONMENTS:
        if not (repo_root / "infra" / "terraform" / "envs" / environment).is_dir():
            continue
        document = {
            "generated_by": "scripts/residency_manifest.py, from committed Terraform inputs",
            "authoritative_producer": "terraform output -json residency_manifest",
            "applied": False,
            "not_a_compliance_assertion": (
                "this records where resources are declared to sit. It asserts no conformance with "
                "the GDPR, DORA, the EU AI Act or any other instrument; it does not account for "
                "the "
                ""
                "jurisdiction of the operator or of anyone with administrative access; and nothing "
                "described here has been deployed"
            ),
            "manifest": render_manifest(repo_root, environment),
        }
        path = out / f"{environment}.json"
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written
