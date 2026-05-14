"""Job worker loop. TODO(task#8): poll jobs table, run the pipeline.

Pipeline order (see design doc):
  download -> ffmpeg -> vast whisper -> summarize -> shortlinks -> store
Status transitions: queued -> downloading -> transcribing -> summarizing -> done|failed
"""


def run_worker() -> None:
    raise NotImplementedError("task#8")
