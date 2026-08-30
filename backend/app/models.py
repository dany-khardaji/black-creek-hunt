from pydantic import BaseModel, model_validator


# Guest brought along on a hunt
class Guest(BaseModel):
    name: str
    phone: str
    stand_id: str


# The request body for POST /api/hunts
class CheckInRequest(BaseModel):
    stand_id: str
    guests: list[Guest] = []

    @model_validator(mode="after")
    def validate_guests(self):
        if len(self.guests) > 2:
            raise ValueError("Guest limit exceeded.")

        for guest in self.guests:
            if guest.stand_id == self.stand_id:
                raise ValueError("Guest cannot be assigned the host's stand.")

        stand_ids = [guest.stand_id for guest in self.guests]

        if len(stand_ids) != len(set(stand_ids)):
            raise ValueError("Two guests cannot share the same stand.")

        return self
