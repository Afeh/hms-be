"""`If-Match` parsing and `ETag` writing for versioned resources."""

from __future__ import annotations

import pytest
from fastapi import Response

from app.core.errors import ApiError
from app.utils.etag import etag_for, require_version, set_etag


def test_etag_round_trips_through_if_match() -> None:
    assert etag_for(4) == '"4"'
    assert require_version('"4"') == 4


def test_set_etag_writes_a_quoted_version() -> None:
    response = Response()
    set_etag(response, 7)
    assert response.headers["etag"] == '"7"'


@pytest.mark.parametrize("header", [None, "", "4", 'W/"4"', '"v4"', "*"])
def test_missing_or_malformed_if_match_is_a_precondition_failure(
    header: str | None,
) -> None:
    with pytest.raises(ApiError) as raised:
        require_version(header)

    assert raised.value.status_code == 412
    assert raised.value.code == "precondition_failed"
