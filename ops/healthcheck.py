#!/usr/bin/env python3
"""Local transport watchdog; never claims end-to-end delivery."""
import subprocess
import urllib.request
from pathlib import Path
for unit, url in [('feishu-dot-adapter', 'http://127.0.0.1:8765/readyz'), ('feishu-dot-tunnel', 'http://127.0.0.1:8766/readyz')]:
    counter = Path('/run/feishu-dot-health') / unit
    counter.parent.mkdir(mode=0o700, exist_ok=True)
    if subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode:
        counter.unlink(missing_ok=True)
        continue
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status != 200: raise ValueError('not ready')
        counter.unlink(missing_ok=True)
    except Exception:
        failures = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(failures))
        print(unit + ': transport readiness failed')
        if failures >= 3:
            subprocess.run(['systemctl', 'restart', unit], check=True)
            counter.unlink(missing_ok=True)
