"""Shared test helpers"""

from http import HTTPStatus

import pytest
import requests


def build_response(
    status_code: int = HTTPStatus.OK,
    content: bytes = b"",
    headers: dict | None = None,
    url: str = "https://example.com/file",
    reason: str = "OK",
) -> requests.Response:
    """Build a real requests.Response with a preset body, for faking downloads

    It supports what collectors use: `with`, iter_content, content, text,
    headers and raise_for_status.

    Args:
        status_code: HTTP status of the response.
        content: Body of the response.
        headers: Response headers, such as ETag or Content-Type.
        url: URL the response came from.
        reason: HTTP reason phrase.

    Returns:
        The response.
    """
    response = requests.Response()
    response.status_code = status_code
    response.reason = reason
    response.url = url
    response.headers.update(headers or {})
    response._content = content
    response._content_consumed = True
    return response


@pytest.fixture
def fake_response():
    """Give tests the build_response function

    Returns:
        build_response, which makes a requests.Response with a preset body.
    """
    return build_response
