import codecs
import os
import subprocess
import sys


def run_in_an_ascii_locale(code: str) -> list[str]:
    env = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONUTF8": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join(path for path in sys.path if path),
    }
    probe = "import locale\nprint(locale.getpreferredencoding(False))\n"
    run = subprocess.run(
        [sys.executable, "-c", probe + code], env=env, capture_output=True, encoding="utf-8", check=False
    )
    assert run.returncode == 0, run.stderr
    encoding, *lines = run.stdout.splitlines()
    assert codecs.lookup(encoding).name == "ascii"
    return lines
