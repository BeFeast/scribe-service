"""Web-UI: browse transcript history. TODO(task#10)."""
from fastapi import APIRouter

router = APIRouter(prefix="", tags=["web"])

# TODO(task#10): GET /            -> transcript list (Jinja)
# TODO(task#10): GET /t/{id}      -> transcript detail (Jinja)
