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


# The request body for POST /api/auth/login
class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(min_length=1, max_length=254)

    # Capped because checking a password takes work: a huge one would tie up
    # the server.
    password: str = Field(min_length=1, max_length=1024)
