"""Pydantic models for API."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CreateCaseRequest(BaseModel):
    company_name: str
    industry_code: str = "generic"
    industry_hint: str = "generic"
    cin: str = ""
    pan: str = ""
    fy_range: List[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str
    skill: Optional[str] = None


class CaseStatusResponse(BaseModel):
    case_id: str
    status: str
    progress: int
    company_name: str
    errors: List[Dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    skill_used: Optional[str] = None
