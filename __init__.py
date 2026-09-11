"""Standalone GitHub App connector for Hermes Agent.

This directory is deliberately outside the managed Hermes source checkout.
It registers namespaced tools through the public plugin context only.
"""

from __future__ import annotations

from .client import github_credentials_available
from .tools import TOOL_SPECS, build_handlers


def register(ctx) -> None:
    handlers = build_handlers(ctx)
    for name, schema, emoji in TOOL_SPECS:
        ctx.register_tool(
            name=name,
            toolset="github_app",
            schema=schema,
            handler=handlers[name],
            check_fn=github_credentials_available,
            emoji=emoji,
        )
