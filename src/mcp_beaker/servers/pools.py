"""Pool-related Beaker tools (1 read)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from mcp_beaker.exceptions import BeakerError, BeakerNotFoundError
from mcp_beaker.servers import beaker_client, mcp
from mcp_beaker.utils.formatting import format_pool_access_policy, format_pool_list

logger = logging.getLogger("mcp-beaker")


def _error(msg: str) -> str:
    return f"Error: {msg}"


@mcp.tool(
    tags={"beaker", "read", "pools"},
    annotations={"title": "Search Pools", "readOnlyHint": True},
)
async def search_pools(
    ctx: Context,
    name: Annotated[
        str,
        Field(description="Pool name to search for (substring match). Leave empty to list all pools."),
    ] = "",
    owner: Annotated[
        str,
        Field(description="Filter by owner (username or group name)."),
    ] = "",
    limit: Annotated[
        int,
        Field(description="Maximum number of pools to return. Default: 50."),
    ] = 50,
) -> str:
    """Search Beaker system pools by name or owner.

    Pools are named groups of systems that can be used in job scheduling.
    Returns pool names, descriptions, owners, and system counts.
    """
    client = beaker_client(ctx)
    page_size = min(limit, 100)

    async def _fetch_all(q: str | None) -> list[dict]:
        params: dict[str, str] = {"page_size": str(page_size)}
        if q:
            params["q"] = q
        result: list[dict] = []
        page = 1
        while True:
            params["page"] = str(page)
            data = await client.rest_get_json("/pools/", params=params)
            batch = data.get("entries", [])
            result.extend(batch)
            if len(result) >= limit or len(batch) < page_size:
                break
            page += 1
        return result

    try:
        q_parts: list[str] = []
        if name:
            q_parts.append(name)

        if owner:
            user_q = " ".join([*q_parts, f"owner.user_name:{owner}"])
            group_q = " ".join([*q_parts, f"owner.group_name:{owner}"])
            user_entries = await _fetch_all(user_q)
            group_entries = await _fetch_all(group_q)
            seen: set[str] = set()
            entries: list[dict] = []
            for e in [*user_entries, *group_entries]:
                pool_name = e.get("name", "")
                if pool_name not in seen:
                    seen.add(pool_name)
                    entries.append(e)
        else:
            q = " ".join(q_parts) if q_parts else None
            entries = await _fetch_all(q)

        return format_pool_list(entries[:limit], name=name, owner=owner)
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to search pools: %s", exc)
        return _error(f"Failed to search pools: {exc}")


@mcp.tool(
    tags={"beaker", "read", "pools"},
    annotations={"title": "Get Pool Access Policy", "readOnlyHint": True},
)
async def get_pool_access_policy(
    ctx: Context,
    pool_name: Annotated[
        str,
        Field(description="Name of the pool to get the access policy for."),
    ],
) -> str:
    """Get the access policy for a Beaker system pool.

    Returns the list of access rules (permissions, users, groups) that
    control who can use, loan, or manage systems in the pool.
    """
    client = beaker_client(ctx)
    try:
        data = await client.rest_get_json(f"/pools/{pool_name}/access-policy/")
        return format_pool_access_policy(data, pool_name=pool_name)
    except BeakerNotFoundError:
        return _error(f"Pool '{pool_name}' not found.")
    except BeakerError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Failed to fetch access policy for pool %s: %s", pool_name, exc)
        return _error(f"Failed to fetch access policy for pool '{pool_name}': {exc}")
