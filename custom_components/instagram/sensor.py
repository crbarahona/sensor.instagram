"""
Instagram sensor for Home Assistant.

Fetches public profile counts from Instagram's web profile endpoint.

This uses an unofficial Instagram endpoint and may break if Instagram changes
or blocks public profile access.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import async_timeout
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import CONF_NAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.entity import Entity

CONF_ACCOUNT = "account"

DEFAULT_NAME = "Instagram"
SCAN_INTERVAL = timedelta(hours=6)

ICON = "mdi:instagram"
BASE_URL = "https://i.instagram.com/api/v1/users/web_profile_info/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "X-IG-App-ID": "936619743392459",
}

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

    session = async_create_clientsession(hass)

    sensor = InstagramSensor(account, name, session)

    # Add the entity even if the first Instagram update fails.
    # This makes startup/debugging easier and avoids losing the entity on
    # temporary 403/429/network failures.
    async_add_entities([sensor], False)


class InstagramSensor(Entity):
    """Instagram sensor."""

    def __init__(self, account: str, name: str, session) -> None:
        """Initialize the sensor."""
        self._account = account
        self._attr_name = name if name != DEFAULT_NAME else f"Instagram {account}"
        self._attr_icon = ICON
        self._attr_should_poll = True

        self._session = session
        self._available = True

        self._full_name: str | None = None
        self._posts: int | None = None
        self._followers: int | None = None
        self._following: int | None = None
        self._profile_pic_url: str | None = None
        self._is_private: bool | None = None
        self._is_verified: bool | None = None

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this sensor."""
        return f"instagram_{self._account}"

    @property
    def available(self) -> bool:
        """Return whether the sensor is available."""
        return self._available

    @property
    def native_value(self):
        """Return the sensor value.

        Followers is used as the primary value because it is numeric and useful
        for history graphs. Other counts are exposed as attributes.
        """
        return self._followers

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return sensor attributes."""
        return {
            "account": self._account,
            "full_name": self._full_name,
            "posts": self._posts,
            "followers": self._followers,
            "following": self._following,
            "is_private": self._is_private,
            "is_verified": self._is_verified,
            "profile_pic_url": self._profile_pic_url,
        }

    async def async_update(self) -> None:
        """Update Instagram data."""
        _LOGGER.debug("Updating Instagram sensor for %s", self._account)

        try:
            async with async_timeout.timeout(10):
                response = await self._session.get(
                    BASE_URL,
                    params={"username": self._account},
                    headers=HEADERS,
                )

                text = await response.text()

                if response.status != 200:
                    _LOGGER.warning(
                        "Instagram returned HTTP %s for %s: %s",
                        response.status,
                        self._account,
                        text[:300],
                    )
                    self._available = False
                    return

                info: dict[str, Any] = await response.json()

            user = info.get("data", {}).get("user")

            if not user:
                _LOGGER.warning(
                    "Instagram response did not include user data for %s: %s",
                    self._account,
                    info,
                )
                self._available = False
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

            self._available = True

        except Exception as error:
            _LOGGER.warning(
                "Could not update Instagram sensor for %s: %s",
                self._account,
                error,
            )
            self._available = False
