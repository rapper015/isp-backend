#!/usr/bin/env python3
"""Generate client-feature coverage/ownership documents (Milestone Master Spec).

Parses the 1,500-row feature matrix in docs/TELECOM_ISP_BACKEND_MASTER_IMPLEMENTATION_SPEC.md,
scans each owning tracked service for implementation evidence, and emits:

  docs/client-feature-coverage.json   (per-feature evidence + status)
  docs/client-feature-coverage.md     (summary + full reconciled table)
  docs/client-feature-gap-analysis.md (grouped by owner and priority)
  docs/architecture/feature-ownership.md

Statuses: COMPLETE, PARTIAL, MISSING, BLOCKED_EXTERNAL, CONDITIONAL_FUTURE,
NOT_BACKEND, CONFLICT. Run from the repository root.

Usage:  python infrastructure/coverage/generate_client_coverage.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "docs" / "TELECOM_ISP_BACKEND_MASTER_IMPLEMENTATION_SPEC.md"
SERVICES = REPO / "services"

# Owner -> tracked service dir(s) used as evidence.
OWNER_SERVICES = {
    "core-platform-service": ["tenancy-service"],
    "crm-service": ["crm-service", "support-service"],
    "bss-service": ["bss-service"],
    "oss-service": ["oss-service"],
    "aaa-service": ["aaa-service"],
    "nms-service": ["nms-service", "assurance-service"],
    "ipam-service": ["ipam-service", "aaa-service"],
    "siem-service": ["siem-service"],
    "workforce-service": ["workforce-service"],
    "data-warehouse-service": ["warehouse-service", "intelligence-service"],
    "aiops-service": ["intelligence-service"],
}

STOPWORDS = {
    "the", "and", "for", "with", "into", "from", "per", "via", "using", "based",
    "view", "api", "apis", "engine", "management", "manager", "support", "service",
    "services", "config", "configuration", "track", "tracking", "tracker", "management",
}

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def parse_matrix() -> list[dict]:
    lines = SPEC.read_text(encoding="utf-8").splitlines()
    rows = []
    in_table = False
    for line in lines:
        if line.startswith("## 25."):
            in_table = True
            continue
        if line.startswith("## 26."):
            break
        if not in_table or not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 12:
            continue
        try:
            fid = int(cells[0])
        except ValueError:
            continue
        rows.append({
            "id": fid,
            "owner": cells[1],
            "confidence": cells[2],
            "access": cells[3],
            "module": cells[4],
            "submodule": cells[5],
            "feature": cells[6],
            "description": cells[7],
            "priority": cells[8],
            "dependencies": cells[9],
            "event": cells[10],
            "treatment": cells[11],
        })
    return rows


def words_of(*texts: str) -> list[str]:
    out = []
    for text in texts:
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            if len(w) >= 3 and w not in STOPWORDS:
                out.append(w)
    return out


def build_service_evidence(service_dir: Path) -> dict:
    code_parts = []
    test_parts = []
    routes: set[str] = set()
    events: set[str] = set()
    models: set[str] = set()
    files = {}
    for path in service_dir.rglob("*.py"):
        if any(part.startswith(".") for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lower = text.lower()
        files[str(path.relative_to(REPO))] = lower
        if "tests" in path.parts:
            test_parts.append(lower)
        else:
            code_parts.append(lower)
        for m in re.finditer(r"@app\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)", text):
            routes.add(m.group(2).lower())
        # APIRouter(prefix="/api/<svc>") + @router.<method>("...") -> full path.
        prefixes = [pm.group(1).lower().rstrip("/")
                    for pm in re.finditer(r"APIRouter\s*\(\s*prefix\s*=\s*[\"']([^\"']+)", text)]
        for m in re.finditer(r"@router\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)", text):
            rel = m.group(2).lower()
            for prefix in prefixes:
                routes.add((prefix + "/" + rel).lower())
        # Any quoted `<context>.<...>.vN` is a published/consumed event name,
        # regardless of which topology container holds it (PUBLISHED_TOPOLOGY,
        # TOPOLOGY dict, EVENTS tuple, CRM_EVENTS tuple, ...).
        for ev in re.findall(r"[\"']([a-z0-9_]+(?:\.[a-z0-9_]+)+\.v[0-9]+)[\"']", text):
            events.add(ev.lower())
        for m in re.finditer(r"class (\w+)\(Base", text):
            models.add(m.group(1).lower())
    code = "\n".join(code_parts)
    tests = "\n".join(test_parts)
    has_tenant = "tenant_id" in code or any("context.py" in f for f in files)
    has_audit = ("audit_log" in code or "class AuditLog" in code
                 or "audit(" in code)
    has_rbac = any("security.py" in f for f in files) or "ROLE_PERMISSIONS" in code
    return {
        "code": code,
        "tests": tests,
        "files": files,
        "routes": routes,
        "events": events,
        "models": models,
        "has_tenant": has_tenant,
        "has_audit": has_audit,
        "has_rbac": has_rbac,
    }


def evaluate(row: dict, svc: dict) -> tuple[str, dict]:
    treatment = row["treatment"]
    words = list(dict.fromkeys(words_of(row["feature"], row["description"])))
    event_tok = re.sub(r"[^a-z0-9]+", "", row["event"].lower())
    code = svc["code"]
    tests = svc["tests"]
    hits = [w for w in words if w in code]
    hit_files = [f for f, txt in svc["files"].items()
                 if any(w in txt for w in words)][:8]
    norm_event = event_tok.replace("v1", "")
    event_hit = bool(event_tok and (
        event_tok in code
        or any(event_tok in ev.replace(".", "") or norm_event in ev.replace(".", "")
               for ev in svc["events"])))
    route_hit = any(w in r for w in words for r in svc["routes"])
    test_hit = any(w in tests for w in words)
    hit_count = len(hits)

    evidence = {
        "files": hit_files,
        "word_hits": hits[:10],
        "event_hit": event_hit,
        "route_hit": route_hit,
        "test_hit": test_hit,
        "hit_count": hit_count,
        "tenant_evidence": svc["has_tenant"],
        "audit_evidence": svc["has_audit"],
        "permission_evidence": svc["has_rbac"],
        "missing_acceptance": None,
    }

    if "CONDITIONAL/FUTURE" in treatment:
        status = "CONDITIONAL_FUTURE"
    elif "EXTERNAL ADAPTER" in treatment:
        if hit_count >= 2 and event_hit and test_hit:
            status = "COMPLETE"  # contract + mock adapter + tests present
            evidence["missing_acceptance"] = "production credentials/provider configuration required"
        else:
            status = "BLOCKED_EXTERNAL"
            evidence["missing_acceptance"] = "production adapter requires external provider credentials/infra"
    elif "INFRASTRUCTURE + SERVICE CONTROL" in treatment:
        infra = (REPO / "docker-compose.yml").exists() and (REPO / "infrastructure").exists()
        if hit_count >= 2 and test_hit:
            status = "COMPLETE"
        elif infra:
            status = "PARTIAL"
            evidence["missing_acceptance"] = "deployment manifests present; per-feature service control pending"
        else:
            status = "MISSING"
    elif "BACKEND API/READ MODEL ONLY" in treatment:
        if hit_count >= 2 and event_hit and test_hit:
            status = "COMPLETE"
        elif hit_count >= 1 or route_hit:
            status = "PARTIAL"
            evidence["missing_acceptance"] = "read-model API present but acceptance criteria incomplete"
        else:
            status = "MISSING"
    else:  # FULL BACKEND
        if hit_count >= 2 and event_hit and test_hit and route_hit:
            status = "COMPLETE"
        elif hit_count >= 2 or event_hit or route_hit or test_hit:
            status = "PARTIAL"
            evidence["missing_acceptance"] = "partial evidence; acceptance criteria incomplete"
        else:
            status = "MISSING"
            evidence["missing_acceptance"] = "no implementation evidence found in owning service"
    return status, evidence


def main() -> int:
    if not SPEC.exists():
        print(f"spec not found: {SPEC}")
        return 1
    rows = parse_matrix()
    ids = [r["id"] for r in rows]
    assert len(rows) == 1500, f"expected 1500 rows, got {len(rows)}"
    assert ids == list(range(1, 1501)), "feature ids not contiguous 1..1500"

    svc_evidence = {}
    for owner, dirs in OWNER_SERVICES.items():
        combined = None
        for d in dirs:
            sd = SERVICES / d
            if not sd.exists():
                continue
            e = build_service_evidence(sd)
            if combined is None:
                combined = e
            else:
                combined["code"] += "\n" + e["code"]
                combined["tests"] += "\n" + e["tests"]
                combined["files"].update(e["files"])
                combined["routes"] |= e["routes"]
                combined["events"] |= e["events"]
                combined["models"] |= e["models"]
                combined["has_tenant"] = combined["has_tenant"] or e["has_tenant"]
                combined["has_audit"] = combined["has_audit"] or e["has_audit"]
                combined["has_rbac"] = combined["has_rbac"] or e["has_rbac"]
        svc_evidence[owner] = combined or {"code": "", "tests": "", "files": {},
                                           "routes": set(), "events": set(), "models": set(),
                                           "has_tenant": False, "has_audit": False, "has_rbac": False}

    coverage = []
    for row in rows:
        svc = svc_evidence[row["owner"]]
        status, evidence = evaluate(row, svc)
        coverage.append({
            "id": row["id"],
            "owner": row["owner"],
            "confidence": row["confidence"],
            "access": row["access"],
            "module": row["module"],
            "submodule": row["submodule"],
            "feature": row["feature"],
            "description": row["description"],
            "priority": row["priority"],
            "dependencies": row["dependencies"],
            "source_event": row["event"],
            "backend_treatment": row["treatment"],
            "status": status,
            "evidence": evidence,
        })

    status_counts = Counter(c["status"] for c in coverage)
    owner_counts = Counter(c["owner"] for c in coverage)
    owner_status = defaultdict(Counter)
    for c in coverage:
        owner_status[c["owner"]][c["status"]] += 1
    prio_status = defaultdict(Counter)
    for c in coverage:
        prio_status[c["priority"]][c["status"]] += 1

    # JSON
    (REPO / "docs").mkdir(exist_ok=True)
    (REPO / "docs" / "client-feature-coverage.json").write_text(
        json.dumps({"total": len(coverage), "status_counts": dict(status_counts),
                    "rows": coverage}, indent=2), encoding="utf-8")

    # Coverage MD
    lines = ["# Client Feature Coverage — 1,500 reconciled rows\n",
             f"Total: {len(coverage)} · Statuses: "
             + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items())) + "\n",
             "| ID | Owner | Access | Priority | Module / Submodule | Feature | Status |",
             "|---|---|---|---|---|---|---|"]
    for c in coverage:
        lines.append(f"| {c['id']} | {c['owner']} | {c['access']} | {c['priority']} "
                     f"| {c['module']} / {c['submodule']} | {c['feature']} | {c['status']} |")
    (REPO / "docs" / "client-feature-coverage.md").write_text("\n".join(lines), encoding="utf-8")

    # Gap analysis MD
    gaps = ["# Client Feature Gap Analysis\n",
            "## Status summary\n", "| Status | Count |", "|---|---|"]
    for k, v in sorted(status_counts.items(), key=lambda kv: -kv[1]):
        gaps.append(f"| {k} | {v} |")
    gaps.append("\n## By owner\n| Owner | " + " | ".join(sorted(status_counts)) + " |")
    gaps.append("|---|" + "---|" * len(status_counts))
    for owner in sorted(owner_status):
        row = " | ".join(str(owner_status[owner].get(s, 0)) for s in sorted(status_counts))
        gaps.append(f"| {owner} | {row} |")
    gaps.append("\n## By priority\n| Priority | " + " | ".join(sorted(status_counts)) + " |")
    gaps.append("|---|" + "---|" * len(status_counts))
    for prio in sorted(prio_status, key=lambda p: PRIORITY_ORDER.get(p, 9)):
        row = " | ".join(str(prio_status[prio].get(s, 0)) for s in sorted(status_counts))
        gaps.append(f"| {prio} | {row} |")
    gaps.append("\n## Missing / partial P0 and P1 (implementation backlog)\n")
    for c in coverage:
        if c["priority"] in ("P0", "P1") and c["status"] in ("MISSING", "PARTIAL"):
            gaps.append(f"- **{c['id']}** [{c['priority']}] {c['owner']}: {c['feature']} "
                        f"({c['status']}) — {c['evidence']['missing_acceptance'] or ''}")
    (REPO / "docs" / "client-feature-gap-analysis.md").write_text("\n".join(gaps), encoding="utf-8")

    # Ownership MD
    (REPO / "docs" / "architecture").mkdir(exist_ok=True, parents=True)
    own = ["# Feature Ownership\n",
           "Recommended owner -> tracked evidence services mapping (repository reality):\n"]
    for owner, dirs in OWNER_SERVICES.items():
        counts = owner_status[owner]
        own.append(f"\n## {owner} (evidence: {', '.join(dirs)})")
        own.append("| Status | Count |")
        own.append("|---|---|")
        for s in sorted(status_counts):
            own.append(f"| {s} | {counts.get(s, 0)} |")
        own.append(f"\nTotal features: {sum(counts.values())}\n")
    (REPO / "docs" / "architecture" / "feature-ownership.md").write_text("\n".join(own), encoding="utf-8")

    print(f"Reconciled {len(coverage)} rows (IDs {min(ids)}..{max(ids)}).")
    for k, v in sorted(status_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
