# Plane milestone task structure

Plane contains two separate planning views. They must not be mixed.

## Delivery milestones

The Plane project uses delivery modules `Milestone 1` through `Milestone 11`,
while `docs/Milestone_Doc.docx` numbers the same sequence from Milestone 0
through Milestone 10. The synchronizer applies the explicit mapping
`Plane milestone = source milestone + 1`. Each work item includes both
milestone numbers, its exact source section, requirements, and definition of
done.

For example, Plane Milestone 2 (source Milestone 1) contains these managed
capability items:

| Work item | Source |
|---|---|
| `[M2.1] Lead Pipeline Management` | Source Milestone 1, section 3.2 |
| `[M2.2] KYC and Identity Verification` | Source Milestone 1, section 3.3 |
| `[M2.3] Customer Profile and Service Address` | Source Milestone 1, section 3.4 |
| `[M2.4] Lifecycle and Risk Management` | Source Milestone 1, section 3.5 |

The same rule applies to every milestone. Managed task names always start with
`[M< milestone >.< capability >]`, such as `[M7.3]` or `[M10.6]`.

## Master-spec backlog

Modules beginning with `[SPEC]` are populated from
`docs/TELECOM_ISP_BACKEND_MASTER_IMPLEMENTATION_SPEC.md`. They contain the
1,500 detailed workbook feature records named `[SPEC-1]` through `[SPEC-1500]`.
These records are grouped by the workbook's **Source Module**, not by delivery
milestone. They must not be presented as milestone capability tasks.

## Synchronization

Preview the complete authoritative milestone-to-task map without connecting to
Plane:

```powershell
python scripts/sync_plane_milestone_tasks.py --dry-run
```

Apply it after putting the Plane API key in the current process environment:

```powershell
$env:PLANE_API_KEY = "your-key"
python scripts/sync_plane_milestone_tasks.py --audit
python scripts/sync_plane_milestone_tasks.py --apply
```

The audit is read-only and classifies every current milestone item as a managed
capability, misplaced `[SPEC-*]` task, or unknown/manual task. The synchronizer
then creates missing milestone modules/tasks, refreshes source descriptions,
and reports unrelated existing tasks without deleting them.

After reviewing the dry run and the existing milestone contents, detach
accidentally linked `[SPEC-*]` items from milestone modules:

```powershell
python scripts/sync_plane_milestone_tasks.py --apply --detach-spec-items
```

Detaching only removes milestone-module membership. It does not delete the work
item from Plane. Unknown/manual tasks are always reported and left untouched so
that a human can decide whether they are valid delivery work.

## Tester child tasks

Every milestone capability has one child work item named
`[QA Mx.y] Tester acceptance - ...`. Its description contains:

- capability-specific expected results extracted from the authoritative
  milestone section;
- test environment and fixture prerequisites;
- functional, validation, state-transition, and idempotency scenarios;
- authorization, privacy, and cross-tenant isolation scenarios;
- downstream failure, duplicate-event, restart, audit, log, and metrics checks;
- the relevant frontend/API contract and automated test command;
- mandatory evidence and objective pass criteria.

Synchronize these tester tasks after the parent milestone tasks:

```powershell
$env:PLANE_API_KEY = "your-key"
python scripts/sync_plane_milestone_qa_tasks.py --dry-run
python scripts/sync_plane_milestone_qa_tasks.py --apply
```

The operation is idempotent: existing `[QA Mx.y]` tasks are refreshed and
missing ones are created. It never deletes work items.

The target can be overridden without editing source code:

```powershell
$env:PLANE_ORIGIN = "https://project.vndinfotech.in"
$env:PLANE_WORKSPACE = "main"
$env:PLANE_PROJECT_ID = "25b0a842-40c4-4f3e-b47b-ec45222ccba1"
```
