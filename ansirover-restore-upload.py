#!/usr/bin/env python3

from pathlib import Path

from ansirover_postgres_reconnect_guard import (
    create_postgres_session_generation_state,
    observe_postgres_session_generation,
)


ENTRYPOINT_MODULE_NAME = __name__
CORE_PATH = Path(__file__).with_name("ansirover_restore_upload_core.py")
CORE_SOURCE = CORE_PATH.read_text(encoding="utf-8")

globals()["__name__"] = "ansirover_restore_upload_core"
exec(compile(CORE_SOURCE, str(CORE_PATH), "exec"), globals(), globals())
globals()["__name__"] = ENTRYPOINT_MODULE_NAME

original_validate_server_target = validate_server_target
original_get_effective_policy = get_effective_policy
CURRENT_EFFECTIVE_POLICY = original_get_effective_policy(None)
STABLE_SWARM_TASK_IDS = {}


def validate_server_target(server_name):
    normalized_server_name = str(server_name)

    if "@" in normalized_server_name:
        fail(
            "CapRover machine/URL target must not contain @; "
            "AnsiRover reserves @ for explicit SSH targets in user@host form"
        )

    original_validate_server_target(normalized_server_name)


def get_effective_policy(policy):
    global CURRENT_EFFECTIVE_POLICY

    effective_policy = original_get_effective_policy(policy)
    CURRENT_EFFECTIVE_POLICY = dict(effective_policy)
    return effective_policy


def get_swarm_stability_period_seconds():
    return duration_to_seconds(
        "swarmStabilityPeriod",
        CURRENT_EFFECTIVE_POLICY["swarmStabilityPeriod"],
    )


def get_running_swarm_task_ids(server_name, app_name):
    service_name = resolve_service_name(server_name, app_name)
    remote_command = (
        "docker service ps "
        + shlex.quote(service_name)
        + " --no-trunc --filter 'desired-state=running' "
        + "--format '{{.ID}}\t{{.CurrentState}}'"
    )
    completed_process = subprocess.run(
        ["ssh", "-n", get_ssh_target(server_name), remote_command],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if completed_process.returncode != 0:
        if completed_process.stderr:
            print(completed_process.stderr, end="", file=sys.stderr)
        fail("could not inspect running Swarm task generation for service: " + service_name)

    running_task_ids = set()

    for line in completed_process.stdout.splitlines():
        if not line.strip():
            continue

        parts = line.split("\t", 1)

        if len(parts) != 2:
            continue

        task_id = parts[0].strip()
        current_state = parts[1].strip()

        if task_id and current_state.startswith("Running "):
            running_task_ids.add(task_id)

    return frozenset(running_task_ids)


def wait_for_swarm_convergence(server_name, app_name, baseline_task_ids):
    stable_key = (str(server_name), str(app_name))
    STABLE_SWARM_TASK_IDS.pop(stable_key, None)

    if is_caprover_url(server_name):
        info("[post-deploy] URL target has no SSH context; Swarm convergence/stability verification skipped.")
        return

    timeout_seconds = float(os.environ.get("CAPROVER_SWARM_WAIT_TIMEOUT_SECONDS", "180"))
    poll_seconds = float(os.environ.get("CAPROVER_SWARM_POLL_SECONDS", "2"))
    stability_seconds = get_swarm_stability_period_seconds()
    deadline = time.monotonic() + timeout_seconds
    service_name = resolve_service_name(server_name, app_name)
    candidate_task_ids = None
    stable_since = None

    info(
        "[post-deploy] Waiting for Swarm service convergence and an unchanged task generation for "
        + str(stability_seconds)
        + "s: "
        + service_name
    )

    while True:
        desired, running = get_service_replica_counts(server_name, app_name)
        failures = get_new_failed_tasks(server_name, app_name, baseline_task_ids)

        if failures and running < desired:
            print_service_tasks(server_name, app_name)
            fail(
                "post-deploy task churn detected: new Failed/Rejected task appeared before convergence for service: "
                + service_name
            )

        if desired == 0 and running == 0:
            STABLE_SWARM_TASK_IDS[stable_key] = frozenset()
            info("[post-deploy] Swarm service converged at 0/0 running; no task generation to stabilize.")
            print_service_tasks(server_name, app_name)
            return

        if running == desired:
            running_task_ids = get_running_swarm_task_ids(server_name, app_name)

            if len(running_task_ids) != desired:
                candidate_task_ids = None
                stable_since = None
            elif running_task_ids != candidate_task_ids:
                previous_task_ids = candidate_task_ids
                candidate_task_ids = running_task_ids
                stable_since = time.monotonic()

                if previous_task_ids is None:
                    info(
                        "[post-deploy] Swarm service reached desired replicas; task-generation stabilization started: "
                        + str(running)
                        + "/"
                        + str(desired)
                        + " running."
                    )
                else:
                    info(
                        "[post-deploy] Swarm task generation changed during stabilization; stability timer restarted."
                    )
            elif stable_since is not None and time.monotonic() - stable_since >= stability_seconds:
                STABLE_SWARM_TASK_IDS[stable_key] = running_task_ids
                info(
                    "[post-deploy] Swarm service stable: "
                    + str(running)
                    + "/"
                    + str(desired)
                    + " running with unchanged task generation for "
                    + str(stability_seconds)
                    + "s."
                )
                print_service_tasks(server_name, app_name)
                return
        else:
            candidate_task_ids = None
            stable_since = None

        if time.monotonic() >= deadline:
            print_service_tasks(server_name, app_name)
            fail(
                "post-deploy timed out waiting for stable service generation: "
                + service_name
                + " ("
                + str(running)
                + "/"
                + str(desired)
                + " running)"
            )

        time.sleep(poll_seconds)


def monitor_post_deploy_postgres_reconnects(args, effective_policy):
    monitor_seconds = duration_to_seconds(
        "gracePeriod",
        effective_policy["gracePeriod"],
    )
    poll_seconds = duration_to_seconds(
        "postgresPollInterval",
        effective_policy["postgresPollInterval"],
    )
    stable_key = (str(args.server_name), str(args.app_name))
    stable_task_ids = STABLE_SWARM_TASK_IDS.pop(stable_key, None)
    monitor_swarm_generation = (
        stable_task_ids is not None and not is_caprover_url(args.server_name)
    )

    psql_available = shutil.which("psql") is not None
    monitorable_connections = []

    if psql_available:
        monitorable_connections = get_monitorable_postgres_connections(args)
    else:
        info("[post-deploy] psql is not installed; PostgreSQL reconnect monitoring skipped.")

    if not monitorable_connections:
        info("[post-deploy] No PostgreSQL URI with enforced application_name is available for reconnect monitoring.")

    if not monitor_swarm_generation and not monitorable_connections:
        return

    reconnect_threshold = int(effective_policy["postgresReconnectThreshold"])
    expected_application_name = None
    session_state_by_env = {}
    reconnect_wave_count = 0

    if monitorable_connections:
        expected_application_name = get_expected_application_name(args)

    for env_name, connection_string in monitorable_connections:
        initial_sessions = get_postgres_session_ids(
            env_name,
            connection_string,
            expected_application_name,
        )
        session_state_by_env[env_name] = create_postgres_session_generation_state(
            initial_sessions
        )

    if monitor_swarm_generation:
        info(
            "[post-deploy] Watching stable Swarm task generation during the post-deploy observation window."
        )

    if monitorable_connections:
        message = (
            "[post-deploy] Monitoring PostgreSQL reconnect generations for "
            + str(monitor_seconds)
            + "s; quarantine threshold="
            + str(reconnect_threshold)
            + " reconnect waves; poll interval="
            + str(poll_seconds)
            + "s."
        )

        if sys.stderr.isatty():
            message = "\033[33m" + message + "\033[0m"

        info(message)
    else:
        info(
            "[post-deploy] Monitoring stable Swarm task generation for "
            + str(monitor_seconds)
            + "s; poll interval="
            + str(poll_seconds)
            + "s."
        )

    deadline = time.monotonic() + monitor_seconds

    while time.monotonic() < deadline:
        time.sleep(poll_seconds)

        if monitor_swarm_generation:
            current_task_ids = get_running_swarm_task_ids(
                args.server_name,
                args.app_name,
            )

            if current_task_ids != stable_task_ids:
                print_service_tasks(args.server_name, args.app_name)
                scale_service_to_zero(args.server_name, args.app_name)
                fail(
                    "post-deploy Swarm task generation changed after stabilization; service quarantined with replicas=0"
                )

        reconnect_wave_observed = False

        for env_name, connection_string in monitorable_connections:
            current_sessions = get_postgres_session_ids(
                env_name,
                connection_string,
                expected_application_name,
            )
            observation = observe_postgres_session_generation(
                session_state_by_env[env_name],
                current_sessions,
            )
            event = observation["event"]

            if event == "pool_expansion":
                info(
                    "[post-deploy] PostgreSQL pool expansion ignored for "
                    + env_name
                    + "; added="
                    + str(observation["added_sessions"])
                    + "; active="
                    + str(observation["active_sessions"])
                )
            elif event == "full_disconnect":
                info(
                    "[post-deploy] PostgreSQL full disconnect observed for "
                    + env_name
                    + "; waiting for a replacement generation."
                )
            elif event == "initial_connection_baseline":
                info(
                    "[post-deploy] PostgreSQL initial connection baseline established for "
                    + env_name
                    + "; active="
                    + str(observation["active_sessions"])
                )
            elif event == "partial_pool_churn":
                info(
                    "[post-deploy] PostgreSQL partial pool churn ignored for "
                    + env_name
                    + "; added="
                    + str(observation["added_sessions"])
                    + "; removed="
                    + str(observation["removed_sessions"])
                    + "; active="
                    + str(observation["active_sessions"])
                )

            if observation["reconnect_wave"]:
                reconnect_wave_observed = True
                info(
                    "[post-deploy] PostgreSQL reconnect generation observed for "
                    + env_name
                    + "; event="
                    + event
                    + "; added="
                    + str(observation["added_sessions"])
                    + "; removed="
                    + str(observation["removed_sessions"])
                    + "; active="
                    + str(observation["active_sessions"])
                )

        if reconnect_wave_observed:
            reconnect_wave_count += 1
            info(
                "[post-deploy] PostgreSQL reconnect wave observed; wave="
                + str(reconnect_wave_count)
                + "/"
                + str(reconnect_threshold)
            )

        if reconnect_wave_count >= reconnect_threshold:
            scale_service_to_zero(args.server_name, args.app_name)
            fail(
                "post-deploy PostgreSQL reconnect loop threshold reached; service quarantined with replicas=0"
            )

    if monitorable_connections:
        info(
            "[post-deploy] PostgreSQL reconnect monitor passed; reconnect waves observed="
            + str(reconnect_wave_count)
        )

    if monitor_swarm_generation:
        info("[post-deploy] Swarm task generation remained stable throughout the observation window.")


if ENTRYPOINT_MODULE_NAME == "__main__":
    main()
