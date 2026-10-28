"""
POST /v1/contact -- the website's Contact form. Anonymous by design (a visitor writing to us), so it is protected by
Cloudflare Turnstile checked on the server, a honeypot, and rate limits; see app/services/contact.py for the whole pipeline
and its honest limits. It returns only {"ok": true}: nothing about the stored row, and no difference a bot could probe.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.services import contact

router = APIRouter(prefix="/v1/contact", tags=["contact"])


async def get_pool(request: Request):
    return request.app.state.pool


class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., max_length=200)
    email: str = Field(..., max_length=320)
    company: Optional[str] = Field(None, max_length=400)
    topic: str = Field("general", max_length=20)
    message: str = Field(..., max_length=8000)
    source_path: Optional[str] = Field(None, max_length=400)
    turnstile_token: Optional[str] = Field(None, max_length=4096)
    website: str = Field("", max_length=400)  # the honeypot: a person never sees or fills it


@router.post("", status_code=202)
async def submit_contact(body: ContactIn, request: Request, pool=Depends(get_pool)) -> dict:
    ip = request.client.host if request.client else None  # the direct socket address; see deps.scope_key_for
    try:
        return await contact.submit(
            pool,
            name=body.name, email=body.email, company=body.company, topic=body.topic, message=body.message,
            source_path=body.source_path, honeypot=body.website, turnstile_token=body.turnstile_token, remote_ip=ip,
        )
    except contact.ContactRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.reason)
