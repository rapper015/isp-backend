"""Synchronize source-traceable milestone capability tasks into Plane.

This is intentionally separate from ``import_plane_master_spec.py``:

* this script manages Plane delivery modules ``Milestone 1`` .. ``Milestone 11``;
* the master-spec importer manages the 1,500 ``[SPEC-*]`` feature backlog;
* no work item is deleted by this script;
* ``--detach-spec-items`` only removes accidental module membership.

The authoritative source is ``docs/Milestone_Doc.docx``. Every managed work
item includes its document section and source paragraphs, so its origin is
visible directly in Plane.

Examples (PowerShell):

    python scripts/sync_plane_milestone_tasks.py --dry-run
    $env:PLANE_API_KEY = "..."
    python scripts/sync_plane_milestone_tasks.py --audit
    python scripts/sync_plane_milestone_tasks.py --apply
    python scripts/sync_plane_milestone_tasks.py --apply --detach-spec-items

The API key is read only from the environment and is never written to disk.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree


DEFAULT_ORIGIN = "https://project.vndinfotech.in"
DEFAULT_WORKSPACE = "main"
DEFAULT_PROJECT_ID = "25b0a842-40c4-4f3e-b47b-ec45222ccba1"
SOURCE_PATH = Path(__file__).resolve().parents[1] / "docs" / "Milestone_Doc.docx"
SOURCE_LABEL = "docs/Milestone_Doc.docx"
MANAGED_ITEM_RE = re.compile(r"^\[M(?P<milestone>\d+)\.(?P<capability>\d+)]\s+")
SPEC_ITEM_RE = re.compile(r"^\[SPEC-\d+]\s+")
MILESTONE_NAME_RE = re.compile(r"(?:^|\b)Milestone[ -](?P<number>11|10|[1-9])(?:\b|\s)", re.I)
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

LEGACY_ITEM_ALIASES = {
    "[M2.1] Lead Pipeline Management": ("Lead Pipeline",),
    "[M2.2] KYC and Identity Verification": ("KYC and Identity Verification",),
    "[M2.4] Lifecycle and Risk Management": ("Lifecycle",),
}


@dataclass
class Capability:
    number: str
    source_section: str
    title: str
    paragraphs: list[str] = field(default_factory=list)

    @property
    def capability_index(self) -> int:
        return int(self.number.split(".", 1)[1])


@dataclass
class Milestone:
    number: int
    source_section: str
    title: str
    objective: list[str] = field(default_factory=list)
    outcome: list[str] = field(default_factory=list)
    capabilities: list[Capability] = field(default_factory=list)

    @property
    def plane_number(self) -> int:
        return self.number + 1

    @property
    def module_name(self) -> str:
        return f"Milestone {self.plane_number} - {self.title}"

    def item_name(self, capability: Capability) -> str:
        return f"[M{self.plane_number}.{capability.capability_index}] {capability.title}"


def docx_paragraphs(path: Path) -> list[str]:
    if not path.is_file():
        raise RuntimeError(f"Milestone source document is missing: {path}")
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{W_NS}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{W_NS}t")).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def parse_milestones(path: Path = SOURCE_PATH) -> list[Milestone]:
    milestone_heading = re.compile(
        r"^(?P<section>\d+)\.\s+Milestone\s+(?P<number>10|[0-9])\s+[–—-]\s+(?P<title>.+)$"
    )
    section_heading = re.compile(r"^(?P<section>\d+\.\d+)\s+(?P<title>.+)$")
    explicit_capability = re.compile(
        r"^Sub-Milestone\s+(?P<number>\d+\.\d+)\s+[–—-]\s+(?P<title>.+)$",
        re.I,
    )

    milestones: list[Milestone] = []
    current: Milestone | None = None
    target: list[str] | None = None
    active_capability: Capability | None = None

    for paragraph in docx_paragraphs(path):
        match = milestone_heading.match(paragraph)
        if match:
            current = Milestone(
                number=int(match.group("number")),
                source_section=match.group("section"),
                title=match.group("title").strip(),
            )
            milestones.append(current)
            target = None
            active_capability = None
            continue
        if current is None:
            continue

        match = section_heading.match(paragraph)
        if match:
            source_section = match.group("section")
            heading = match.group("title").strip()
            if heading.casefold() == "objective":
                target = current.objective
                active_capability = None
                continue
            if heading.casefold().startswith(f"milestone {current.number} outcome"):
                target = current.outcome
                active_capability = None
                continue

            explicit = explicit_capability.match(heading)
            if explicit:
                capability_number = explicit.group("number")
                title = explicit.group("title").strip()
            else:
                # Milestones 3-10 call these sections capabilities instead of
                # "Sub-Milestones". Normalize the document section to Mx.y.
                subsection = int(source_section.split(".", 1)[1]) - 1
                capability_number = f"{current.number}.{subsection}"
                title = heading
            active_capability = Capability(
                number=capability_number,
                source_section=source_section,
                title=title,
            )
            current.capabilities.append(active_capability)
            target = active_capability.paragraphs
            continue

        if paragraph.startswith("Estimated Development Effort:"):
            continue
        if target is not None:
            target.append(paragraph)

    numbers = [item.number for item in milestones]
    if numbers != list(range(11)):
        raise RuntimeError(f"Expected milestones 0..10, parsed {numbers}")
    for milestone in milestones:
        if not milestone.capabilities:
            raise RuntimeError(f"Milestone {milestone.number} has no parsed capabilities")
    return milestones


def api_rows(value: object) -> list[dict]:
    if isinstance(value, list):
        return value
    return value.get("results", []) if isinstance(value, dict) else []


class PlaneClient:
    def __init__(self, api_key: str, origin: str, workspace: str, project_id: str):
        self.api_key = api_key
        self.root = (
            f"{origin.rstrip('/')}/api/v1/workspaces/"
            f"{urllib.parse.quote(workspace)}/projects/{urllib.parse.quote(project_id)}"
        )

    def request(self, method: str, path: str, payload: dict | None = None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.root}{path}",
            data=body,
            method=method,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
        )
        for attempt in range(10):
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in {429, 500, 502, 503, 504} and attempt < 9:
                    retry_after = exc.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 30)
                    print(f"Plane returned {exc.code}; retrying in {delay:.0f}s", flush=True)
                    time.sleep(delay)
                    continue
                raise RuntimeError(
                    f"Plane {method} {path} failed ({exc.code}): {detail[:500]}"
                ) from exc

    def list_all(self, path: str) -> list[dict]:
        result: list[dict] = []
        cursor: str | None = None
        while True:
            separator = "&" if "?" in path else "?"
            suffix = "" if cursor is None else f"{separator}cursor={urllib.parse.quote(cursor)}"
            page = self.request("GET", f"{path}{suffix}")
            batch = api_rows(page)
            result.extend(batch)
            total = page.get("total_results") if isinstance(page, dict) else None
            if not batch or (total is not None and len(result) >= total):
                return result
            next_cursor = page.get("next_cursor") if isinstance(page, dict) else None
            if not next_cursor or next_cursor == cursor:
                return result
            cursor = next_cursor


def module_number(module: dict) -> int | None:
    external_id = str(module.get("external_id") or "")
    match = re.fullmatch(r"delivery-milestone-(11|10|[1-9])", external_id)
    if match:
        return int(match.group(1))
    match = MILESTONE_NAME_RE.search(str(module.get("name") or ""))
    return int(match.group("number")) if match else None


def module_description(milestone: Milestone) -> str:
    objective = " ".join(milestone.objective)
    return (
        f"Plane Delivery Milestone {milestone.plane_number}: {milestone.title}. "
        f"Contains {len(milestone.capabilities)} capability tasks. "
        f"Authoritative source: {SOURCE_LABEL}, source Milestone {milestone.number}, "
        f"section {milestone.source_section}. "
        f"Objective: {objective}"
    )


def item_description(milestone: Milestone, capability: Capability) -> str:
    esc = lambda value: html.escape(value, quote=True)
    requirements = "".join(f"<p>{esc(text)}</p>" for text in capability.paragraphs)
    return (
        "<h2>Source and scope</h2><ul>"
        f"<li><strong>Plane delivery milestone:</strong> {milestone.plane_number} — {esc(milestone.title)}</li>"
        f"<li><strong>Source-document milestone:</strong> {milestone.number}</li>"
        f"<li><strong>Capability:</strong> {esc(capability.number)} — {esc(capability.title)}</li>"
        f"<li><strong>Authoritative source:</strong> <code>{esc(SOURCE_LABEL)}</code></li>"
        f"<li><strong>Source section:</strong> {esc(capability.source_section)}</li>"
        "</ul><p>This is a milestone-delivery capability, not a generated 1,500-feature SPEC item.</p>"
        f"<h2>Source requirements</h2>{requirements}"
        "<h2>Definition of done</h2><ul>"
        "<li>The source requirements above are implemented in the owning service boundary.</li>"
        "<li>Tenant isolation, authorization, validation, auditability, and failure paths are tested.</li>"
        "<li>API/event contracts, migrations, observability, deployment, and rollback documentation are complete.</li>"
        "<li>Implementation evidence references this Plane item and its milestone capability number.</li>"
        "</ul>"
    )


def print_source_map(milestones: list[Milestone]) -> None:
    for milestone in milestones:
        print(f"{milestone.module_name} [{SOURCE_LABEL}, section {milestone.source_section}]")
        for capability in milestone.capabilities:
            print(
                f"  {milestone.item_name(capability)} "
                f"[{SOURCE_LABEL}, section {capability.source_section}]"
            )


def audit_plane(client: PlaneClient) -> dict:
    """Return milestone membership classified by its visible source prefix."""
    report: dict[str, dict[str, object]] = {}
    matched_numbers: set[int] = set()
    for module in client.list_all("/modules/"):
        number = module_number(module)
        if number is None:
            continue
        if number in matched_numbers:
            raise RuntimeError(
                f"Multiple Plane modules match Milestone {number}; "
                "rename or archive the duplicate before synchronization"
            )
        matched_numbers.add(number)
        members = client.list_all(f"/modules/{module['id']}/module-issues/")
        managed: list[str] = []
        wrong_milestone: list[str] = []
        for item in members:
            match = MANAGED_ITEM_RE.match(item.get("name", ""))
            if match and int(match.group("milestone")) == number:
                managed.append(item.get("name", ""))
            elif match:
                wrong_milestone.append(item.get("name", ""))
        spec = [item.get("name", "") for item in members if SPEC_ITEM_RE.match(item.get("name", ""))]
        unknown = [
            item.get("name", "")
            for item in members
            if not MANAGED_ITEM_RE.match(item.get("name", ""))
            and not SPEC_ITEM_RE.match(item.get("name", ""))
        ]
        report[str(number)] = {
            "module_id": module.get("id"),
            "module_name": module.get("name"),
            "managed_capability_tasks": managed,
            "capability_tasks_from_wrong_milestone": wrong_milestone,
            "misplaced_spec_tasks": spec,
            "unknown_or_manual_tasks": unknown,
        }
    return {"mode": "audit", "milestone_modules": report}


def synchronize(client: PlaneClient, milestones: list[Milestone], detach_spec_items: bool) -> dict:
    modules = client.list_all("/modules/")
    modules_by_number: dict[int, dict] = {}
    ambiguous: dict[int, list[str]] = {}
    for module in modules:
        number = module_number(module)
        if number is None:
            continue
        if number in modules_by_number:
            ambiguous.setdefault(number, [modules_by_number[number]["name"]]).append(module["name"])
        else:
            modules_by_number[number] = module
    if ambiguous:
        raise RuntimeError(
            "Multiple Plane modules match the same milestone; rename/archive duplicates first: "
            + json.dumps(ambiguous, ensure_ascii=False)
        )

    created_modules = 0
    updated_modules = 0
    created_items = 0
    updated_items = 0
    linked_items = 0
    detached_spec_items = 0
    unrelated_by_module: dict[str, list[str]] = {}

    all_items = client.list_all("/work-items/")
    managed_by_name = {
        item.get("name", ""): item
        for item in all_items
        if MANAGED_ITEM_RE.match(item.get("name", ""))
    }
    all_items_by_name = {item.get("name", ""): item for item in all_items}

    for milestone in milestones:
        module = modules_by_number.get(milestone.plane_number)
        desired_module_description = module_description(milestone)
        if module is None:
            module = client.request("POST", "/modules/", {
                "name": milestone.module_name,
                "description": desired_module_description,
                "status": "planned",
                "external_source": "milestone-delivery-document",
                "external_id": f"delivery-milestone-{milestone.plane_number}",
            })
            modules_by_number[milestone.plane_number] = module
            created_modules += 1
        elif (
            module.get("name") != milestone.module_name
            or module.get("description") != desired_module_description
        ):
            module_payload = {
                "name": milestone.module_name,
                "description": desired_module_description,
                "external_source": "milestone-delivery-document",
                "external_id": f"delivery-milestone-{milestone.plane_number}",
            }
            updated_module = client.request("PATCH", f"/modules/{module['id']}/", module_payload)
            if isinstance(updated_module, dict) and updated_module.get("id"):
                module = updated_module
            else:
                # Some self-hosted Plane versions return 200/204 without the
                # updated object. Retain the already known immutable ID.
                module = {**module, **module_payload}
            modules_by_number[milestone.plane_number] = module
            updated_modules += 1

        current_members = client.list_all(f"/modules/{module['id']}/module-issues/")
        current_ids = {item["id"] for item in current_members}
        unrelated = [
            item.get("name", "")
            for item in current_members
            if not MANAGED_ITEM_RE.match(item.get("name", ""))
            and not SPEC_ITEM_RE.match(item.get("name", ""))
        ]
        if unrelated:
            unrelated_by_module[module["name"]] = unrelated

        if detach_spec_items:
            for item in current_members:
                if SPEC_ITEM_RE.match(item.get("name", "")):
                    client.request(
                        "DELETE",
                        f"/modules/{module['id']}/module-issues/{item['id']}/",
                    )
                    current_ids.discard(item["id"])
                    detached_spec_items += 1

        for capability in milestone.capabilities:
            description = item_description(milestone, capability)
            desired_name = milestone.item_name(capability)
            item = managed_by_name.get(desired_name)
            if item is None:
                item = next(
                    (
                        all_items_by_name.get(alias)
                        for alias in LEGACY_ITEM_ALIASES.get(desired_name, ())
                        if all_items_by_name.get(alias) is not None
                    ),
                    None,
                )
            payload = {
                "name": desired_name,
                "description_html": description,
                "priority": "high",
                "module": module["id"],
            }
            if item is None:
                item = client.request("POST", "/work-items/", payload)
                managed_by_name[desired_name] = item
                created_items += 1
            else:
                # Reapply the canonical source description and milestone module.
                known_item = item
                updated_item = client.request("PATCH", f"/work-items/{item['id']}/", payload)
                if isinstance(updated_item, dict) and updated_item.get("id"):
                    item = updated_item
                else:
                    item = {**known_item, **payload}
                managed_by_name[desired_name] = item
                updated_items += 1
            if item["id"] not in current_ids:
                client.request(
                    "POST",
                    f"/modules/{module['id']}/module-issues/",
                    {"issues": [item["id"]]},
                )
                current_ids.add(item["id"])
                linked_items += 1

    return {
        "source": SOURCE_LABEL,
        "milestones": len(milestones),
        "capability_tasks": sum(len(item.capabilities) for item in milestones),
        "created_modules": created_modules,
        "updated_modules": updated_modules,
        "created_items": created_items,
        "updated_items": updated_items,
        "linked_items": linked_items,
        "detached_spec_items": detached_spec_items,
        "unrelated_items_left_untouched": unrelated_by_module,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Parse and print the source map; make no API calls (default).")
    mode.add_argument("--audit", action="store_true", help="Read Plane and classify current milestone work items; make no changes.")
    mode.add_argument("--apply", action="store_true", help="Create/update Plane milestone modules and capability tasks.")
    parser.add_argument(
        "--detach-spec-items",
        action="store_true",
        help="With --apply, remove [SPEC-*] membership from milestone modules without deleting work items.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.detach_spec_items and not args.apply:
        raise RuntimeError("--detach-spec-items requires --apply")
    milestones = parse_milestones()
    if not args.apply and not args.audit:
        print_source_map(milestones)
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "milestones": len(milestones),
                    "capability_tasks": sum(len(item.capabilities) for item in milestones),
                },
                indent=2,
            )
        )
        return 0

    api_key = os.environ.get("PLANE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("PLANE_API_KEY is required with --audit or --apply")
    client = PlaneClient(
        api_key=api_key,
        origin=os.environ.get("PLANE_ORIGIN", DEFAULT_ORIGIN),
        workspace=os.environ.get("PLANE_WORKSPACE", DEFAULT_WORKSPACE),
        project_id=os.environ.get("PLANE_PROJECT_ID", DEFAULT_PROJECT_ID),
    )
    if args.audit:
        print(json.dumps(audit_plane(client), indent=2, ensure_ascii=False))
        return 0
    summary = synchronize(client, milestones, args.detach_spec_items)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
