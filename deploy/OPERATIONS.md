# Review remediation: deployment and recovery

These changes are local source changes until an administrator deploys them. The production server has not been modified by the development work.

## Behavior changes to review before deployment

- Privileged actions use explicit owner commands. Model-generated moderation, voice, status, thinking and memory tags never execute.
- New installations default `server_powers` to false. An existing explicit setting is preserved. Enable with `lato-config set server_powers on` when ready.
- `PRIVATE_CHANNEL_IDS` is empty and `RAG_ENABLED=0` after installation or upgrade as a safe default. Existing notes remain on disk. Do not enable private context until channel audience and retention policy have been approved.
- Message/reply/reasoning content is no longer logged by default. `CONTENT_LOGS=1` is temporary troubleshooting only; it can persist personal content in the journal.
- Memory import uses an optimistic snapshot: any database change after export causes a clean rejection. Export afresh and merge your edits. Legacy `#exported` files are rejected; preserve them and merge into a fresh export. The supplied `#initial-import` example is accepted only for an empty database.
- RAG V2 stores rows and vectors together in `rag_store_v2.sqlite`. Old SQLite/NPZ stores are never modified. Re-ingest original JSONL exports into V2 before enabling retrieval. Do not delete the old stores or exports during validation.
- Inference has one global slot, bounded admission, reserved owner capacity and expiry. Images use a separate subprocess with CPU, address-space and wall-clock limits, a format allowlist, and the first three frames only. This is resource isolation, not a separate OS security principal.
- Requests too large for the conservative context budget receive an error rather than silently truncating the newest request or persona. Large custom personas may require a higher `NUM_CTX` or a shorter persona.
- The deployment lock targets CPython 3.14 on Linux x86_64. Other runtimes/platforms require a newly resolved and audited lock.

## Read-only production checks

Copy the reviewed source to a staging folder on the server, then run:

```bash
bash deploy/collect-review-evidence.sh YOUR_MODEL_NAME
```

This prints dependency versions, codec versions, selected systemd properties, file permissions, the Ollama listener, and model capabilities. It does not print tokens, persona text, memory, environment contents or messages. Review this output before installing. Check Discord's role hierarchy, channel overwrites, message-content/member intents, and separate Pin Messages permission in the Discord UI.

## Pre-deployment

1. Keep an independently verified copy of `/etc/latochan.env`, the active persona, `/var/lib/latochan`, source, and existing systemd units. Restrict backups to the administrator. Confirm a SQLite backup can be opened and that expected note counts are present.
2. Pause other writers, including LatoDash, during the upgrade. The reviewed LatoDash uses installed CLI helpers and is compatible with the new settings protocol. Deploy its paired persona/model helpers and stdin-posting update before resuming dashboard operations. Any separate direct file writer must adopt the new sidecar-lock protocol.
3. Review `setup.sh` and the lock. The installer requires preinstalled Ollama and Python 3.14 with venv/pip. It does not execute a remote installer as root. It preserves the selected model and custom persona, creates private data backups, stages a separate root-owned release, and atomically activates `/opt/latochan/current`.
4. Run the local tests first:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.lock
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests_gifmode.py .
bash -n setup.sh
```

The installer tests substitute temporary paths and fake host commands. They do not provision a real systemd server or test real Discord permissions.

## Installation

```bash
sudo bash setup.sh
```

The existing `LLM_MODEL` is preserved unless an explicit `MODEL=...` is passed. An existing custom model does not need to be replaced with the fresh-install default. GPU tuning is never installed or changed by this command. Existing `PERSONA_PATH` is preserved; otherwise the installer preserves an existing persona into `/etc/latochan/persona.md`.

The operator may need to reconnect their SSH session after being added to the service/admin group before using the CLI without sudo.

The installer records a backup under `/opt/latochan/backups/` and the old release path. Old releases and backups are retained for operator-approved cleanup. No production data migration is automatic.

## Verification after activation

- Confirm the service is active and a real owner chat receives a reply from the intended model.
- Check an ordinary request to mute someone produces no moderation. Test explicit `/lato` commands in a disposable channel using a test member, including a wrong target, denied permission, duplicate event and reconnect.
- Check actual action outcomes match replies. Test pin/unpin with `PIN_MESSAGES` and message delete with `MANAGE_MESSAGES` separately.
- Keep private context disabled. Confirm ordinary chat cannot write notes through model-generated tags.
- Confirm image replies with your vision-capable model, text-only fallback when images are disabled, and bounded behavior under a small controlled burst. Watch latency/RAM and the journal for errors without enabling content logs unnecessarily.
- Confirm code/venv are root-owned and readable by the service; data is private to the service/admin group; `/etc/latochan.env` remains root-only. Verify effective sandbox settings and local-only Ollama behavior.
- Test voice and the fallback model separately. Obtain another review before enabling hardware-specific fan/watchdog scripts.

## Recovery

An activation failure attempts to restore the previous release symlink, backed-up environment, helpers and bot unit configuration, and restarts the old bot if it was previously active. Read the reported backup location and verify the server state. Hardening changes to data permissions are not reversed automatically. A first-time failed installation has no prior working service to restore.

For a manual application rollback, use the exact previous release path printed by the installer:

```bash
# Replace /opt/latochan/releases/PREVIOUS with the verified previous release.
sudo ln -s /opt/latochan/releases/PREVIOUS /opt/latochan/current.rollback
sudo mv -Tf /opt/latochan/current.rollback /opt/latochan/current
sudo systemctl restart latochan
```

For the first upgrade from a legacy layout, the previous target may be `/opt/latochan` itself. The old files and venv are retained. Restore the backed-up service, helpers and env together if their old paths differ. Do not point legacy code at new storage formats without checking compatibility.

Do not restore old databases over newer notes casually. Stop all writers, preserve the current state, and inspect/restore a copy first. Correcting source code cannot recover notes lost before these changes.
