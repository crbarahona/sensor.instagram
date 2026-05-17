![hacs_badge](https://img.shields.io/badge/hacs-custom-orange.svg)

# Instagram Sensor Component

Custom Home Assistant YAML sensor platform for public Instagram profile and recent content metrics.

This integration currently fetches:

- Profile totals: followers, posts, following
- Profile metadata: full name, private status, verified status, profile picture URL
- Recent Reel metrics over a 7-day window: count, likes, comments, views
- Recent non-Reel post metrics over a 7-day window: count, likes, comments
- Combined recent content metrics over a 7-day window: count, likes, comments, engagement
- Per-item dashboard attributes for recent Reels, posts, and combined recent content

## Important note

This integration uses unofficial Instagram web endpoints. Instagram may rate-limit, block, or change these endpoints without notice.

The integration shells out to `curl` instead of using Home Assistant's normal Python HTTP stack because Instagram may treat those clients differently. Do not commit real cookies, CSRF tokens, or authenticated session data.

## How it works

Profile counts and normal recent post engagement are fetched from:

```text
https://i.instagram.com/api/v1/users/web_profile_info/?username=<account>
```

Reel view data is fetched from Instagram's clips endpoint:

```text
https://www.instagram.com/api/v1/clips/user/
```

The clips endpoint requires an anonymous CSRF token. The integration bootstraps an anonymous cookie jar by loading:

```text
https://www.instagram.com/<account>/reels/
```

It extracts `csrftoken` from the temporary cookie jar, then posts to `/api/v1/clips/user/` using the configured `target_user_id`.

## Installation

Install this repository as a custom repository in HACS, or copy the custom component into:

```text
config/custom_components/instagram/
```

Restart Home Assistant after installing or updating the component.

## YAML configuration

Basic profile-only configuration:

```yaml
sensor:
  - platform: instagram
    account: living_with_jiji
    name: Jiji's Insta
    scan_interval:
      hours: 6
```

Configuration with Reel view support:

```yaml
sensor:
  - platform: instagram
    account: living_with_jiji
    name: Jiji's Insta
    scan_interval:
      hours: 1
    target_user_id: "31141176789"
```

### Options

| Option | Required | Description |
| --- | --- | --- |
| `account` | Yes | Instagram username without `@`. |
| `name` | No | Base Home Assistant sensor name. Defaults to `Instagram`. |
| `scan_interval` | No | Home Assistant time period for polling. Defaults to 24 hours in the current platform code. Use a conservative interval to avoid rate limits. |
| `target_user_id` | No | Numeric Instagram user ID required for `/api/v1/clips/user/` Reel metrics, especially views. |

## Sensors

For a config named `Jiji's Insta`, Home Assistant will create entities similar to:

```text
sensor.jiji_s_insta_followers
sensor.jiji_s_insta_posts
sensor.jiji_s_insta_following

sensor.jiji_s_insta_reels_7d_count
sensor.jiji_s_insta_reels_7d_likes
sensor.jiji_s_insta_reels_7d_comments
sensor.jiji_s_insta_reels_7d_views

sensor.jiji_s_insta_posts_7d_count
sensor.jiji_s_insta_posts_7d_likes
sensor.jiji_s_insta_posts_7d_comments

sensor.jiji_s_insta_recent_7d_count
sensor.jiji_s_insta_recent_7d_likes
sensor.jiji_s_insta_recent_7d_comments
sensor.jiji_s_insta_recent_7d_engagement
```

## Attributes for dashboards

Recent item lists are exposed as sensor attributes:

| Attribute | Exposed on | Description |
| --- | --- | --- |
| `reels_7d_items` | Reel sensors and recent sensors | Per-Reel detail, including views from the clips endpoint when available. |
| `posts_7d_items` | Post sensors and recent sensors | Per-post detail for non-Reel content returned by the profile endpoint. |
| `recent_7d_items` | Recent sensors | Combined recent content from the profile endpoint. Includes likes/comments/engagement for Reels and posts, but not the clips-derived Reel views. |

Per-item fields may include:

```text
shortcode
url
taken_at
caption
type
likes
comments
views
engagement
age_hours
likes_per_hour
comments_per_hour
engagement_per_hour
views_per_hour
like_rate_percent
source
```

`views` and `views_per_hour` are expected for Reels when the clips endpoint is working. Normal photo/carousel posts generally do not expose public view counts.

## Example dashboard summary card

```yaml
type: vertical-stack
cards:
  - type: heading
    heading: Jiji's Insta
    icon: mdi:instagram

  - type: grid
    columns: 3
    square: false
    cards:
      - type: tile
        entity: sensor.jiji_s_insta_followers
        name: Followers
        icon: mdi:account-group
      - type: tile
        entity: sensor.jiji_s_insta_posts
        name: Posts
        icon: mdi:grid
      - type: tile
        entity: sensor.jiji_s_insta_following
        name: Following
        icon: mdi:account-arrow-right

      - type: tile
        entity: sensor.jiji_s_insta_recent_7d_count
        name: Recent 7d
        icon: mdi:image-multiple
      - type: tile
        entity: sensor.jiji_s_insta_recent_7d_likes
        name: Likes 7d
        icon: mdi:heart-multiple
      - type: tile
        entity: sensor.jiji_s_insta_recent_7d_comments
        name: Comments 7d
        icon: mdi:comment-multiple

      - type: tile
        entity: sensor.jiji_s_insta_recent_7d_engagement
        name: Engagement 7d
        icon: mdi:chart-line
      - type: tile
        entity: sensor.jiji_s_insta_reels_7d_count
        name: Reels 7d
        icon: mdi:movie-open-play
      - type: tile
        entity: sensor.jiji_s_insta_reels_7d_views
        name: Reel Views 7d
        icon: mdi:eye

  - type: history-graph
    title: Last 7 Days
    hours_to_show: 168
    entities:
      - entity: sensor.jiji_s_insta_followers
        name: Followers
      - entity: sensor.jiji_s_insta_recent_7d_likes
        name: Likes 7d
      - entity: sensor.jiji_s_insta_recent_7d_comments
        name: Comments 7d
      - entity: sensor.jiji_s_insta_recent_7d_engagement
        name: Engagement 7d
      - entity: sensor.jiji_s_insta_reels_7d_views
        name: Reel Views 7d

grid_options:
  columns: full
```

## Example flex-table-card dashboard

The built-in Markdown card does not render attribute arrays as a clean table. The examples below use `custom:flex-table-card` from HACS.

### Recent content engagement

This table mixes Reels, images, videos, and carousels using `recent_7d_items`. It is best for likes, comments, and engagement.

```yaml
type: custom:flex-table-card
title: Recent Content Engagement
entities:
  include: sensor.jiji_s_insta_recent_7d_engagement
columns:
  - name: Type
    data: recent_7d_items
    modify: x.type || 'post'
  - name: Item
    data: recent_7d_items
    modify: "'<a href=\"' + x.url + '\" target=\"_blank\">' + x.shortcode + '</a>'"
  - name: Caption
    data: recent_7d_items
    modify: |-
      (x.caption || 'No caption').length > 80
        ? (x.caption || 'No caption').substring(0, 80) + '...'
        : (x.caption || 'No caption')
  - name: Likes
    data: recent_7d_items
    align: right
    modify: x.likes.toLocaleString()
  - name: Comments
    data: recent_7d_items
    align: right
    modify: x.comments.toLocaleString()
  - name: Engagement
    data: recent_7d_items
    align: right
    modify: x.engagement.toLocaleString()
  - name: Eng/hr
    data: recent_7d_items
    align: right
    modify: x.engagement_per_hour
sort_by: engagement_per_hour-
```

### Recent Reel views

This table uses `reels_7d_items`, which is where the clips-derived Reel view metrics live.

```yaml
type: custom:flex-table-card
title: Recent Reels Views
entities:
  include: sensor.jiji_s_insta_recent_7d_engagement
columns:
  - name: Reel
    data: reels_7d_items
    modify: "'<a href=\"' + x.url + '\" target=\"_blank\">' + x.shortcode + '</a>'"
  - name: Caption
    data: reels_7d_items
    modify: |-
      (x.caption || 'No caption').length > 80
        ? (x.caption || 'No caption').substring(0, 80) + '...'
        : (x.caption || 'No caption')
  - name: Views
    data: reels_7d_items
    align: right
    modify: x.views.toLocaleString()
  - name: Likes
    data: reels_7d_items
    align: right
    modify: x.likes.toLocaleString()
  - name: Comments
    data: reels_7d_items
    align: right
    modify: x.comments.toLocaleString()
  - name: Views/hr
    data: reels_7d_items
    align: right
    modify: x.views_per_hour
  - name: Like %
    data: reels_7d_items
    align: right
    modify: x.like_rate_percent + '%'
sort_by: views_per_hour-
```

## Troubleshooting

### I do not see Reel views

Make sure `target_user_id` is configured. Without it, the integration can still fetch profile data and recent profile media, but it cannot use the clips endpoint for better Reel view metrics.

### I see likes and comments but not views in `recent_7d_items`

That is expected. `recent_7d_items` is sourced from the profile endpoint and is intended for mixed content engagement. Use `reels_7d_items` for clips-derived Reel views.

### I do not see `posts_7d_items` on a sensor

`posts_7d_items` is exposed on post sensors and recent sensors. `reels_7d_items` is exposed on Reel sensors and recent sensors. `recent_7d_items` is exposed on recent sensors.

### The endpoint starts returning errors or 429s

Increase `scan_interval`, restart Home Assistant, and avoid testing with repeated manual refreshes. Instagram may throttle anonymous traffic.

## Development notes

Current development version label in sensor attributes:

```text
1.1.5-curl-clips-posts-dashboard
```

The live Home Assistant copy may differ from GitHub while testing. Treat the file running in Home Assistant as the source of truth until changes are committed and deployed.
