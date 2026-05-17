from pydantic import BaseModel


class RegisterDeviceRequest(BaseModel):
    subscription_id: str
