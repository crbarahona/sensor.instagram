"""
Instagram sensor for Home Assistant.

Fetches public profile counts from Instagram's web profile endpoint using curl.

This uses an unofficial Instagram endpoint and may break if Instagram changes
or blocks public profile access.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
from typing import Any
from urllib.parse import quote

import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.const import CONF_NAME

CONF_ACCOUNT = "account"

DEFAULT_NAME = "Instagram"
SCAN_INTERVAL = timedelta(hours=24)

ICON = "mdi:instagram"
BASE_URL = "https://i.instagram.com/api/v1/users/web_profile_info/"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ACCOUNT): vol.Match(r"^[A-Za-z0-9._]+$"),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Instagram sensor platform."""
    account = config[CONF_ACCOUNT]
    name = config[CONF_NAME]

    _LOGGER.warning("Setting up Instagram sensor platform for %s", account)

    sensor = InstagramSensor(account, name)
    async_add_entities([sensor], False)


class InstagramSensor(SensorEntity):
    """Instagram sensor."""

    _attr_icon = ICON
    _attr_should_poll = True

    def __init__(self, account: str, name: str) -> None:
        """Initialize the sensor."""
        self._account = account

        self._attr_name = name if name != DEFAULT_NAME else f"Instagram {account}"
        self._attr_unique_id = f"instagram_{account}"

        # Keep available so diagnostics remain visible.
        self._attr_available = True
        self._attr_native_value = "not updated"

        self._full_name: str | None = None
        self._posts: int | None = None
        self._followers: int | None = None
        self._following: int | None = None
        self._profile_pic_url: str | None = None
        self._is_private: bool | None = None
        self._is_verified: bool | None = None

        self._last_error: str | None = "not updated yet"
        self._last_status: int | None = None
        self._last_response_preview: str | None = None

        self._refresh_attrs()

    def _refresh_attrs(self) -> None:
        """Refresh entity attributes."""
        self._attr_extra_state_attributes = {
            "account": self._account,
            "integration_version": "1.0.8-curl",
            "fetch_method": "curl",
            "full_name": self._full_name,
            "posts": self._posts,
            "followers": self._followers,
            "following": self._following,
            "is_private": self._is_private,
            "is_verified": self._is_verified,
            "profile_pic_url": self._profile_pic_url,
            "last_error": self._last_error,
            "last_status": self._last_status,
            "last_response_preview": self._last_response_preview,
        }

    async def _fetch_with_curl(self) -> tuple[int, str, str]:
        """Fetch Instagram profile data using curl.

        Returns:
            A tuple of HTTP status code, response body, and stderr.
        """
        username = quote(self._account, safe="")
        url = f"{BASE_URL}?username={username}"

        cmd = [
            "curl",
            "-sS",
            "-L",
            "--max-time",
            "15",
            "-w",
            "\n%{http_code}",
            url,
            "-H",
            (
                "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "-H",
            "Accept: application/json",
            "-H",
            "Accept-Language: en-US,en;q=0.9",
            "-H",
            "X-IG-App-ID: 936619743392459",
            "-H",
            "Referer: https://www.instagram.com/",
            "-H",
            "Origin: https://www.instagram.com",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            raise RuntimeError(
                f"curl failed with exit code {proc.returncode}: {stderr_text}"
            )

        try:
            body, status_text = stdout_text.rsplit("\n", 1)
            status = int(status_text.strip())
        except ValueError as error:
            raise RuntimeError(f"Could not parse curl status output: {stdout_text}") from error

        return status, body, stderr_text

    async def async_update(self) -> None:
        """Update Instagram data."""
        _LOGGER.warning("Updating Instagram sensor for %s using curl", self._account)

        try:
            status, body, stderr_text = await self._fetch_with_curl()
            preview = body[:500]

            self._last_status = status
            self._last_response_preview = preview
            self._last_error = None

            if status != 200:
                self._last_error = f"HTTP {status}"
                if stderr_text:
                    self._last_error = f"{self._last_error}: {stderr_text}"

                self._attr_native_value = f"error {status}"
                self._attr_available = True
                self._refresh_attrs()

                _LOGGER.warning(
                    "Instagram returned HTTP %s for %s: %s",
                    status,
                    self._account,
                    preview,
                )
                return

            try:
                info: dict[str, Any] = json.loads(body)
            except Exception as json_error:
                self._last_error = f"JSON parse error: {json_error}"
                self._attr_native_value = "json error"
                self._attr_available = True
                self._refresh_attrs()

                _LOGGER.warning(
                    "Instagram returned non-JSON response for %s: %s",
                    self._account,
                    preview,
                )
                return

            user = info.get("data", {}).get("user")

            if not user:
                self._last_error = "No user object in Instagram response"
                self._attr_native_value = "parse error"
                self._attr_available = True
                self._refresh_attrs()

                _LOGGER.warning(
                    "Instagram response did not include user data for %s: %s",
                    self._account,
                    info,
                )
                return

            self._full_name = user.get("full_name")
            self._posts = user.get("edge_owner_to_timeline_media", {}).get("count")
            self._followers = user.get("edge_followed_by", {}).get("count")
            self._following = user.get("edge_follow", {}).get("count")
            self._profile_pic_url = user.get("profile_pic_url_hd") or user.get(
                "profile_pic_url"
            )
            self._is_private = user.get("is_private")
            self._is_verified = user.get("is_verified")

            self._attr_native_value = self._followers
            self._attr_available = True
            self._last_error = None
            self._refresh_attrs()

        except Exception as error:
            self._last_error = str(error)
            self._attr_native_value = "exception"
            self._attr_available = True
            self._refresh_attrs()

            _LOGGER.warning(
                "Could not update Instagram sensor for %s using curl: %s",
                self._account,
                error,
            )
