# KickRSS

KickRSS is a small self-hostable web service that turns Kick channel pages into RSS feeds.

Kick does not provide official RSS feeds. This service fetches Kick's public web endpoints, builds RSS 2.0 XML, and caches responses for a short time so feed readers can poll it safely.

Current version: `0.1.1`

## Usage

Run the service, then subscribe to:

- `https://your-kickrss.example/vod/xqc`
- `https://your-kickrss.example/vodonly/xqc`

`/vod/<channel>` includes the current live stream when Kick returns one.

`/vodonly/<channel>` only returns recent videos.

## Endpoints

- `/` - basic help page
- `/vod/<channel>` - live stream plus videos
- `/vodonly/<channel>` - videos only
- `/healthz` - health check

## Docker

```sh
docker build -t kickrss .
docker run --rm -p 8000:8000 -e HOST=0.0.0.0 -e PORT=8000 kickrss
```

Optional environment variables:

- `HOST` - bind host, default `0.0.0.0`
- `PORT` - bind port, default `8000`
- `PUBLIC_BASE_URL` - public service URL used in feed self links
- `CACHE_TTL_SECONDS` - Kick response cache duration, default `600`
- `REQUEST_TIMEOUT_SECONDS` - Kick request timeout, default `10`
- `DEBUG` - set to any value for debug logging

## FreshRSS

If you use a FreshRSS URL-converter extension, point Kick channel URLs to:

`https://your-kickrss.example/vod/<channel>`

With this architecture the FreshRSS extension can be a regular user extension, because FreshRSS no longer needs an internal extension controller during background refreshes.

## Notes

Kick's public web API is not an official RSS API and may block server-side requests or change without notice. The endpoint list and JSON parsing are intentionally kept in one file.

## Changelog

### 0.1.1

- Fixed duplicated RSS namespace attributes.
- Treated large Kick video duration values as milliseconds.

### 0.1.0

- Added RSS endpoints for Kick channels.
- Added live stream and recent VOD item generation.
- Added in-memory TTL caching.
- Added Docker support.

## License

MIT.
