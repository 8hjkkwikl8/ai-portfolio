"""在本机启动 InsightRAG 与 DataChat BI，并打开作品集展示页。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
SERVICES = (
    ("InsightRAG", ROOT / "insightrag", 8001),
    ("DataChat BI", ROOT / "datachat_bi", 8002),
)


def _check_environment() -> None:
    missing = [name for name in ("fastapi", "uvicorn") if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError(
            f"缺少依赖：{', '.join(missing)}。请先运行：{sys.executable} -m pip install -r requirements.txt"
        )
    for name, _, port in SERVICES:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(f"{name} 所需端口 {port} 已被占用，请关闭对应程序后重试。") from exc


def _wait_until_ready(name: str, port: int, process: subprocess.Popen[bytes]) -> None:
    # 本机健康检查不经过系统代理。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError(f"{name} 启动失败，请查看终端日志。")
        try:
            with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=0.4) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok":
                print(f"[就绪] {name}: http://127.0.0.1:{port}")
                return
        except (OSError, ValueError):
            time.sleep(0.15)
    raise RuntimeError(f"{name} 健康检查超时。")


def _stop(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 AI 作品集的两个本地 Web 服务")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开默认浏览器")
    parser.add_argument("--smoke-test", action="store_true", help="健康检查通过后立即停止服务")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    processes: list[subprocess.Popen[bytes]] = []
    try:
        _check_environment()
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        for name, cwd, port in SERVICES:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.api:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=cwd,
                env=environment,
            )
            processes.append(process)
            _wait_until_ready(name, port, process)

        dashboard = ROOT / "index.html"
        if not args.no_browser:
            webbrowser.open(dashboard.as_uri())
        if args.smoke_test:
            print("冒烟测试通过：两个 API 均已就绪，本次服务将自动停止。")
            return 0
        print("\n两个服务已启动，展示页已准备就绪。")
        print("PromptLens 报告可直接从展示页打开；按 Ctrl+C 停止本次启动的服务。")
        # 轮询两个子进程，任一退出时都在 finally 中清理另一个。
        while True:
            for process in processes:
                exit_code = process.poll()
                if exit_code is not None:
                    return 0 if exit_code == 0 else 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n正在停止本次启动的服务…")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    finally:
        _stop(processes)


if __name__ == "__main__":
    raise SystemExit(main())
