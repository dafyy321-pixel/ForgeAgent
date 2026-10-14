"""Shared HTTP dependencies; resource authorization remains centralized."""
from typing import Annotated

from fastapi import Depends

from .auth import Identity, authorized_identity

Actor = Annotated[Identity, Depends(authorized_identity)]
