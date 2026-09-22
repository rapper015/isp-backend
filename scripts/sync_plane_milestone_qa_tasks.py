"""Create one detailed tester child task under every Plane milestone capability.

The parent delivery tasks are managed by ``sync_plane_milestone_tasks.py``.
This script adds an idempotent ``[QA Mx.y]`` child to each parent. It does not
delete or detach any Plane item.

Usage:
    python scripts/sync_plane_milestone_qa_tasks.py --dry-run
    PLANE_API_KEY=... python scripts/sync_plane_milestone_qa_tasks.py --apply
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from dataclasses import dataclass

from sync_plane_milestone_tasks import (
    DEFAULT_ORIGIN,
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE,
    MANAGED_ITEM_RE,
    PlaneClient,
    module_number,
    parse_milestones,
)


QA_ITEM_RE = re.compile(r"^\[QA M(?P<milestone>\d+)\.(?P<capability>\d+)]\s+")


@dataclass(frozen=True)
class TestContext:
    api_doc: str
    services: str
    automated_command: str
    deployment_gate: str = "The milestone API and required workers are deployed and healthy."


TEST_CONTEXTS: dict[int, TestContext] = {
    1: TestContext(
        "docs/apis/milestone-0-aaa-nas-radius.md",
        "platform-core-service, aaa-service, and the foundational billing/customer mappings",
        "pytest services/aaa-service/tests services/platform-core-service/tests",
    ),
    2: TestContext(
        "docs/apis/milestone-1-crm.md",
        "crm-service",
        "pytest services/crm-service/tests",
    ),
    3: TestContext(
        "docs/apis/milestone-2-oss.md",
        "oss-service and its CRM/AAA adapters",
        "pytest services/oss-service/tests",
    ),
    4: TestContext(
        "docs/apis/milestone-3-network-control.md",
        "aaa-service, nms-service, NAS/RouterOS adapters, and network-control workers",
        "pytest services/aaa-service/tests services/nms-service/tests",
    ),
    5: TestContext(
        "docs/apis/milestone-4-bss.md",
        "bss-service and payment-provider adapters",
        "pytest services/bss-service/tests",
    ),
    6: TestContext(
        "docs/apis/milestone-5-frontend-integration.md",
        "support-service and support worker",
        "pytest services/support-service/tests",
        "Support must first be wired into Compose, PostgreSQL, storage, and the gateway. If it is not reachable, record this QA task as BLOCKED with deployment evidence; do not report a pass.",
    ),
    7: TestContext(
        "docs/apis/milestone-6-frontend-integration.md",
        "workforce-service and workforce-worker",
        "pytest services/workforce-service/tests",
    ),
    8: TestContext(
        "docs/apis/milestone-7-device-management.md",
        "device-management-service, worker, firmware storage, and GenieACS adapter",
        "pytest services/device-management-service/tests",
        "Select and document ACS_PROVIDER=fake for deterministic tests or a reachable isolated GenieACS test instance. Never run disruptive RPC or firmware tests against production devices.",
    ),
    9: TestContext(
        "docs/apis/milestone-8-tenancy.md",
        "tenancy-service and tenancy-worker",
        "pytest services/tenancy-service/tests",
    ),
    10: TestContext(
        "docs/apis/milestone-9-assurance.md",
        "assurance-service, assurance-worker, and observability dependencies",
        "pytest services/assurance-service/tests",
    ),
    11: TestContext(
        "docs/apis/milestone-10-intelligence.md",
        "intelligence-service/worker plus SIEM, Warehouse, AIOps, and IPAM integrations",
        "pytest services/intelligence-service/tests services/siem-service/tests services/warehouse-service/tests",
    ),
}


def qa_name(plane_number: int, capability_index: int, title: str) -> str:
    return f"[QA M{plane_number}.{capability_index}] Tester acceptance - {title}"


def requirement_sentences(paragraphs: list[str]) -> list[str]:
    results: list[str] = []
    for paragraph in paragraphs:
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph.strip()):
            sentence = sentence.strip()
            if len(sentence) < 20:
                continue
            sentence = re.sub(r"^(The platform|The system|This component)\s+shall\s+", "", sentence, flags=re.I)
            sentence = re.sub(r"^All\s+", "all ", sentence)
            results.append(sentence[0].upper() + sentence[1:])
    return results


def list_html(items: list[str]) -> str:
    return "".join(f"<li>{html.escape(item)}</li>" for item in items)


def qa_description(milestone, capability) -> str:
    context = TEST_CONTEXTS[milestone.plane_number]
    code = f"M{milestone.plane_number}.{capability.capability_index}"
    source_checks = [f"Verify that {item[0].lower() + item[1:]}" for item in requirement_sentences(capability.paragraphs)]
    preconditions = [
        context.deployment_gate,
        "Use a non-production QA environment with migrations applied and health checks passing.",
        "Prepare Tenant A and Tenant B, an authorized role, an authenticated but unauthorized role, and an unauthenticated client.",
        "Record the deployed commit SHA, API base URL, test data IDs, timestamps, and correlation/request IDs.",
        f"Use {context.api_doc} as the endpoint/payload contract; test the {context.services} boundary.",
    ]
    execution = [
        "Create the minimum valid fixtures through supported APIs or documented seed helpers; do not insert directly into private service tables.",
        "Execute the primary happy path, then retrieve/list the affected resource and verify every persisted field and relationship.",
        "Exercise every documented valid state transition and verify the final state plus transition history.",
        "Submit missing required fields, malformed values, invalid enum/state transitions, duplicate identifiers, and boundary-size values; expect stable 4xx validation errors and no partial write.",
        "Repeat a retryable create/command with the same idempotency identity; expect one business effect, not duplicate records, events, ledger entries, or jobs.",
    ]
    security = [
        "Without a token, expect 401 and no state change.",
        "With a valid token lacking the required permission, expect 403 and no state change.",
        "Create data in Tenant A, request/update/delete it as Tenant B, and verify fail-closed 403/404 behavior with no data leakage in lists, exports, counts, logs, caches, or events.",
        "Verify sensitive credentials, identity documents, payment data, secrets, and internal fields are masked or omitted for roles without explicit access.",
        "Verify an append-only audit record contains tenant, actor/service identity, action, target, outcome, timestamp, and correlation ID for each material state change and denied privileged action.",
    ]
    resilience = [
        "Simulate timeout, connection refusal, and 5xx from every downstream adapter used by this capability; expect a bounded timeout, stable error, no false success, and a recoverable/compensated state.",
        "Where events/jobs are used, deliver the same message twice and out of order; verify idempotent consumption, retry/dead-letter behavior, and no duplicate business effect.",
        "Restart the API/worker between command acceptance and completion; verify durable work resumes or reaches a clearly recoverable failed state.",
        "Verify structured logs and metrics include the correlation ID and outcome without leaking tokens, passwords, document contents, or encryption material.",
        "Run relevant automated tests and one adjacent milestone regression flow; record the exact command, result counts, failures, and links to evidence.",
    ]
    evidence = [
        "API requests and responses with secrets/tokens redacted.",
        "Before/after resource state and database migration revision.",
        "Audit/event/job evidence with correlation IDs.",
        "Screenshots only where UI behavior is part of the acceptance path; API evidence remains mandatory.",
        "Automated test output and a defect link for every failed expected result. Do not mark Done while a P0/P1 defect or deployment blocker remains.",
    ]
    return (
        f"<h2>Test assignment</h2><p>Validate <strong>{html.escape(code)} — {html.escape(capability.title)}</strong>. "
        f"Parent source: <code>docs/Milestone_Doc.docx</code>, source Milestone {milestone.number}, section {html.escape(capability.source_section)}. "
        f"API contract: <code>{html.escape(context.api_doc)}</code>.</p>"
        f"<h2>Preconditions</h2><ol>{list_html(preconditions)}</ol>"
        f"<h2>Capability-specific expected results</h2><ol>{list_html(source_checks)}</ol>"
        f"<h2>Functional and API scenarios</h2><ol>{list_html(execution)}</ol>"
        f"<h2>Authorization, privacy, and tenant-isolation scenarios</h2><ol>{list_html(security)}</ol>"
        f"<h2>Failure, retry, audit, and regression scenarios</h2><ol>{list_html(resilience)}</ol>"
        f"<h2>Automation command</h2><pre><code>{html.escape(context.automated_command)}</code></pre>"
        f"<h2>Required evidence</h2><ul>{list_html(evidence)}</ul>"
        "<h2>Pass criteria</h2><p>Every applicable expected result passes, mandatory evidence is attached, no cross-tenant disclosure or unauthorized mutation occurs, retries produce no duplicate effect, and no unresolved P0/P1 defect remains. Mark non-applicable cases individually with a technical reason; never silently omit them.</p>"
    )


def sync(client: PlaneClient, apply: bool) -> dict:
    milestones = parse_milestones()
    modules = {
        number: module
        for module in client.list_all("/modules/")
        if (number := module_number(module)) is not None
    }
    planned: list[dict] = []
    created = updated = linked = 0

    for milestone in milestones:
        module = modules.get(milestone.plane_number)
        if module is None:
            raise RuntimeError(f"Plane Milestone {milestone.plane_number} module is missing; sync parent tasks first")
        members = client.list_all(f"/modules/{module['id']}/module-issues/")
        members_by_name = {item.get("name", ""): item for item in members}
        member_ids = {item["id"] for item in members}
        new_child_ids: list[str] = []

        for capability in milestone.capabilities:
            parent_name = milestone.item_name(capability)
            parent = members_by_name.get(parent_name)
            if parent is None:
                raise RuntimeError(f"Parent task is missing from {module['name']}: {parent_name}")
            name = qa_name(milestone.plane_number, capability.capability_index, capability.title)
            payload = {
                "name": name,
                "description_html": qa_description(milestone, capability),
                "priority": "high",
                "parent": parent["id"],
                "module": module["id"],
            }
            existing = members_by_name.get(name)
            planned.append({"module": module["name"], "parent": parent_name, "qa_child": name, "action": "update" if existing else "create"})
            if not apply:
                continue
            if existing:
                client.request("PATCH", f"/work-items/{existing['id']}/", payload)
                updated += 1
            else:
                child = client.request("POST", "/work-items/", payload)
                if not isinstance(child, dict) or not child.get("id"):
                    raise RuntimeError(f"Plane did not return an ID for created QA task: {name}")
                members_by_name[name] = child
                if child["id"] not in member_ids:
                    new_child_ids.append(child["id"])
                    member_ids.add(child["id"])
                created += 1

        if apply and new_child_ids:
            client.request(
                "POST",
                f"/modules/{module['id']}/module-issues/",
                {"issues": new_child_ids},
            )
            linked += len(new_child_ids)

    return {
        "mode": "apply" if apply else "dry-run",
        "milestones": len(milestones),
        "qa_child_tasks": len(planned),
        "created": created,
        "updated": updated,
        "linked": linked,
        "plan": planned if not apply else [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        milestones = parse_milestones()
        preview = [
            qa_name(m.plane_number, c.capability_index, c.title)
            for m in milestones
            for c in m.capabilities
        ]
        print(json.dumps({"mode": "dry-run", "qa_child_tasks": len(preview), "names": preview}, indent=2))
        return 0
    api_key = os.environ.get("PLANE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("PLANE_API_KEY is required with --apply")
    client = PlaneClient(
        api_key,
        os.environ.get("PLANE_ORIGIN", DEFAULT_ORIGIN),
        os.environ.get("PLANE_WORKSPACE", DEFAULT_WORKSPACE),
        os.environ.get("PLANE_PROJECT_ID", DEFAULT_PROJECT_ID),
    )
    print(json.dumps(sync(client, apply=True), indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
