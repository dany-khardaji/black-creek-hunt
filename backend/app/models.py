from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    @model_validator(mode="after")
    def validate_guests(self):
        for guest in self.guests:
            if guest.stand_id == self.stand_id:
                raise ValueError("Guest cannot be assigned the host's stand.")

        stand_ids = [guest.stand_id for guest in self.guests]

        if len(stand_ids) != len(set(stand_ids)):
            raise ValueError("Two guests cannot share the same stand.")

        return self
