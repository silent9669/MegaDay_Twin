"""AI tools (07 §3): registry + read / compute / draft tools.

``execute(name, arguments, ctx)`` is the single entry point; ``specs()`` lists the tools and
``ToolSpec.openai_spec()`` gives the strict function schema for the Responses API.
"""

from __future__ import annotations

from app.ai.tools.registry import (
    REGISTRY,
    Source,
    ToolContext,
    ToolData,
    ToolResult,
    ToolSpec,
    execute,
    get_spec,
    specs,
)

__all__ = [
    "REGISTRY",
    "Source",
    "ToolContext",
    "ToolData",
    "ToolResult",
    "ToolSpec",
    "execute",
    "get_spec",
    "specs",
]
