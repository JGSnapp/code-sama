"""Tiny code-execution sandbox.

We're not pretending to be Docker — this is a subprocess wrapped in a temp
cwd with a wallclock timeout. Good enough for the agent to type a script
and watch ``print()`` output land in the in-OS terminal.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    elapsed: float
    timed_out: bool = False


class Sandbox:
    """Run short scripts in a scratch directory under the OS temp dir."""

    def __init__(self) -> None:
        self.root = Path(tempfile.gettempdir()) / "code-sama-sandbox"
        self.root.mkdir(parents=True, exist_ok=True)

    async def run_python(
        self,
        code: str,
        *,
        filename: str = "main.py",
        timeout: float = 8.0,
    ) -> RunResult:
        return await self._run_file(code, filename, [sys.executable, "-X", "utf8"], timeout)

    async def run_node(
        self,
        code: str,
        *,
        filename: str = "main.js",
        timeout: float = 8.0,
    ) -> RunResult:
        node = shutil.which("node")
        if not node:
            return RunResult(False, -1, "", "node interpreter not found on PATH", 0.0)
        return await self._run_file(code, filename, [node], timeout)

    async def _run_file(
        self,
        code: str,
        filename: str,
        argv_prefix: list[str],
        timeout: float,
    ) -> RunResult:
        script_dir = self.root / f"job-{int(time.time() * 1000)}"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / filename
        script_path.write_text(code, encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "TEMP": os.environ.get("TEMP", str(self.root)),
            "TMP": os.environ.get("TMP", str(self.root)),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        argv = [*argv_prefix, str(script_path)]
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(script_dir),
                env=env,
            )
        except FileNotFoundError as exc:
            return RunResult(False, -1, "", f"failed to launch: {exc}", 0.0)

        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:  # pragma: no cover
                pass
            stdout, stderr = await proc.communicate()
        elapsed = time.monotonic() - start
        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        if timed_out:
            err = (err + "\n" if err else "") + f"[sandbox killed after {timeout:.1f}s]"
        return RunResult(
            ok=(proc.returncode == 0 and not timed_out),
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=out,
            stderr=err,
            elapsed=elapsed,
            timed_out=timed_out,
        )
