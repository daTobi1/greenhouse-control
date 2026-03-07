from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any

import state

router = APIRouter()


@router.get("")
async def get_settings():
    """Return all settings."""
    return await state.db.get_all_settings()


@router.put("")
async def update_settings(updates: dict[str, Any]):
    """Update one or more settings."""
    await state.db.update_settings(updates)
    return await state.db.get_all_settings()
