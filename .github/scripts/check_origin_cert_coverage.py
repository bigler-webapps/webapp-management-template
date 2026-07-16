#!/usr/bin/env python3
"""Stage 3-A: Origin-cert coverage / drift check.

Every PROXIED app domain (project.yaml `environments.production` with
`use_traefik: true`) must be TLS-covered, either by a Cloudflare Origin
Certificate (one per zone, provisioned via deploy-traefik from secrets.yaml) or
by a zone that intentionally uses Cloudflare Universal SSL (no origin cert).

This catches two drifts WITHOUT trying to mechanically generate the cert list
(the zones are a small, curated, low-churn set with one non-mechanical secret
var name — full generation is poor ROI; see the registry memo):
  - GAP   : a proxied domain not covered by any configured cert zone nor an
            allowlisted Universal-SSL zone -> potential TLS outage. Fails (exit 1).
  - UNUSED: a configured cert zone no app domain uses anymore -> dead cert. Warns.

Coverage is by SUFFIX match (domain == zone or domain endswith "."+zone), so no
public-suffix extraction is needed.

Sources (single sources of truth, nothing hand-maintained twice):
  - proxied domains : each app repo's project.yaml (via resolve_app_inventory)
  - configured certs: secrets.yaml `proton://YOUR-VAULT/domain-<zone>/origin_cert`
  - universal zones : project.yaml `infra.tls_universal_zones`
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

import resolve_app_inventory as rai


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def configured_cert_zones(secrets_path: Path) -> set[str]:
    if not secrets_path.exists():
        fail(f"secrets file not found: {secrets_path}")
    text = secrets_path.read_text(encoding="utf-8")
    # proton://YOUR-VAULT/domain-<zone>/origin_cert  -> <zone>
    zones = set(re.findall(r"proton://YOUR-VAULT/domain-([^/]+)/origin_cert", text))
    if not zones:
        fail("No origin-cert zones found in secrets.yaml (proton domain-<zone>/origin_cert)")
    return zones


def exempt_zones(config_path: Path) -> set[str]:
    """Zones intentionally without one of OUR origin certs — either Cloudflare
    Universal SSL, or a domain we don't own / don't manage TLS for (external).
    From project.yaml `infra.tls_exempt_zones`."""
    if not config_path.exists():
        return set()
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    zones = ((data.get("infra") or {}).get("tls_exempt_zones")) or []
    if not isinstance(zones, list):
        fail("infra.tls_exempt_zones must be a list")
    return {str(z).strip() for z in zones if str(z).strip()}


def covered_by(domain: str, zones: set[str]) -> str | None:
    for z in zones:
        if domain == z or domain.endswith("." + z):
            return z
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Check that every proxied app domain is TLS-covered.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--repos-dir", help="Local dir of <repo>/project.yaml checkouts.")
    src.add_argument("--github-app", action="store_true", help="Enumerate org repos via GH_APP_TOKEN.")
    parser.add_argument("--secrets", default="secrets.yaml", help="secrets.yaml providing the origin-cert zones.")
    parser.add_argument("--config", default="project.yaml", help="project.yaml providing infra.tls_universal_zones.")
    parser.add_argument("--min-apps", type=int, default=10, help="Fail-closed floor: abort if fewer apps enumerate (degraded run must not falsely pass).")
    args = parser.parse_args()

    if args.github_app:
        import os

        token = os.environ.get("GH_APP_TOKEN", "").strip()
        if not token:
            fail("--github-app requires GH_APP_TOKEN")
        apps = rai.derive_from_github(token)
    else:
        apps = rai.derive_from_dir(Path(args.repos_dir))

    # Honour the same infra-side exclusion as the registry: repos not on our
    # infra (e.g. an externally-hosted app) are not our TLS concern.
    exclude = rai.load_exclude(Path(args.config))
    if exclude:
        apps = [a for a in apps if a["repo"].lower() not in exclude and a["project_name"].lower() not in exclude]

    if len(apps) < args.min_apps:
        fail(f"Only {len(apps)} apps enumerated, below --min-apps={args.min_apps} — degraded run, not trusting coverage.")

    cert_zones = configured_cert_zones(Path(args.secrets))
    exempt = exempt_zones(Path(args.config))
    all_zones = cert_zones | exempt

    # Collect proxied production domains -> which app declares each.
    domain_owner: dict[str, str] = {}
    for app in apps:
        if not app.get("use_traefik", True):
            continue
        for d in app["domains"]:
            domain_owner.setdefault(d, app["project_name"])

    gaps = sorted(
        (d, owner) for d, owner in domain_owner.items() if covered_by(d, all_zones) is None
    )
    used_cert_zones = {
        z
        for d in domain_owner
        for z in [covered_by(d, cert_zones)]
        if z is not None
    }
    unused = sorted(cert_zones - used_cert_zones)

    print(f"Proxied domains checked: {len(domain_owner)}")
    print(f"Configured cert zones:   {sorted(cert_zones)}")
    print(f"TLS-exempt zones:        {sorted(exempt)}")

    if unused:
        print("\nWARN: origin-cert zones with no proxied app domain (dead cert?):")
        for z in unused:
            print(f"   - {z}")

    if gaps:
        print("\nFAIL: proxied domains with NO origin cert and not a TLS-exempt zone:")
        for d, owner in gaps:
            print(f"   - {d}  (app: {owner})")
        print(
            "\nFix: add the zone's origin cert (secrets.yaml + deploy-traefik write_cert + "
            "dynamic/origin-certs.yml), OR list the zone under infra.tls_exempt_zones "
            "if it intentionally uses Universal SSL or is an external domain we don't manage."
        )
        sys.exit(1)

    print("\nOK: all proxied app domains are TLS-covered.")


if __name__ == "__main__":
    main()
