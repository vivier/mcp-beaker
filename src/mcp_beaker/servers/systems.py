"""System-related Beaker tools (5 read + 6 write)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from mcp_beaker.exceptions import BeakerError, BeakerNotFoundError
from mcp_beaker.models.system import SystemHistoryEntry, SystemInfo, SystemListItem
from mcp_beaker.servers import beaker_client, mcp
from mcp_beaker.utils.formatting import (
    format_system_arches,
    format_system_details,
    format_system_history,
    format_system_inventory,
    format_system_list,
)

logger = logging.getLogger("mcp-beaker")

ATOM_NS = "http://www.w3.org/2005/Atom"
FILTER_ENDPOINTS: dict[str, str] = {
    "all": "/",
    "available": "/available/",
    "free": "/free/",
}


def _error(msg: str) -> str:
    return f"Error: {msg}"


def _parse_atom_feed(xml_text: str) -> list[SystemListItem]:
    root = ET.fromstring(xml_text)
    systems: list[SystemListItem] = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        title_el = entry.find(f"{{{ATOM_NS}}}title")
        fqdn = title_el.text if title_el is not None and title_el.text else "Unknown"
        system_url = ""
        for link in entry.findall(f"{{{ATOM_NS}}}link"):
            href = link.get("href", "")
            link_type = link.get("type", "")
            if "html" in link_type or not link_type:
                system_url = href
                break
        systems.append(SystemListItem(fqdn=fqdn, url=system_url))
    return systems


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


@mcp.tool(
    tags={"beaker", "read", "systems"},
    annotations={"title": "List Systems", "readOnlyHint": True},
)
async def list_systems(
    ctx: Context,
    filter_type: Annotated[
        str,
        Field(description="System filter: 'all', 'available', or 'free'. Default: 'available'."),
    ] = "available",
    limit: Annotated[
        int,
        Field(description="Maximum number of systems to return. Use 0 for all. Default: 20."),
    ] = 20,
) -> str:
    """List Beaker systems matching the filter criteria.

    Returns a list of system FQDNs filtered by availability status.
    Use 'available' for systems you can reserve, 'free' for idle ones,
    or 'all' for the complete inventory.
    """
    client = beaker_client(ctx)
    if filter_type not in FILTER_ENDPOINTS:
        return _error(
            f"Invalid filter_type '{filter_type}'. "
            f"Must be one of: {', '.join(FILTER_ENDPOINTS.keys())}."
        )
    url_path = FILTER_ENDPOINTS[filter_type]
    params = {"tg_format": "atom", "list_tgp_limit": str(limit)}
    try:
        response = await client.rest_get(url_path, params=params)
        systems = _parse_atom_feed(response.text)
        return format_system_list(systems, filter_type)
    except BeakerError as exc:
        return _error(str(exc))
    except ET.ParseError:
        return _error("Failed to parse Atom feed from the server.")
    except Exception as exc:
        logger.error("Failed to list systems: %s", exc)
        return _error(f"Failed to list systems: {exc}")


@mcp.tool(
    tags={"beaker", "read", "systems"},
    annotations={"title": "Get System Details", "readOnlyHint": True},
)
async def get_system_details(
    ctx: Context,
    fqdn: Annotated[str, Field(description="Fully qualified domain name of the system.")],
) -> str:
    """Get detailed information about a specific Beaker system.

    Returns hardware specs, ownership, status, architectures, and
    lab controller assignment for the given system FQDN.
    """
    client = beaker_client(ctx)
    try:
        data = await client.rest_get_json(f"/systems/{fqdn}/")
        info = SystemInfo.model_validate(data)
        return format_system_details(info)
    except BeakerNotFoundError:
        return _error(f"System '{fqdn}' not found.")
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to fetch system details for %s: %s", fqdn, exc)
        return _error(f"Failed to fetch details for system '{fqdn}': {exc}")


@mcp.tool(
    tags={"beaker", "read", "systems"},
    annotations={"title": "Get System History", "readOnlyHint": True},
)
async def get_system_history(
    ctx: Context,
    fqdn: Annotated[str, Field(description="Fully qualified domain name of the system.")],
    since: Annotated[
        str,
        Field(description="ISO timestamp to fetch history from. Omit for last 24 hours."),
    ] = "",
) -> str:
    """Get activity history for a Beaker system.

    Shows who used the system, what changed, and when. Useful for
    investigating system state changes and usage patterns.
    """
    client = beaker_client(ctx)
    try:
        since_arg = since if since else None
        entries_raw = await client.systems_history(fqdn, since_arg)
        entries = [
            SystemHistoryEntry.model_validate(
                {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                 for k, v in e.items()}
            )
            for e in entries_raw
        ]
        return format_system_history(entries, fqdn)
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to fetch history for %s: %s", fqdn, exc)
        return _error(f"Failed to fetch history for '{fqdn}': {exc}")


@mcp.tool(
    tags={"beaker", "read", "systems"},
    annotations={"title": "Get System Inventory", "readOnlyHint": True},
)
async def get_system_inventory(
    ctx: Context,
    fqdn: Annotated[str, Field(description="Fully qualified domain name of the system.")],
) -> str:
    """Get hardware inventory key-value pairs for a Beaker system.

    Returns key-value data from the system inventory including PCI device
    IDs, CPU model, disk controllers, USB devices, kernel modules, and
    other hardware details not available in the standard system details.
    """
    client = beaker_client(ctx)
    try:
        kv = await client.systems_get_inventory(fqdn)
        return format_system_inventory(kv, fqdn)
    except BeakerNotFoundError:
        return _error(f"System '{fqdn}' not found.")
    except BeakerError as exc:
        return _error(str(exc))
    except ET.ParseError:
        return _error(f"Failed to parse inventory data for '{fqdn}'.")
    except Exception as exc:
        logger.error("Failed to fetch inventory for %s: %s", fqdn, exc)
        return _error(f"Failed to fetch inventory for '{fqdn}': {exc}")


@mcp.tool(
    tags={"beaker", "read", "systems"},
    annotations={"title": "Get System Architectures", "readOnlyHint": True},
)
async def get_system_arches(
    ctx: Context,
    fqdn: Annotated[str, Field(description="Fully qualified domain name of the system.")],
) -> str:
    """Get supported OS families and architectures for a Beaker system.

    Returns a mapping of distro family names to their supported
    architecture list for the given system.
    """
    client = beaker_client(ctx)
    try:
        arches = await client.systems_get_osmajor_arches(fqdn)
        return format_system_arches(arches, fqdn)
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to fetch arches for %s: %s", fqdn, exc)
        return _error(f"Failed to fetch arches for '{fqdn}': {exc}")


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


@mcp.tool(
    tags={"beaker", "write", "systems"},
    annotations={"title": "Reserve System", "readOnlyHint": False},
)
async def reserve_system(
    ctx: Context,
    fqdn: Annotated[str, Field(description="FQDN of the system to reserve.")],
) -> str:
    """Manually reserve a Beaker system.

    The system must be in 'Manual' condition and not currently in use.
    You must have permission to use the system. After reserving, you
    can provision it at will.
    """
    client = beaker_client(ctx)
    try:
        await client.systems_reserve(fqdn)
        return f"Successfully reserved system '{fqdn}'."
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to reserve %s: %s", fqdn, exc)
        return _error(f"Failed to reserve '{fqdn}': {exc}")


@mcp.tool(
    tags={"beaker", "write", "systems"},
    annotations={"title": "Release System", "readOnlyHint": False},
)
async def release_system(
    ctx: Context,
    fqdn: Annotated[str, Field(description="FQDN of the system to release.")],
) -> str:
    """Release a manually reserved Beaker system.

    You must be the current user of the system (i.e. you reserved it).
    """
    client = beaker_client(ctx)
    try:
        await client.systems_release(fqdn)
        return f"Successfully released system '{fqdn}'."
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to release %s: %s", fqdn, exc)
        return _error(f"Failed to release '{fqdn}': {exc}")


@mcp.tool(
    tags={"beaker", "write", "systems"},
    annotations={"title": "Power System", "readOnlyHint": False},
)
async def power_system(
    ctx: Context,
    fqdn: Annotated[str, Field(description="FQDN of the system to power control.")],
    action: Annotated[str, Field(description="Power action: 'on', 'off', or 'reboot'.")],
    force: Annotated[
        bool,
        Field(description="Override safety check if system is in use. Default: false."),
    ] = False,
) -> str:
    """Control power for a Beaker system (on, off, or reboot).

    Power control is not normally permitted when the system is in
    use by someone else. Use force=true to override this safety check.
    """
    if action not in ("on", "off", "reboot"):
        return _error(f"Invalid action '{action}'. Must be 'on', 'off', or 'reboot'.")
    client = beaker_client(ctx)
    try:
        await client.systems_power(action, fqdn, force=force)
        return f"Power {action} command sent to '{fqdn}'."
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to power %s %s: %s", action, fqdn, exc)
        return _error(f"Failed to power {action} '{fqdn}': {exc}")


@mcp.tool(
    tags={"beaker", "write", "systems"},
    annotations={"title": "Loan System", "readOnlyHint": False},
)
async def loan_system(
    ctx: Context,
    fqdn: Annotated[str, Field(description="FQDN of the system to loan.")],
    recipient: Annotated[
        str,
        Field(description="Username of the loan recipient. If empty, loans to the current user."),
    ] = "",
    comment: Annotated[
        str,
        Field(description="Reason or purpose for the loan."),
    ] = "",
) -> str:
    """Grant a loan for a Beaker system.

    The loan recipient gets full permissions to reserve, provision, and
    schedule jobs on the system. While loaned, only the recipient and
    the system owner can use it. You must have permission to loan the
    system (typically the owner or an admin).
    """
    client = beaker_client(ctx)
    try:
        await client.systems_loan_grant(
            fqdn,
            recipient=recipient or None,
            comment=comment,
        )
        target = recipient if recipient else "yourself"
        return f"Successfully loaned system '{fqdn}' to {target}."
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to loan %s: %s", fqdn, exc)
        return _error(f"Failed to loan '{fqdn}': {exc}")


@mcp.tool(
    tags={"beaker", "write", "systems"},
    annotations={"title": "Return System Loan", "readOnlyHint": False},
)
async def return_loan(
    ctx: Context,
    fqdn: Annotated[str, Field(description="FQDN of the system whose loan to return.")],
) -> str:
    """Return a current loan on a Beaker system.

    Either the loan recipient or a user with permission to loan the
    system can return it. The system reverts to its normal access policy.
    """
    client = beaker_client(ctx)
    try:
        await client.systems_loan_return(fqdn)
        return f"Successfully returned loan for system '{fqdn}'."
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to return loan for %s: %s", fqdn, exc)
        return _error(f"Failed to return loan for '{fqdn}': {exc}")


@mcp.tool(
    tags={"beaker", "write", "systems"},
    annotations={"title": "Provision System", "readOnlyHint": False},
)
async def provision_system(
    ctx: Context,
    fqdn: Annotated[str, Field(description="FQDN of the system to provision.")],
    distro_tree_id: Annotated[
        int,
        Field(description="Numeric distro tree ID (from list_distro_trees results)."),
    ],
    ks_meta: Annotated[str, Field(description="Kickstart metadata variables.")] = "",
    kernel_options: Annotated[str, Field(description="Kernel options for installation.")] = "",
    kernel_options_post: Annotated[
        str, Field(description="Kernel options for the installed system.")
    ] = "",
    kickstart: Annotated[str, Field(description="Complete custom kickstart content.")] = "",
    reboot: Annotated[
        bool, Field(description="Reboot system after provisioning. Default: true.")
    ] = True,
) -> str:
    """Provision a reserved Beaker system with a specific distro.

    The system must be in 'Manual' condition and already reserved by you.
    Use list_distro_trees to find the distro_tree_id first.
    """
    client = beaker_client(ctx)
    try:
        await client.systems_provision(
            fqdn,
            distro_tree_id,
            ks_meta=ks_meta,
            kernel_options=kernel_options,
            kernel_options_post=kernel_options_post,
            kickstart=kickstart,
            reboot=reboot,
        )
        return f"Provisioning started for '{fqdn}' with distro tree {distro_tree_id}."
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to provision %s: %s", fqdn, exc)
        return _error(f"Failed to provision '{fqdn}': {exc}")
