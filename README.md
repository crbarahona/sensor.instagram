![hacs_badge](https://img.shields.io/badge/hacs-custom-orange.svg)

# Instagram Sensor Component

Custom Home Assistant sensor that fetches public Instagram profile information:

- Full name
- Posts
- Followers
- Following
- Private account status
- Verified status
- Profile picture URL

## Important note

This integration uses an unofficial Instagram public web endpoint. Instagram may rate-limit, block, or change this endpoint without notice.

The default polling interval is intentionally conservative:

```text
6 hours
