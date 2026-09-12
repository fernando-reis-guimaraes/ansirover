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


def validate_server_target(server_name):
    normalized_server_name = str(server_name)

    if "@" in normalized_server_name:
        fail(
            "CapRover machine/URL target must not contain @; "
            "AnsiRover reserves @ for explicit SSH targets in user@host form"
        )

    original_validate_server_target(normalized_server_name)


def monitor_post_deploy_postgres_reconnects(args, effective_policy):
    monitor_seconds = duration_to_seconds(
        "gracePeriod",
        effective_policy["gracePeriod"],
    )

    if shutil.which("psql") is None:
        info("[post-deploy] psql is not installed; PostgreSQL reconnect monitor skipped.")
        return

    monitorable_connections = get_monitorable_postgres_connections(args)

    if not monitorable_connections:
        info("[post-deploy] No PostgreSQL URI with enforced application_name is available for reconnect monitoring.")
        return

    reconnect_threshold = int(effective_policy["postgresReconnectThreshold"])
    poll_seconds = duration_to_seconds(
        "postgresPollInterval",
        effective_policy["postgresPollInterval"],
    )

    expected_application_name = get_expected_application_name(args)
    session_state_by_env = {}
    reconnect_wave_count = 0

    for env_name, connection_string in monitorable_connections:
        initial_sessions = get_postgres_session_ids(
            env_name,
            connection_string,
            expected_application_name,
        )
        session_state_by_env[env_name] = create_postgres_session_generation_state(
            initial_sessions
        )

    info(
        "[post-deploy] Monitoring PostgreSQL reconnect generations for "
        + str(monitor_seconds)
        + "s; quarantine threshold="
        + str(reconnect_threshold)
        + " reconnect waves; poll interval="
        + str(poll_seconds)
        + "s."
    )
    deadline = time.monotonic() + monitor_seconds

    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
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

    info(
        "[post-deploy] PostgreSQL reconnect monitor passed; reconnect waves observed="
        + str(reconnect_wave_count)
    )


if ENTRYPOINT_MODULE_NAME == "__main__":
    main()
