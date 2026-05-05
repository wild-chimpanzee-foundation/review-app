from pathlib import Path

from fastapi import Request
from fastapi.responses import Response

_video_dirs: list[Path] = []


def set_media_dirs(dirs: list[Path | str]) -> None:
    _video_dirs.clear()
    _video_dirs.extend(Path(d).resolve() for d in dirs)


def setup_media_route() -> None:
    from nicegui import app as nicegui_app
    from nicegui.app.range_response import get_range_response

    @nicegui_app.get("/media/{filepath:path}")
    def serve_media(request: Request, filepath: str, nicegui_chunk_size: int = 8192) -> Response:
        for base in _video_dirs:
            try:
                candidate = (base / filepath).resolve()
                if candidate.is_relative_to(base) and candidate.is_file():
                    return get_range_response(candidate, request, chunk_size=nicegui_chunk_size)
            except Exception:
                continue
        return Response(status_code=404, content="Not Found")
