import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.notifications import RegisterDeviceRequest
from app.services import onesignal

router = APIRouter()


@router.post("/register-device")
async def register_device(
    body: RegisterDeviceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Register or update the OneSignal subscription ID for push notifications."""
    user.onesignal_subscription_id = body.subscription_id
    await db.commit()

    asyncio.create_task(onesignal.tag_new_user(body.subscription_id, user))

    return {"status": "ok"}
