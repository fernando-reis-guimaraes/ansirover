#!/usr/bin/env python3

import argparse
import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "ansirover-restore-upload.py"


def load_script_module():
    specification = importlib.util.spec_from_file_location(
        "ansirover_restore_upload_swarm_stability",
        SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += float(seconds)


class SwarmStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script_module()

    def setUp(self):
        self.module.STABLE_SWARM_TASK_IDS.clear()
        self.module.get_effective_policy(None)

    def test_wait_for_swarm_stability_restarts_timer_when_task_generation_changes(self):
        clock = FakeClock()
        self.module.get_effective_policy({"swarmStabilityPeriod": "2s"})
        task_generations = [
            frozenset({"task-a"}),
            frozenset({"task-b"}),
            frozenset({"task-b"}),
            frozenset({"task-b"}),
        ]

        with mock.patch.dict(
            os.environ,
            {
                "CAPROVER_SWARM_WAIT_TIMEOUT_SECONDS": "10",
                "CAPROVER_SWARM_POLL_SECONDS": "1",
            },
            clear=False,
        ), mock.patch.object(
            self.module,
            "is_caprover_url",
            return_value=False,
        ), mock.patch.object(
            self.module,
            "resolve_service_name",
            return_value="srv-captain--example",
        ), mock.patch.object(
            self.module,
            "get_service_replica_counts",
            return_value=(1, 1),
        ), mock.patch.object(
            self.module,
            "get_new_failed_tasks",
            return_value=[],
        ), mock.patch.object(
            self.module,
            "get_running_swarm_task_ids",
            side_effect=task_generations,
        ) as get_running_task_ids, mock.patch.object(
            self.module,
            "print_service_tasks",
        ), mock.patch.object(
            self.module.time,
            "monotonic",
            side_effect=clock.monotonic,
        ), mock.patch.object(
            self.module.time,
            "sleep",
            side_effect=clock.sleep,
        ):
            self.module.wait_for_swarm_convergence(
                "storage",
                "example",
                baseline_task_ids=set(),
            )

        self.assertEqual(get_running_task_ids.call_count, 4)
        self.assertEqual(
            self.module.STABLE_SWARM_TASK_IDS[("storage", "example")],
            frozenset({"task-b"}),
        )

    def test_initialization_crash_loop_still_fails_before_any_database_baseline(self):
        with mock.patch.object(
            self.module,
            "is_caprover_url",
            return_value=False,
        ), mock.patch.object(
            self.module,
            "resolve_service_name",
            return_value="srv-captain--example",
        ), mock.patch.object(
            self.module,
            "get_service_replica_counts",
            return_value=(1, 0),
        ), mock.patch.object(
            self.module,
            "get_new_failed_tasks",
            return_value=["failed-task"],
        ), mock.patch.object(
            self.module,
            "print_service_tasks",
        ), mock.patch.object(
            self.module,
            "get_running_swarm_task_ids",
        ) as get_running_task_ids:
            with self.assertRaises(SystemExit):
                self.module.wait_for_swarm_convergence(
                    "storage",
                    "example",
                    baseline_task_ids=set(),
                )

        get_running_task_ids.assert_not_called()

    def test_task_generation_change_after_stabilization_quarantines_immediately(self):
        clock = FakeClock()
        args = argparse.Namespace(server_name="storage", app_name="example")
        self.module.STABLE_SWARM_TASK_IDS[("storage", "example")] = frozenset(
            {"stable-task"}
        )
        effective_policy = self.module.get_effective_policy(
            {
                "gracePeriod": "10s",
                "postgresPollInterval": "1s",
            }
        )

        with mock.patch.object(
            self.module.shutil,
            "which",
            return_value=None,
        ), mock.patch.object(
            self.module,
            "is_caprover_url",
            return_value=False,
        ), mock.patch.object(
            self.module,
            "get_running_swarm_task_ids",
            return_value=frozenset({"replacement-task"}),
        ), mock.patch.object(
            self.module,
            "print_service_tasks",
        ), mock.patch.object(
            self.module,
            "scale_service_to_zero",
        ) as scale_service_to_zero, mock.patch.object(
            self.module.time,
            "monotonic",
            side_effect=clock.monotonic,
        ), mock.patch.object(
            self.module.time,
            "sleep",
            side_effect=clock.sleep,
        ):
            with self.assertRaises(SystemExit):
                self.module.monitor_post_deploy_postgres_reconnects(
                    args,
                    effective_policy,
                )

        scale_service_to_zero.assert_called_once_with("storage", "example")

    def test_stable_task_generation_is_watched_even_without_postgres(self):
        clock = FakeClock()
        args = argparse.Namespace(server_name="storage", app_name="example")
        stable_task_ids = frozenset({"stable-task"})
        self.module.STABLE_SWARM_TASK_IDS[("storage", "example")] = stable_task_ids
        effective_policy = self.module.get_effective_policy(
            {
                "gracePeriod": "2s",
                "postgresPollInterval": "1s",
            }
        )

        with mock.patch.object(
            self.module.shutil,
            "which",
            return_value=None,
        ), mock.patch.object(
            self.module,
            "is_caprover_url",
            return_value=False,
        ), mock.patch.object(
            self.module,
            "get_running_swarm_task_ids",
            return_value=stable_task_ids,
        ) as get_running_task_ids, mock.patch.object(
            self.module,
            "scale_service_to_zero",
        ) as scale_service_to_zero, mock.patch.object(
            self.module.time,
            "monotonic",
            side_effect=clock.monotonic,
        ), mock.patch.object(
            self.module.time,
            "sleep",
            side_effect=clock.sleep,
        ):
            self.module.monitor_post_deploy_postgres_reconnects(
                args,
                effective_policy,
            )

        self.assertEqual(get_running_task_ids.call_count, 2)
        scale_service_to_zero.assert_not_called()


if __name__ == "__main__":
    unittest.main()
