from pydantic import BaseModel, ConfigDict, Field


# Guest brought along on a hunt
class Guest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    stand_id: str = Field(min_length=1)


# The request body for POST /api/hunts
class CheckInRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    stand_id: str = Field(min_length=1)
    guests: list[Guest] = Field(default_factory=list, max_length=2)
