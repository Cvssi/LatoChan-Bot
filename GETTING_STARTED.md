# Run LatoChan

## Supported server

Use a Linux x86_64 server with systemd, CPython 3.14 and venv/pip support, curl, and a separately installed local Ollama. Choose a model that fits your RAM/VRAM and download it before installation. Other platforms need their own resolved and audited dependency lock.

Create a Discord application and bot, enable Message Content Intent and Server Members Intent, and invite it with only the permissions required for your chosen features. Collect your owner user ID and allowed channel IDs. Keep the bot token private.

## Get and validate the source

Clone the published repository:

```bash
git clone https://github.com/Cvssi/LatoChan-Bot.git
cd LatoChan-Bot
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.lock
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests_gifmode.py .
bash -n setup.sh
```

Tests use fake external connections. They do not start a live Discord bot.

## Install when ready

Read [deployment and recovery](deploy/OPERATIONS.md), review `setup.sh`, and verify backups before upgrading an existing installation. Pause dashboard writers. Choose your downloaded local model explicitly for a fresh installation:

```bash
sudo env PYTHON_BIN=python3.14 MODEL=YOUR_MODEL_NAME bash setup.sh
```

The installer asks for the Discord token, owner ID and channel IDs when missing. Do not put the token in command arguments. It installs and starts the systemd service. It preserves existing model selection when `MODEL` is omitted. Private memory/retrieval stays disabled until explicitly configured after installation.

```bash
systemctl is-active latochan
bash deploy/collect-review-evidence.sh YOUR_MODEL_NAME
```

Reconnect SSH to pick up group membership. Configure secrets using `sudoedit /etc/latochan.env`, then restart with `sudo systemctl restart latochan`. Keep that file root-owned mode 0600. Edit the installed persona at `/etc/latochan/persona.md` unless an existing custom path was preserved. Do not edit release code in place.

In an allowed test channel, mention the bot and check its reply. Keep server powers disabled until explicit owner commands and Discord permissions have been tested. See the README for commands and settings. LatoDash is optional and has its own setup guide.
