#!/usr/bin/env python3


def create_postgres_session_generation_state(initial_sessions):
    previous_sessions = set(initial_sessions)
    has_observed_sessions = False

    if previous_sessions:
        has_observed_sessions = True

    return {
        "previous_sessions": previous_sessions,
        "has_observed_sessions": has_observed_sessions,
        "waiting_for_reconnect": False,
    }


def observe_postgres_session_generation(state, current_sessions):
    previous_sessions = set(state["previous_sessions"])
    current_sessions = set(current_sessions)
    added_sessions = current_sessions - previous_sessions
    removed_sessions = previous_sessions - current_sessions
    shared_sessions = previous_sessions & current_sessions

    event = "steady"
    reconnect_wave = False

    if not state["has_observed_sessions"]:
        if current_sessions:
            state["has_observed_sessions"] = True
            event = "initial_connection_baseline"
        else:
            event = "waiting_for_initial_connection"

        state["previous_sessions"] = current_sessions
        return {
            "event": event,
            "reconnect_wave": reconnect_wave,
            "added_sessions": len(added_sessions),
            "removed_sessions": len(removed_sessions),
            "active_sessions": len(current_sessions),
        }

    if state["waiting_for_reconnect"]:
        if current_sessions:
            reconnect_wave = True
            state["waiting_for_reconnect"] = False
            event = "reconnect_after_full_disconnect"
        else:
            event = "fully_disconnected"

        state["previous_sessions"] = current_sessions
        return {
            "event": event,
            "reconnect_wave": reconnect_wave,
            "added_sessions": len(added_sessions),
            "removed_sessions": len(removed_sessions),
            "active_sessions": len(current_sessions),
        }

    if previous_sessions and not current_sessions:
        state["waiting_for_reconnect"] = True
        event = "full_disconnect"
    elif previous_sessions and current_sessions and not shared_sessions:
        reconnect_wave = True
        event = "session_generation_replaced"
    elif added_sessions and not removed_sessions:
        event = "pool_expansion"
    elif removed_sessions and not added_sessions:
        event = "pool_shrink"
    elif added_sessions and removed_sessions:
        event = "partial_pool_churn"

    state["previous_sessions"] = current_sessions

    return {
        "event": event,
        "reconnect_wave": reconnect_wave,
        "added_sessions": len(added_sessions),
        "removed_sessions": len(removed_sessions),
        "active_sessions": len(current_sessions),
    }
