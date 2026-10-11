"""Process runner (section 61) - run user-defined commands (tests, MATLAB...).

Version 1 only exposes "Run Custom Command"; output is captured for the UI
so it can be fed back into the next prompt (test loop is a later phase).

The command is executed fully detached from this application: stdout/stderr
are written to temporary files and collected afterwards. No console window
is ever shown on Windows (CREATE_NO_WINDOW) and the child process is not
attached to any terminal of ours.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class RunResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append("[stderr]\n" + self.stderr.strip())
        if self.exit_code != 0:
            parts.append(f"[exit code] {self.exit_code}")
        return "\n".join(parts)


def _popen_flags() -> int:
    flags = 0
    if sys.platform == "win32":
        # never flash a console window for the child process
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    return flags


def run_command(command: str, cwd: str, timeout: int = 300,
                env_extra: Optional[dict] = None) -> RunResult:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    out_fd, out_path = tempfile.mkstemp(prefix="apm_out_", suffix=".log")
    err_fd, err_path = tempfile.mkstemp(prefix="apm_err_", suffix=".log")
    os.close(out_fd)
    os.close(err_fd)
    try:
        with open(out_path, "wb") as fout, open(err_path, "wb") as ferr:
            proc = subprocess.Popen(
                command, shell=True, cwd=cwd, env=env,
                stdout=fout, stderr=ferr, stdin=subprocess.DEVNULL,
                creationflags=_popen_flags())
            deadline = time.monotonic() + max(1, timeout)
            while True:
                code = proc.poll()
                if code is not None:
                    break
                if time.monotonic() > deadline:
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                    code = -1
                    break
                time.sleep(0.05)
        timed_out = code == -1
        stdout = _read_file(out_path)
        stderr = _read_file(err_path)
        if timed_out:
            stderr = (stderr + f"\ncommand timed out after {timeout}s").strip()
        return RunResult(code == 0, code if code is not None else -1, stdout, stderr)
    except OSError as exc:
        return RunResult(False, -1, "", str(exc))
    finally:
        for p in (out_path, err_path):
            try:
                os.remove(p)
            except OSError:
                pass


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""
