from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.waitlist import WaitlistEntry

router = APIRouter()


class WaitlistRequest(BaseModel):
    email: EmailStr
    source: str | None = None


class WaitlistResponse(BaseModel):
    message: str
    email: str


@router.post("/", response_model=WaitlistResponse, status_code=status.HTTP_201_CREATED)
async def join_waitlist(payload: WaitlistRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WaitlistEntry).where(WaitlistEntry.email == payload.email)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    entry = WaitlistEntry(email=payload.email, source=payload.source)
    db.add(entry)
    await db.commit()
    return WaitlistResponse(message="You're on the list!", email=payload.email)
