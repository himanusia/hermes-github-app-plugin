"""Standalone GitHub App connector for Hermes Agent.

This directory is deliberately outside the managed Hermes source checkout.
It registers namespaced tools through the public plugin context only.
"""

from __future__ import annotations

import hashlib
import json
import uuid

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
            check_fn=lambda: github_credentials_available(ctx),
            emoji=emoji,
        )

    write_names = {"github_create_issue", "github_comment_issue", "github_review_pr", "github_merge_pr"}

    def require_operator_approval(tool_name: str, args: dict, **kwargs) -> dict | None:
        if tool_name not in write_names:
            return None
        payload = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
        if len(payload) > 3000:
            return {
                "action": "block",
                "message": "GitHub write payload is too large for a safe approval prompt; reduce it and retry.",
            }
        description = _approval_description(tool_name, payload)
        call_id = uuid.uuid4().hex
        digest = hashlib.sha256(
            json.dumps(
                {"tool": tool_name, "args": args, "tool_call_id": call_id},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "action": "approve",
            "message": description,
            "rule_key": f"github_app:{tool_name}:{digest}",
        }

    ctx.register_hook("pre_tool_call", require_operator_approval)


def _approval_description(tool_name: str, payload: str) -> str:
    return f"Approve this GitHub write ({tool_name}) with exact payload: {payload}"
