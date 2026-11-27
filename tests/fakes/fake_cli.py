import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _next_mode(name: str, counter: Path) -> str:
    modes = os.environ.get(f"FAKE_{name.upper()}", "none").split(",")
    with counter.open("r+", encoding="ascii") as stream:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX)
        index = int(stream.read())
        stream.seek(0)
        stream.write(str(index + 1))
    return modes[min(index, len(modes) - 1)]


def main(name: str, args: list[str]) -> int:
    counter = Path(os.environ["FAKE_BIN"]) / f"{name}.count"
    mode = _next_mode(name, counter)
    with (counter.parent / f"{name}.args").open("a", encoding="utf-8") as f:
        f.write(json.dumps(args) + "\n")
    if name == "codex" and args == ["login", "status"]:
        print("Logged in using ChatGPT")
        return 0
    if name == "agy" and args == ["models"]:
        print("gemini-3.8-flash-high")
        return 0

    if mode == "quota":
        print("You've hit your usage limit", file=sys.stderr)
        return 1
    if mode == "hang":
        with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"]) as child:
            counter.with_suffix(".child.pid").write_text(str(child.pid), encoding="ascii")
            time.sleep(300)
        return 0

    if mode.startswith("write:"):
        _, rel, text = mode.split(":", 2)
        tree = Path(args[args.index("-C") + 1]) if "-C" in args else Path.cwd()
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        output = f"Wrote {rel}\n"
    else:
        outputs = {
            "none": "NO FINDINGS\n",
            "findings": (
                "🔴 src/a.py:1 — Missing input check — Empty input raises an exception\n"
                "🟢 src/b.py:2 — Misleading name — The count is reported as a duration\n"
            ),
            "prose": "I reviewed the changes and found nothing to flag.\n",
            "empty": "",
        }
        if mode not in outputs:
            raise SystemExit(f"unknown fake {name} mode: {mode!r}")
        output = outputs[mode]

    if name == "codex" and "-o" in args:
        Path(args[args.index("-o") + 1]).write_text(output, encoding="utf-8")
    else:
        sys.stdout.buffer.write(output.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
