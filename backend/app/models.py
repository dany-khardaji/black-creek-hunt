from pydantic import BaseModel


class Guest(BaseModel):
    name: str
    phone: str
    stand_id: str


class CheckInRequest(BaseModel):
    stand_id: str
    guests: list[Guest] = []
