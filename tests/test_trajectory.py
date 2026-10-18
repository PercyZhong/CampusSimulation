"""Trajectory counts use each student's events in order, including rejected choices."""

import pytest

from campus_sim.analysis import (
    ACTION_NAMES,
    campus_environment_metrics,
    course_exposure_metrics,
    interaction_network_metrics,
    interaction_records,
    support_recovery_metrics,
    support_window_metrics,
    trajectory_metrics,
    trajectory_assessment,
    trajectory_profiles,
)


def event(ordinal, day, actor, action, status="completed", detail=None):
    return {"ordinal": ordinal, "day": day, "actor": actor, "action": action,
            "status": status, "detail": detail or {}}


def test_rule_paths_and_repeated_peer_relationships():
    events = [event(1, 1, "s000", "attend"),
              event(2, 1, "s001", "rest"),
              event(3, 2, "s000", "peer", detail={"partner": "s001"}),
              event(4, 2, "s001", "peer", detail={"partner": "s000"}),
              event(5, 3, "s000", "peer", detail={"partner": "s001"}),
              event(6, 3, "s001", "request", "rejected")]
    result = trajectory_metrics(events)
    matrix = result["trajectory_transition_matrix"]
    assert matrix["attend"]["peer"] == 1
    assert matrix["rest"]["peer"] == 1
    assert matrix["peer"]["peer"] == 1
    assert matrix["peer"]["request"] == 1
    assert sum(sum(row.values()) for row in matrix.values()) == 4
    assert result["trajectory_action_transitions"] == 3
    assert result["trajectory_longest_same_action_streak"] == 2
    assert result["trajectory_observed_students"] == 2
    assert result["peer_relationships_unique"] == 1
    assert result["peer_repeat_contacts"] == 2
    assert result["peer_repeat_contact_rate"] == 2 / 3


def test_llm_paths_use_decisions_and_empty_peer_rate_is_undefined():
    events = [event(1, 1, "s000", "model_decision", detail={"action": "rest"}),
              event(2, 1, "s000", "rest"),
              event(3, 2, "s000", "model_decision", detail={"action": "request"}),
              event(4, 2, "s000", "request", "rejected"),
              event(5, 1, "s001", "model_decision", detail={"action": "attend"})]
    result = trajectory_metrics(events)
    assert result["trajectory_transition_matrix"]["rest"]["request"] == 1
    assert result["trajectory_action_transitions"] == 1
    assert result["trajectory_observed_students"] == 2
    assert result["peer_repeat_contact_rate"] is None
    assert set(result["trajectory_transition_matrix"]) == set(ACTION_NAMES)


def test_trajectory_profiles_separate_opportunity_contact_and_censoring():
    students = [{"id": f"s{number:03d}"} for number in range(6)]
    events = []
    ordinal = 0
    for day in range(1, 8):
        for actor in ("s000", "s001", "s002", "s003", "s005"):
            ordinal += 1
            events.append(event(ordinal, day, actor, "student_state"))
    for day in (1, 2, 3):
        ordinal += 1
        events.append(event(ordinal, day, "s000", "course_absence"))
    ordinal += 1
    events.append(event(ordinal, 1, "s001", "course_absence"))
    for day in (2, 4):
        ordinal += 1
        events.append(event(ordinal, day, "s001", "request"))
    ordinal += 1
    events.append(event(ordinal, 3, "s002", "peer_invitation", detail={
        "partner": "s003"}))
    ordinal += 1
    events.append(event(ordinal, 5, "s003", "peer", detail={"partner": "s004"}))
    profiles = trajectory_profiles(events, students, complete_step=7)
    rows = profiles["by_student"]
    assert profiles["eligible_students"] == 5
    assert rows["s000"]["trajectory_type"] == "persistent_absence"
    assert rows["s000"]["isolation_status"] == "no_contact_despite_opportunity"
    assert rows["s001"]["trajectory_type"] == "repeated_help_attempts"
    assert rows["s002"]["trajectory_type"] == "no_social_contact"
    assert rows["s002"]["social_opportunity_days"] == 1
    assert rows["s003"]["isolation_status"] == "contact_observed"
    assert rows["s004"]["trajectory_type"] == "insufficient_observation"
    assert rows["s005"]["isolation_status"] == "no_observed_opportunity"
    assert trajectory_profiles(events, students, complete_step=6)[
        "eligible_students"] == 0


def test_trajectory_assessment_adds_daily_paths_and_student_counts():
    students = [{"id": "s000"}, {"id": "s001"}]
    events = [event(1, 1, "s000", "student_state"),
              event(2, 1, "s001", "student_state"),
              event(3, 1, "s000", "rest"),
              event(4, 2, "s000", "peer", detail={"partner": "s001"})]
    result = trajectory_assessment(events, students, complete_step=2, window_days=2)
    assert result["daily_action_counts"] == [
        {"day": 1, "attend": 0, "rest": 1, "peer": 0, "request": 0,
         "accept_support": 0},
        {"day": 2, "attend": 0, "rest": 0, "peer": 1, "request": 0,
         "accept_support": 0}]
    assert result["students"]["s000"]["action_days"] == 2
    assert result["students"]["s000"]["action_counts"] == {"rest": 1, "peer": 1}


def test_interaction_network_tracks_new_edges_and_repeated_contacts():
    events = [event(1, 1, "s000", "attend", detail={"classmate": "s001"}),
              event(2, 1, "s000", "study_group",
                    detail={"participants": ["s000", "s001", "s002"]}),
              event(3, 2, "s001", "peer", detail={"partner": "s000"}),
              event(4, 2, "s002", "peer", "rejected", {"partner": "s003"})]
    result = interaction_network_metrics(events)
    assert result["interaction_network_unique_edges"] == 3
    assert result["interaction_network_edges_by_source"] == {
        "peer": 1, "class_contact": 1, "study_group_copresence": 3,
        "residence_copresence": 0, "club_copresence": 0}
    assert result["interaction_network_contacts_by_source"] == {
        "peer": 1, "class_contact": 1, "study_group_copresence": 3,
        "residence_copresence": 0, "club_copresence": 0}
    assert result["interaction_network_daily_change"] == [
        {"day": 1, "contacts": 4, "new_edges": 3, "repeat_contacts": 1},
        {"day": 2, "contacts": 1, "new_edges": 0, "repeat_contacts": 1}]


def test_interaction_network_without_contacts_has_no_edges():
    result = interaction_network_metrics([event(1, 1, "s000", "rest")])
    assert result["interaction_network_unique_edges"] == 0
    assert result["interaction_network_daily_change"] == []


def test_residence_copresence_records_only_observed_roommate_pairs():
    events = [event(1, 1, "s000", "rest", detail={
                  "trigger": "shared_residence_presence_v1",
                  "context": {"venue": "residence-00"}, "roommates": []}),
              event(2, 1, "s001", "rest", detail={
                  "trigger": "shared_residence_presence_v1",
                  "context": {"venue": "residence-00"}, "roommates": ["s000"]}),
              event(3, 1, "s003", "rest", detail={
                  "trigger": "shared_residence_presence_v1",
                  "context": {"venue": "residence-01"}, "roommates": []})]
    network = interaction_network_metrics(events)
    assert network["interaction_network_edges_by_source"]["residence_copresence"] == 1
    assert network["interaction_network_contacts_by_source"]["residence_copresence"] == 1
    records = interaction_records(events)
    assert [record["participants"] for record in records] == [
        ["s000"], ["s001", "s000"], ["s003"]]
    assert records[1]["venue"] == "residence-00"
    assert records[1]["response"] == "not_recorded"


def test_club_copresence_records_prior_same_club_arrivals():
    events = [event(1, 1, "s000", "attend", detail={
                  "club_context": {"club_id": "club-00", "venue": "student_center-00"}}),
              event(2, 1, "s001", "attend", detail={
                  "club_context": {"club_id": "club-00", "venue": "student_center-00"}}),
              event(3, 1, "s001", "club_copresence", detail={
                  "participants": ["s000", "s001"],
                  "context": {"club_id": "club-00", "venue": "student_center-00"},
                  "trigger": "same_club_prior_attendance_v1"})]
    network = interaction_network_metrics(events)
    assert network["interaction_network_edges_by_source"]["club_copresence"] == 1
    records = interaction_records(events)
    assert records[-1]["kind"] == "club_copresence"
    assert records[-1]["response"] == "not_recorded"


def test_support_windows_separate_complete_observation_from_censoring():
    events = [event(1, 1, "s000", "attend"),
              event(2, 2, "s000", "peer", detail={"partner": "s001"}),
              event(3, 3, "s000", "rest"),
              event(4, 4, "s000", "request"),
              event(5, 4, "s001", "request"),
              event(6, 5, "s000", "accept_support"),
              event(7, 6, "s000", "attend"),
              event(8, 7, "s000", "peer", detail={"partner": "s001"}),
              event(9, 8, "s000", "rest"),
              event(10, 9, "s001", "accept_support")]
    result = support_window_metrics(events, complete_step=9)
    assert result["window_days"] == 3
    assert result["request_before_eligible_students"] == 2
    assert result["request_before_action_counts"]["peer"] == 1
    assert result["request_after_eligible_students"] == 2
    assert result["request_after_action_counts"]["accept_support"] == 1
    assert result["support_after_eligible_students"] == 1
    assert result["support_after_action_counts"] == {
        "attend": 1, "rest": 1, "peer": 1, "request": 0, "accept_support": 0}
    assert result["support_after_censored_students"] == 1


def test_support_windows_use_llm_decisions_for_rejected_choices():
    events = [event(1, 1, "s000", "request"),
              event(2, 2, "s000", "model_decision", detail={"action": "accept_support"}),
              event(3, 2, "s000", "accept_support", "rejected")]
    result = support_window_metrics(events, complete_step=4, width=1)
    assert result["request_after_action_counts"]["accept_support"] == 1
    assert result["support_after_eligible_students"] == 0


def test_course_exposure_counts_recorded_attendance_contexts():
    events = [event(1, 1, "s000", "attend", detail={
                  "context": {"course_id": "course-100", "venue": "building-1"}}),
              event(2, 2, "s000", "attend", detail={
                  "context": {"course_id": "course-101", "venue": "building-1"}}),
              event(3, 2, "s001", "attend", detail={
                  "context": {"course_id": "course-101", "venue": "building-1"}}),
              event(4, 3, "s001", "attend"),
              event(5, 3, "s000", "attend", "rejected")]
    result = course_exposure_metrics(events)
    assert result["attendance_events"] == 4
    assert result["context_recorded_events"] == 3
    assert result["context_missing_events"] == 1
    assert result["unique_courses"] == 2
    assert result["unique_venues"] == 1
    assert result["students_with_attendance"] == 2
    assert result["students_with_recorded_context"] == 2
    assert result["by_student"]["s000"] == {
        "attendance_days": 2, "recorded_contexts": 2,
        "unique_courses": 2, "unique_venues": 1}
    assert result["by_student"]["s001"]["attendance_days"] == 2


def test_course_exposure_distinguishes_missing_context_from_zero_attendance():
    missing = course_exposure_metrics([event(1, 1, "s000", "attend")])
    empty = course_exposure_metrics([event(1, 1, "s000", "rest")])
    assert missing["unique_courses"] is None
    assert missing["context_missing_events"] == 1
    assert empty["attendance_events"] == 0
    assert empty["context_missing_events"] == 0


def test_environment_metrics_count_observed_absences_and_rejections():
    events = [event(1, 1, "s000", "attend", detail={
                  "context": {"venue": "building-1"}}),
              event(2, 1, "s001", "attend", "rejected", detail={
                  "context": {"venue": "building-1"}}),
              event(3, 1, "s001", "course_absence", detail={
                  "missed_course_streak": 1})]
    events[1]["reason"] = "capacity_full"
    result = campus_environment_metrics(events)
    assert result["course_attendance_events"] == 1
    assert result["course_rejection_reasons"] == {"capacity_full": 1}
    assert result["missed_scheduled_courses"] == 1
    assert result["venue_usage"] == {"building-1": 1}


def test_environment_metrics_reject_overbooked_course():
    schedule = {"course_id": "course-100", "venue": "building-1", "slot": "morning",
                "open": True, "capacity": 1}
    events = [event(1, 1, "s000", "attend", detail={"schedule": schedule}),
              event(2, 1, "s001", "attend", detail={"schedule": schedule})]
    with pytest.raises(ValueError, match="capacity"):
        campus_environment_metrics(events)


def test_support_recovery_uses_student_snapshots_and_tracks_missing_data():
    events = [event(1, 2, "s000", "accept_support"),
              event(2, 2, "s000", "student_state", detail={
                  "phase": "end_of_day_v1", "stress": 0.5, "mood": 0.4,
                  "connection": 0.3, "supported": True}),
              event(3, 5, "s000", "student_state", detail={
                  "phase": "end_of_day_v1", "stress": 0.3, "mood": 0.6,
                  "connection": 0.4, "supported": True}),
              event(4, 2, "s001", "accept_support"),
              event(5, 6, "s002", "accept_support")]
    result = support_recovery_metrics(events, complete_step=6)
    assert result["supported_students"] == 3
    assert result["observed_students"] == 1
    assert result["missing_snapshot_students"] == 1
    assert result["censored_students"] == 1
    assert round(result["mean_change"]["stress"], 6) == -0.2
    assert round(result["mean_change"]["mood"], 6) == 0.2
    assert round(result["mean_change"]["connection"], 6) == 0.1


def test_support_recovery_old_events_report_missing_snapshots():
    result = support_recovery_metrics(
        [event(1, 2, "s000", "accept_support")], complete_step=6)
    assert result["observed_students"] == 0
    assert result["missing_snapshot_students"] == 1
    assert result["mean_change"] == {
        "stress": None, "mood": None, "connection": None}


def test_interaction_records_separate_dialogue_copresence_and_service():
    events = [event(1, 1, "s000", "peer", detail={"partner": "s001"}),
              event(2, 1, "s002", "peer", "rejected"),
              event(3, 1, "s000", "attend", detail={
                  "classmate": "s001", "context": {"venue": "building-1"}}),
              event(4, 1, "s002", "study_group", detail={
                  "participants": ["s000", "s001", "s002"],
                  "context": {"venue": "building-1"}}),
              event(5, 2, "s001", "accept_support",
                    detail={"staff_id": "c000"}),
              event(6, 3, "s001", "staff_follow_up",
                    detail={"staff_id": "c000"}),
              event(7, 3, "s001", "student_state")]
    events[1]["reason"] = "no_peer_edge"
    records = interaction_records(events)
    assert len(records) == 6
    assert records[0]["participants"] == ["s000", "s001"]
    assert records[0]["response"] == "dialogue_recorded"
    assert records[1]["response"] == "unavailable"
    assert records[1]["outcome"] == "no_peer_edge"
    assert records[2]["venue"] == "building-1"
    assert records[2]["response"] == "not_recorded"
    assert records[3]["outcome"] == "copresent"
    assert records[4]["participants"] == ["s001", "c000"]
    assert records[5]["participants"] == ["c000", "s001"]
    assert records[5]["response"] == "not_recorded"


def test_interaction_records_preserve_legacy_service_endpoint():
    records = interaction_records([event(1, 1, "s000", "accept_support")])
    assert records[0]["participants"] == ["s000", "service:main"]


def test_peer_record_preserves_partner_response_metadata():
    records = interaction_records([event(1, 1, "s000", "peer", detail={
        "partner": "s001", "partner_response": "accepted",
        "response_basis": "partner_edge_and_dialogue_completed_v1"})])
    assert records[0]["partner_response"] == "accepted"
    assert records[0]["response_basis"] == "partner_edge_and_dialogue_completed_v1"
