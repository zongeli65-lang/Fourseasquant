from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEST_WORK_DIRECTORY = REPOSITORY_ROOT / "work" / "system-tests"


def available_port() -> int:
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


class LocalApplicationTest(unittest.TestCase):
    temporary_directory: tempfile.TemporaryDirectory[str]
    port: int
    process: subprocess.Popen[str]

    def setUp(self) -> None:
        TEST_WORK_DIRECTORY.mkdir(parents=True, exist_ok=True)
        self.temporary_directory = tempfile.TemporaryDirectory(
            dir=TEST_WORK_DIRECTORY
        )
        self.port = available_port()
        environment = os.environ.copy()
        environment["FOURSEASQUANT_DB_PATH"] = str(
            Path(self.temporary_directory.name) / "application.db"
        )
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--app-dir",
                "backend",
                "fourseasquant.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                _, error_output = self.process.communicate()
                self.temporary_directory.cleanup()
                raise RuntimeError(f"应用启动失败：\n{error_output}")
            try:
                with urlopen(
                    f"http://127.0.0.1:{self.port}/api/health", timeout=0.2
                ):
                    return
            except URLError:
                time.sleep(0.05)
        raise RuntimeError("应用未在五秒内启动")

    def tearDown(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        self.process.communicate(timeout=5)
        self.temporary_directory.cleanup()

    def test_health_endpoint_reports_application_and_database_ready(self) -> None:
        with urlopen(
            f"http://127.0.0.1:{self.port}/api/health", timeout=1
        ) as response:
            payload = json.load(response)

        self.assertEqual(
            payload,
            {
                "application": "Fourseasquant",
                "status": "ok",
                "database": "ready",
            },
        )

    def test_dashboard_reports_target_date_before_any_snapshot_exists(self) -> None:
        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-21",
            timeout=1,
        ) as response:
            payload = json.load(response)

        self.assertEqual(
            payload,
            {
                "target_date": "2026-07-21",
                "actual_data_date": None,
                "last_updated_at": None,
                "task_status": "not_run",
                "snapshot": None,
            },
        )

    def test_user_can_run_and_publish_a_deterministic_daily_snapshot(self) -> None:
        request = Request(
            f"http://127.0.0.1:{self.port}/api/tasks/daily",
            data=json.dumps({"target_date": "2026-07-20"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            task = json.load(response)

        self.assertEqual(task["trigger_method"], "manual")
        self.assertEqual(task["target_date"], "2026-07-20")
        self.assertEqual(task["status"], "succeeded")
        self.assertIsNotNone(task["started_at"])
        self.assertIsNotNone(task["finished_at"])

        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-20",
            timeout=1,
        ) as response:
            dashboard = json.load(response)

        self.assertEqual(dashboard["target_date"], "2026-07-20")
        self.assertEqual(dashboard["actual_data_date"], "2026-07-20")
        self.assertEqual(dashboard["task_status"], "succeeded")
        self.assertIsNotNone(dashboard["last_updated_at"])
        self.assertEqual(
            dashboard["snapshot"],
            {
                "source": "simulation",
                "label": "确定性模拟快照",
                "seed": 20260720,
            },
        )

    def test_dashboard_falls_back_to_the_latest_snapshot_before_target_date(self) -> None:
        request = Request(
            f"http://127.0.0.1:{self.port}/api/tasks/daily",
            data=json.dumps({"target_date": "2026-07-17"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2):
            pass

        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-19",
            timeout=1,
        ) as response:
            dashboard = json.load(response)

        self.assertEqual(dashboard["target_date"], "2026-07-19")
        self.assertEqual(dashboard["actual_data_date"], "2026-07-17")
        self.assertEqual(dashboard["task_status"], "not_run")
        self.assertEqual(dashboard["snapshot"]["seed"], 20260717)

    def test_root_page_exposes_the_desktop_dashboard_shell(self) -> None:
        with urlopen(f"http://127.0.0.1:{self.port}/", timeout=1) as response:
            page = response.read().decode("utf-8")

        self.assertIn("<title>Fourseasquant</title>", page)
        self.assertIn("Fourseasquant", page)
        self.assertIn("应用状态", page)
        self.assertIn('data-theme="dark"', page)
class DevelopmentCommandTest(unittest.TestCase):
    def test_one_command_starts_the_frontend_backend_and_database(self) -> None:
        api_port = available_port()
        web_port = available_port()
        TEST_WORK_DIRECTORY.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=TEST_WORK_DIRECTORY
        ) as temporary_directory:
            environment = os.environ.copy()
            environment["FOURSEASQUANT_DB_PATH"] = str(
                Path(temporary_directory) / "development.db"
            )
            environment["FOURSEASQUANT_API_PORT"] = str(api_port)
            environment["FOURSEASQUANT_WEB_PORT"] = str(web_port)
            process = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=REPOSITORY_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        output, _ = process.communicate()
                        self.fail(f"开发命令提前退出：\n{output}")
                    try:
                        with urlopen(
                            f"http://127.0.0.1:{api_port}/api/health", timeout=0.2
                        ) as health_response:
                            health = json.load(health_response)
                        with urlopen(
                            f"http://127.0.0.1:{web_port}/", timeout=0.2
                        ) as page_response:
                            page = page_response.read().decode("utf-8")
                        break
                    except URLError:
                        time.sleep(0.1)
                else:
                    self.fail("开发命令未在十秒内同时启动前端和后端")

                self.assertEqual(health["database"], "ready")
                self.assertIn("<title>Fourseasquant</title>", page)
            finally:
                if process.poll() is None:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
