"""HTTP API. TODO(task#9): implement against the DB + queue."""
from fastapi import APIRouter

router = APIRouter(prefix="", tags=["api"])


# TODO(task#9): POST /jobs {url, source?} -> {job_id, status}
# TODO(task#9): GET  /jobs/{id} -> {status, result?}
# TODO(task#9): GET  /transcripts -> paginated list
# TODO(task#9): GET  /transcripts/{id} -> transcript + summary + metadata
# TODO(task#9): GET  /transcripts/{id}/{transcript,summary}.md -> raw markdown
