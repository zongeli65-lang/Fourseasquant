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
from typing import ClassVar
from urllib.error import URLError
from urllib.request import urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def available_port() -> int:
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


class LocalApplicationTest(unittest.TestCase):
    temporary_directory: ClassVar[tempfile.TemporaryDirectory[str]]
    port: ClassVar[int]
    process: ClassVar[subprocess.Popen[str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.port = available_port()
        environment = os.environ.copy()
        environment["FOURSEASQUANT_DB_PATH"] = str(
            Path(cls.temporary_directory.name) / "application.db"
        )
        cls.process = subprocess.Popen(
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
                str(cls.port),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                _, error_output = cls.process.communicate()
                raise RuntimeError(f"应用启动失败：\n{error_output}")
            try:
                with urlopen(
                    f"http://127.0.0.1:{cls.port}/api/health", timeout=0.2
                ):
                    return
            except URLError:
                time.sleep(0.05)
        raise RuntimeError("应用未在五秒内启动")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.process.poll() is None:
            cls.process.terminate()
        cls.process.communicate(timeout=5)
        cls.temporary_directory.cleanup()

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
        with tempfile.TemporaryDirectory() as temporary_directory:
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
