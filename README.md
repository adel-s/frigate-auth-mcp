# frigate-auth-mcp

## What

An [MCP](https://modelcontextprotocol.io) server with authentication for [Frigate NVR](https://frigate.video): lets an AI
assistant (Claude Code, Claude Desktop, Hermes or any other MCP client) look at your cameras, events and reviews -
and nothing more.

It logs in to Frigate's authenticated port as a user with the `viewer` role, so Frigate itself enforces what the
assistant may see. Tested with Frigate 0.17.x.

## Install

Python 3.11+ and [uv](https://docs.astral.sh/uv/). Nothing to install by hand: the MCP client starts the server
with

```
uvx --from git+https://github.com/adel-s/frigate-auth-mcp frigate-auth-mcp
```

## Setup

1. In Frigate, create a user for the assistant (Settings -> Users) with the role `viewer`. If the role lets you
   limit cameras, give it only the cameras the assistant should see.
2. Add the server to your MCP client. For Claude Code, in `.mcp.json`:

```json
{
  "mcpServers": {
    "frigate": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/adel-s/frigate-auth-mcp", "frigate-auth-mcp"],
      "env": {
        "FRIGATE_URL": "https://frigate.example.com:8971",
        "FRIGATE_USER": "assistant",
        "FRIGATE_PASSWD": "${FRIGATE_PASSWD}"
      }
    }
  }
}
```

Keep the password out of the file: put it in the environment the client runs in.

## Tools

| Tool             | What it returns                                                          | Frigate API                                  |
|------------------|--------------------------------------------------------------------------|----------------------------------------------|
| `frigate_status` | version, uptime, the cameras this user may see, per-camera fps           | `/api/stats`, `/api/profile`, `/api/version` |
| `list_events`    | tracked-object events, filters by camera, label, time, GenAI description | `/api/events`                                |
| `get_event`      | one event in detail, with its GenAI description                          | `/api/events/<id>`                           |
| `event_snapshot` | snapshot of an event with the bounding box, optionally cropped           | `/api/events/<id>/snapshot.jpg`              |
| `latest_frame`   | the current frame of a camera                                            | `/api/<camera>/latest.jpg`                   |
| `list_reviews`   | review items as in the Frigate UI, last 7 days by default                | `/api/review`                                |
| `review_summary` | alert and detection counts for the last 24 hours and per day             | `/api/review/summary`                        |

## Environment

| Variable                | Meaning                                                             |
|-------------------------|---------------------------------------------------------------------|
| `FRIGATE_IMAGE_CAMERAS` | cameras allowed to return images, comma-separated; default all      |
| `FRIGATE_PASSWD`        | password of the Frigate user, required (secret)                     |
| `FRIGATE_TIMEOUT`       | seconds per request, default `30`                                   |
| `FRIGATE_URL`           | base URL of the authenticated port, default `http://localhost:8971` |
| `FRIGATE_USER`          | Frigate username, required                                          |

## Safety

- Talks to the authenticated port (`8971` by default) only, never to the unauthenticated internal port `5000`,
  which gives full API access to anyone who can reach it.
- Logs in as a Frigate user with the role `viewer`; the only non-GET call is `POST /api/login`.
- No tool deletes, exports, edits the config or restarts anything.
- The raw Frigate config is not exposed: it carries camera stream URLs with credentials.
- Optional image filter `FRIGATE_IMAGE_CAMERAS`: only these cameras return images (snapshots, frames). This is the
  second layer of protection, after the camera limit of the Frigate user's role; by default all cameras are
  allowed. Use it when the MCP client sends images to a third-party model and some frames, such as indoor
  cameras, must not leave your home.

## Why

The Frigate MCP server I found talks to port `5000` without authentication and exposes tools that delete events,
start exports, save the config or restart Frigate. Handing that to an AI model is more trust than a camera system
deserves. This server gives the assistant eyes, not hands.

## Notes

- Without `after`, Frigate's `/api/review` returns only the last 24 hours; `list_reviews` asks for the last 7 days
  instead, so "what happened this week" gets a full answer.
- `list_events` with `has_description` filters on the GenAI description, which the Frigate API cannot do itself.

## Development

I don't accept pull requests, sorry: my code, my rules. But if something doesn't work or you need a new feature,
feel free to open an issue and I'll fix it ASAP.

## License

MIT, see [LICENSE](LICENSE).
