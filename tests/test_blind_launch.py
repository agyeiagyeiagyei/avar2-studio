"""T1.1: a blind launch (no source) shuts down without UnboundLocalError.

main() assigned OBSERVER without declaring it global; the finally block
then read a never-assigned local on shutdown whenever the watcher branch
never ran (blind launch / no watchdog).
"""
import os
import signal
import subprocess
import sys
import time

STUDIO_PORT = 5199


def test_blind_launch_shuts_down_cleanly(tmp_path):
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)  # keep ~/.avar2-studio out of the test
    proc = subprocess.Popen(
        [sys.executable, "-m", "avar2_studio", "--port", str(STUDIO_PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env,
        text=True)
    try:
        # wait for the server to come up (any HTTP answer)
        import urllib.request
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{STUDIO_PORT}/api/health", timeout=2)
                break
            except Exception:
                if proc.poll() is not None:
                    break
                time.sleep(0.3)
        assert proc.poll() is None, "server died on blind launch"
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            _, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
    assert "UnboundLocalError" not in err
