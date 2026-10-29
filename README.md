# LatoChan

A self-hosted Discord companion bot powered by a **local LLM** ([Ollama](https://ollama.com)). No cloud AI, no API keys beyond your Discord bot token: everything the bot says is generated on your own machine.

**Features**
- Chats in your Discord server using a compatible Ollama model (vision is detected before images are processed)
- Fully editable personality (`persona.md`), re-read on every message (recommend basing it off an existing character) 
- Per-person notes it saves and recalls (SQLite)
- Reaction GIFs by mood, emoji reactions, optional voice/moderation actions for the owner only
- Sleep/wake cycle, burst handling (waits until you finish typing), quiet hours
- About 30 live settings (`lato-config`), no restart needed
- Runs as a sandboxed systemd service
- Optional hardware-specific AMD GPU tuning; see [GPU tuning notes](deploy/gpu/README.md).

An optional web dashboard lives in a separate repository: **LatoDash**.

## Requirements
- A Linux server with systemd (tested on Ubuntu). RAM/VRAM requirements depend on your model, quantization, context size and inference backend.
- CPython 3.14, `curl`, venv/pip, and preinstalled Ollama; see `deploy/OPERATIONS.md`
- A Discord bot token with **Message Content Intent** and **Server Members Intent** enabled

For a fresh checkout, follow [GETTING_STARTED.md](GETTING_STARTED.md). See [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md) for the GitHub publication checklist.

## Install or upgrade

This reviewed release requires **CPython 3.14 on Linux x86_64**, venv/pip support, and an already installed local Ollama. The installer does not execute remote shell scripts. Review [deployment and recovery instructions](deploy/OPERATIONS.md) before upgrading a live server.

```bash
sudo bash setup.sh
```

Existing models, persona, settings and notes are preserved. New releases are root-owned under `/opt/latochan/releases/`, selected by `/opt/latochan/current`. The installer stages hashed dependencies, checks the model, backs up state, and attempts rollback if activation fails. The configured model remains selected. Pause other writers during the upgrade. Deploy the reviewed LatoDash compatibility update and server helpers before resuming dashboard operations.

## Make it yours
- **Personality:** edit `persona.md` (installed to `/etc/latochan/persona.md` (or your existing `PERSONA_PATH`); live on the next message). Keep `[[REACT: ..]]`, `[[GIF: mood]]`, and `[[SKIP]]` if you use those features. Privileged actions and note writes require explicit owner commands. `{{BOT_NAME}}` and `{{USER_NAME}}` come from `BOT_NAME` / `USER_NAME` in the env file.
- **Custom emotes:** run `sudo python3 emojis.py` from the repo folder on the server to list your server's emote codes, then add them to the "Server emotes" section of `persona.md`.
- **GIFs:** put `https://` links (one per line) into `/var/lib/latochan/gifs/<mood>/links.txt`. Moods: waking, sleepy, happy, sad, annoyed, bored, love, laughing, confused, excited, smug, shocked. Empty is fine.
- **Notes about people:** use `lato-memory export > file`, edit, then `lato-memory import < file`. Imports reject stale snapshots. The example supports initial import into an empty database only. Private context is disabled until explicitly configured.

## Configuration
Secrets and IDs live in `/etc/latochan.env` (root only, mode 600; template: `deploy/latochan.env.example`). Edit it, then `sudo systemctl restart latochan`.

| Variable | Meaning |
|---|---|
| `DISCORD_BOT_TOKEN` | Required. |
| `OWNER_ID` | Required. Your Discord user ID; the only person who can use server actions. |
| `CHANNEL_IDS` | Required. Main channel ID(s), comma separated. |
| `LLM_MODEL` | Ollama model name. |
| `FRIEND_IDS` | Optional. User IDs the bot chats with normally (others are treated as strangers). |
| `BOT_ID`, `GUILD_ID` | Optional (used by the diagnostic scripts and `lato-say`). |
| `BOT_NAME`, `USER_NAME` | Names substituted into the persona. |
| `TIMEZONE` | IANA name (default `UTC`) for clocks and quiet hours. |
| `FORCE_LOWERCASE` | `1` to lowercase every reply outside code/links/emotes. |
| `NUM_CTX`, `OLLAMA_URL`, `SLEEP_MODE` | Context size, Ollama address, `0` to force sleep mode off. |

**Live settings** (no restart): `lato-config` shows them all; `lato-config set gif_chance 40`, `lato-config thinking toggle`, `lato-config reset`. They are defined once in `botsettings.py`:

| Group | Settings |
|---|---|
| Brain | `thinking` |
| Voice and memory | `temperature`, `top_p`, `max_reply_tokens`, `history_turns`, `memory_enabled` |
| Sleep and wake | `sleep_mode`, `awake_min_minutes`, `awake_max_minutes`, `friends_can_wake`, `chimes_enabled`, `chime_min_hours`, `chime_max_hours`, `chime_wait_seconds`, `quiet_hours_enabled`, `quiet_from_hour`, `quiet_to_hour` |
| GIFs | `gifs_enabled`, `gif_chance`, `wake_gif_chance`, `gif_cooldown_seconds` |
| Chat timing | `debounce_seconds`, `max_wait_seconds`, `other_cooldown_seconds`, `chatter_reply_chance` |
| Abilities | `vision_enabled`, `server_powers` |
| Personality | `sass` (0-10), `swearing` (`off`, `rare`, `more`) |

## Commands
| Command | Purpose |
|---|---|
| `lato-watch` | Live view: messages in, raw model output, actions taken |
| `lato-config` | Show/change live settings |
| `lato-gifs` | Inspect/modify the GIF library |
| `lato-memory list\|export\|import` | Per-person notes |
| `sudo lato-say "text"` | Post as the bot (pings only the owner) |

## Files
```
gateway.py        the bot            persona.md      personality + action tags
botsettings.py    settings schema    gifstore.py     GIF library rules
memory.py         per-person notes   rag.py          optional retrieval over your own chat history
latoids.py        ID handling        setup.sh        installer
lato-*            CLI helpers        emojis.py perm.py chancheck.py   diagnostics (run on the server)
gifs/             GIF link templates tests_gifmode.py behaviour test (python tests_gifmode.py .)
deploy/           systemd units, env template, Modelfile example, optional AMD GPU tuning
```

## Security
- The token and IDs live only in `/etc/latochan.env`; nothing in this repo contains a secret. `.gitignore` excludes env files, keys, databases and logs.
- The bot never uses `@everyone`/`@here` and restricts mentions to users.
- Server actions require a validated `/lato` command from the real `OWNER_ID`, bound to exact IDs and arguments. Model-generated privileged tags cannot trigger actions. Duplicate command attempts are durably blocked.
- Images are fetched only from Discord's CDN over HTTPS with byte limits, allowlisted formats, and decoder subprocess resource limits.
- The service runs as its own unprivileged user in a systemd sandbox (`deploy/systemd/latochan.service.d/hardening.conf`: read-only code, write access only to `/var/lib/latochan`).
- Leaked token? Reset it in the Developer Portal, update `/etc/latochan.env`, restart.
- Keep Ollama bound to `127.0.0.1` (the installer does this).

## Limits
Chat history is bounded by entries and a conservative context budget. A small command-attempt ledger is persisted without command contents. Notes/retrieval require approved private channels and are disabled by default. `setup.sh` is written for Ubuntu/Debian; expect small tweaks elsewhere. AMD/Vulkan GPU tuning examples are in `deploy/gpu/` (hardware specific, optional).

## License
Licensed under the [MIT License](LICENSE). Dependencies and separately downloaded models retain their own licenses.

## Explicit owner commands

These are ordinary Discord messages starting with `/lato`, not registered slash interactions. Use numeric Discord IDs or actual mentions; names and fuzzy matches are rejected. Moderation requires `server_powers` enabled in live settings and the relevant Discord permissions.

```text
/lato mute @member
/lato unmute @member
/lato deafen @member
/lato undeafen @member
/lato disconnect @member
/lato timeout @member 10m
/lato untimeout @member
/lato move @member #voice-channel
/lato nick @member "New nickname"
/lato pin
/lato unpin
/lato delete
/lato join
/lato leave
/lato status custom text
/lato vc-status custom text
/lato thinking on
/lato thinking off
/lato thinking toggle
```

Pin/unpin/delete require replying to the exact target message. `@member` and `#voice-channel` mean real Discord mentions or numeric IDs, not typed display names. Pinning requires the separate **Pin Messages** permission.

Private notes stay disabled for now. After an administrator explicitly approves owner-only channels in `PRIVATE_CHANNEL_IDS`, `/lato remember NOTE` and `/lato forget NOTE` operate on the owner's own notes. Never store sensitive data. `RAG_ENABLED=1` additionally enables an ingested V2 private retrieval store; see `rag.py` and the operations guide. Changing environment settings requires a restart.

## Verification

Run `python -m unittest discover -s tests -v` and `python tests_gifmode.py .` inside a venv installed from `requirements.lock`. Production integration checks and recovery steps are in `deploy/OPERATIONS.md`.
