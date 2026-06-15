"""Pydantic models for API."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CreateCaseRequest(BaseModel):
    company_name: str
    industry_code: str = "generic"
    industry_hint: str = "generic"
    country: str = "Singapore"
    jurisdiction: str = "Singapore"
    uen: str = ""
    entity_type: str = ""
    company_status: str = ""
    incorporation_date: str = ""
    fiscal_year_end: str = ""
    primary_ssic_code: str = ""
    primary_ssic_desc: str = ""
    registered_address: str = ""
    currency: str = "SGD"
    facility_type: str = ""
    requested_limit: str = ""
    relationship_manager: str = ""
    priority: str = "normal"
    onboarding_stage: str = "entity_profile"
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
    tool_trace: List[Dict[str, Any]] = Field(default_factory=list)
    skill_used: Optional[str] = None
