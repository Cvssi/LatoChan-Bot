#!/bin/bash
# Installs / updates the voice worker. Run ON THE SERVER from the staged latochan folder:
#     bash workers/voice/install.sh
# First run: creates user latovoice, /opt/latovoice (code + venv), /var/lib/latovoice (models), a shared token
# in /etc/latovoice.env + /etc/latochan.env (VOICE_TOKEN), downloads Whisper, starts the service.
# Later runs: update code/packages and restart. The bot's own install is separate (setup.sh).
set -euo pipefail
cd "$(dirname "$0")"
id latovoice >/dev/null 2>&1 || sudo useradd --system --home /var/lib/latovoice --shell /usr/sbin/nologin latovoice
sudo mkdir -p /opt/latovoice /var/lib/latovoice/voices /var/lib/latovoice/whisper
[ -x /opt/latovoice/venv/bin/python ] || sudo python3 -m venv /opt/latovoice/venv
sudo /opt/latovoice/venv/bin/pip install -q --upgrade pip
sudo /opt/latovoice/venv/bin/pip install -q -r requirements.txt
sudo install -m 644 audio.py server.py requirements.txt /opt/latovoice/
# models (needs internet once; the service itself runs offline)
sudo env WHISPER_DIR=/var/lib/latovoice/whisper /opt/latovoice/venv/bin/python -c "
import os; from faster_whisper import WhisperModel
WhisperModel(os.environ.get('WHISPER_MODEL','small'), device='cpu', compute_type='int8', download_root=os.environ['WHISPER_DIR'])"
sudo chown -R latovoice:latovoice /var/lib/latovoice
# shared token (never printed)
if ! sudo grep -q '^VOICE_TOKEN=' /etc/latovoice.env 2>/dev/null; then
  T=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
  echo "VOICE_TOKEN=$T" | sudo tee /etc/latovoice.env >/dev/null
  sudo cp /etc/latochan.env ~/latochan.env.bak-voice
  sudo sed -i '/^VOICE_TOKEN=/d' /etc/latochan.env
  echo "VOICE_TOKEN=$T" | sudo tee -a /etc/latochan.env >/dev/null
  unset T
fi
sudo chown root:latovoice /etc/latovoice.env; sudo chmod 640 /etc/latovoice.env
sudo install -m 644 latovoice.service /etc/systemd/system/latovoice.service
sudo systemctl daemon-reload
sudo systemctl enable latovoice >/dev/null 2>&1
sudo systemctl restart latovoice
for i in $(seq 1 30); do curl -sf localhost:8770/health >/dev/null && break; sleep 1; done
curl -s localhost:8770/health; echo
echo "voice worker installed. LatoChan needs a restart to pick up VOICE_TOKEN (the next deploy does that)."
