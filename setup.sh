#!/usr/bin/env bash
# Reviewed release installer. Stage first, preserve data/configuration, then switch atomically.
# Requires an installed local Ollama and CPython 3.14 with venv/pip support.
set -Eeuo pipefail
umask 027
APP_DIR=/opt/latochan
DATA_DIR=/var/lib/latochan
ENV_FILE=/etc/latochan.env
PYTHON_BIN="${PYTHON_BIN:-python3}"
ADMIN_USER="${SUDO_USER:-}"
SRC_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
[[ $(id -u) -eq 0 ]] || { echo 'Run: sudo bash setup.sh'; exit 1; }
[[ "$ADMIN_USER" != root ]] || ADMIN_USER=''
"$PYTHON_BIN" -c 'import sys; assert sys.version_info[:2] == (3, 14), "This lock is tested on Python 3.14. Set PYTHON_BIN to that interpreter."'
command -v ollama >/dev/null || { echo 'Install and verify Ollama separately before running this installer. No remote script is executed as root.'; exit 1; }
command -v curl >/dev/null
[[ -s "$SRC_DIR/requirements.lock" ]] || { echo 'Missing reviewed requirements.lock'; exit 1; }
[[ ! -L "$APP_DIR" && ! -L "$DATA_DIR" ]] || { echo "Application/data roots must not be symlinks"; exit 1; }
[[ ! -e "$APP_DIR/current" || -L "$APP_DIR/current" ]] || { echo '/opt/latochan/current must be a release symlink'; exit 1; }

MODEL="${MODEL:-}"
if [[ -z "$MODEL" && -f "$ENV_FILE" ]]; then
  MODEL=$("$PYTHON_BIN" - "$SRC_DIR" "$ENV_FILE" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from envconfig import read_env
print(read_env(sys.argv[2]).get('LLM_MODEL', ''))
PY
)
fi
MODEL="${MODEL:-llama3.1:8b}"
[[ "$MODEL" =~ ^[a-zA-Z0-9_./:-]+$ && "$MODEL" != *cloud* ]] || { echo 'Use a local model name.'; exit 1; }

id latochan >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin latochan
install -d -o root -g root -m 755 "$APP_DIR" "$APP_DIR/releases"
exec 9>"$APP_DIR/.install.lock"
flock -n 9 || { echo 'Another installation is in progress'; exit 1; }
release="$APP_DIR/releases/$(date -u +%Y%m%dT%H%M%S)-$$"
install -d -o root -g root -m 755 "$release"
app_files=(gateway.py botsettings.py gifstore.py latoids.py memory.py rag.py commands.py storage.py workload.py media_worker.py actionlog.py envconfig.py requirements.txt requirements.lock)
for file in "${app_files[@]}"; do install -o root -g root -m 644 "$SRC_DIR/$file" "$release/$file"; done
"$PYTHON_BIN" -m venv "$release/venv"
"$release/venv/bin/python" -m pip install --require-hashes --only-binary=:all: -r "$release/requirements.lock"
"$release/venv/bin/python" -m pip check
chmod -R a+rX,go-w "$release"
"$release/venv/bin/python" -m compileall -q "$release"

# Back up configuration and consistent databases before activation. Retain all backups;
# an administrator chooses retention after checking restore requirements.
backup="$APP_DIR/backups/$(date -u +%Y%m%dT%H%M%S)-$$"
install -d -m 700 "$backup"
old_release=''
if [[ -L "$APP_DIR/current" ]]; then
  old_release=$(readlink -f "$APP_DIR/current")
  [[ "$old_release" == "$APP_DIR/releases/"* || "$old_release" == "$APP_DIR" ]] || { echo 'Unexpected current release target'; exit 1; }
elif [[ -x "$APP_DIR/venv/bin/python" ]]; then
  old_release="$APP_DIR"  # keep the legacy installation usable for rollback
fi
was_active=0
systemctl is-active --quiet latochan && was_active=1
activated=0
rollback() {
  status=$?
  trap - ERR
  if [[ "$activated" -eq 1 && -n "$old_release" ]]; then
    ln -s "$old_release" "$APP_DIR/current.rollback"
    mv -Tf "$APP_DIR/current.rollback" "$APP_DIR/current"
  fi
  if [[ -f "$backup/latochan.env" ]]; then install -m 600 "$backup/latochan.env" "$ENV_FILE"; fi
  if [[ -d "$backup/helpers" ]]; then cp -a "$backup/helpers/." /usr/local/bin/; fi
  if [[ -d "$backup/units" ]]; then
    cp -a "$backup/units/." /etc/systemd/system/
    if [[ ! -f "$backup/units/latochan.service.d/hardening.conf" ]]; then
      rm -f /etc/systemd/system/latochan.service.d/hardening.conf
    fi
    if [[ ! -f "$backup/units/ollama.service.d/latochan-security.conf" ]]; then
      rm -f /etc/systemd/system/ollama.service.d/latochan-security.conf
    fi
    systemctl daemon-reload
    systemctl restart ollama || true
  fi
  if [[ "$was_active" -eq 1 ]]; then systemctl restart latochan || true; fi
  printf 'Installation failed. Backup: %s. Review deploy/OPERATIONS.md before recovery.\n' "$backup" >&2
  exit "$status"
}
trap rollback ERR
if [[ "$was_active" -eq 1 ]]; then systemctl stop latochan; fi
if [[ -f "$ENV_FILE" ]]; then install -m 600 "$ENV_FILE" "$backup/latochan.env"; fi
install -d -m 700 "$backup/units" "$backup/helpers"
for helper in lato-config lato-gifs lato-memory lato-say lato-watch; do
  if [[ -f "/usr/local/bin/$helper" ]]; then cp -a "/usr/local/bin/$helper" "$backup/helpers/"; fi
done
if [[ -f /etc/systemd/system/ollama.service.d/latochan-security.conf ]]; then
  install -d -m 700 "$backup/units/ollama.service.d"
  cp -a /etc/systemd/system/ollama.service.d/latochan-security.conf "$backup/units/ollama.service.d/"
fi
for item in latochan.service latochan.service.d; do
  if [[ -e "/etc/systemd/system/$item" ]]; then cp -a "/etc/systemd/system/$item" "$backup/units/"; fi
done
if [[ -d "$DATA_DIR" ]]; then
  "$PYTHON_BIN" - "$DATA_DIR" "$backup" <<'PY'
import os, pathlib, shutil, sqlite3, sys
source, target = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]) / 'data'
target.mkdir(mode=0o700)
for path in source.rglob('*'):
    if path.is_symlink():
        raise ValueError('Refusing symlink in data backup: ' + str(path))
    dest = target / path.relative_to(source)
    if path.is_dir():
        dest.mkdir(mode=0o700, exist_ok=True)
    elif path.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.suffix in {'.db', '.sqlite'}:
            src = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            dst = sqlite3.connect(dest)
            try: src.backup(dst)
            finally: src.close(); dst.close()
        elif path.name.endswith(('-wal', '-shm', '-journal')):
            continue
        else:
            shutil.copy2(path, dest)
        if dest.exists(): os.chmod(dest, 0o600)
PY
fi

install -d -o root -g root -m 755 /etc/latochan
if [[ ! -f /etc/latochan/persona.md ]]; then
  previous="$APP_DIR/persona.md"
  [[ ! -f "$APP_DIR/current/persona.md" ]] || previous="$APP_DIR/current/persona.md"
  [[ -f "$previous" ]] || previous="$SRC_DIR/persona.md"
  install -o root -g root -m 644 "$previous" /etc/latochan/persona.md
fi
# Keep a preserved persona available if an existing PERSONA_PATH uses the current symlink.
install -o root -g root -m 644 /etc/latochan/persona.md "$release/persona.md"
# Preserve every existing env assignment, including custom model/persona paths.
"$PYTHON_BIN" - "$SRC_DIR" "$ENV_FILE" "$MODEL" <<'PY'
import os, shlex, sys, tempfile
sys.path.insert(0, sys.argv[1])
from envconfig import read_env
path, model = sys.argv[2:]
values = read_env(path) if os.path.exists(path) else {}
values['LLM_MODEL'] = model
values.setdefault('PERSONA_PATH', '/etc/latochan/persona.md')
values.setdefault('MEMBERS_INTENT', '1')
values.setdefault('DISCORD_BOT_TOKEN', '')
values.setdefault('OWNER_ID', '')
values.setdefault('CHANNEL_IDS', '')
# Private context stays disabled until explicitly configured after installation.
values['PRIVATE_CHANNEL_IDS'] = ''
values['RAG_ENABLED'] = '0'
for key, prompt in [('DISCORD_BOT_TOKEN', 'Discord bot token'), ('OWNER_ID', 'Owner Discord user ID'), ('CHANNEL_IDS', 'Main channel IDs, comma separated')]:
    if not values[key]:
        if key == 'DISCORD_BOT_TOKEN':
            import getpass
            values[key] = getpass.getpass(prompt + ': ')
        else:
            with open('/dev/tty') as terminal:
                print(prompt + ': ', end='', flush=True)
                values[key] = terminal.readline().strip()
if not values['DISCORD_BOT_TOKEN'] or not values['OWNER_ID'].isdigit() or not all(x.strip().isdigit() for x in values['CHANNEL_IDS'].split(',')):
    raise ValueError('Token, OWNER_ID, and CHANNEL_IDS must be configured before activation')
fd, temp = tempfile.mkstemp(dir='/etc', prefix='.latochan.env-')
try:
    with os.fdopen(fd, 'w') as out:
        os.fchmod(out.fileno(), 0o600)
        for key, value in values.items():
            # Values contain no shell expansions; envconfig and systemd both accept these double quotes.
            value = value.replace('\\', '\\\\').replace('"', '\\"')
            if '\n' in value or '\r' in value: raise ValueError('Multiline env values are not supported')
            out.write(f'{key}="{value}"\n')
        out.flush(); os.fsync(out.fileno())
    os.replace(temp, path)
finally:
    if os.path.exists(temp): os.unlink(temp)
PY

install -d -o latochan -g latochan -m 2770 "$DATA_DIR" "$DATA_DIR/gifs" "$DATA_DIR/rag"
if [[ -n "$ADMIN_USER" ]]; then usermod -aG latochan "$ADMIN_USER"; fi
# Never follow links while changing existing data permissions.
find "$DATA_DIR" -type d -exec chown latochan:latochan {} + -exec chmod 2770 {} +
find "$DATA_DIR" -type f -exec chown latochan:latochan {} + -exec chmod 660 {} +
if [[ ! -f "$DATA_DIR/settings.json" ]]; then printf '{}\n' > "$DATA_DIR/settings.json"; fi
chown latochan:latochan "$DATA_DIR/settings.json"; chmod 660 "$DATA_DIR/settings.json"
for mood in waking sleepy happy sad annoyed bored love laughing confused excited smug shocked; do
  install -d -o latochan -g latochan -m 2770 "$DATA_DIR/gifs/$mood"
  if [[ ! -f "$DATA_DIR/gifs/$mood/links.txt" ]]; then
    install -o latochan -g latochan -m 660 /dev/null "$DATA_DIR/gifs/$mood/links.txt"
  fi
done

install -d -m 755 /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/latochan-security.conf <<'CONF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_NO_CLOUD=1"
CONF
systemctl daemon-reload
systemctl restart ollama
ready=0
for _ in $(seq 1 30); do
  if curl --max-time 2 -fsS http://127.0.0.1:11434/api/version >/dev/null; then ready=1; break; fi
  sleep 1
done
[[ "$ready" -eq 1 ]] || { echo 'Ollama readiness failed'; false; }
if ! ollama show "$MODEL" >/dev/null 2>&1; then ollama pull "$MODEL"; fi
# Test the selected custom model before changing the running app symlink.
"$release/venv/bin/python" - "$MODEL" <<'PY'
import requests, sys
r = requests.post('http://127.0.0.1:11434/api/chat', json={'model': sys.argv[1], 'stream': False, 'think': False, 'messages': [{'role': 'user', 'content': 'Say hello briefly.'}], 'options': {'num_predict': 60}}, timeout=(5, 120))
r.raise_for_status()
if not r.json().get('message', {}).get('content'): raise ValueError('Model returned no answer')
PY
ln -s "$release" "$APP_DIR/current.new"
mv -Tf "$APP_DIR/current.new" "$APP_DIR/current"
activated=1
install -m 644 "$SRC_DIR/deploy/systemd/latochan.service" /etc/systemd/system/latochan.service
install -d -m 755 /etc/systemd/system/latochan.service.d
install -m 644 "$SRC_DIR/deploy/systemd/latochan.service.d/hardening.conf" /etc/systemd/system/latochan.service.d/hardening.conf
for helper in lato-config lato-gifs lato-memory lato-say lato-watch; do
  install -o root -g root -m 755 "$SRC_DIR/$helper" "/usr/local/bin/$helper"
done
systemctl daemon-reload
systemctl enable --now latochan
systemctl restart latochan
sleep 5
systemctl is-active --quiet latochan
trap - ERR
printf 'Release active: %s\nPrevious release: %s\nBackup: %s\n' "$release" "${old_release:-legacy installation}" "$backup"
printf 'Verify a Discord reply and /lato commands. See deploy/OPERATIONS.md for rollback and remaining production checks.\n'
