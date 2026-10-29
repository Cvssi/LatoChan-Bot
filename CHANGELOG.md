# Changelog

## 1.1.0 — review remediation (unreleased)
- Replace model-authorized server actions with explicit owner commands and durable duplicate protection.
- Disable private context by default; scope notes/retrieval and remove model-generated memory writes.
- Bound inference admission, context and media work; isolate decoding with resource limits.
- Add conflict-safe memory imports, transactional retrieval V2, synchronized GIF updates and atomic settings.
- Preserve custom deployment configuration; stage root-owned releases with audited hashed dependencies and rollback.
- Add regression tests and deployment/recovery instructions. Production integration remains an operator check.

## 1.0.1
- Barebones, fully local release: no cloud hand-off, no external APIs besides Discord.
- All Discord IDs come from environment variables (`latoids.py`); installer prompts for them.
- ~30 live settings (`botsettings.py`, `lato-config`), GIF library (`gifstore.py`, `lato-gifs`).
- Configurable `TIMEZONE` and `FORCE_LOWERCASE`; generic `persona.md` template.

## 1.0.0
- Local-model chat, burst handling, name mentions, per-person memory, vision, action tags, sleep mode, sandboxed systemd service.
