#!/usr/bin/env bash
# Read-only: no env/token, persona, message history or memory contents are printed.
set -euo pipefail
python=/opt/latochan/current/venv/bin/python
[[ -x "$python" ]] || python=/opt/latochan/venv/bin/python
"$python" - <<'PY'
import importlib.metadata as metadata
import json
import sys
print('Python:', sys.version)
for name in ['discord.py', 'aiohttp', 'numpy', 'requests', 'Pillow', 'PyNaCl', 'davey']:
    try: print(name + '=' + metadata.version(name))
    except metadata.PackageNotFoundError: print(name + '=missing')
try:
    from PIL import features
    print('Pillow codecs:', json.dumps({key: features.version_codec(key) for key in ['jpg', 'jpg_2000', 'zlib']}))
except ImportError:
    pass
PY
systemctl show latochan -p User -p Group -p UMask -p ProtectSystem -p ProtectHome -p ReadWritePaths -p NoNewPrivileges -p PrivateTmp -p CapabilityBoundingSet -p ActiveState
for path in /opt/latochan /opt/latochan/current /var/lib/latochan /var/lib/latochan/settings.json /var/lib/latochan/memory.db /etc/latochan.env; do
  [[ ! -e "$path" ]] || stat -c '%A %a %U:%G %n' "$path"
done
ss -ltn '( sport = :11434 )'
"$python" - "${1:?Pass the configured Ollama model name as the first argument}" <<'PY'
import json
import sys
import urllib.request
try:
    request = urllib.request.Request('http://127.0.0.1:11434/api/show', json.dumps({'model': sys.argv[1]}).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response: details = json.load(response)
    print('Model:', sys.argv[1])
    print(json.dumps({key: details.get(key) for key in ['capabilities', 'thinking']}, indent=2))
except Exception as error:
    print('Model check:', type(error).__name__)
PY
