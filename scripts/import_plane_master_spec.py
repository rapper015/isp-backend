"""Idempotently import the 1,500-feature ISP master spec into Plane.

This importer owns only ``[SPEC]`` modules and ``[SPEC-*]`` work items. It does
not populate delivery Milestone modules. Use ``sync_plane_milestone_tasks.py``
for the source-traceable Milestone 0-10 capability tasks.

Usage:
    PLANE_API_KEY=... python scripts/import_plane_master_spec.py

The API key is read only from the environment and is never written to disk.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

PLANE_ORIGIN = "https://project.vndinfotech.in"
WORKSPACE = "main"
PROJECT_ID = "25b0a842-40c4-4f3e-b47b-ec45222ccba1"
SPEC_PATH = Path(__file__).resolve().parents[1] / "docs" / "TELECOM_ISP_BACKEND_MASTER_IMPLEMENTATION_SPEC.md"
API_ROOT = f"{PLANE_ORIGIN}/api/v1/workspaces/{WORKSPACE}/projects/{PROJECT_ID}"
MODULE_PREFIX = "[SPEC] "
ISSUE_PREFIX = "[SPEC-"


def request(api_key: str, method: str, path: str, payload: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_ROOT}{path}",
        data=body,
        method=method,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    )
    for attempt in range(15):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {429, 500, 502, 503, 504} and attempt < 14:
                retry_after = exc.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 60)
                print(f"Plane returned {exc.code}; retrying in {delay:.0f}s", flush=True)
                time.sleep(delay)
                continue
            raise RuntimeError(f"Plane {method} {path} failed ({exc.code}): {detail[:500]}") from exc


def rows(value) -> list[dict]:
    if isinstance(value, list):
        return value
    return value.get("results", []) if isinstance(value, dict) else []


def list_all(api_key: str, path: str) -> list[dict]:
    result: list[dict] = []
    cursor: str | None = None
    while True:
        separator = "&" if "?" in path else "?"
        suffix = "" if cursor is None else f"{separator}cursor={urllib.parse.quote(cursor)}"
        page = request(api_key, "GET", f"{path}{suffix}")
        batch = rows(page)
        result.extend(batch)
        total = page.get("total_results") if isinstance(page, dict) else None
        if not batch or (total is not None and len(result) >= total):
            return result
        next_cursor = page.get("next_cursor") if isinstance(page, dict) else None
        if not next_cursor or next_cursor == cursor:
            return result
        cursor = next_cursor


def parse_spec() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    in_matrix = False
    headers: list[str] = []
    for line in SPEC_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("## 25. Complete client feature matrix"):
            in_matrix = True
            continue
        if in_matrix and line.startswith("## 26."):
            break
        if not in_matrix or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == "ID":
            headers = cells
            continue
        if not headers or not cells or not cells[0].isdigit() or len(cells) != len(headers):
            continue
        records.append(dict(zip(headers, cells)))
    ids = [int(record["ID"]) for record in records]
    if len(records) != 1500 or ids != list(range(1, 1501)):
        raise RuntimeError(f"Expected feature IDs 1..1500, parsed {len(records)} rows")
    return records


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def module_description(name: str, records: list[dict[str, str]]) -> str:
    ids = [int(record["ID"]) for record in records]
    priorities = Counter(record["Priority"] for record in records)
    owners = sorted({record["Recommended Owner"] for record in records})
    return (
        f"Master-spec module '{name}'. Covers {len(records)} feature records "
        f"(IDs {min(ids)}–{max(ids)}; non-contiguous IDs possible). "
        f"Priorities: {dict(sorted(priorities.items()))}. Recommended owners: {', '.join(owners)}. "
        "Source: docs/TELECOM_ISP_BACKEND_MASTER_IMPLEMENTATION_SPEC.md."
    )


def issue_description(record: dict[str, str]) -> str:
    esc = lambda value: html.escape(value or "Not specified")
    criteria = [
        f"The feature implements: {record['Description']}.",
        "The authoritative owning service and database boundary are respected; no cross-service table access is introduced.",
        "Tenant context is required and enforced for all tenant-owned reads, writes, jobs, events, caches, and exports.",
        f"Access is enforced for the declared audience ({record['Access']}) with least privilege and deny-by-default behavior.",
        "The API validates input, returns stable error codes, supports safe retry/idempotency where applicable, and is documented for consumers.",
        "Every material state change creates an append-only audit record with actor, tenant, action, correlation ID, and timestamp.",
        "Unit, authorization, tenant-isolation, validation, integration/failure-path, and regression tests pass.",
        "Structured logs, metrics, health/readiness behavior, migrations, rollback notes, and operator runbook updates are included.",
        "No placeholder, fake-success response, silent no-op, hard-coded tenant, or unapproved external side effect remains.",
    ]
    if record["Source Event"] not in {"", "None", "N/A"}:
        criteria.append(f"The `{record['Source Event']}` event is emitted/consumed through an idempotent versioned contract as required.")
    criteria_html = "".join(f"<li>{esc(item)}</li>" for item in criteria)
    return (
        f"<h2>Objective</h2><p>{esc(record['Feature'])}: {esc(record['Description'])}</p>"
        "<h2>Specification traceability</h2><ul>"
        f"<li><strong>Feature ID:</strong> {esc(record['ID'])}</li>"
        f"<li><strong>Source module:</strong> {esc(record['Source Module'])}</li>"
        f"<li><strong>Submodule:</strong> {esc(record['Submodule'])}</li>"
        f"<li><strong>Recommended owner:</strong> {esc(record['Recommended Owner'])}</li>"
        f"<li><strong>Ownership confidence:</strong> {esc(record['Ownership Confidence'])}</li>"
        f"<li><strong>Access:</strong> {esc(record['Access'])}</li>"
        f"<li><strong>Priority:</strong> {esc(record['Priority'])}</li>"
        f"<li><strong>Dependencies:</strong> {esc(record['Source Dependencies'])}</li>"
        f"<li><strong>Source event:</strong> {esc(record['Source Event'])}</li>"
        f"<li><strong>Backend treatment:</strong> {esc(record['Backend Treatment'])}</li></ul>"
        f"<h2>Acceptance criteria</h2><ul>{criteria_html}</ul>"
        "<h2>Definition of done</h2><p>Implementation, migrations, security controls, tests, observability, documentation, and deployment/rollback evidence are complete and reviewed. Reference the feature ID in commits and test evidence.</p>"
    )


def main() -> int:
    api_key = os.environ.get("PLANE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("PLANE_API_KEY is required")
    records = parse_spec()
    by_module: dict[str, list[dict[str, str]]] = {}
    for record in records:
        by_module.setdefault(record["Source Module"], []).append(record)

    existing_modules = rows(request(api_key, "GET", "/modules/"))
    modules_by_name = {module["name"]: module for module in existing_modules}
    created_modules = 0
    for name, module_records in by_module.items():
        plane_name = f"{MODULE_PREFIX}{name}"
        if plane_name not in modules_by_name:
            module = request(api_key, "POST", "/modules/", {
                "name": plane_name,
                "description": module_description(name, module_records),
                "status": "backlog",
                "external_source": "telecom-isp-master-spec",
                "external_id": f"spec-module-{slug(name)}",
            })
            modules_by_name[plane_name] = module
            created_modules += 1
            time.sleep(1.1)

    existing_items = list_all(api_key, "/work-items/")
    existing_spec_ids: set[int] = set()
    for item in existing_items:
        match = re.match(r"^\[SPEC-(\d+)]", item.get("name", ""))
        if match:
            existing_spec_ids.add(int(match.group(1)))

    priority_map = {"P0": "urgent", "P1": "high", "P2": "medium", "P3": "low"}
    created_items = 0
    for index, record in enumerate(records, start=1):
        feature_id = int(record["ID"])
        if feature_id in existing_spec_ids:
            continue
        module = modules_by_name[f"{MODULE_PREFIX}{record['Source Module']}"]
        request(api_key, "POST", "/work-items/", {
            "name": f"[SPEC-{feature_id}] {record['Feature']}",
            "description_html": issue_description(record),
            "priority": priority_map.get(record["Priority"], "none"),
            "module": module["id"],
        })
        created_items += 1
        if created_items % 25 == 0:
            print(f"Created {created_items} work items (through feature {feature_id})", flush=True)
        time.sleep(1.1)

    final_items = list_all(api_key, "/work-items/")
    items_by_spec_id: dict[int, dict] = {}
    for item in final_items:
        match = re.match(r"^\[SPEC-(\d+)]", item.get("name", ""))
        if match:
            items_by_spec_id[int(match.group(1))] = item

    linked_items = 0
    member_ids: set[str] = set()
    membership_mismatches: list[dict[str, object]] = []
    for module_name, module_records in by_module.items():
        module = modules_by_name[f"{MODULE_PREFIX}{module_name}"]
        current_members = list_all(api_key, f"/modules/{module['id']}/module-issues/")
        current_member_ids = {item["id"] for item in current_members}
        member_ids.update(current_member_ids)
        desired_ids = [
            items_by_spec_id[int(record["ID"])]["id"]
            for record in module_records
            if int(record["ID"]) in items_by_spec_id
        ]
        unlinked_ids = [item_id for item_id in desired_ids if item_id not in current_member_ids]
        for start in range(0, len(unlinked_ids), 50):
            chunk = unlinked_ids[start:start + 50]
            request(api_key, "POST", f"/modules/{module['id']}/module-issues/", {"issues": chunk})
            member_ids.update(chunk)
            linked_items += len(chunk)
            time.sleep(1.1)
        current_member_ids.update(unlinked_ids)
        if current_member_ids != set(desired_ids):
            membership_mismatches.append({
                "module": module_name,
                "expected": len(desired_ids),
                "actual": len(current_member_ids),
            })

    final_items = list_all(api_key, "/work-items/")
    final_spec_ids = {
        int(match.group(1))
        for item in final_items
        if (match := re.match(r"^\[SPEC-(\d+)]", item.get("name", "")))
    }
    missing = sorted(set(range(1, 1501)) - final_spec_ids)
    duplicates = len(final_spec_ids) != sum(1 for item in final_items if item.get("name", "").startswith(ISSUE_PREFIX))
    unlinked = [
        item.get("name") for item in final_items
        if item.get("name", "").startswith(ISSUE_PREFIX) and item.get("id") not in member_ids
    ]
    summary = {
        "source_records": len(records),
        "source_modules": len(by_module),
        "created_modules": created_modules,
        "created_work_items": created_items,
        "linked_work_items": linked_items,
        "final_spec_work_items": len(final_spec_ids),
        "missing_feature_ids": missing[:20],
        "duplicate_spec_ids": duplicates,
        "unlinked_spec_work_items": unlinked[:20],
        "module_membership_mismatches": membership_mismatches,
    }
    print(json.dumps(summary, indent=2))
    return 0 if not missing and not duplicates and not unlinked and not membership_mismatches else 2


if __name__ == "__main__":
    sys.exit(main())
