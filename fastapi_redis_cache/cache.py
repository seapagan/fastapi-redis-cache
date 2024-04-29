"""The main cache decorator code and helpers."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from functools import partial, update_wrapper, wraps
from http import HTTPStatus
from typing import Any, Callable, Union

from fastapi import Response

from fastapi_redis_cache.client import FastApiRedisCache
from fastapi_redis_cache.enums import RedisEvent
from fastapi_redis_cache.util import (
    ONE_DAY_IN_SECONDS,
    ONE_HOUR_IN_SECONDS,
    ONE_MONTH_IN_SECONDS,
    ONE_WEEK_IN_SECONDS,
    ONE_YEAR_IN_SECONDS,
    deserialize_json,
    serialize_json,
)

JSON_MEDIA_TYPE = "application/json"


def cache(
    *,
    expire: Union[int, timedelta] = ONE_YEAR_IN_SECONDS,
    tag: str | None = None,
) -> Callable[..., Any]:
    """Enable caching behavior for the decorated function.

    Args:
        expire (Union[int, timedelta], optional): The number of seconds
            from now when the cached response should expire. Defaults to
            31,536,000 seconds (i.e., the number of seconds in one year).
        tag (str, optional): A tag to associate with the cached response. This
            can later be used to invalidate all cached responses with the same
            tag, or for further fine-grained cache expiry. Defaults to None.
    """

    def outer_wrapper(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def inner_wrapper(
            *args: Any,  # noqa: ANN401
            **kwargs: Any,  # noqa: ANN401
        ) -> Any:  # noqa: ANN401
            """Return cached value if one exists.

            Otherwise evaluate the wrapped function and cache the result.
            """
            func_kwargs = kwargs.copy()
            request = func_kwargs.pop("request", None)
            response = func_kwargs.pop("response", None)
            create_response_directly = not response

            if create_response_directly:
                response = Response()
                # below fix by @jaepetto on the original repo.
                if "content-length" in response.headers:
                    del response.headers["content-length"]

            redis_cache = FastApiRedisCache()
            if (
                redis_cache.not_connected
                or redis_cache.request_is_not_cacheable(request)
            ):
                # if the redis client is not connected or request is not
                # cacheable, no caching behavior is performed.
                return await get_api_response_async(func, *args, **kwargs)

            key = redis_cache.get_cache_key(tag, func, *args, **kwargs)
            ttl, in_cache = redis_cache.check_cache(key)
            if in_cache:
                redis_cache.set_response_headers(
                    response, True, deserialize_json(in_cache), ttl
                )
                if redis_cache.requested_resource_not_modified(
                    request, in_cache
                ):
                    response.status_code = int(HTTPStatus.NOT_MODIFIED)
                    return (
                        Response(
                            content=None,
                            status_code=response.status_code,
                            media_type=JSON_MEDIA_TYPE,
                            headers=response.headers,
                        )
                        if create_response_directly
                        else response
                    )
                return (
                    Response(
                        content=in_cache,
                        media_type="application/json",
                        headers=response.headers,
                    )
                    if create_response_directly
                    else deserialize_json(in_cache)
                )
            response_data = await get_api_response_async(func, *args, **kwargs)
            ttl = calculate_ttl(expire)
            cached = redis_cache.add_to_cache(key, response_data, ttl)
            if tag:
                # if tag is provided, add the key to the tag set. This should
                # help us search quicker for keys to invalidate.
                redis_cache.add_key_to_tag_set(tag, key)
            if cached:
                redis_cache.set_response_headers(
                    response,
                    cache_hit=False,
                    response_data=response_data,
                    ttl=ttl,
                )
                return (
                    Response(
                        content=serialize_json(response_data),
                        media_type=JSON_MEDIA_TYPE,
                        headers=response.headers,
                    )
                    if create_response_directly
                    else response_data
                )
            return response_data

        return inner_wrapper

    return outer_wrapper


def expires(
    tag: str | None = None,
    arg: str | None = None,  # noqa: ARG001
) -> Callable[..., Any]:
    """Invalidate all cached responses with the same tag.

    Args:
        tag (str, optional): The tag to search for keys to expire.
            Defaults to None.
        arg: (str, optional): The function arguement to filter for expiry. This
            would generally be the varying arguement suppplied to the route.
            Defaults to None. If not specified, the kwargs for the route will
            be used to search for the key to expire.
    """

    def outer_wrapper(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def inner_wrapper(
            *args: Any,  # noqa: ANN401
            **kwargs: Any,  # noqa: ANN401
        ) -> Any:  # noqa: ANN401
            """Invalidate all cached responses with the same tag."""
            response = kwargs.get("response", None)
            create_response_directly = not response

            if create_response_directly:
                response = Response()
                if "content-length" in response.headers:
                    del response.headers["content-length"]

            redis_cache = FastApiRedisCache()
            orig_response = await get_api_response_async(func, *args, **kwargs)

            ignore_args = redis_cache.ignore_arg_types

            if redis_cache.redis and redis_cache.connected and tag and kwargs:
                # remove any args that should not be used to generate the cache
                # key.
                filtered_kwargs = kwargs.copy()
                for key in list(filtered_kwargs.keys()):
                    if type(filtered_kwargs[key]) in ignore_args:
                        del filtered_kwargs[key]
                # create the search string to find the keys to expire.
                search = "".join(
                    [
                        f"({key}={value})"
                        for key, value in filtered_kwargs.items()
                    ]
                )
                tag_keys = redis_cache.get_tagged_keys(tag)
                found_keys = [key for key in tag_keys if search.encode() in key]
                for this_key in found_keys:
                    key_str = (
                        this_key.decode()
                        if isinstance(this_key, bytes)
                        else this_key
                    )

                    redis_cache.log(
                        RedisEvent.KEY_DELETED_FROM_CACHE, key=str(key_str)
                    )
                    redis_cache.redis.delete(key_str)
                    redis_cache.redis.srem(tag, key_str)

            return Response(
                content=serialize_json(orig_response),
                media_type=JSON_MEDIA_TYPE,
                headers=response.headers,
            )

        return inner_wrapper

    return outer_wrapper


async def get_api_response_async(
    func: Callable[..., Any],
    *args: Any,  # noqa: ANN401
    **kwargs: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Helper function that to handle both async and non-async functions."""
    return (
        await func(*args, **kwargs)
        if asyncio.iscoroutinefunction(func)
        else func(*args, **kwargs)
    )


def calculate_ttl(expire: Union[int, timedelta]) -> int:
    """Converts expire time to total seconds.

    Also ensures ttl is capped at one year.
    """
    if isinstance(expire, timedelta):
        expire = int(expire.total_seconds())
    return min(expire, ONE_YEAR_IN_SECONDS)


cache_one_minute = partial(cache, expire=60)
cache_one_hour = partial(cache, expire=ONE_HOUR_IN_SECONDS)
cache_one_day = partial(cache, expire=ONE_DAY_IN_SECONDS)
cache_one_week = partial(cache, expire=ONE_WEEK_IN_SECONDS)
cache_one_month = partial(cache, expire=ONE_MONTH_IN_SECONDS)
cache_one_year = partial(cache, expire=ONE_YEAR_IN_SECONDS)

update_wrapper(cache_one_minute, cache)
update_wrapper(cache_one_hour, cache)
update_wrapper(cache_one_day, cache)
update_wrapper(cache_one_week, cache)
update_wrapper(cache_one_month, cache)
update_wrapper(cache_one_year, cache)
