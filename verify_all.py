"""顺序运行三个项目的完整测试与离线演示，任一失败立即返回非零。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def _run(command: list[str], cwd: Path, environment: dict[str, str]) -> None:
    print(f"\n{'=' * 72}\n[{cwd.name}] {' '.join(command)}\n{'=' * 72}", flush=True)
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    projects = [ROOT / name for name in ("insightrag", "promptlens", "datachat_bi")]
    try:
        for project in projects:
            _run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                project,
                environment,
            )
        _run(
            [sys.executable, "demo.py", "--eval-output", "artifacts/evaluation.json"],
            ROOT / "insightrag",
            environment,
        )
        _run([sys.executable, "demo.py"], ROOT / "promptlens", environment)
        _run([sys.executable, "demo.py"], ROOT / "datachat_bi", environment)
    except subprocess.CalledProcessError as exc:
        print(f"\n验证失败：{exc.cmd} 返回 {exc.returncode}", file=sys.stderr)
        return exc.returncode or 1
    print("\n全部通过：127 项自动化测试及三个离线演示均已成功完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
