"""Human-readable formatters for Beaker API responses."""

from __future__ import annotations

import json
from typing import Any

from mcp_beaker.models.distro import DistroTreeInfo
from mcp_beaker.models.job import JobInfo, LogFileEntry
from mcp_beaker.models.system import SystemHistoryEntry, SystemInfo, SystemListItem

# ---------------------------------------------------------------------------
# Status / result constants
# ---------------------------------------------------------------------------

FAILURE_STATUSES = {"Aborted", "Cancelled"}
FAILURE_RESULTS = {"Fail", "Warn", "Panic"}
RUNNING_STATUSES = {
    "New",
    "Queued",
    "Scheduled",
    "Waiting",
    "Running",
    "Installing",
    "Reserved",
    "Processed",
}
POSITIVE_SETTLED_STATUSES = {"Reserved"}


# ---------------------------------------------------------------------------
# Systems
# ---------------------------------------------------------------------------


def format_system_list(systems: list[SystemListItem], filter_type: str) -> str:
    if not systems:
        return f"No {filter_type} systems found on this Beaker server."
    lines = [f"Found {len(systems)} {filter_type} system(s):\n"]
    for idx, system in enumerate(systems, start=1):
        lines.append(f"  {idx}. {system.fqdn}")
        if system.url:
            lines.append(f"     URL: {system.url}")
    return "\n".join(lines)


def format_system_details(info: SystemInfo) -> str:
    lines: list[str] = []
    lines.append(f"System: {info.fqdn}")
    lines.append("=" * (len(info.fqdn) + 8))

    field_map: list[tuple[str, str]] = [
        ("status", "Status"),
        ("system_type", "Type"),
        ("lender", "Lender"),
        ("location", "Location"),
        ("vendor", "Vendor"),
        ("model", "Model"),
        ("serial_number", "Serial Number"),
        ("mac_address", "MAC Address"),
        ("hypervisor", "Hypervisor"),
        ("kernel_type", "Kernel Type"),
        ("power_type", "Power Type"),
        ("power_address", "Power Address"),
        ("release_action", "Release Action"),
    ]

    if info.owner:
        lines.append(f"  Owner: {info.owner.user_name}")
    if info.user:
        lines.append(f"  Current User: {info.user.user_name}")
    if info.memory is not None:
        lines.append(f"  Memory (MB): {info.memory}")
    if info.numa_nodes is not None:
        lines.append(f"  NUMA Nodes: {info.numa_nodes}")

    for attr, label in field_map:
        value = getattr(info, attr, None)
        if value:
            lines.append(f"  {label}: {value}")

    if info.arches:
        lines.append(f"  Architectures: {', '.join(info.arches)}")
    if info.lab_controller and isinstance(info.lab_controller, dict):
        lines.append(f"  Lab Controller: {info.lab_controller.get('fqdn', 'N/A')}")

    return "\n".join(lines)


def format_system_history(entries: list[SystemHistoryEntry], fqdn: str) -> str:
    if not entries:
        return f"No history found for system '{fqdn}'."
    lines = [f"Activity history for {fqdn} ({len(entries)} entries):\n"]
    for entry in entries:
        line = f"  [{entry.created}] {entry.user} {entry.action} {entry.field_name}"
        if entry.old_value or entry.new_value:
            line += f": '{entry.old_value}' -> '{entry.new_value}'"
        lines.append(line)
    return "\n".join(lines)


def format_system_inventory(kv: dict[str, list[str]], fqdn: str) -> str:
    if not kv:
        return f"No inventory key-value data found for system '{fqdn}'."
    lines = [f"Inventory key-values for {fqdn}:\n"]
    for key_name in sorted(kv.keys()):
        vals = kv[key_name]
        if len(vals) == 1:
            lines.append(f"  {key_name}: {vals[0]}")
        elif len(vals) <= 10:
            lines.append(f"  {key_name}: {', '.join(vals)}")
        else:
            lines.append(f"  {key_name}: [{len(vals)} values]")
            for v in vals:
                lines.append(f"    - {v}")
    return "\n".join(lines)


def format_system_arches(arches: dict[str, list[str]], fqdn: str) -> str:
    if not arches:
        return f"No OS/arch information found for system '{fqdn}'."
    lines = [f"OS families and architectures for {fqdn}:\n"]
    for family, arch_list in sorted(arches.items()):
        lines.append(f"  {family}: {', '.join(arch_list)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def format_job_ids(job_ids: list[str], owner: str) -> str:
    if not job_ids:
        return f"No jobs found for owner '{owner}'."
    lines = [f"Found {len(job_ids)} job(s) for owner '{owner}':\n"]
    for idx, jid in enumerate(job_ids, start=1):
        lines.append(f"  {idx}. {jid}")
    return "\n".join(lines)


def format_job_details(jobs: list[JobInfo], owner: str) -> str:
    if not jobs:
        return f"No jobs found for owner '{owner}'."
    lines = [f"Found {len(jobs)} job(s) for owner '{owner}':\n"]
    for idx, job in enumerate(jobs, start=1):
        lines.append(f"  {idx}. J:{job.id}")
        lines.append(f"     Owner: {job.owner_name}")
        lines.append(f"     Status: {job.status}  |  Result: {job.result}")
        if job.whiteboard:
            lines.append(f"     Whiteboard: {job.whiteboard}")
        if job.submitted_time:
            lines.append(f"     Submitted: {job.submitted_time}")
        lines.append(f"     Finished: {'Yes' if job.is_finished else 'No'}")
        if job.recipesets:
            lines.append(f"     Recipe Sets: {len(job.recipesets)}")
            for rs_idx, rs in enumerate(job.recipesets, start=1):
                rs_info = f"       RS {rs_idx}: status={rs.status}, result={rs.result}"
                if rs.priority:
                    rs_info += f", priority={rs.priority}"
                lines.append(rs_info)
        lines.append("")
    return "\n".join(lines)


def format_job_logs(files: list[LogFileEntry], taskid: str) -> str:
    if not files:
        return f"No log files found for {taskid}."
    lines = [f"Log files for {taskid} ({len(files)} files):\n"]
    for idx, f in enumerate(files, start=1):
        display = f.url or f.path or f.filename
        lines.append(f"  {idx}. {display}")
    return "\n".join(lines)


def format_submit_success(base_url: str, job_id: str, auth_method: str = "unknown") -> str:
    numeric_id = str(job_id).replace("J:", "")
    return (
        f"Job submitted successfully!\n"
        f"  Job ID : {job_id}\n"
        f"  URL    : {base_url}/jobs/{numeric_id}\n"
        f"  Auth   : {auth_method}"
    )


# ---------------------------------------------------------------------------
# Distros
# ---------------------------------------------------------------------------


def format_distro_trees(trees: list[DistroTreeInfo], filters_desc: str) -> str:
    if not trees:
        return f"No distro trees found matching: {filters_desc}"
    lines = [f"Found {len(trees)} distro tree(s) matching: {filters_desc}\n"]
    for idx, tree in enumerate(trees, start=1):
        lines.append(f"  {idx}. {tree.distro_name}")
        if tree.variant:
            lines.append(f"     Variant       : {tree.variant}")
        if tree.arch:
            lines.append(f"     Arch          : {tree.arch}")
        if tree.distro_id is not None:
            lines.append(f"     Distro ID     : {tree.distro_id}")
        if tree.distro_tree_id is not None:
            lines.append(f"     Distro Tree ID: {tree.distro_tree_id}")
        if tree.distro_tags:
            lines.append(f"     Tags          : {', '.join(str(t) for t in tree.distro_tags)}")
        if tree.available:
            lc_names = [lc if isinstance(lc, str) else str(lc) for lc in tree.available[:5]]
            lines.append(f"     Lab Controllers: {', '.join(lc_names)}")
            if len(tree.available) > 5:
                lines.append(f"                      ... and {len(tree.available) - 5} more")
    return "\n".join(lines)


def format_os_families(families: list[str]) -> str:
    if not families:
        return "No OS families found."
    lines = [f"Available OS families ({len(families)}):\n"]
    for f in sorted(families):
        lines.append(f"  - {f}")
    return "\n".join(lines)


def format_lab_controllers(controllers: list[str]) -> str:
    if not controllers:
        return "No lab controllers found."
    lines = [f"Lab controllers ({len(controllers)}):\n"]
    for lc in sorted(controllers):
        lines.append(f"  - {lc}")
    return "\n".join(lines)


def format_tasks(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "No tasks found matching the criteria."
    lines = [f"Found {len(tasks)} task(s):\n"]
    for idx, task in enumerate(tasks, start=1):
        name = task.get("name", "?")
        arches = task.get("arches", [])
        line = f"  {idx}. {name}"
        if arches:
            line += f"  (excluded arches: {', '.join(arches)})"
        lines.append(line)
    return "\n".join(lines)


def format_whoami(info: dict[str, Any]) -> str:
    lines = ["Authenticated user:\n"]
    lines.append(f"  Username: {info.get('username', info.get('user_name', '?'))}")
    if info.get("email_address"):
        lines.append(f"  Email   : {info['email_address']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pools
# ---------------------------------------------------------------------------


def format_pool_list(
    entries: list[dict[str, Any]], name: str = "", owner: str = "",
) -> str:
    filters: list[str] = []
    if name:
        filters.append(f"name='{name}'")
    if owner:
        filters.append(f"owner='{owner}'")
    desc = ", ".join(filters) if filters else "all"
    if not entries:
        return f"No pools found matching: {desc}"
    lines = [f"Found {len(entries)} pool(s) matching: {desc}\n"]
    for idx, pool in enumerate(entries, start=1):
        pool_name = pool.get("name", "?")
        lines.append(f"  {idx}. {pool_name}")
        owner_info = pool.get("owner")
        if isinstance(owner_info, dict):
            owner_name = owner_info.get("user_name") or owner_info.get("group_name", "?")
            lines.append(f"     Owner: {owner_name}")
        elif isinstance(owner_info, str):
            lines.append(f"     Owner: {owner_info}")
        if pool.get("description"):
            lines.append(f"     Description: {pool['description']}")
        systems = pool.get("systems")
        if systems is not None:
            lines.append(f"     Systems: {len(systems)}")
    return "\n".join(lines)


def format_generic_result(data: Any, label: str = "Result") -> str:
    """Format an arbitrary result as indented JSON."""
    if isinstance(data, (dict, list)):
        return f"{label}:\n{json.dumps(data, indent=2, ensure_ascii=False)}"
    return f"{label}: {data}"
