from pydantic import BaseModel, ConfigDict


class LifeAreaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
