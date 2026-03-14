"""
Compatibility module.

Tools are dynamically discovered and registered via ai_tools/registry.py.
This module keeps a stable import shape if something expects evidence_tools.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_agent.ai_tools.registry import build_tools  # re-export

