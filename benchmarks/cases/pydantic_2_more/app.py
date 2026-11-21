from pydantic import BaseModel
from pydantic import PyObject  # BREAK
from pydantic import ConstrainedInt  # BREAK


class M(BaseModel):
    x: int = 1
