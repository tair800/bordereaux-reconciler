This manifest states where the resources of one environment are **declared** to live, and which
inference topology is declared to process a model call in which place. It is generated from the
Terraform configuration in `infra/terraform/`, and every environment root emits it as the
`residency_manifest` output.

It is not a statement of legal or regulatory compliance. It does not assert that any listed
arrangement satisfies the GDPR, DORA, the EU AI Act, or any other instrument; it does not account
for the jurisdiction of the operator or of anyone with administrative access; and it does not
address lawful-access powers that reach a provider's parent company regardless of where a byte sits.
Those are questions for a data protection officer and for counsel. This file is one input to that
conversation, not a substitute for it.

"Declared" is meant literally. Terraform sends a create request naming a region; Azure decides what
to do with it. Nothing here is a measurement of where data physically came to rest, and no
configuration file can make one. For the hosted-API topology even the declaration is second-hand —
it records what the provider states, names the document it was taken from, and marks itself as
unverified.
