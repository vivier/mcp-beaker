"""Tests for pool tools."""

from __future__ import annotations

from unittest.mock import AsyncMock

from mcp_beaker.exceptions import BeakerError, BeakerNotFoundError
from mcp_beaker.servers.pools import get_pool_access_policy, search_pools

POOL_ENTRIES = [
    {
        "name": "gpu-pool",
        "description": "Systems with NVIDIA GPUs",
        "owner": {"user_name": "admin"},
        "systems": [
            {"fqdn": "gpu1.example.com"},
            {"fqdn": "gpu2.example.com"},
        ],
    },
    {
        "name": "arm-pool",
        "description": "AArch64 systems",
        "owner": {"user_name": "jdoe"},
        "systems": [],
    },
]


class TestSearchPools:
    async def test_success(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(
            return_value={"entries": POOL_ENTRIES, "count": 2},
        )
        result = await search_pools(ctx)
        assert "gpu-pool" in result
        assert "arm-pool" in result
        assert "NVIDIA" in result
        assert "Systems: 2" in result
        mock_client.rest_get_json.assert_awaited_once()

    async def test_search_by_name(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(
            return_value={"entries": [POOL_ENTRIES[0]], "count": 1},
        )
        result = await search_pools(ctx, name="gpu")
        assert "gpu-pool" in result
        call_kwargs = mock_client.rest_get_json.call_args
        assert call_kwargs[1]["params"]["q"] == "gpu"

    async def test_search_by_owner(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(
            return_value={"entries": [POOL_ENTRIES[1]], "count": 1},
        )
        result = await search_pools(ctx, owner="jdoe")
        assert "arm-pool" in result
        call_kwargs = mock_client.rest_get_json.call_args
        assert call_kwargs[1]["params"]["q"] == "owner.user_name:jdoe"

    async def test_search_by_name_and_owner(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(
            return_value={"entries": [POOL_ENTRIES[0]], "count": 1},
        )
        result = await search_pools(ctx, name="gpu", owner="admin")
        assert "gpu-pool" in result
        call_kwargs = mock_client.rest_get_json.call_args
        assert call_kwargs[1]["params"]["q"] == "gpu owner.user_name:admin"

    async def test_empty_results(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(
            return_value={"entries": [], "count": 0},
        )
        result = await search_pools(ctx, name="nonexistent")
        assert "No pools found" in result

    async def test_connection_error(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(side_effect=BeakerError("conn err"))
        result = await search_pools(ctx)
        assert "Error" in result

    async def test_generic_error(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(side_effect=RuntimeError("boom"))
        result = await search_pools(ctx)
        assert "Error" in result


ACCESS_POLICY = {
    "rules": [
        {"permission": "view", "everybody": True},
        {"permission": "reserve", "group": "virt-admin"},
        {"permission": "loan_self", "user": "eperezma"},
    ],
}


class TestGetPoolAccessPolicy:
    async def test_success(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(return_value=ACCESS_POLICY)
        result = await get_pool_access_policy(ctx, pool_name="my-pool")
        assert "my-pool" in result
        assert "view -> everybody" in result
        assert "reserve -> group: virt-admin" in result
        assert "loan_self -> user: eperezma" in result
        mock_client.rest_get_json.assert_awaited_once()

    async def test_not_found(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(
            side_effect=BeakerNotFoundError("not found"),
        )
        result = await get_pool_access_policy(ctx, pool_name="nope")
        assert "not found" in result.lower()

    async def test_empty_rules(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(return_value={"rules": []})
        result = await get_pool_access_policy(ctx, pool_name="empty-pool")
        assert "No access rules" in result

    async def test_generic_error(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(side_effect=RuntimeError("boom"))
        result = await get_pool_access_policy(ctx, pool_name="fail")
        assert "Error" in result
