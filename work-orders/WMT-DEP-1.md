# WMT-DEP-1 — Close out the open PRs: Renovate bundle and the pending back-ports

Prepared: 2026-10-15. Target repository: `webapp-management-template` (`main`).

# A. Envelope — addressed to everyone

## Goal and expected outcome

webapp-management-template has no open PR left. Its seven Renovate PRs land as one change on `main`; each of its five pending back-port PRs from webapp-management is either merged (still applicable and generic) or closed with the reason (superseded or not generic).

## Tier

Tier 3: dependency changes, majors included.

## Operator decisions this Envelope records (2026-10-15)

1. The bundle contains these open Renovate PRs' updates, in one change on `main`:

| PR | Update |
|---|---|
| #11 | docker non-major |
| #17 | github-actions |
| #18 | terraform cloudflare ~> 5.27 |
| #19 | terraform hashicorp/terraform >= 1.16.5 |
| #20 | actions/checkout v7 |
| #21 | actions/create-github-app-token v3 |
| #22 | actions/setup-python v7 |
| #23 | bigler-webapps/workflow-templates v2 |

2. A major that needs more than small adaptations (configuration, a renamed import, a test helper) is taken out of the bundle, left as its PR, and reported with the reason. The bundle does not grow into a migration.
3. Verification is one CI run of the repository's own CI on the candidate (for an app: a `ci-test/<ID>` ref with the full job set). A red job is fixed within decision 2 and the run repeated; not more than that.
4. After the push Renovate closes the PRs whose update landed; a PR it rebases instead is an update that did not land and is reported.
5. The back-port PRs #12 (2026-06-18), #13 (2026-07-13), #14 (2026-07-16), #15 (2026-07-30) and #16 (2026-10-08) are reviewed one by one against today's `webapp-management`: a change that is still generic and not yet in the template is merged (rebased onto the bundle where needed); a change that is superseded, estate-specific or already present is closed with a one-line reason. Each carries different content; none supersedes another.

## Scope

1. Dependency manifests and lockfiles, workflow `uses:` pins, image pins the updates touch.
2. The small adaptations decision 2 allows, with tests where behaviour is touched.
3. Each update's changelog read for breaking changes before it goes in; a breaking change the code meets is adapted (decision 2) or the update is taken out.

## Non-goals

- No new features, no refactoring beyond decision 2.
- No change to the Renovate configuration.
- No change to `webapp-management` itself.

## Risks

- The template's Terraform and Ansible are not applied anywhere from this repository; a wrong merge shows only when a new estate is created from the template. Mitigation: the template's own CI and the one-by-one review of decision 5.

## Required tests to write

None beyond the adaptations of decision 2. The evidence is the CI run of decision 3.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Part of the PR close-out round of 2026-10-15; stagger the CI run with the other bundles of that round (one heavy run at a time on the shared runner).
