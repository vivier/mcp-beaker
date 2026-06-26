"""Beaker MCP server instance and tool registration."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from mcp_beaker.client import BeakerClient
from mcp_beaker.config import BeakerConfig, ServerSettings

__all__ = ["mcp"]

logger = logging.getLogger("mcp-beaker")


class LifespanContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: BeakerClient
    config: BeakerConfig
    settings: ServerSettings


@asynccontextmanager
async def beaker_lifespan(server: FastMCP) -> AsyncIterator[LifespanContext]:
    """Create the BeakerClient and app context at startup."""
    config = BeakerConfig.from_env()
    settings = ServerSettings()

    logger.info("Connecting to Beaker at %s (auth=%s)", config.url, config.auth_method)

    client = BeakerClient(config)
    yield LifespanContext(client=client, config=config, settings=settings)

    logger.info("Beaker MCP server shutting down.")


def beaker_client(ctx) -> BeakerClient:  # noqa: ANN001
    """Retrieve the BeakerClient from the lifespan context.

    This is the DI helper every tool calls. Uses the standard FastMCP v3
    access pattern: ``ctx.request_context.lifespan_context``.
    """
    return ctx.request_context.lifespan_context.client


mcp = FastMCP("Beaker MCP", lifespan=beaker_lifespan)

# Import tool modules to register them with the MCP server.
# This must happen after mcp is created so the @mcp.tool() decorators can reference it.
from mcp_beaker.servers import (  # noqa: E402, F401
    distros,
    general,
    jobs,
    pools,
    prompts,
    resources,
    systems,
    tasks,
)
