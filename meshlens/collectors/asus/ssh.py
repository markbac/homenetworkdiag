"""SSH transport for ASUS units (paramiko, as in ``homelab-unified``).

Key authentication is tried first (D-09), then the stored password (D-16).
The script is fed on stdin to ``sh -s``; the router's shell runs it.
"""
from __future__ import annotations

import logging

import paramiko

log = logging.getLogger("meshlens.asus.ssh")


class SshError(RuntimeError):
    pass


def run_script(host: str, port: int, username: str, password: str | None, script: str, timeout: int = 15) -> str:
    """Run ``script`` on the unit and return its combined output."""
    client = paramiko.SSHClient()
    # Units are on the LAN and their host keys are learnt on first contact (stored in the user's known_hosts).
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=port, username=username, password=password, timeout=timeout,
                       banner_timeout=timeout, auth_timeout=timeout, look_for_keys=True, allow_agent=False)
        stdin, stdout, stderr = client.exec_command("sh -s", timeout=timeout * 4)
        stdin.write(script)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", errors="replace")
        return out
    except (paramiko.SSHException, OSError) as e:
        raise SshError(f"{host}:{port}: {e}") from e
    finally:
        client.close()
