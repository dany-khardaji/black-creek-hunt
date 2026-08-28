from pydantic import BaseModel


# Guest brought along on a hunt
class Guest(BaseModel):
    name: str
    phone: str
    stand_id: str


# The request body for POST /api/hunts
class CheckInRequest(BaseModel):
    stand_id: str
    guests: list[Guest] = []
