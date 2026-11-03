---
name: frigate-cameras
description: "Answer questions about home cameras with the frigate-auth-mcp tools: what happened, who or what was seen, show a camera. Text first, images last and small."
version: 0.3.0
license: MIT
metadata:
  hermes:
    tags: [frigate, cameras, nvr, home, security]
---

# Frigate cameras

Use this skill when the user asks about their cameras: "what happened at night", "was anyone at the door",
"did the cat go out", "show me the terrace". The tools come from the `frigate-auth-mcp` server; they only read.

## Pick the tool

| Question                                   | Start with                                                      |
|--------------------------------------------|-----------------------------------------------------------------|
| "Anything happened?" / "how was the night" | `review_summary`, then `list_reviews` for the busy days/cameras |
| "Who or what was there?" / "any people?"   | `list_events` with `camera`, `label`, `after` / `before`        |
| "What exactly happened in that event?"     | `get_event` (has the GenAI `description` when there is one)     |
| "Show me the camera" / "what is it now"    | `latest_frame`                                                  |
| "Show me that event"                       | `event_snapshot`                                                |
| "Is Frigate OK?" / "which cameras exist"   | `frigate_status`                                                |

Times go in as ISO 8601 or unix seconds. Convert "last night", "this morning" to a concrete range in the
user's time zone before the call, and say which range you looked at.

## Text first, images last

- Answer from text whenever you can: events, reviews and GenAI descriptions are cheap; an image costs far
  more context and, with a cloud model, leaves the house.
- Fetch an image only when the user asks to see something, or when the question cannot be answered without
  looking (and then say that you looked).
- Call `latest_frame` / `event_snapshot` with the default `height` and `quality`. Raise them only when the
  user needs a small detail (a face, a plate, a label) and the default image did not show it. The server
  may cap both; do not retry with bigger values to get around the cap.
- One image per question unless the user asks for more. For "show me all cameras", list the cameras first
  and ask which ones.

## Report what is there, nothing else

- An empty result means "no events in that range": say so plainly. Do not guess what might have happened.
- Quote the time, camera and label of each event; give the score only when it is low (below 0.7) - then the
  detection may be wrong, so say "probably".
- A GenAI `description` is Frigate's model guessing from a few frames. Pass it on as a description, not as
  a verified fact, and keep it short.
- Review items marked `reviewed: true` were already seen by a person; mention only the new ones unless the
  user asks for all.

## Errors

| Message                               | Meaning and what to tell the user                                      |
|---------------------------------------|------------------------------------------------------------------------|
| `Frigate has no such object`          | wrong id, or the event already expired from Frigate's retention        |
| `Images from camera ... are disabled` | this client may not get images from that camera; answer from text only |
| `Frigate denied access`               | the Frigate user's role does not include that camera                   |
| `Frigate rejected the login`          | wrong credentials in the server config: the user must fix them         |
| `Frigate is unreachable` / timeout    | Frigate or the network is down; say so, do not retry in a loop         |

Never try to get around a refusal: no other camera's snapshot, no other tool, no direct HTTP call.

## Privacy

- Images from cameras that are disabled for this client stay out of reach on purpose, often because they
  show the inside of the home. Do not describe what such a camera might show.
- Do not store frames or snapshots in notes or memory; describe them in words if something must be kept.
