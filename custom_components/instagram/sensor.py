"""
Instagram sensors for Home Assistant.

Fetches public profile counts, recent reel aggregates, and recent post
engagement aggregates from Instagram's web endpoints using curl.

This uses unofficial Instagram endpoints and may break if Instagram changes
or blocks public profile access.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import re
import tempfile
from typing import Any
from urllib.parse import quote

import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import CONF_NAME, CONF_SCAN_INTERVAL
from homeassistant.helpers.event import async_track_time_interval

CONF_ACCOUNT = "account"
CONF_TARGET_USER_ID = "target_user_id"

DEFAULT_NAME = "Instagram"
DEFAULT_SCAN_INTERVAL = timedelta(hours=24)

ICON = "mdi:instagram"
PROFILE_URL = "https://i.instagram.com/api/v1/users/web_profile_info/"
CLIPS_USER_URL = "https://www.instagram.com/api/v1/clips/user/"
RECENT_MEDIA_WINDOW_DAYS = 7

PROFILE_METRIC_KEYS = (
    "followers",
    "posts",
    "following",
    "posts_7d_count",
    "posts_7d_likes",
    "posts_7d_comments",
    "recent_7d_count",
    "recent_7d_likes",
    "recent_7d_comments",
    "recent_7d_engagement",
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ACCOUNT): vol.Match(r"^[A-Za-z0-9._]+$"),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): cv.time_period,
        vol.Optional(CONF_TARGET_USER_ID): cv.string,
    }
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up Instagram sensors."""
    account = config[CONF_ACCOUNT]
    name = config[CONF_NAME]
    scan_interval = config[CONF_SCAN_INTERVAL]
    target_user_id = config.get(CONF_TARGET_USER_ID)

    _LOGGER.warning(
        "Setting up Instagram sensors for %s with scan interval %s",
        account,
        scan_interval,
    )

    data = InstagramData(account, scan_interval, target_user_id)

    entities = [
        InstagramMetricSensor(data, name, "followers", "Followers", "mdi:account-group"),
        InstagramMetricSensor(data, name, "posts", "Posts", "mdi:grid"),
        InstagramMetricSensor(data, name, "following", "Following", "mdi:account-arrow-right"),
        InstagramMetricSensor(data, name, "reels_7d_count", "Reels 7d Count", "mdi:movie-open-play"),
        InstagramMetricSensor(data, name, "reels_7d_likes", "Reels 7d Likes", "mdi:heart"),
        InstagramMetricSensor(data, name, "reels_7d_comments", "Reels 7d Comments", "mdi:comment"),
        InstagramMetricSensor(data, name, "reels_7d_views", "Reels 7d Views", "mdi:eye"),
        InstagramMetricSensor(data, name, "posts_7d_count", "Posts 7d Count", "mdi:grid"),
        InstagramMetricSensor(data, name, "posts_7d_likes", "Posts 7d Likes", "mdi:heart"),
        InstagramMetricSensor(data, name, "posts_7d_comments", "Posts 7d Comments", "mdi:comment"),
        InstagramMetricSensor(data, name, "recent_7d_count", "Recent 7d Count", "mdi:image-multiple"),
        InstagramMetricSensor(data, name, "recent_7d_likes", "Recent 7d Likes", "mdi:heart-multiple"),
        InstagramMetricSensor(data, name, "recent_7d_comments", "Recent 7d Comments", "mdi:comment-multiple"),
        InstagramMetricSensor(data, name, "recent_7d_engagement", "Recent 7d Engagement", "mdi:chart-line"),
    ]

    async_add_entities(entities, False)

    async def update_all(now=None):
        await data.async_update()
        for entity in entities:
            entity.async_write_ha_state()

    hass.async_create_task(update_all())
    async_track_time_interval(hass, update_all, scan_interval)


class InstagramData:
    def __init__(self, account: str, scan_interval: timedelta, target_user_id: str | None) -> None:
        self.account = account
        self.scan_interval = scan_interval
        self.target_user_id = target_user_id
        self.values: dict[str, Any] = {
            "followers": None, "posts": None, "following": None,
            "reels_7d_count": None, "reels_7d_likes": None, "reels_7d_comments": None, "reels_7d_views": None,
            "posts_7d_count": None, "posts_7d_likes": None, "posts_7d_comments": None,
            "recent_7d_count": None, "recent_7d_likes": None, "recent_7d_comments": None, "recent_7d_engagement": None,
        }
        self.profile: dict[str, Any] = {"full_name": None, "is_private": None, "is_verified": None, "profile_pic_url": None}
        self.diagnostics: dict[str, Any] = {
            "integration_version": "1.1.6-preserve-profile-values",
            "fetch_method": "curl",
            "scan_interval_seconds": int(scan_interval.total_seconds()),
            "target_user_id": target_user_id,
            "profile_last_error": "not updated yet",
            "profile_last_status": None,
            "profile_last_response_preview": None,
            "profile_values_preserved": False,
            "profile_last_success": None,
            "clips_fetch_enabled": bool(target_user_id),
            "clips_last_error": None,
            "clips_last_status": None,
            "clips_last_response_preview": None,
            "clips_recent_count": None,
            "views_source": "not updated yet",
            "last_success": None,
            "recent_media_count": None,
            "recent_media_window_days": RECENT_MEDIA_WINDOW_DAYS,
            "profile_recent_items_available": None,
            "reels_7d_items": [],
            "posts_7d_items": [],
            "recent_7d_items": [],
        }

    async def async_update(self) -> None:
        _LOGGER.warning("Updating Instagram data for %s using curl", self.account)
        profile_user = await self._update_from_profile()
        if profile_user:
            self._parse_recent_media_from_profile(profile_user)
        if self.target_user_id:
            clips_items = await self._fetch_clips_items()
            if clips_items is not None:
                self._parse_recent_reels_from_clips(clips_items)
                self.diagnostics["views_source"] = "clips_user_api"
            elif profile_user:
                self._parse_recent_reels_from_profile(profile_user)
                self.diagnostics["views_source"] = "web_profile_info_fallback"
        elif profile_user:
            self._parse_recent_reels_from_profile(profile_user)
            self.diagnostics["views_source"] = "web_profile_info"
        if self.diagnostics.get("profile_last_error") is None or self.diagnostics.get("clips_last_error") is None:
            self.diagnostics["last_success"] = datetime.now(timezone.utc).isoformat()

    def _has_profile_values(self) -> bool:
        return any(self.values.get(key) is not None for key in PROFILE_METRIC_KEYS)

    @staticmethod
    def _instagram_error_message(body: str) -> str | None:
        try:
            info = json.loads(body)
        except Exception:
            return None
        if not isinstance(info, dict):
            return None
        parts = []
        for key in ("message", "status", "error_type"):
            value = info.get(key)
            if value:
                parts.append(f"{key}={value}")
        for key in ("require_login", "igweb_rollout"):
            value = info.get(key)
            if value is not None:
                parts.append(f"{key}={value}")
        if parts:
            return ", ".join(parts)
        return None

    def _mark_profile_values_preserved(self, error: str) -> None:
        self.diagnostics["profile_last_error"] = error
        self.diagnostics["profile_values_preserved"] = self._has_profile_values()
        if self.diagnostics["profile_values_preserved"]:
            _LOGGER.warning(
                "Instagram profile fetch failed for %s; preserving previous profile values. %s",
                self.account,
                error,
            )
        else:
            _LOGGER.warning(
                "Instagram profile fetch failed for %s and no previous profile values are available. %s",
                self.account,
                error,
            )

    async def _update_from_profile(self) -> dict[str, Any] | None:
        try:
            status, body, stderr_text = await self._fetch_profile_with_curl()
            preview = body[:500]
            self.diagnostics["profile_last_status"] = status
            self.diagnostics["profile_last_response_preview"] = preview
            self.diagnostics["profile_last_error"] = None
            self.diagnostics["profile_values_preserved"] = False
            if status != 200:
                error = f"HTTP {status}"
                instagram_message = self._instagram_error_message(body)
                if instagram_message:
                    error = f"{error}: {instagram_message}"
                if stderr_text:
                    error = f"{error}; curl stderr={stderr_text}"
                self._mark_profile_values_preserved(error)
                return None
            try:
                info: dict[str, Any] = json.loads(body)
            except Exception as json_error:
                self._mark_profile_values_preserved(f"JSON parse error: {json_error}")
                return None
            user = info.get("data", {}).get("user")
            if not user:
                instagram_message = self._instagram_error_message(body)
                message = instagram_message or "No user object in Instagram response"
                self._mark_profile_values_preserved(message)
                return None
            self.profile["full_name"] = user.get("full_name")
            self.profile["is_private"] = user.get("is_private")
            self.profile["is_verified"] = user.get("is_verified")
            self.profile["profile_pic_url"] = user.get("profile_pic_url_hd") or user.get("profile_pic_url")
            self.values["posts"] = self._count_from_edge(user.get("edge_owner_to_timeline_media"))
            self.values["followers"] = self._count_from_edge(user.get("edge_followed_by"))
            self.values["following"] = self._count_from_edge(user.get("edge_follow"))
            self.diagnostics["profile_last_success"] = datetime.now(timezone.utc).isoformat()
            return user
        except Exception as error:
            self._mark_profile_values_preserved(str(error))
            return None

    async def _fetch_profile_with_curl(self) -> tuple[int, str, str]:
        username = quote(self.account, safe="")
        url = f"{PROFILE_URL}?username={username}"
        cmd = ["curl", "-sS", "-L", "--max-time", "15", "-w", "\n%{http_code}", url, "-H", f"User-Agent: {USER_AGENT}", "-H", "Accept: application/json", "-H", "Accept-Language: en-US,en;q=0.9", "-H", "X-IG-App-ID: 936619743392459", "-H", "Referer: https://www.instagram.com/", "-H", "Origin: https://www.instagram.com"]
        return await self._run_curl_with_status(cmd)

    async def _fetch_clips_items(self) -> list[dict[str, Any]] | None:
        if not self.target_user_id:
            return None
        with tempfile.NamedTemporaryFile(prefix="ig-cookies-", delete=False) as cookie_file:
            cookie_jar = cookie_file.name
        try:
            csrf_token = await self._bootstrap_anonymous_csrf(cookie_jar)
            if not csrf_token:
                self.diagnostics["clips_last_error"] = "Could not extract csrftoken from anonymous session"
                return None
            status, body, stderr_text = await self._post_clips_user(cookie_jar, csrf_token)
            preview = body[:500]
            self.diagnostics["clips_last_status"] = status
            self.diagnostics["clips_last_response_preview"] = preview
            self.diagnostics["clips_last_error"] = None
            if status != 200:
                error = f"HTTP {status}"
                if stderr_text:
                    error = f"{error}: {stderr_text}"
                self.diagnostics["clips_last_error"] = error
                return None
            try:
                info: dict[str, Any] = json.loads(body)
            except Exception as json_error:
                self.diagnostics["clips_last_error"] = f"JSON parse error: {json_error}"
                return None
            items = info.get("items")
            if not isinstance(items, list):
                self.diagnostics["clips_last_error"] = "No items list in clips response"
                return None
            self.diagnostics["clips_recent_count"] = len(items)
            return items
        except Exception as error:
            self.diagnostics["clips_last_error"] = str(error)
            return None
        finally:
            try:
                os.remove(cookie_jar)
            except OSError:
                pass

    async def _bootstrap_anonymous_csrf(self, cookie_jar: str) -> str | None:
        account = quote(self.account, safe="")
        reels_url = f"https://www.instagram.com/{account}/reels/"
        cmd = ["curl", "-sS", "-L", "--max-time", "15", "-c", cookie_jar, reels_url, "-H", f"User-Agent: {USER_AGENT}", "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "-H", "Accept-Language: en-US,en;q=0.9"]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        stderr_text = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"csrf bootstrap curl failed with exit code {proc.returncode}: {stderr_text}")
        return self._read_cookie_value(cookie_jar, "csrftoken")

    @staticmethod
    def _read_cookie_value(cookie_jar: str, cookie_name: str) -> str | None:
        try:
            with open(cookie_jar, encoding="utf-8") as file:
                lines = file.readlines()
        except OSError:
            return None
        for line in reversed(lines):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"\s+", line)
            if len(parts) >= 7 and parts[5] == cookie_name:
                return parts[6]
        return None

    async def _post_clips_user(self, cookie_jar: str, csrf_token: str) -> tuple[int, str, str]:
        data = "include_feed_video=true" "&page_size=12" f"&target_user_id={quote(self.target_user_id or '', safe='')}"
        referer_account = quote(self.account, safe="")
        referer = f"https://www.instagram.com/{referer_account}/reels/"
        cmd = ["curl", "-sS", "-L", "--max-time", "15", "-w", "\n%{http_code}", "-b", cookie_jar, "-c", cookie_jar, CLIPS_USER_URL, "-H", "Accept: */*", "-H", "Accept-Language: en-US,en;q=0.9", "-H", "Content-Type: application/x-www-form-urlencoded", "-H", "Origin: https://www.instagram.com", "-H", f"Referer: {referer}", "-H", f"User-Agent: {USER_AGENT}", "-H", "X-ASBD-ID: 359341", "-H", f"X-CSRFToken: {csrf_token}", "-H", "X-IG-App-ID: 936619743392459", "-H", "X-Requested-With: XMLHttpRequest", "--data-raw", data]
        return await self._run_curl_with_status(cmd)

    @staticmethod
    async def _run_curl_with_status(cmd: list[str]) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"curl failed with exit code {proc.returncode}: {stderr_text}")
        try:
            body, status_text = stdout_text.rsplit("\n", 1)
            status = int(status_text.strip())
        except ValueError as error:
            raise RuntimeError(f"Could not parse curl status output: {stdout_text}") from error
        return status, body, stderr_text

    @staticmethod
    def _count_from_edge(edge: dict[str, Any] | None) -> int | None:
        if not isinstance(edge, dict):
            return None
        count = edge.get("count")
        if isinstance(count, int):
            return count
        return None

    @staticmethod
    def _is_reel(node: dict[str, Any]) -> bool:
        if not isinstance(node, dict):
            return False
        if node.get("product_type") == "clips":
            return True
        if node.get("__typename") == "GraphVideo" and node.get("is_video") is True:
            return True
        return False

    @staticmethod
    def _extract_view_candidates(node: dict[str, Any]) -> dict[str, Any]:
        clips_metadata = node.get("clips_metadata", {})
        if not isinstance(clips_metadata, dict):
            clips_metadata = {}
        return {"clips_metadata_ig_play_count": clips_metadata.get("ig_play_count"), "clips_metadata_play_count": clips_metadata.get("play_count"), "clips_metadata_view_count": clips_metadata.get("view_count"), "ig_play_count": node.get("ig_play_count"), "play_count": node.get("play_count"), "view_count": node.get("view_count"), "video_play_count": node.get("video_play_count"), "video_view_count": node.get("video_view_count")}

    @staticmethod
    def _choose_view_count(view_candidates: dict[str, Any]) -> int:
        preferred_keys = ["clips_metadata_ig_play_count", "ig_play_count", "clips_metadata_play_count", "play_count", "clips_metadata_view_count", "view_count", "video_play_count", "video_view_count"]
        for key in preferred_keys:
            value = view_candidates.get(key)
            if isinstance(value, int):
                return value
        return 0

    @staticmethod
    def _extract_like_count(node: dict[str, Any]) -> int:
        like_count = node.get("like_count")
        if isinstance(like_count, int):
            return like_count
        edge_like_count = InstagramData._count_from_edge(node.get("edge_liked_by"))
        if isinstance(edge_like_count, int):
            return edge_like_count
        return 0

    @staticmethod
    def _extract_comment_count(node: dict[str, Any]) -> int:
        comment_count = node.get("comment_count")
        if isinstance(comment_count, int):
            return comment_count
        edge_comment_count = InstagramData._count_from_edge(node.get("edge_media_to_comment"))
        if isinstance(edge_comment_count, int):
            return edge_comment_count
        edge_preview_comment_count = InstagramData._count_from_edge(node.get("edge_media_preview_comment"))
        if isinstance(edge_preview_comment_count, int):
            return edge_preview_comment_count
        return 0

    @staticmethod
    def _extract_shortcode(node: dict[str, Any]) -> str | None:
        shortcode = node.get("shortcode") or node.get("code")
        if isinstance(shortcode, str):
            return shortcode
        return None

    @staticmethod
    def _extract_timestamp(node: dict[str, Any]) -> int | None:
        for key in ("taken_at_timestamp", "taken_at", "device_timestamp"):
            value = node.get(key)
            if isinstance(value, int):
                if key == "device_timestamp" and value > 10_000_000_000:
                    value = int(value / 1_000_000)
                return value
        return None

    @staticmethod
    def _extract_caption(node: dict[str, Any]) -> str | None:
        caption_text = node.get("caption_text")
        if isinstance(caption_text, str) and caption_text:
            return caption_text[:180]
        caption = node.get("caption")
        if isinstance(caption, dict):
            text = caption.get("text")
            if isinstance(text, str) and text:
                return text[:180]
        caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
        if isinstance(caption_edges, list) and caption_edges:
            try:
                text = caption_edges[0]["node"]["text"]
                if isinstance(text, str) and text:
                    return text[:180]
            except (KeyError, IndexError, TypeError):
                return None
        return None

    @staticmethod
    def _permalink_for_item(shortcode: str | None, media_kind: str) -> str | None:
        if not shortcode:
            return None
        if media_kind == "reel":
            return f"https://www.instagram.com/reel/{shortcode}/"
        return f"https://www.instagram.com/p/{shortcode}/"

    @staticmethod
    def _media_kind(node: dict[str, Any]) -> str:
        if InstagramData._is_reel(node):
            return "reel"
        if node.get("__typename") == "GraphSidecar":
            return "carousel"
        if node.get("is_video") is True:
            return "video"
        return "post"

    @staticmethod
    def _extract_profile_media_nodes(user: dict[str, Any]) -> list[dict[str, Any]]:
        media = user.get("edge_owner_to_timeline_media", {})
        edges = media.get("edges", [])
        if not isinstance(edges, list):
            edges = []
        nodes = []
        for edge in edges:
            if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                nodes.append(edge["node"])
        return nodes

    def _parse_recent_media_from_profile(self, user: dict[str, Any]) -> None:
        nodes = self._extract_profile_media_nodes(user)
        self.diagnostics["recent_media_count"] = len(nodes)
        self.diagnostics["profile_recent_items_available"] = len(nodes)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=RECENT_MEDIA_WINDOW_DAYS)
        post_items: list[dict[str, Any]] = []
        recent_items: list[dict[str, Any]] = []
        total_post_likes = 0
        total_post_comments = 0
        total_recent_likes = 0
        total_recent_comments = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            timestamp = self._extract_timestamp(node)
            if timestamp is None:
                continue
            taken_at = datetime.fromtimestamp(timestamp, timezone.utc)
            if taken_at < cutoff:
                continue
            shortcode = self._extract_shortcode(node)
            likes = self._extract_like_count(node)
            comments = self._extract_comment_count(node)
            caption = self._extract_caption(node)
            media_kind = self._media_kind(node)
            age_hours = max((now - taken_at).total_seconds() / 3600, 0.01)
            likes_per_hour = round(likes / age_hours, 1)
            comments_per_hour = round(comments / age_hours, 1)
            engagement = likes + comments
            engagement_per_hour = round(engagement / age_hours, 1)
            item = {"shortcode": shortcode, "taken_at": taken_at.isoformat(), "caption": caption, "type": media_kind, "likes": likes, "comments": comments, "engagement": engagement, "age_hours": round(age_hours, 1), "likes_per_hour": likes_per_hour, "comments_per_hour": comments_per_hour, "engagement_per_hour": engagement_per_hour, "typename": node.get("__typename"), "product_type": node.get("product_type"), "media_type": node.get("media_type"), "is_video": node.get("is_video"), "source": "web_profile_info", "url": self._permalink_for_item(shortcode, media_kind)}
            total_recent_likes += likes
            total_recent_comments += comments
            recent_items.append(item)
            if media_kind != "reel":
                total_post_likes += likes
                total_post_comments += comments
                post_items.append(item)
        self.values["posts_7d_count"] = len(post_items)
        self.values["posts_7d_likes"] = total_post_likes
        self.values["posts_7d_comments"] = total_post_comments
        self.values["recent_7d_count"] = len(recent_items)
        self.values["recent_7d_likes"] = total_recent_likes
        self.values["recent_7d_comments"] = total_recent_comments
        self.values["recent_7d_engagement"] = total_recent_likes + total_recent_comments
        self.diagnostics["posts_7d_items"] = post_items
        self.diagnostics["recent_7d_items"] = recent_items

    def _parse_recent_reels_from_profile(self, user: dict[str, Any]) -> None:
        nodes = self._extract_profile_media_nodes(user)
        self.diagnostics["recent_media_count"] = len(nodes)
        self._parse_recent_reel_nodes(nodes, source="web_profile_info")

    def _parse_recent_reels_from_clips(self, items: list[dict[str, Any]]) -> None:
        nodes = []
        for item in items:
            if not isinstance(item, dict):
                continue
            media = item.get("media")
            if isinstance(media, dict):
                nodes.append(media)
            else:
                nodes.append(item)
        self.diagnostics["recent_media_count"] = len(nodes)
        self._parse_recent_reel_nodes(nodes, source="clips_user_api")

    def _parse_recent_reel_nodes(self, nodes: list[dict[str, Any]], source: str) -> None:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=RECENT_MEDIA_WINDOW_DAYS)
        reel_items: list[dict[str, Any]] = []
        total_likes = 0
        total_comments = 0
        total_views = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if source == "web_profile_info" and not self._is_reel(node):
                continue
            timestamp = self._extract_timestamp(node)
            if timestamp is None:
                continue
            taken_at = datetime.fromtimestamp(timestamp, timezone.utc)
            if taken_at < cutoff:
                continue
            shortcode = self._extract_shortcode(node)
            likes = self._extract_like_count(node)
            comments = self._extract_comment_count(node)
            view_candidates = self._extract_view_candidates(node)
            views = self._choose_view_count(view_candidates)
            caption = self._extract_caption(node)
            age_hours = max((now - taken_at).total_seconds() / 3600, 0.01)
            views_per_hour = round(views / age_hours, 1)
            likes_per_hour = round(likes / age_hours, 1)
            comments_per_hour = round(comments / age_hours, 1)
            engagement = likes + comments
            engagement_per_hour = round(engagement / age_hours, 1)
            like_rate_percent = round((likes / views) * 100, 2) if views else 0
            total_likes += likes
            total_comments += comments
            total_views += views
            reel_items.append({"shortcode": shortcode, "taken_at": taken_at.isoformat(), "caption": caption, "type": "reel", "likes": likes, "comments": comments, "views": views, "engagement": engagement, "age_hours": round(age_hours, 1), "views_per_hour": views_per_hour, "likes_per_hour": likes_per_hour, "comments_per_hour": comments_per_hour, "engagement_per_hour": engagement_per_hour, "like_rate_percent": like_rate_percent, "view_candidates": view_candidates, "typename": node.get("__typename"), "product_type": node.get("product_type"), "media_type": node.get("media_type"), "is_video": node.get("is_video"), "source": source, "url": self._permalink_for_item(shortcode, "reel")})
        self.values["reels_7d_count"] = len(reel_items)
        self.values["reels_7d_likes"] = total_likes
        self.values["reels_7d_comments"] = total_comments
        self.values["reels_7d_views"] = total_views
        self.diagnostics["reels_7d_items"] = reel_items


class InstagramMetricSensor(SensorEntity):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, data: InstagramData, base_name: str, metric_key: str, metric_name: str, icon: str) -> None:
        self._data = data
        self._metric_key = metric_key
        if base_name == DEFAULT_NAME:
            base_name = f"Instagram {data.account}"
        self._attr_name = f"{base_name} {metric_name}"
        self._attr_unique_id = f"instagram_{data.account}_{metric_key}"
        self._attr_icon = icon

    @property
    def native_value(self):
        return self._data.values.get(self._metric_key)

    @property
    def available(self) -> bool:
        if self.native_value is not None:
            return True
        return self._data.diagnostics.get("profile_last_error") is None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {"account": self._data.account, "metric": self._metric_key, **self._data.profile, **self._data.diagnostics}
        is_reels_metric = self._metric_key.startswith("reels_7d")
        is_posts_metric = self._metric_key.startswith("posts_7d")
        is_recent_metric = self._metric_key.startswith("recent_7d")
        if not (is_reels_metric or is_recent_metric):
            attrs.pop("reels_7d_items", None)
        if not (is_posts_metric or is_recent_metric):
            attrs.pop("posts_7d_items", None)
        if not is_recent_metric:
            attrs.pop("recent_7d_items", None)
        return attrs
