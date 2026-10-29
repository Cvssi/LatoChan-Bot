"""Exercise release activation/rollback with isolated paths and fake host commands."""
from pathlib import Path
import os
import subprocess
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
def run_installer_case(fail, legacy=False):
    with tempfile.TemporaryDirectory(prefix='lato-install-smoke-') as directory:
        base = Path(directory)
        fakebin = base / 'bin'; fakebin.mkdir()
        app = base / 'opt/latochan'
        previous = app if legacy else app / 'releases/previous'
        (previous / 'venv/bin').mkdir(parents=True)
        (previous / 'venv/bin/python').write_text('#!/bin/sh\nexit 0\n')
        (previous / 'venv/bin/python').chmod(0o755)
        (previous / 'persona.md').write_text('CUSTOM PERSONA DO NOT OVERWRITE')
        if not legacy:
            (app / 'current').symlink_to(previous)
        etc = base / 'etc'; etc.mkdir()
        (etc / 'latochan.env').write_text('DISCORD_BOT_TOKEN=test\nOWNER_ID=111111111111111111\nCHANNEL_IDS=100\nLLM_MODEL=custom-model:latest\n')
        (etc / 'systemd/system').mkdir(parents=True)
        original_unit='[Service]\nUser=latochan\n'
        (etc / 'systemd/system/latochan.service').write_text(original_unit)
        (base / 'usr/local/bin').mkdir(parents=True)
        (base / 'usr/local/bin/lato-say').write_text('old helper')
        mocks = base / 'mocks'; mocks.mkdir()
        (mocks / 'requests.py').write_text("class R:\n def raise_for_status(self): pass\n def json(self): return {'message':{'content':'hello'}}\ndef post(*a,**k):return R()\n")
        def tool(name, text):
            path = fakebin / name
            path.write_text(text); path.chmod(0o755)
        tool('id', '#!/bin/sh\n[ "$1" != -u ] || echo 0\nexit 0\n')
        for name in ('useradd','usermod','chown','ollama','sleep','curl'):
            tool(name, '#!/bin/sh\nexit 0\n')
        tool('systemctl', '''#!/bin/sh
if [ "$1" = is-active ]; then
 n=0; [ ! -f "$SMOKE_COUNT" ] || n=$(cat "$SMOKE_COUNT")
 n=$((n+1)); echo "$n" > "$SMOKE_COUNT"
 if [ "$SMOKE_FAIL" = 1 ] && [ "$n" -gt 1 ]; then exit 1; fi
fi
exit 0
''')
        tool('install', f'''#!{PYTHON}
import subprocess,sys
args=[];i=1
while i<len(sys.argv):
 if sys.argv[i] in ('-o','-g'): i+=2
 else: args.append(sys.argv[i]);i+=1
sys.exit(subprocess.call(['/usr/bin/install',*args]))
''')
        tool('python-test', f'''#!/bin/sh
if [ "$1" = -m ] && [ "$2" = venv ]; then
 mkdir -p "$3/bin"
 ln -s "{fakebin}/python-test" "$3/bin/python"
 exit 0
fi
if [ "$1" = -m ] && [ "$2" = pip ]; then exit 0; fi
exec {PYTHON} "$@"
''')
        script = (ROOT / 'setup.sh').read_text()
        script = script.replace('SRC_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"', 'SRC_DIR=' + str(ROOT))
        for prefix in ['/opt/latochan', '/var/lib/latochan', '/etc', '/usr/local/bin']:
            script = script.replace(prefix, str(base) + prefix)
        # compileall never traverses fake interpreter symlinks; application source is real.
        path = base / 'install.sh'; path.write_text(script)
        env = dict(os.environ, PATH=str(fakebin) + ':' + os.environ['PATH'],
                   PYTHON_BIN=str(fakebin / 'python-test'), PYTHONPATH=str(mocks),
                   SUDO_USER='', SMOKE_COUNT=str(base / 'counter'), SMOKE_FAIL=str(int(fail)))
        result = subprocess.run(['bash', str(path)], env=env, capture_output=True, text=True, timeout=20)
        if bool(result.returncode) != fail:
            raise AssertionError(result.stdout + result.stderr)
        target=(app / 'current').resolve()
        if fail:
            assert target == previous, (target, result.stdout, result.stderr)
            assert (etc / 'systemd/system/latochan.service').read_text() == original_unit
            assert (base / 'usr/local/bin/lato-say').read_text() == 'old helper'
        else:
            assert target != previous
            assert (etc / 'latochan/persona.md').read_text() == 'CUSTOM PERSONA DO NOT OVERWRITE'
            assert 'custom-model:latest' in (etc / 'latochan.env').read_text()
            assert (target / 'gateway.py').exists()
        print('PASS installer', 'failed-activation rollback' if fail else 'upgrade preserves model/persona and activates release')


class InstallerSmoke(unittest.TestCase):
    def test_upgrade_preserves_configuration(self):
        run_installer_case(False)

    def test_activation_failure_restores_previous_release(self):
        run_installer_case(True)

    def test_legacy_upgrade_preserves_configuration(self):
        run_installer_case(False, legacy=True)

    def test_legacy_activation_failure_restores_previous_layout(self):
        run_installer_case(True, legacy=True)
