import json

import pytest

from scheduler.agent import MAX_MODEL_RESPONSE_CHARS, SchedulingAgent
from scheduler.model import (
    FakeModel,
    final,
    recommendation,
    recommendations,
    tool_call,
)
from scheduler.store import Store


def test_agent_returns_final_answer():
    model = FakeModel([final("Happy to help!")])
    agent = SchedulingAgent(model, Store.load())
    assert agent.run("hello") == "Happy to help!"


def test_agent_rejects_model_response_just_over_size_limit():
    model = FakeModel([" " * (MAX_MODEL_RESPONSE_CHARS + 1)])

    answer = SchedulingAgent(model, Store.load()).run("hello")

    assert answer == "I couldn't process the model response because it was too large."
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        OSError("connection failed"),
        ConnectionError("connection refused"),
        TimeoutError("request timed out"),
    ],
)
def test_agent_handles_transport_errors_from_model(error):
    class FailingModel:
        def complete(self, _messages):
            del _messages
            raise error

    answer = SchedulingAgent(FailingModel(), Store.load()).run("hello")

    assert answer == f"I couldn't get a valid response from the model: {error}"


def test_agent_calls_tool_and_returns_result_to_model():
    model = FakeModel([
        tool_call("find_free_slots", attendees=["priya", "sam"],
                  date="2026-11-03", duration_minutes=60, earliest="12:00"),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            start="13:00",
            end="14:00",
        ),
    ])
    agent = SchedulingAgent(model, Store.load())

    answer = agent.run("Find an hour for Priya and Sam on Tuesday afternoon")

    assert answer == (
        "Priya Raman and Sam Okafor can meet on 2026-11-03 "
        "from 13:00 to 14:00 (60 minutes)."
    )
    second_call = model.calls[1]
    assert second_call[-1]["role"] == "tool"
    assert {"start": "13:00", "end": "14:00"} in json.loads(second_call[-1]["content"])


def test_agent_rejects_tuesday_demo_search_for_friday_request():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            earliest="12:00",
        )
    ])
    result = SchedulingAgent(model, Store.load()).run(
        "Find an hour for Priya and Sam on Friday afternoon"
    )
    assert result == (
        "I couldn't run the requested tool: the requested weekday (Friday) "
        "does not match the search date 2026-11-03 (Tuesday)."
    )
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    ("user_request", "search_date", "start", "end"),
    [
        (
            "Find 45 minutes with Priya and Sam next Tuesday afternoon",
            "2026-11-03",
            "13:00",
            "13:45",
        ),
        (
            "Find 45 minutes with Priya and Sam Tuesday afternoon",
            "2026-11-03",
            "13:00",
            "13:45",
        ),
        (
            "Find 45 minutes with Priya and Sam Monday afternoon",
            "2026-11-02",
            "12:00",
            "12:45",
        ),
        (
            "Find 45 minutes with Priya and Sam next Wednesday afternoon",
            "2026-11-04",
            "13:00",
            "13:45",
        ),
    ],
)
def test_agent_matches_weekday_names_without_guessing_week_offsets(
    user_request, search_date, start, end
):
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date=search_date,
            duration_minutes=45,
            earliest="12:00",
        ),
        recommendation(
            attendees=["priya", "sam"],
            date=search_date,
            duration_minutes=45,
            start=start,
            end=end,
        ),
    ])

    answer = SchedulingAgent(model, Store.load()).run(user_request)

    assert f"on {search_date} from {start} to {end}" in answer
    assert "does not match the search date" not in answer


@pytest.mark.parametrize(
    ("user_request", "search_date"),
    [
        ("Find 30 minutes with Priya before Wednesday", "2026-11-02"),
        ("Find 30 minutes with Priya after Monday", "2026-11-03"),
        ("Find 30 minutes with Priya by Wednesday", "2026-11-03"),
        ("Find 30 minutes with Priya until Wednesday", "2026-11-03"),
        ("Find 30 minutes with Priya Monday through Wednesday", "2026-11-03"),
        ("Find 30 minutes with Priya Monday to Wednesday", "2026-11-03"),
        ("Find 30 minutes with Priya between Monday and Wednesday", "2026-11-03"),
        (
            "Find 30 minutes with Priya on Tuesday, after Monday standup",
            "2026-11-03",
        ),
    ],
)
def test_agent_rejects_unsupported_or_ambiguous_weekday_ranges(
    user_request, search_date
):
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya"],
            date=search_date,
            duration_minutes=30,
        ),
    ])

    answer = SchedulingAgent(model, Store.load()).run(user_request)

    assert answer == (
        "I couldn't run the requested tool: weekday ranges are not supported; "
        "please specify one weekday."
    )
    assert len(model.calls) == 1


def test_agent_accepts_demo_search_for_matching_tuesday_request():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            earliest="12:00",
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            start="13:00",
            end="14:00",
        ),
    ])
    result = SchedulingAgent(model, Store.load()).run(
        "Find an hour for Priya and Sam on Tuesday afternoon"
    )
    assert result == (
        "Priya Raman and Sam Okafor can meet on 2026-11-03 "
        "from 13:00 to 14:00 (60 minutes)."
    )

def test_agent_reports_unknown_attendee_without_proposing_a_time():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "ghost"],
            date="2026-11-03",
            duration_minutes=30,
        )
    ])

    answer = SchedulingAgent(model, Store.load()).run(
        "Find a time with Priya and Ghost"
    )

    assert answer == (
        "I couldn't find these people in the employee directory: ghost."
    )
    assert len(model.calls) == 1


def test_agent_rejects_search_that_omits_unknown_requested_attendee():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya"],
            date="2026-11-03",
            duration_minutes=30,
        )
    ])
    answer = SchedulingAgent(model, Store.load()).run(
        "Find 30 minutes with Priya and Ghost"
    )
    assert answer == (
        "I couldn't find these people in the employee directory: Ghost."
    )
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    ("user_request", "attendees"),
    [
        ("Find 30 minutes between Priya and Bob", ["priya"]),
        (
            "Schedule Priya, Sam and Zed for 30 minutes on Tuesday",
            ["priya", "sam"],
        ),
    ],
)
def test_agent_reports_unknown_attendees_in_multi_person_requests(
    user_request, attendees
):
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=attendees,
            date="2026-11-03",
            duration_minutes=30,
        )
    ])

    answer = SchedulingAgent(model, Store.load()).run(user_request)

    assert "employee directory" in answer
    assert answer.endswith("Bob." if "Bob" in user_request else "Zed.")
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        '{"x":' + "[" * 5000 + "0" + "]" * 5000 + "}",
        '{"value":' + "9" * 5000 + "}",
    ],
)
def test_agent_rejects_json_parser_edge_cases(response):
    answer = SchedulingAgent(FakeModel([response]), Store.load()).run("hello")
    assert answer == (
        "I couldn't process the model response, so I stopped without making a recommendation."
    )


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "[]",
        "null",
        '{"final":"ok","tool":"list_people","args":{}}',
        '{"unexpected":"value"}',
        '{"final":42}',
        '{"final":"ok","final":"not ok"}',
    ],
)
def test_agent_rejects_malformed_or_unsupported_model_actions(response):
    answer = SchedulingAgent(FakeModel([response]), Store.load()).run("hello")
    assert answer == (
        "I couldn't process the model response, so I stopped without making a recommendation."
    )


def test_agent_rejects_unknown_tool():
    model = FakeModel(['{"tool":"delete_everything","args":{}}'])
    answer = SchedulingAgent(model, Store.load()).run("hello")
    assert answer == "I couldn't run the requested tool because its name is not recognized."


def test_agent_reports_date_outside_calendar_coverage():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2027-01-01",
            duration_minutes=30,
        )
    ])
    answer = SchedulingAgent(model, Store.load()).run("Find time next year")
    assert answer == "No calendar data is available for 2027-01-01."


@pytest.mark.parametrize(
    "response",
    [
        '{"tool":"find_free_slots","args":[]}',
        '{"tool":"find_free_slots","args":{"attendees":["priya"],"date":"2026-11-03"}}',
        '{"tool":"find_free_slots","args":{"attendees":"priya","date":"2026-11-03","duration_minutes":30}}',
        '{"tool":"get_busy_times","args":{"person":"sam","date":"tomorrow"}}',
        '{"tool":"list_people","args":{"unexpected":true}}',
    ],
)
def test_agent_rejects_invalid_tool_arguments(response):
    answer = SchedulingAgent(FakeModel([response]), Store.load()).run("hello")
    assert answer.startswith("I couldn't run the requested tool:")


def test_agent_rejects_final_time_not_supported_by_search_results():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=45,
            earliest="12:00",
        ),
        final("They are free from 13:00 to 14:00."),
    ])
    answer = SchedulingAgent(model, Store.load()).run(
        "Find 45 minutes with Priya and Sam Tuesday afternoon"
    )
    assert answer == (
        "I can't confirm that availability from the calendar results, "
        "so I won't recommend that time."
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Dana is free from 13:00 to 13:45.",
        "Priya and Sam are free all day.",
        "Priya and Sam can meet on Wednesday from 13:00 to 13:45.",
        "Priya and Sam can meet today from 13:00 to 13:45.",
        "Priya and Sam can meet from 13:00 to 14:00 for 45 minutes.",
        "Priya and Sam are free November 99 from 13:00 to 13:45.",
    ],
)
def test_agent_rejects_free_form_availability_claims(answer):
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=45,
            earliest="12:00",
        ),
        final(answer),
    ])
    result = SchedulingAgent(model, Store.load()).run(
        "Find 45 minutes with Priya and Sam Tuesday afternoon"
    )
    assert result == (
        "I can't confirm that availability from the calendar results, "
        "so I won't recommend that time."
    )


def test_agent_renders_validated_recommendation_deterministically():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=45,
            earliest="12:00",
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=45,
            start="13:00",
            end="13:45",
        ),
    ])
    result = SchedulingAgent(model, Store.load()).run(
        "Find 45 minutes with Priya and Sam Tuesday afternoon"
    )
    assert result == (
        "Priya Raman and Sam Okafor can meet on 2026-11-03 "
        "from 13:00 to 13:45 (45 minutes)."
    )


def test_agent_renders_starter_style_final_when_time_is_grounded():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            earliest="12:00",
        ),
        final("13:00 works for both."),
    ])
    assert SchedulingAgent(model, Store.load()).run(
        "Find an hour for Priya and Sam on Tuesday afternoon"
    ) == (
        "Priya Raman and Sam Okafor can meet on 2026-11-03 "
        "from 13:00 to 14:00 (60 minutes)."
    )


def test_agent_rejects_recommendation_with_invented_attendee():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=45,
            earliest="12:00",
        ),
        recommendation(
            attendees=["priya", "sam", "ghost"],
            date="2026-11-03",
            duration_minutes=45,
            start="13:00",
            end="13:45",
        ),
    ])
    answer = SchedulingAgent(model, Store.load()).run(
        "Find 45 minutes with Priya and Sam Tuesday afternoon"
    )
    assert answer == (
        "I can't confirm that availability from the calendar results, "
        "so I won't recommend that time."
    )


def test_agent_rejects_valid_but_wrong_recommendation_date():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=45,
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-04",
            duration_minutes=45,
            start="13:00",
            end="13:45",
        ),
    ])
    answer = SchedulingAgent(model, Store.load()).run(
        "Find 45 minutes with Priya and Sam Tuesday"
    )
    assert answer == (
        "I can't confirm that availability from the calendar results, "
        "so I won't recommend that time."
    )


def test_agent_rejects_malformed_date_in_structured_recommendation():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=45,
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-99-99",
            duration_minutes=45,
            start="13:00",
            end="13:45",
        ),
    ])
    answer = SchedulingAgent(model, Store.load()).run(
        "Find 45 minutes with Priya and Sam Tuesday"
    )
    assert answer == (
        "I can't confirm that availability from the calendar results, "
        "so I won't recommend that time."
    )


@pytest.mark.parametrize(
    ("user_request", "attendees", "duration"),
    [
        ("Find an hour with Priya and Sam", ["priya", "sam"], 45),
        ("Find 45 minutes with Priya and Sam", ["priya", "dana"], 45),
    ],
)
def test_agent_rejects_search_that_conflicts_with_explicit_request(
    user_request, attendees, duration
):
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=attendees,
            date="2026-11-03",
            duration_minutes=duration,
        )
    ])
    result = SchedulingAgent(model, Store.load()).run(user_request)
    assert result.startswith("I couldn't run the requested tool:")
    assert len(model.calls) == 1


def test_agent_warns_when_recommendation_uses_missing_calendar_entry():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["mei"],
            date="2026-11-04",
            duration_minutes=30,
        ),
        recommendation(
            attendees=["mei"],
            date="2026-11-04",
            duration_minutes=30,
            start="09:00",
            end="09:30",
        ),
    ])
    answer = SchedulingAgent(model, Store.load()).run(
        "Find 30 minutes with Mei on Wednesday"
    )
    assert answer == (
        "Mei Tanaka can meet on 2026-11-04 from 09:00 to 09:30 (30 minutes). "
        "Note: No calendar entry was recorded for Mei Tanaka on this date, so "
        "this result assumes no busy blocks were recorded; please confirm "
        "availability."
    )
    assert len(model.calls) == 2


@pytest.mark.parametrize(
    "user_request",
    [
        "Find 30 or 45 minutes with Priya and Sam",
        "Find half an hour with Priya for one hour",
    ],
)
def test_agent_rejects_conflicting_duration_constraints(user_request):
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=30,
        )
    ])
    result = SchedulingAgent(model, Store.load()).run(user_request)
    assert result == (
        "I couldn't run the requested tool: the request contains conflicting "
        "meeting durations; please clarify."
    )
    assert len(model.calls) == 1


def test_agent_rejects_ambiguous_attendee_choice():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=30,
        )
    ])
    result = SchedulingAgent(model, Store.load()).run(
        "Find 30 minutes with Priya or Sam"
    )
    assert result == (
        "I couldn't run the requested tool: the requested attendees are "
        "ambiguous; please clarify who should attend."
    )
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    "user_request",
    [
        "Find an hour with Priya and Sam ASAP",
        "Find an hour with Priya and Sam Nov 3",
        "Find an hour with Priya and Sam 3 Nov",
        "Find an hour with Priya and Sam Tue",
    ],
)
def test_agent_accepts_nonconflicting_date_and_attendee_qualifiers(user_request):
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            earliest="12:00",
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            start="13:00",
            end="14:00",
        ),
    ])
    answer = SchedulingAgent(model, Store.load()).run(user_request)
    assert "from 13:00 to 14:00" in answer


@pytest.mark.parametrize(
    "user_request",
    [
        "Find 1.5 hours with Priya and Sam on Tuesday",
        "Find an hour and a half with Priya and Sam on Tuesday",
    ],
)
def test_agent_recognizes_one_and_a_half_hour_durations(user_request):
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=90,
        ),
        final("No slots found."),
    ])

    answer = SchedulingAgent(model, Store.load()).run(user_request)

    assert "does not match the search duration" not in answer
    assert answer == (
        "No matching availability was found for Priya Raman and Sam Okafor "
        "on 2026-11-03 for 90 minutes."
    )


@pytest.mark.parametrize(
    ("user_request", "tool_date"),
    [
        ("Find an hour with Priya and Sam tomorrow", "2026-11-04"),
        ("Find an hour with Priya and Sam on Nov 3", "2026-11-04"),
        ("Find an hour with Priya and Sam on Tuesday or Wednesday", "2026-11-03"),
    ],
)
def test_agent_rejects_conflicting_or_ambiguous_dates(user_request, tool_date):
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date=tool_date,
            duration_minutes=60,
        )
    ])
    answer = SchedulingAgent(model, Store.load()).run(user_request)
    assert answer.startswith("I couldn't run the requested tool:")
    assert len(model.calls) == 1


def test_agent_does_not_treat_relative_availability_as_duration():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            earliest="12:00",
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            start="13:00",
            end="14:00",
        ),
    ])
    answer = SchedulingAgent(model, Store.load()).run(
        "Find an hour with Priya and Sam in the next 2 hours"
    )
    assert "from 13:00 to 14:00" in answer


def test_agent_treats_null_optional_bounds_as_omitted():
    model = FakeModel([
        '{"tool":"find_free_slots","args":{"attendees":["priya","sam"],'
        '"date":"2026-11-03","duration_minutes":60,"earliest":null,"latest":null}}',
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            start="13:00",
            end="14:00",
        ),
    ])

    answer = SchedulingAgent(model, Store.load()).run(
        "Find an hour with Priya and Sam on Tuesday"
    )

    assert "from 13:00 to 14:00" in answer


def test_agent_rejects_default_one_hour_demo_for_45_minute_request():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
        )
    ])

    answer = SchedulingAgent(model, Store.load()).run(
        "Find 45 minutes with Priya and Sam on Tuesday"
    )

    assert answer == (
        "I couldn't run the requested tool: the requested meeting duration "
        "does not match the search duration."
    )
    assert len(model.calls) == 1


def test_agent_rejects_recommendation_outside_requested_time_of_day():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=45,
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=45,
            start="13:00",
            end="13:45",
        ),
    ])
    answer = SchedulingAgent(model, Store.load()).run(
        "Find 45 minutes with Priya and Sam Tuesday afternoon"
    )
    assert "from 13:00 to 13:45" in answer
    tool_result = json.loads(model.calls[1][-1]["content"])
    assert all(window["start"] >= "12:00" for window in tool_result)


@pytest.mark.parametrize(
    ("user_request", "earliest", "latest", "message"),
    [
        (
            "Find 30 minutes with Priya and Sam Tuesday morning",
            "13:00",
            None,
            "requested morning period does not match the search bounds",
        ),
        (
            "Find 30 minutes with Priya and Sam Tuesday afternoon",
            None,
            "11:00",
            "requested afternoon period does not match the search bounds",
        ),
    ],
)
def test_agent_rejects_search_bounds_that_conflict_with_requested_period(
    user_request, earliest, latest, message
):
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=30,
            earliest=earliest,
            latest=latest,
        )
    ])

    answer = SchedulingAgent(model, Store.load()).run(user_request)

    assert message in answer
    assert len(model.calls) == 1


def test_agent_returns_deterministic_message_when_no_slots_exist():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=30,
            earliest="10:00",
            latest="10:30",
        ),
        final("I could not find a time."),
    ])
    answer = SchedulingAgent(model, Store.load()).run(
        "Find 30 minutes with Priya and Sam Tuesday morning"
    )
    assert answer == (
        "No matching availability was found for Priya Raman and Sam Okafor "
        "in the morning on 2026-11-03 for 30 minutes."
    )
    assert len(model.calls) == 2


def test_empty_first_search_continues_to_later_search_with_availability():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=30,
            earliest="10:00",
            latest="10:00",
        ),
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-02",
            duration_minutes=30,
            earliest="12:00",
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-02",
            duration_minutes=30,
            start="12:00",
            end="12:30",
        ),
    ])

    answer = SchedulingAgent(model, Store.load()).run(
        "Find 30 minutes with Priya and Sam"
    )

    assert "on 2026-11-02 from 12:00 to 12:30" in answer
    assert len(model.calls) == 3


def test_empty_search_does_not_allow_fallback_outside_requested_weekday():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=30,
            earliest="10:00",
            latest="10:00",
        ),
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-02",
            duration_minutes=30,
            earliest="12:00",
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-02",
            duration_minutes=30,
            start="12:00",
            end="12:30",
        ),
    ])

    answer = SchedulingAgent(model, Store.load()).run(
        "Find 30 minutes with Priya and Sam next Tuesday"
    )

    assert answer == (
        "I couldn't run the requested tool: the requested weekday (Tuesday) "
        "does not match the search date 2026-11-02 (Monday)."
    )
    assert len(model.calls) == 2


def test_all_empty_searches_return_deterministic_no_availability():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=30,
            earliest="10:00",
            latest="10:00",
        ),
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-02",
            duration_minutes=30,
            earliest="10:00",
            latest="10:00",
        ),
        final("No slots are available."),
    ])

    answer = SchedulingAgent(model, Store.load()).run(
        "Find 30 minutes with Priya and Sam"
    )

    assert answer == (
        "No matching availability was found for Priya Raman and Sam Okafor "
        "on the searched dates (2026-11-02, 2026-11-03) for 30 minutes."
    )
    assert len(model.calls) == 3


def test_empty_later_search_keeps_earlier_matching_results():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-02",
            duration_minutes=30,
            earliest="12:00",
        ),
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=30,
            earliest="10:00",
            latest="10:00",
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-02",
            duration_minutes=30,
            start="12:00",
            end="12:30",
        ),
    ])

    answer = SchedulingAgent(model, Store.load()).run(
        "Find 30 minutes with Priya and Sam"
    )

    assert "on 2026-11-02 from 12:00 to 12:30" in answer
    assert len(model.calls) == 3


def test_agent_renders_multiple_validated_options():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=45,
            earliest="12:00",
        ),
        recommendations([
            {
                "attendees": ["priya", "sam"],
                "date": "2026-11-03",
                "duration_minutes": 45,
                "start": "13:00",
                "end": "13:45",
            },
            {
                "attendees": ["priya", "sam"],
                "date": "2026-11-03",
                "duration_minutes": 45,
                "start": "13:15",
                "end": "14:00",
            },
        ]),
    ])
    answer = SchedulingAgent(model, Store.load()).run(
        "Find 45 minutes with Priya and Sam Tuesday afternoon"
    )
    assert answer.startswith("Available options:\n1.")
    assert "from 13:00 to 13:45" in answer
    assert "2. Priya Raman and Sam Okafor" in answer


def test_agent_joins_three_attendee_names_naturally():
    model = FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam", "dana"],
            date="2026-11-03",
            duration_minutes=30,
            earliest="12:00",
        ),
        recommendation(
            attendees=["priya", "sam", "dana"],
            date="2026-11-03",
            duration_minutes=30,
            start="13:00",
            end="13:30",
        ),
    ])

    answer = SchedulingAgent(model, Store.load()).run(
        "Find 30 minutes with Priya, Sam and Dana on Tuesday afternoon"
    )

    assert answer.startswith(
        "Priya Raman, Sam Okafor, and Dana Whitfield can meet"
    )


@pytest.mark.parametrize("response", [None, [], 42, {}])
def test_agent_handles_non_string_model_responses(response):
    received_messages = []

    class NonStringModel:
        def complete(self, messages):
            received_messages.append(messages)
            return response

    result = SchedulingAgent(NonStringModel(), Store.load()).run("hello")
    assert result == (
        "I couldn't process the model response, so I stopped without making a recommendation."
    )
    assert len(received_messages) == 1


def test_agent_stops_after_maximum_tool_calls():
    model = FakeModel(
        [tool_call("list_people")],
        loop_last=True,
    )
    answer = SchedulingAgent(model, Store.load()).run("Keep searching")
    assert answer == "I couldn't complete this request within 8 tool calls."
    assert len(model.calls) == 9


def test_system_prompt_includes_directory():
    model = FakeModel([final("ok")])
    SchedulingAgent(model, Store.load()).run("hi")
    system = model.calls[0][0]["content"]
    assert "Priya Raman" in system
    assert "priya.raman@northwind.example" not in system
    assert "+1-415-555-01" not in system
