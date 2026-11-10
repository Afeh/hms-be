"""Response models for the health probes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

State = Literal["ok", "unavailable"]


class Dependencies(BaseModel):
    mongo: State
    redis: State


class LiveHealth(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyHealth(BaseModel):
    status: Literal["ok", "degraded"]
    dependencies: Dependencies
