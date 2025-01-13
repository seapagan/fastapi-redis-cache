"""Dummy FastAPI app to test the cache decorator and functionality."""

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Union

from fastapi import FastAPI, Request, Response

from fastapi_redis_cache import cache, cache_one_hour, cache_one_minute

app = FastAPI(title="FastAPI Redis Cache Test App")


@app.get("/cache_never_expire")
@cache()
def cache_never_expire(
    request: Request, response: Response
) -> dict[str, Union[bool, str]]:
    """Route where the cache never expires."""
    return {"success": True, "message": "this data can be cached indefinitely"}


@app.get("/cache_expires")
@cache(expire=timedelta(seconds=5))
async def cache_expires() -> dict[str, Union[bool, str]]:
    """Route where the cache expires after 5 seconds."""
    return {
        "success": True,
        "message": "this data should be cached for five seconds",
    }


@app.get("/cache_json_encoder")
@cache()
def cache_json_encoder() -> dict[
    str, Union[bool, str, datetime, date, Decimal]
]:
    """Route that returns a dictionary with different data types."""
    return {
        "success": True,
        "start_time": datetime(2021, 4, 20, 7, 17, 17, tzinfo=timezone.utc),
        "finish_by": date(2021, 4, 21),
        "final_calc": Decimal(3.14),
    }


@app.get("/cache_one_hour")
@cache_one_hour()
def partial_cache_one_hour(response: Response) -> dict[str, Union[bool, str]]:
    """Route where the cache expires after one hour."""
    return {
        "success": True,
        "message": "this data should be cached for one hour",
    }


@app.get("/cache_invalid_type", response_model=None)
@cache_one_minute()
def cache_invalid_type(request: Request, response: Response) -> logging.Logger:
    """Route that returns a type that cannot be cached."""
    logging.basicConfig()
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    return logger
