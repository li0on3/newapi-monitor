from __future__ import annotations

import urllib.request
from typing import Any


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def open_without_redirects(request: urllib.request.Request, timeout: int):
    return urllib.request.build_opener(NoRedirectHandler()).open(request, timeout=timeout)
