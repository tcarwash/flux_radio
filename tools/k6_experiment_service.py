#!/usr/bin/env python3
"""Install/remove/status a user-level Flux trusted experiment scheduler.

Only runs offline pinned experiments. Hardware jobs cannot start from this
service until a separately reviewed hardware execution mode is implemented.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
UNIT="flux-experiment-autopilot"
SERVICE=f"""[Unit]
Description=Flux trusted experiment queue (offline pinned runners only)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={ROOT}
ExecStart={sys.executable} {ROOT}/tools/k6_experiment_autopilot.py --once --publish
ExecStart={sys.executable} {ROOT}/tools/k6_experiment_review.py --once
TimeoutStartSec=650
NoNewPrivileges=yes
UMask=0077
"""
TIMER=f"""[Unit]
Description=Periodic trusted Flux experiment queue processing

[Timer]
OnBootSec=90s
OnUnitActiveSec=10min
RandomizedDelaySec=20s
Persistent=true
Unit={UNIT}.service

[Install]
WantedBy=timers.target
"""

def user_systemctl(*args, check=True):
    env=os.environ.copy()
    uid=os.getuid()
    runtime=env.setdefault("XDG_RUNTIME_DIR",f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS",f"unix:path={runtime}/bus")
    return subprocess.run(["systemctl","--user",*args],check=check,
                          timeout=20,env=env)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation",choices=("install","status","remove"))
    a=p.parse_args(argv)
    d=Path.home()/".config/systemd/user"
    service=d/f"{UNIT}.service"
    timer=d/f"{UNIT}.timer"
    if a.operation=="install":
        d.mkdir(parents=True,exist_ok=True)
        for path,content in ((service,SERVICE),(timer,TIMER)):
            if path.exists() and path.read_text()!=content:
                original=content.replace(
                    f"ExecStart={sys.executable} {ROOT}/tools/k6_experiment_review.py --once\n", "")
                if path.read_text()!=original:
                    p.error(f"refuse overwrite of modified existing unit: {path}")
            path.write_text(content)
        for args in (["daemon-reload"],["enable","--now",f"{UNIT}.timer"]):
            user_systemctl(*args)
    elif a.operation=="remove":
        user_systemctl("disable","--now",f"{UNIT}.timer")
        service.unlink(missing_ok=True);timer.unlink(missing_ok=True)
        user_systemctl("daemon-reload")
    else:
        user_systemctl("status",f"{UNIT}.timer","--no-pager",check=False)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
