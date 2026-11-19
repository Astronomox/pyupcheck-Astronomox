from pydantic import BaseModel, Field
from pydantic import BaseSettings  # BREAK


class User(BaseModel):
    name: str = Field(default="x")


class Settings(BaseSettings):  # BREAK
    debug: bool = False
