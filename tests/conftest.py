"""Shared fixtures for mcp-beaker tool tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from mcp_beaker.client import BeakerClient
from mcp_beaker.config import BeakerConfig, ServerSettings
from mcp_beaker.servers import LifespanContext


def _make_config(**overrides: Any) -> BeakerConfig:
    defaults = {
        "url": "https://beaker.test.example.com",
        "auth_method": "password",
        "username": "testuser",
        "password": "testpass",
        "owner": "testuser",
        "ssl_verify": False,
    }
    defaults.update(overrides)
    return BeakerConfig(**defaults)


@pytest.fixture()
def beaker_config() -> BeakerConfig:
    return _make_config()


@pytest.fixture()
def mock_client(beaker_config: BeakerConfig) -> BeakerClient:
    """BeakerClient where every async method is an AsyncMock."""
    client = BeakerClient(beaker_config)
    client.whoami = AsyncMock(
        return_value={"username": "testuser", "email_address": "test@example.com"},
    )
    client.lab_controllers = AsyncMock(return_value=["lc1.example.com", "lc2.example.com"])
    client.jobs_filter = AsyncMock(return_value=["J:100", "J:101"])
    client.jobs_upload = AsyncMock(return_value="J:200")
    client.jobs_set_response = AsyncMock(return_value=None)
    client.taskactions_task_info = AsyncMock(return_value={"state": "Completed", "method": "test"})
    client.taskactions_to_xml = AsyncMock(return_value="<job>cloned</job>")
    client.taskactions_files = AsyncMock(return_value=[
        {
            "filename": "console.log",
            "url": "https://beaker.test/logs/console.log",
            "path": "", "server": "", "basepath": "",
        },
    ])
    client.taskactions_stop = AsyncMock(return_value=None)
    client.distrotrees_filter = AsyncMock(return_value=[
        {
            "distro_name": "RHEL-10.2", "arch": "x86_64",
            "variant": "BaseOS", "distro_id": 1, "distro_tree_id": 100,
        },
    ])
    client.distros_get_osmajors = AsyncMock(return_value=["RedHatEnterpriseLinux10", "Fedora42"])
    client.tasks_filter = AsyncMock(return_value=[
        {"name": "/distribution/reservesys", "arches": []},
    ])
    client.systems_reserve = AsyncMock(return_value=None)
    client.systems_release = AsyncMock(return_value=None)
    client.systems_power = AsyncMock(return_value=None)
    client.systems_provision = AsyncMock(return_value=None)
    client.systems_history = AsyncMock(return_value=[
        {"created": "2026-03-12T10:00:00", "user": "testuser", "service": "XMLRPC",
         "action": "Returned", "field_name": "User", "old_value": "testuser", "new_value": ""},
    ])
    client.systems_get_osmajor_arches = AsyncMock(return_value={
        "RedHatEnterpriseLinux10": ["x86_64", "aarch64"],
    })
    client.systems_get_inventory = AsyncMock(return_value={
        "CPUMODEL": ["Intel(R) Xeon(R) Gold 6330 CPU @ 2.00GHz"],
        "CPUVENDOR": ["GenuineIntel"],
        "PCIID": ["10de:20b5", "8086:a194"],
        "FORMFACTOR": ["rackmount"],
        "HVM": ["1"],
        "NR_DISKS": ["1"],
        "NR_ETH": ["4"],
    })
    client.systems_loan_grant = AsyncMock(return_value=None)
    client.systems_loan_return = AsyncMock(return_value=None)
    client.recipes_tasks_extend = AsyncMock(return_value=None)

    _json_response = {
        "id": 100, "status": "Completed", "result": "Pass",
        "whiteboard": "Test job", "is_finished": True, "submitted_time": "2026-03-12",
        "owner": {"user_name": "testuser"}, "recipesets": [],
    }
    _text_response = httpx.Response(200, text="<feed/>")
    _json_http = httpx.Response(200, json=_json_response)

    client.rest_get = AsyncMock(return_value=_text_response)
    client.rest_get_json = AsyncMock(return_value=_json_response)
    client.rest_get_text = AsyncMock(return_value="<feed/>")
    return client


@pytest.fixture()
def ctx(mock_client: BeakerClient, beaker_config: BeakerConfig) -> MagicMock:
    """Fake FastMCP Context whose lifespan_context holds our mock client."""
    lifespan = LifespanContext(
        client=mock_client,
        config=beaker_config,
        settings=ServerSettings(),
    )
    mock_ctx = MagicMock()
    mock_ctx.request_context.lifespan_context = lifespan
    return mock_ctx


SAMPLE_JOB_XML = """\
<job retention_tag="scratch">
  <whiteboard>Test job</whiteboard>
  <recipeSet priority="Normal">
    <recipe role="RECIPE_MEMBERS">
      <autopick random="false"/>
      <watchdog panic="ignore"/>
      <distroRequires>
        <and>
          <distro_family op="=" value="RedHatEnterpriseLinux10"/>
          <distro_variant op="=" value="BaseOS"/>
          <distro_name op="=" value="RHEL-10.2-20260127.0"/>
          <distro_arch op="=" value="x86_64"/>
        </and>
      </distroRequires>
      <hostRequires>
        <and>
          <hostname op="like" value="host%"/>
          <system_type value="Machine"/>
        </and>
      </hostRequires>
      <task name="/distribution/reservesys" role="STANDALONE"/>
    </recipe>
  </recipeSet>
</job>"""
