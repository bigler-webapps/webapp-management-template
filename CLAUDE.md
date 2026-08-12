# <your-project>

@AGENTS.md

---

## ⚠ Terraform → Prod-Infra-Gate

**Any push/merge to `main` that touches `terraform/**` or
`.github/workflows/terraform-*.yml` triggers `terraform-apply.yml`
and mutates Prod-Infra.**

Execution model:
- GitHub Actions → Composite Action `terraform-apply@<version>`
- TFC Remote Execution — Workspace **<your-org>/<your-project>**
- Environment Gate: **main-prod**
- Plan-Preview: `terraform-plan.yml` comments on PRs (same path filters)

Before every main-commit with Terraform changes:
1. Review the plan output in the PR comment — no surprises
2. Obtain explicit operator approval
3. Verify `auto-apply` mode in the TFC workspace (TFC-UI → Workspace-Settings)

No separate apply step — the merge is the apply.

---

## Branching

Infra-/platform-repo: primary branch `main`, optional `develop`.
No feature branches (harness-enforced).
Only PR `develop → main` for promotions (when develop is used).
