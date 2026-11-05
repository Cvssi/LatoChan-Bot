# Getting started

LatoChan runs on Linux with systemd, CPython 3.14, Cairo, and a separately installed Ollama. Pick a locally available model appropriate to your hardware. Use a disposable Discord server for the first integration test.

1. Create a Discord application and add its bot to your test server. Enable Message Content Intent. Enable Server Members Intent if configured, and Presence Intent only for presence-based features.
2. Review [the environment template](deploy/latochan.env.example) and [the operating notes](deploy/OPERATIONS.md). The token, channel configuration, and persona belong on the server, outside the repository.
3. Review `setup.sh` before running it. It stages a root-owned release, keeps a backup, and uses a separate service account. Confirm the release checks in [Release status](RELEASE_STATUS.md) before relying on the installer in production.
4. Run `lato-config show` to see live settings. Set `GUILD_ID` for server-specific `/help` registration.

Voice, weather, device control, and reminders need additional configuration. The dashboard and server helpers must come from compatible revisions.

For optional speech synthesis, place a Piper voice model and its companion configuration in `/var/lib/latovoice/voices/`. Set `VOICE_NAME` to the model’s filename without the `.onnx` extension in `/etc/latovoice.env` and restart the worker. Choose a voice whose license fits your use.
