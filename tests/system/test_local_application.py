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
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEST_WORK_DIRECTORY = REPOSITORY_ROOT / "work" / "system-tests"


def available_port() -> int:
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


class LocalApplicationTest(unittest.TestCase):
    temporary_directory: tempfile.TemporaryDirectory[str]
    environment: dict[str, str]
    port: int
    process: subprocess.Popen[str]

    def setUp(self) -> None:
        TEST_WORK_DIRECTORY.mkdir(parents=True, exist_ok=True)
        self.temporary_directory = tempfile.TemporaryDirectory(
            dir=TEST_WORK_DIRECTORY
        )
        self.port = available_port()
        self.environment = dict(os.environ)
        self.environment["FOURSEASQUANT_DB_PATH"] = str(
            Path(self.temporary_directory.name) / "application.db"
        )
        self.environment["FOURSEASQUANT_LOG_PATH"] = str(
            Path(self.temporary_directory.name) / "tasks.jsonl"
        )
        self.environment["FOURSEASQUANT_ENABLE_FAILURE_SIMULATION"] = "1"
        self.environment["FOURSEASQUANT_ENABLE_STARTUP_CATCHUP"] = "0"
        self.process = self.start_application()

    def start_application(self) -> subprocess.Popen[str]:
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
            env=self.environment,
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
                    return self.process
            except URLError:
                time.sleep(0.05)
        raise RuntimeError("应用未在五秒内启动")

    def restart_application(self) -> None:
        self.process.terminate()
        self.process.communicate(timeout=5)
        self.process = self.start_application()

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
                "failure": None,
                "snapshot": None,
            },
        )

    def test_failures_preserve_last_snapshot_and_retry_publishes_atomically(self) -> None:
        self.run_task("2026-07-20")
        stages = [
            ("market_prepare", "市场准备"),
            ("strategy_run", "策略运行"),
            ("output_validation", "输出校验"),
            ("transactional_publish", "事务发布"),
        ]
        for stage, label in stages:
            task = self.run_task("2026-07-21", simulate_failure_stage=stage)
            self.assertEqual(task["status"], "failed")
            self.assertEqual(task["stage"], stage)

            with urlopen(
                f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-21",
                timeout=1,
            ) as response:
                dashboard = json.load(response)
            self.assertEqual(dashboard["task_status"], "failed")
            self.assertEqual(dashboard["actual_data_date"], "2026-07-20")
            self.assertEqual(dashboard["snapshot"]["seed"], 20260720)
            self.assertEqual(dashboard["failure"]["stage_label"], label)
            self.assertIsNotNone(dashboard["failure"]["failed_at"])

        with urlopen(
            f"http://127.0.0.1:{self.port}/api/tasks/history?limit=10",
            timeout=1,
        ) as response:
            history = json.load(response)
        failures = [item for item in history if item["status"] == "failed"]
        self.assertEqual(len(failures), 4)
        self.assertTrue(
            all(
                item["target_date"] == "2026-07-21"
                and item["trigger_method"] == "manual"
                and item["started_at"]
                and item["finished_at"]
                and item["error_summary"]
                for item in failures
            )
        )

        retry_request = Request(
            f"http://127.0.0.1:{self.port}/api/tasks/daily/retry",
            data=json.dumps({"target_date": "2026-07-21"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(retry_request, timeout=2) as response:
            retry = json.load(response)
        self.assertEqual(retry["status"], "succeeded")
        self.assertEqual(retry["trigger_method"], "retry")

        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-21",
            timeout=1,
        ) as response:
            dashboard = json.load(response)
        self.assertEqual(dashboard["actual_data_date"], "2026-07-21")
        self.assertEqual(dashboard["task_status"], "succeeded")
        self.assertIsNone(dashboard["failure"])
        successful_snapshot = dashboard["snapshot"]

        duplicate = self.run_task("2026-07-21")
        self.assertEqual(duplicate["status"], "succeeded")
        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-21",
            timeout=1,
        ) as response:
            repeated_dashboard = json.load(response)
        self.assertEqual(repeated_dashboard["snapshot"], successful_snapshot)

        log_text = Path(self.environment["FOURSEASQUANT_LOG_PATH"]).read_text()
        self.assertIn('"event": "task_failed"', log_text)
        self.assertIn('"stage": "transactional_publish"', log_text)
        self.assertNotIn("token", log_text.lower())

    def test_backfill_previews_trading_days_and_preserves_manual_review(self) -> None:
        with urlopen(
            f"http://127.0.0.1:{self.port}/api/backfill/preview?start_date=2026-07-17&end_date=2026-07-21",
            timeout=1,
        ) as response:
            preview = json.load(response)
        self.assertEqual(preview["trading_day_count"], 3)
        self.assertEqual(
            preview["trading_days"],
            ["2026-07-17", "2026-07-20", "2026-07-21"],
        )

        self.run_task("2026-07-20")
        review_request = Request(
            f"http://127.0.0.1:{self.port}/api/reviews/2026-07-20",
            data=json.dumps({"note": "保留这条人工复盘", "tags": ["补算保护"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urlopen(review_request, timeout=2):
            pass

        partial_request = Request(
            f"http://127.0.0.1:{self.port}/api/testing/backfill",
            data=json.dumps(
                {
                    "start_date": "2026-07-17",
                    "end_date": "2026-07-21",
                    "failure_date": "2026-07-20",
                    "failure_stage": "strategy_run",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(partial_request, timeout=5) as response:
            partial = json.load(response)
        self.assertEqual(partial["total"], 3)
        self.assertEqual(partial["succeeded"], 2)
        self.assertEqual(partial["failed"], 1)
        self.assertEqual(
            [item["target_date"] for item in partial["results"] if item["status"] == "failed"],
            ["2026-07-20"],
        )

        backfill_request = Request(
            f"http://127.0.0.1:{self.port}/api/backfill",
            data=json.dumps(
                {"start_date": "2026-07-17", "end_date": "2026-07-21"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(backfill_request, timeout=5) as response:
            repeated = json.load(response)
        self.assertEqual(repeated["succeeded"], 3)
        self.assertEqual(repeated["failed"], 0)

        with urlopen(
            f"http://127.0.0.1:{self.port}/api/reviews/2026-07-20",
            timeout=1,
        ) as response:
            review = json.load(response)
        self.assertEqual(review["note"], "保留这条人工复盘")
        self.assertEqual(review["tags"], ["补算保护"])

        with urlopen(
            f"http://127.0.0.1:{self.port}/api/tasks/history?limit=20",
            timeout=1,
        ) as response:
            history = json.load(response)
        self.assertTrue(
            any(item["trigger_method"] == "backfill" for item in history)
        )

    def run_task(
        self,
        target_date: str,
        *,
        simulate_failure_stage: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"target_date": target_date}
        if simulate_failure_stage:
            payload["simulate_failure_stage"] = simulate_failure_stage
        endpoint = (
            "/api/testing/tasks/daily"
            if simulate_failure_stage
            else "/api/tasks/daily"
        )
        if simulate_failure_stage:
            payload = {"target_date": target_date, "stage": simulate_failure_stage}
        request = Request(
            f"http://127.0.0.1:{self.port}{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            return cast(dict[str, object], json.load(response))

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
        self.assertEqual(dashboard["snapshot"]["source"], "simulation")
        self.assertEqual(dashboard["snapshot"]["label"], "确定性模拟快照")
        self.assertEqual(dashboard["snapshot"]["seed"], 20260720)

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

    def test_market_overview_uses_only_eligible_main_board_securities(self) -> None:
        request = Request(
            f"http://127.0.0.1:{self.port}/api/tasks/daily",
            data=json.dumps({"target_date": "2026-07-21"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2):
            pass

        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-21",
            timeout=1,
        ) as response:
            overview = json.load(response)["snapshot"]["market_overview"]

        self.assertEqual(
            overview["indices"],
            [
                {"name": "上证指数", "change_pct": 0.62},
                {"name": "深证成指", "change_pct": -0.31},
                {"name": "创业板指", "change_pct": 0.18},
                {"name": "沪深 300", "change_pct": 0.44},
            ],
        )
        self.assertEqual(
            overview["breadth"],
            {
                "advancers": 2,
                "decliners": 2,
                "unchanged": 1,
                "advancer_ratio": 40.0,
                "decliner_ratio": 40.0,
                "unchanged_ratio": 20.0,
            },
        )
        self.assertEqual(
            overview["limit_activity"],
            {"limit_up": 1, "limit_down": 1, "max_limit_up_streak": 3},
        )
        self.assertEqual(
            overview["turnover"],
            {"amount_cny": 270_000_000_000, "change_vs_20d_pct": 12.5},
        )
        self.assertEqual(
            overview["high_low"],
            {"new_high_20d": 2, "new_low_20d": 2},
        )
        self.assertEqual(overview["eligible_security_count"], 5)
        self.assertNotIn("sentiment_score", overview)

    def test_snapshot_does_not_publish_simulated_sector_leaders(self) -> None:
        request = Request(
            f"http://127.0.0.1:{self.port}/api/tasks/daily",
            data=json.dumps({"target_date": "2026-07-21"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2):
            pass

        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-21",
            timeout=1,
        ) as response:
            snapshot = json.load(response)["snapshot"]

        self.assertNotIn("sector_performance", snapshot)

    def test_strategy_snapshot_contains_three_year_daily_history(self) -> None:
        request = Request(
            f"http://127.0.0.1:{self.port}/api/tasks/daily",
            data=json.dumps({"target_date": "2026-07-21"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2):
            pass

        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-21",
            timeout=1,
        ) as response:
            strategy = json.load(response)["snapshot"]["strategy_performance"]

        self.assertTrue(strategy["is_demo"])
        self.assertEqual(strategy["benchmark_label"], "沪深 300")
        self.assertEqual(strategy["points"][-1]["date"], "2026-07-21")
        self.assertGreaterEqual(len(strategy["points"]), 720)
        self.assertLessEqual(len(strategy["points"]), 740)
        point_dates = {point["date"] for point in strategy["points"]}
        self.assertNotIn("2024-02-12", point_dates)
        self.assertNotIn("2025-10-01", point_dates)
        self.assertNotIn("2026-02-16", point_dates)
        self.assertEqual(
            strategy["daily_summary"]["excess_return_pct"],
            round(
                strategy["daily_summary"]["strategy_return_pct"]
                - strategy["daily_summary"]["benchmark_return_pct"],
                4,
            ),
        )
        self.assertLessEqual(strategy["statistics"]["max_drawdown_pct"], 0)
        self.assertNotEqual(
            strategy["range_statistics"]["all"],
            strategy["range_statistics"]["quarter"],
        )

    def test_review_note_and_tags_survive_application_restart(self) -> None:
        task_request = Request(
            f"http://127.0.0.1:{self.port}/api/tasks/daily",
            data=json.dumps({"target_date": "2026-07-21"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(task_request, timeout=2):
            pass

        save_request = Request(
            f"http://127.0.0.1:{self.port}/api/reviews/2026-07-21",
            data=json.dumps(
                {
                    "note": "指数分化，关注主板成交持续性。",
                    "tags": ["放量", "观察", "放量"],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urlopen(save_request, timeout=2) as response:
            saved = json.load(response)

        self.assertEqual(saved["date"], "2026-07-21")
        self.assertEqual(saved["tags"], ["放量", "观察"])
        self.assertIsNotNone(saved["updated_at"])

        self.restart_application()
        with urlopen(
            f"http://127.0.0.1:{self.port}/api/reviews/2026-07-21",
            timeout=1,
        ) as response:
            restored = json.load(response)

        self.assertEqual(restored["note"], "指数分化，关注主板成交持续性。")
        self.assertEqual(restored["tags"], ["放量", "观察"])

    def test_portfolio_snapshot_is_balanced_and_contributions_reconcile(self) -> None:
        request = Request(
            f"http://127.0.0.1:{self.port}/api/tasks/daily",
            data=json.dumps({"target_date": "2026-07-21"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2):
            pass

        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-21",
            timeout=1,
        ) as response:
            snapshot = json.load(response)["snapshot"]

        portfolio = snapshot["portfolio_review"]
        holdings = portfolio["holdings"]
        self.assertEqual(len(holdings), 24)
        self.assertEqual(
            len({holding["security"]["code"] for holding in holdings}),
            24,
        )
        self.assertAlmostEqual(
            sum(holding["weight_pct"] for holding in holdings),
            100.0,
            places=3,
        )
        self.assertTrue(
            all(trade["date"] == "2026-07-21" for trade in portfolio["trades"])
        )
        self.assertAlmostEqual(
            sum(item["contribution_pct"] for item in portfolio["contributions"]),
            snapshot["strategy_performance"]["daily_summary"]["strategy_return_pct"],
            places=4,
        )

    def test_settings_persist_and_affect_the_next_daily_task(self) -> None:
        baseline_request = Request(
            f"http://127.0.0.1:{self.port}/api/tasks/daily",
            data=json.dumps({"target_date": "2026-07-21"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(baseline_request, timeout=2):
            pass
        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-21",
            timeout=1,
        ) as response:
            baseline = json.load(response)["snapshot"]

        update_request = Request(
            f"http://127.0.0.1:{self.port}/api/settings",
            data=json.dumps(
                {
                    "auto_update_time": "16:45",
                    "benchmark": "中证 500",
                    "data_adapter": "simulation_conservative",
                    "new_stock_exclusion_days": 15,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urlopen(update_request, timeout=2):
            pass

        self.restart_application()
        with urlopen(
            f"http://127.0.0.1:{self.port}/api/settings", timeout=1
        ) as response:
            settings = json.load(response)

        self.assertEqual(settings["auto_update_time"], "16:45")
        self.assertEqual(settings["benchmark"], "中证 500")
        self.assertEqual(settings["data_adapter"], "simulation_conservative")
        self.assertEqual(settings["new_stock_exclusion_days"], 15)
        self.assertNotIn("token", json.dumps(settings).lower())

        task_request = Request(
            f"http://127.0.0.1:{self.port}/api/tasks/daily",
            data=json.dumps({"target_date": "2026-07-21"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(task_request, timeout=2):
            pass
        with urlopen(
            f"http://127.0.0.1:{self.port}/api/dashboard?target_date=2026-07-21",
            timeout=1,
        ) as response:
            snapshot = json.load(response)["snapshot"]

        self.assertEqual(snapshot["market_overview"]["eligible_security_count"], 6)
        self.assertEqual(snapshot["source"], "simulation_conservative")
        self.assertNotEqual(
            snapshot["market_overview"]["turnover"]["amount_cny"],
            baseline["market_overview"]["turnover"]["amount_cny"],
        )
        self.assertEqual(
            snapshot["strategy_performance"]["benchmark_label"],
            "中证 500",
        )
        self.assertNotEqual(
            snapshot["strategy_performance"]["daily_summary"]["benchmark_return_pct"],
            baseline["strategy_performance"]["daily_summary"]["benchmark_return_pct"],
        )

    def test_untrusted_host_cannot_modify_local_settings(self) -> None:
        request = Request(
            f"http://127.0.0.1:{self.port}/api/settings",
            data=json.dumps(
                {
                    "auto_update_time": "16:45",
                    "benchmark": "中证 500",
                    "data_adapter": "simulation_conservative",
                    "new_stock_exclusion_days": 15,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Host": "evil.example"},
            method="PUT",
        )

        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=2)

        self.assertEqual(error.exception.code, 400)

    def test_root_page_exposes_the_desktop_dashboard_shell(self) -> None:
        with urlopen(f"http://127.0.0.1:{self.port}/", timeout=1) as response:
            page = response.read().decode("utf-8")

        self.assertIn(
            "<title>Fourseasquant · A 股量化工作台</title>",
            page,
        )
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
                self.assertIn(
                    "<title>Fourseasquant · A 股量化工作台</title>",
                    page,
                )
            finally:
                if process.poll() is None:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
