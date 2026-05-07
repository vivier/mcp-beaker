"""Tests for system tools (read and write)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from xmlrpc.client import DateTime

import httpx

from mcp_beaker.exceptions import BeakerError, BeakerNotFoundError
from mcp_beaker.servers.systems import (
    _parse_atom_feed,
    get_system_arches,
    get_system_details,
    get_system_history,
    get_system_inventory,
    list_systems,
    loan_system,
    power_system,
    provision_system,
    release_system,
    reserve_system,
    return_loan,
)

ATOM_FEED = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Systems</title>
  <entry>
    <title>host1.example.com</title>
    <link type="text/html" href="https://beaker.test/view/host1.example.com"/>
  </entry>
  <entry>
    <title>host2.example.com</title>
    <link type="text/html" href="https://beaker.test/view/host2.example.com"/>
  </entry>
</feed>"""


# ---- parse_atom_feed helper ------------------------------------------------

class TestParseAtomFeed:
    def test_parses_entries(self):
        systems = _parse_atom_feed(ATOM_FEED)
        assert len(systems) == 2
        assert systems[0].fqdn == "host1.example.com"
        assert systems[1].fqdn == "host2.example.com"
        assert "host1.example.com" in systems[0].url

    def test_empty_feed(self):
        xml = '<feed xmlns="http://www.w3.org/2005/Atom"><title>Empty</title></feed>'
        systems = _parse_atom_feed(xml)
        assert systems == []


# ---- list_systems ----------------------------------------------------------

class TestListSystems:
    async def test_success(self, ctx, mock_client):
        mock_client.rest_get = AsyncMock(
            return_value=httpx.Response(200, text=ATOM_FEED)
        )
        result = await list_systems(ctx)
        assert "host1.example.com" in result
        assert "host2.example.com" in result

    async def test_invalid_filter(self, ctx):
        result = await list_systems(ctx, filter_type="bogus")
        assert "Error" in result
        assert "Invalid filter_type" in result

    async def test_bad_xml(self, ctx, mock_client):
        mock_client.rest_get = AsyncMock(
            return_value=httpx.Response(200, text="not xml")
        )
        result = await list_systems(ctx)
        assert "Error" in result

    async def test_connection_error(self, ctx, mock_client):
        mock_client.rest_get = AsyncMock(side_effect=BeakerError("conn err"))
        result = await list_systems(ctx)
        assert "Error" in result


# ---- get_system_details ----------------------------------------------------

class TestGetSystemDetails:
    async def test_success(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(return_value={
            "fqdn": "host1.example.com",
            "status": "Automated",
            "type": "Machine",
            "arch": ["x86_64"],
            "owner": {"user_name": "admin"},
            "lab_controller": {"fqdn": "lc1"},
            "lender": None,
            "location": None,
            "serial_number": None,
            "model": None,
            "vendor": None,
            "mac_address": None,
            "memory": 16384,
            "numa_nodes": 2,
            "cpu": {"model_name": "Intel Xeon"},
        })
        result = await get_system_details(ctx, fqdn="host1.example.com")
        assert "host1.example.com" in result

    async def test_not_found(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(
            side_effect=BeakerNotFoundError("not found")
        )
        result = await get_system_details(ctx, fqdn="ghost.example.com")
        assert "not found" in result.lower()

    async def test_generic_error(self, ctx, mock_client):
        mock_client.rest_get_json = AsyncMock(side_effect=RuntimeError("boom"))
        result = await get_system_details(ctx, fqdn="host")
        assert "Error" in result


# ---- get_system_history ----------------------------------------------------

class TestGetSystemHistory:
    async def test_success(self, ctx, mock_client):
        result = await get_system_history(ctx, fqdn="host1.example.com")
        assert "testuser" in result

    async def test_xmlrpc_datetime(self, ctx, mock_client):
        """DateTime objects from XML-RPC should be stringified automatically."""
        mock_client.systems_history = AsyncMock(return_value=[
            {
                "created": DateTime("20260312T10:00:00"),
                "user": "testuser", "service": "XMLRPC",
                "action": "Changed", "field_name": "User",
                "old_value": "admin", "new_value": "testuser",
            },
        ])
        result = await get_system_history(ctx, fqdn="host1.example.com")
        assert "testuser" in result

    async def test_with_since(self, ctx, mock_client):
        await get_system_history(ctx, fqdn="host1", since="2026-01-01T00:00:00")
        mock_client.systems_history.assert_awaited_with("host1", "2026-01-01T00:00:00")

    async def test_without_since(self, ctx, mock_client):
        await get_system_history(ctx, fqdn="host1")
        mock_client.systems_history.assert_awaited_with("host1", None)

    async def test_error(self, ctx, mock_client):
        mock_client.systems_history = AsyncMock(side_effect=BeakerError("fail"))
        result = await get_system_history(ctx, fqdn="h")
        assert "Error" in result


# ---- get_system_inventory --------------------------------------------------

class TestGetSystemInventory:
    async def test_success(self, ctx, mock_client):
        result = await get_system_inventory(ctx, fqdn="host1.example.com")
        assert "CPUMODEL" in result
        assert "PCIID" in result
        assert "10de:20b5" in result
        assert "GenuineIntel" in result

    async def test_not_found(self, ctx, mock_client):
        mock_client.systems_get_inventory = AsyncMock(
            side_effect=BeakerNotFoundError("not found")
        )
        result = await get_system_inventory(ctx, fqdn="ghost.example.com")
        assert "not found" in result.lower()

    async def test_error(self, ctx, mock_client):
        mock_client.systems_get_inventory = AsyncMock(
            side_effect=BeakerError("fail")
        )
        result = await get_system_inventory(ctx, fqdn="host1")
        assert "Error" in result

    async def test_generic_error(self, ctx, mock_client):
        mock_client.systems_get_inventory = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        result = await get_system_inventory(ctx, fqdn="host1")
        assert "Error" in result


# ---- get_system_arches -----------------------------------------------------

class TestGetSystemArches:
    async def test_success(self, ctx, mock_client):
        result = await get_system_arches(ctx, fqdn="host1")
        assert "x86_64" in result
        assert "aarch64" in result

    async def test_error(self, ctx, mock_client):
        mock_client.systems_get_osmajor_arches = AsyncMock(
            side_effect=BeakerError("deny")
        )
        result = await get_system_arches(ctx, fqdn="host1")
        assert "Error" in result


# ---- reserve_system --------------------------------------------------------

class TestReserveSystem:
    async def test_success(self, ctx, mock_client):
        result = await reserve_system(ctx, fqdn="host1.example.com")
        assert "reserved" in result.lower()
        mock_client.systems_reserve.assert_awaited_with("host1.example.com")

    async def test_error(self, ctx, mock_client):
        mock_client.systems_reserve = AsyncMock(side_effect=BeakerError("already in use"))
        result = await reserve_system(ctx, fqdn="host1")
        assert "Error" in result
        assert "already in use" in result


# ---- release_system --------------------------------------------------------

class TestReleaseSystem:
    async def test_success(self, ctx, mock_client):
        result = await release_system(ctx, fqdn="host1.example.com")
        assert "released" in result.lower()

    async def test_error(self, ctx, mock_client):
        mock_client.systems_release = AsyncMock(side_effect=BeakerError("not yours"))
        result = await release_system(ctx, fqdn="host1")
        assert "Error" in result


# ---- power_system ----------------------------------------------------------

class TestPowerSystem:
    async def test_reboot(self, ctx, mock_client):
        result = await power_system(ctx, fqdn="host1", action="reboot")
        assert "reboot" in result.lower()
        mock_client.systems_power.assert_awaited_with("reboot", "host1", force=False)

    async def test_on(self, ctx, mock_client):
        result = await power_system(ctx, fqdn="host1", action="on")
        assert "on" in result.lower()

    async def test_off_with_force(self, ctx, mock_client):
        result = await power_system(ctx, fqdn="host1", action="off", force=True)
        assert "off" in result.lower()
        mock_client.systems_power.assert_awaited_with("off", "host1", force=True)

    async def test_invalid_action(self, ctx):
        result = await power_system(ctx, fqdn="host1", action="destroy")
        assert "Error" in result
        assert "Invalid action" in result

    async def test_error(self, ctx, mock_client):
        mock_client.systems_power = AsyncMock(side_effect=BeakerError("denied"))
        result = await power_system(ctx, fqdn="host1", action="reboot")
        assert "Error" in result


# ---- provision_system ------------------------------------------------------

class TestProvisionSystem:
    async def test_success(self, ctx, mock_client):
        result = await provision_system(ctx, fqdn="host1", distro_tree_id=100)
        assert "Provisioning" in result
        mock_client.systems_provision.assert_awaited_once()
        call_kwargs = mock_client.systems_provision.call_args
        assert call_kwargs[0][0] == "host1"
        assert call_kwargs[0][1] == 100

    async def test_with_options(self, ctx, mock_client):
        result = await provision_system(
            ctx,
            fqdn="host1",
            distro_tree_id=100,
            ks_meta="autopart",
            kernel_options="ks=nfs",
            reboot=False,
        )
        assert "Provisioning" in result
        mock_client.systems_provision.assert_awaited_once()

    async def test_error(self, ctx, mock_client):
        mock_client.systems_provision = AsyncMock(side_effect=BeakerError("no perms"))
        result = await provision_system(ctx, fqdn="host1", distro_tree_id=100)
        assert "Error" in result


# ---- loan_system -----------------------------------------------------------

class TestLoanSystem:
    async def test_loan_to_recipient(self, ctx, mock_client):
        result = await loan_system(ctx, fqdn="host1.example.com", recipient="jdoe")
        assert "loaned" in result.lower()
        assert "jdoe" in result
        mock_client.systems_loan_grant.assert_awaited_with(
            "host1.example.com", recipient="jdoe", comment="",
        )

    async def test_loan_to_self(self, ctx, mock_client):
        result = await loan_system(ctx, fqdn="host1.example.com")
        assert "loaned" in result.lower()
        assert "yourself" in result
        mock_client.systems_loan_grant.assert_awaited_with(
            "host1.example.com", recipient=None, comment="",
        )

    async def test_loan_with_comment(self, ctx, mock_client):
        result = await loan_system(
            ctx, fqdn="host1", recipient="jdoe", comment="TPM testing",
        )
        assert "loaned" in result.lower()
        mock_client.systems_loan_grant.assert_awaited_with(
            "host1", recipient="jdoe", comment="TPM testing",
        )

    async def test_error(self, ctx, mock_client):
        mock_client.systems_loan_grant = AsyncMock(
            side_effect=BeakerError("no permission"),
        )
        result = await loan_system(ctx, fqdn="host1", recipient="jdoe")
        assert "Error" in result
        assert "no permission" in result

    async def test_generic_error(self, ctx, mock_client):
        mock_client.systems_loan_grant = AsyncMock(side_effect=RuntimeError("boom"))
        result = await loan_system(ctx, fqdn="host1")
        assert "Error" in result


# ---- return_loan -----------------------------------------------------------

class TestReturnLoan:
    async def test_success(self, ctx, mock_client):
        result = await return_loan(ctx, fqdn="host1.example.com")
        assert "returned" in result.lower()
        mock_client.systems_loan_return.assert_awaited_with("host1.example.com")

    async def test_error(self, ctx, mock_client):
        mock_client.systems_loan_return = AsyncMock(
            side_effect=BeakerError("no active loan"),
        )
        result = await return_loan(ctx, fqdn="host1")
        assert "Error" in result
        assert "no active loan" in result

    async def test_generic_error(self, ctx, mock_client):
        mock_client.systems_loan_return = AsyncMock(side_effect=RuntimeError("boom"))
        result = await return_loan(ctx, fqdn="host1")
        assert "Error" in result
