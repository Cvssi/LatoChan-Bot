# LatoChan

LatoChan is a self-hosted Discord companion that uses a local Ollama model. The modular bot supports text chat, reactions, optional direct messages, drawings, and configurable integrations. LatoDash is an optional local dashboard.

## Source layout

| Path | Purpose |
|---|---|
| `gateway.py`, `core/` | Discord client, routing, prompts, inference, and short-term conversation history |
| `modules/` | Features with live settings and their own prompt rules |
| `workers/voice/` | Optional speech worker. Speech models are downloaded separately and have their own licenses |
| `deploy/`, `setup.sh` | Linux systemd release tooling and sample environment |
| `tests/` | Unit and regression tests with synthetic fixtures |

## Configuration and development

Start with [Getting Started](GETTING_STARTED.md). Keep your bot token, IDs, persona, and runtime data outside version control. The [environment template](deploy/latochan.env.example) lists the available configuration fields.

By default, message text and model replies stay out of the journal. Conversation history is transient unless a channel is explicitly approved in `PRIVATE_CHANNEL_IDS`. Enable notes and recall separately. Approve only channels with the intended audience, since enabling recall persists content for retrieval.

On Linux with Python 3.14 and Cairo installed, run the development checks from the repository root:

```sh
python -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.lock
.venv/bin/python -m unittest discover -s tests
.venv/bin/python tests/golden.py
```

This is a release candidate. See [Release status](RELEASE_STATUS.md) for the live checks still required.

## License

Source is licensed under the [MIT License](LICENSE). External services, dependencies, and separately downloaded models retain their own terms and licenses.
