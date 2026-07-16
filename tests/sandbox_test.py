"""Tests for the Docker sandbox (D33 / SANDBOX_DESIGN.md).

Two tiers:
- Unit: a fake `docker` runner records the exact CLI args, so the isolation
  flags, per-workspace container naming, exec dispatch, and teardown are
  asserted with NO daemon (the always-green tier every other test lives in).
- Integration: gated on a reachable Docker daemon (skipped with a printed
  notice otherwise) -- actually runs alpine to prove a command cannot read a
  host file outside the workspace, that --network=none blocks egress, and
  that the workspace mount is shared both ways.
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sandbox import SandboxConfig, SandboxManager


class _FakeDocker:
    """Records (args, timeout) calls; returns scripted (code, output)."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args, timeout):
        self.calls.append(args)
        if args[0] == "info":
            return 0, "27.0.0"
        if args[0] == "run":
            return 0, "container-id\n"
        if args[0] == "exec":
            # Echo back the command so the test can see it was dispatched.
            return 0, f"ran: {args[-1]}"
        if args[0] == "rm":
            return 0, ""
        return 0, ""


def test_container_flags_and_naming():
    fake = _FakeDocker()
    mgr = SandboxManager(SandboxConfig(image="alpine", memory="512m", cpus="1", pids=64), runner=fake)
    with tempfile.TemporaryDirectory() as ws:
        out = mgr.exec(ws, "echo hi", timeout=30)
        assert out.startswith("exit code: 0")
        run_call = [c for c in fake.calls if c[0] == "run"][0]
        joined = " ".join(run_call)
        # The security-critical flags are all present.
        for flag in ("--memory 512m", "--cpus 1", "--pids-limit 64",
                     "--network none", "--cap-drop ALL",
                     "--security-opt no-new-privileges", "--read-only"):
            assert flag in joined, f"missing {flag!r} in: {joined}"
        # Mounts ONLY the workspace, at /workspace, as the workdir.
        assert f"{os.path.abspath(ws)}:/workspace:rw" in joined
        assert "-w /workspace" in joined
        # Deterministic per-workspace name (same workspace -> same name).
        assert mgr._container_name(ws) == mgr._container_name(ws)
    print("  container run flags, workspace-only mount, naming OK")


def test_reuses_container_then_tears_down():
    fake = _FakeDocker()
    mgr = SandboxManager(SandboxConfig(image="alpine"), runner=fake)
    with tempfile.TemporaryDirectory() as ws:
        mgr.exec(ws, "one")
        mgr.exec(ws, "two")
        runs = [c for c in fake.calls if c[0] == "run"]
        execs = [c for c in fake.calls if c[0] == "exec"]
        assert len(runs) == 1, "second command must reuse the container, not start a new one"
        # The command is wrapped in an in-container `timeout` (so a runaway
        # command is actually killed), so it's embedded in the last arg.
        assert len(execs) == 2 and "two" in execs[1][-1] and "timeout" in execs[1][-1]
        mgr.close_all()
        assert any(c[0] == "rm" and c[1] == "-f" for c in fake.calls), "close must rm -f the container"
    print("  container reused across commands, torn down on close OK")


def test_preflight_fails_loud_when_daemon_down():
    def dead_daemon(args, timeout):
        return 1, "Cannot connect to the Docker daemon"
    mgr = SandboxManager(SandboxConfig(), runner=dead_daemon)
    try:
        mgr.preflight()
    except Exception as exc:
        assert "daemon not reachable" in str(exc).lower()
    else:
        raise AssertionError("preflight must raise when the daemon is down")
    print("  preflight fails loud when the daemon is unreachable OK")


def test_setup_failure_returns_error_string_not_raise():
    def run_fails(args, timeout):
        if args[0] == "run":
            return 1, "no such image"
        return 0, ""
    mgr = SandboxManager(SandboxConfig(image="nope"), runner=run_fails)
    with tempfile.TemporaryDirectory() as ws:
        out = mgr.exec(ws, "echo hi")
        assert out.startswith("Error:") and "no such image" in out
    print("  a container-start failure degrades to an error string (never raises) OK")


def _docker_available() -> bool:
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=30
        ).returncode == 0
    except Exception:
        return False


def test_real_container_isolation():
    if not _docker_available():
        print("  [skipped] real-container isolation (no reachable Docker daemon)")
        return
    mgr = SandboxManager(SandboxConfig(image="alpine:latest"))
    with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as outside:
        secret = os.path.join(outside, "host-secret.txt")
        with open(secret, "w") as f:
            f.write("TOP-SECRET-HOST-DATA")
        try:
            # A file written in the workspace is visible to the container.
            with open(os.path.join(ws, "hello.txt"), "w") as f:
                f.write("from-host")
            assert "from-host" in mgr.exec(ws, "cat /workspace/hello.txt")

            # The container cannot read a host path outside the workspace.
            leak = mgr.exec(ws, f"cat {secret} 2>&1 || echo BLOCKED")
            assert "TOP-SECRET-HOST-DATA" not in leak
            assert "BLOCKED" in leak or "No such file" in leak, leak

            # --network=none blocks egress.
            net = mgr.exec(ws, "wget -T 3 -q -O- http://example.com 2>&1 || echo NO-NET", timeout=20)
            assert "NO-NET" in net or "bad address" in net.lower(), net

            # A file the container writes is visible back on the host.
            mgr.exec(ws, "echo from-container > /workspace/out.txt")
            assert os.path.exists(os.path.join(ws, "out.txt"))
            with open(os.path.join(ws, "out.txt")) as f:
                assert f.read().strip() == "from-container"

            # A runaway command is killed by the in-container timeout, and
            # the process is actually gone afterward (not left running).
            killed = mgr.exec(ws, "sleep 30", timeout=2)
            assert "timed out" in killed, killed
            # After the -k grace window, the runaway `sleep 30` must be gone.
            # Match its exact argv "sleep 30" (whole line) so we don't count
            # the container's own `sleep infinity` keepalive (PID 1) or the
            # `timeout ... sh -c 'sleep 30'` wrapper.
            still = mgr.exec(ws, "sleep 3; ps -o args= 2>/dev/null | grep -cx 'sleep 30' || echo 0")
            assert still.rstrip().endswith("0"), f"sleep left running: {still}"
        finally:
            mgr.close_all()
    print("  real container: workspace shared both ways, host reads blocked, egress denied OK")


def main():
    test_container_flags_and_naming()
    test_reuses_container_then_tears_down()
    test_preflight_fails_loud_when_daemon_down()
    test_setup_failure_returns_error_string_not_raise()
    test_real_container_isolation()
    print("SANDBOX TESTS PASSED")


if __name__ == "__main__":
    main()
