"""Run custom commands with incremental, bounded output capture."""
from __future__ import annotations

import codecs
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class RunResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    output_truncated: bool = False
    duration_seconds: float = 0.0

    @property
    def combined(self) -> str:
        parts = []
        if self.output_truncated:
            parts.append("[output truncated in returned result; last portion retained]")
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append("[stderr]\n" + self.stderr.strip())
        if self.exit_code != 0:
            parts.append(f"[exit code] {self.exit_code}")
        return "\n".join(parts)


def no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0


def _popen_flags() -> int:
    flags = 0
    if sys.platform == "win32":
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    return flags


def _append_tail(buffer: bytearray, chunk: bytes, limit: int) -> bool:
    """Append bytes to a bounded tail buffer; return whether truncation occurred."""
    if not chunk:
        return False
    if len(chunk) >= limit:
        buffer[:] = chunk[-limit:]
        return True
    overflow = len(buffer) + len(chunk) - limit
    if overflow > 0:
        del buffer[:overflow]
    buffer.extend(chunk)
    return overflow > 0


def run_command(command: str, cwd: str, timeout: int = 300,
                env_extra: Optional[dict] = None,
                on_output: Optional[Callable[[str, str], None]] = None,
                max_output_bytes: int = 1024 * 1024) -> RunResult:
    """Execute a shell command, streaming chunks while retaining bounded tails.

    Reader threads drain stdout/stderr concurrently into a bounded queue. The
    calling thread forwards chunks to ``on_output`` and maintains only the last
    ``max_output_bytes`` per stream, keeping memory use predictable.
    """
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    max_output_bytes = max(4096, int(max_output_bytes))
    started = time.monotonic()
    proc = None
    tails = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    decoders = {name: codecs.getincrementaldecoder("utf-8")("replace")
                for name in tails}
    output_queue = queue.Queue(maxsize=64)  # at most ~4 MiB of queued pipe data

    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, creationflags=_popen_flags(),
            bufsize=0)

        def pump(stream: str, pipe) -> None:
            try:
                while True:
                    chunk = pipe.read(65536)
                    if not chunk:
                        break
                    output_queue.put((stream, chunk))
            except (OSError, ValueError):
                pass
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass
                output_queue.put((stream, None))

        readers = [
            threading.Thread(target=pump, args=("stdout", proc.stdout), daemon=True),
            threading.Thread(target=pump, args=("stderr", proc.stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()

        deadline = time.monotonic() + max(1, int(timeout))
        post_exit_deadline = None
        completed_streams = 0
        code = None
        timed_out = False
        while True:
            if code is None:
                code = proc.poll()
                if code is not None:
                    post_exit_deadline = time.monotonic() + 5.0
                elif time.monotonic() >= deadline:
                    timed_out = True
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                    code = -1
                    post_exit_deadline = time.monotonic() + 5.0

            try:
                stream, chunk = output_queue.get(timeout=0.05)
            except queue.Empty:
                stream, chunk = None, None

            if stream is not None and chunk is None:
                completed_streams += 1
            elif stream is not None and chunk:
                truncated[stream] = _append_tail(
                    tails[stream], chunk, max_output_bytes) or truncated[stream]
                if on_output is not None:
                    try:
                        text = decoders[stream].decode(chunk, final=False)
                        if text:
                            on_output(stream, text)
                    except Exception:
                        # A UI callback must never stop draining the child pipes.
                        pass

            if code is not None and completed_streams >= 2 and output_queue.empty():
                break
            if (code is not None and post_exit_deadline is not None
                    and time.monotonic() >= post_exit_deadline
                    and output_queue.empty()):
                # A detached descendant may have inherited a pipe. Do not hang
                # the application indefinitely waiting for its EOF.
                for pipe in (proc.stdout, proc.stderr):
                    try:
                        pipe.close()
                    except Exception:
                        pass
                break

        for stream in tails:
            if on_output is not None:
                try:
                    remaining = decoders[stream].decode(b"", final=True)
                    if remaining:
                        on_output(stream, remaining)
                except Exception:
                    pass
        for reader in readers:
            reader.join(timeout=1)

        stdout = bytes(tails["stdout"]).decode("utf-8", errors="replace")
        stderr = bytes(tails["stderr"]).decode("utf-8", errors="replace")
        if timed_out:
            stderr = (stderr + f"\ncommand timed out after {timeout}s").strip()
        return RunResult(
            ok=(code == 0 and not timed_out),
            exit_code=-1 if timed_out else (code if code is not None else -1),
            stdout=stdout,
            stderr=stderr,
            output_truncated=any(truncated.values()),
            duration_seconds=max(0.0, time.monotonic() - started),
        )
    except OSError as exc:
        return RunResult(False, -1, "", str(exc),
                         duration_seconds=max(0.0, time.monotonic() - started))
    finally:
        if proc is not None:
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except Exception:
                        pass
            if proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
