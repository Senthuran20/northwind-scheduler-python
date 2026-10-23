import pytest

from scheduler.store import InvalidCalendarDataError, Store
from scheduler.tools import (
    AmbiguousPersonError,
    CalendarDateUnavailableError,
    UnknownPersonError,
    find_free_slots,
    get_busy_times,
    list_people,
)


def test_get_busy_times():
    store = Store.load()
    busy = get_busy_times(store, "sam", "2026-11-04")
    assert busy == [{"start": "09:00", "end": "09:30"}]


def test_tools_accept_unique_case_insensitive_name_forms():
    store = Store.load()
    assert get_busy_times(store, "SAM OKAFOR", "2026-11-04") == [
        {"start": "09:00", "end": "09:30"}
    ]
    assert find_free_slots(
        store,
        ["Priya", "Sam Okafor"],
        "2026-11-03",
        duration_minutes=60,
        earliest="12:00",
    )


def test_find_free_slots_resolves_first_name_before_checking_busy_blocks():
    store = Store.load()
    by_id = find_free_slots(
        store,
        ["priya"],
        "2026-11-03",
        duration_minutes=30,
    )
    by_first_name = find_free_slots(
        store,
        ["Priya"],
        "2026-11-03",
        duration_minutes=30,
    )

    assert by_first_name == by_id
    assert not any(
        window["start"] < "11:00" and window["end"] > "09:30"
        for window in by_first_name
    )


def test_find_free_slots_rejects_ambiguous_first_name():
    store = Store(
        people=[
            {"id": "alex_smith", "name": "Alex Smith"},
            {"id": "alex_jones", "name": "Alex Jones"},
        ],
        calendars={
            "alex_smith": {"2026-11-03": []},
            "alex_jones": {"2026-11-03": []},
        },
    )

    with pytest.raises(AmbiguousPersonError, match="matches multiple people"):
        find_free_slots(
            store,
            ["Alex"],
            "2026-11-03",
            duration_minutes=30,
        )


def test_list_people_exposes_only_identity_fields():
    people = list_people(Store.load())
    assert all(set(person) == {"id", "name"} for person in people)
    assert "phone" not in str(people)
    assert "email" not in str(people)


def test_get_busy_times_omits_private_event_titles():
    store = Store.load()
    busy = get_busy_times(store, "dana", "2026-11-03")

    assert busy == [{"start": "11:00", "end": "12:00"}]
    assert all(set(block) == {"start", "end"} for block in busy)
    assert "confidential" not in str(busy).lower()


def test_get_busy_times_rejects_person_outside_directory():
    with pytest.raises(UnknownPersonError, match="employee directory: ghost"):
        get_busy_times(Store.load(), "ghost", "2026-11-03")


def test_get_busy_times_rejects_date_outside_calendar_coverage():
    with pytest.raises(CalendarDateUnavailableError, match="No calendar data"):
        get_busy_times(Store.load(), "sam", "2027-01-01")


def test_missing_calendar_entry_means_no_busy_blocks_were_recorded():
    store = Store.load()
    assert "mei" in {person["id"] for person in store.people}
    assert "2026-11-04" not in store.calendars["mei"]

    assert get_busy_times(store, "mei", "2026-11-04") == []
    slots = find_free_slots(store, ["mei"], "2026-11-04", duration_minutes=8 * 60)
    assert slots == [{"start": "09:00", "end": "17:00"}]


def test_find_free_slots_uses_no_recorded_busy_blocks_for_missing_entry():
    slots = find_free_slots(
        Store.load(),
        ["priya", "mei"],
        "2026-11-04",
        duration_minutes=30,
    )
    assert slots


def test_find_free_slots_for_two_people_in_the_afternoon():
    store = Store.load()
    slots = find_free_slots(
        store,
        attendees=["priya", "sam"],
        date="2026-11-03",
        duration_minutes=30,
        earliest="13:00",
    )
    assert slots == [
        {"start": "13:00", "end": "13:30"},
        {"start": "13:15", "end": "13:45"},
        {"start": "13:30", "end": "14:00"},
        {"start": "15:00", "end": "15:30"},
        {"start": "16:00", "end": "16:30"},
        {"start": "16:15", "end": "16:45"},
        {"start": "16:30", "end": "17:00"},
    ]


def test_find_free_slots_hour_long_meeting():
    store = Store.load()
    slots = find_free_slots(
        store, attendees=["priya", "sam"], date="2026-11-03", duration_minutes=60
    )
    assert {"start": "13:00", "end": "14:00"} in slots


def test_find_free_slots_honors_45_minute_duration():
    slots = find_free_slots(
        Store.load(),
        attendees=["priya", "sam"],
        date="2026-11-03",
        duration_minutes=45,
    )
    assert {"start": "11:00", "end": "11:45"} in slots
    assert all(
        (int(slot["end"][:2]) * 60 + int(slot["end"][3:]))
        - (int(slot["start"][:2]) * 60 + int(slot["start"][3:]))
        == 45
        for slot in slots
    )


def test_find_free_slots_respects_latest_as_meeting_end():
    slots = find_free_slots(
        Store.load(),
        attendees=["priya", "sam"],
        date="2026-11-04",
        duration_minutes=30,
        earliest="09:30",
        latest="10:00",
    )
    assert slots == [{"start": "09:30", "end": "10:00"}]


@pytest.mark.parametrize(
    ("earliest", "latest", "expected"),
    [
        ("08:00", None, "09:00"),
        ("13:10", None, "13:15"),
        ("09:00", "13:10", "13:00"),
        ("09:00", "18:00", "17:00"),
    ],
)
def test_find_free_slots_clamps_or_rounds_time_bounds(earliest, latest, expected):
    store = Store(
        people=[{"id": "priya", "name": "Priya Raman"}],
        calendars={"priya": {"2026-11-03": []}},
    )
    slots = find_free_slots(
        store,
        ["priya"],
        "2026-11-03",
        duration_minutes=15,
        earliest=earliest,
        latest=latest,
    )
    if earliest == "09:00" and latest in {"13:10", "18:00"}:
        assert slots[-1]["end"] == expected
    else:
        assert slots[0]["start"] == expected


def test_meeting_can_end_exactly_when_busy_block_starts():
    store = Store(
        people=[{"id": "priya", "name": "Priya Raman"}],
        calendars={"priya": {"2026-11-03": [{"start": "10:00", "end": "10:30"}]}},
    )

    slots = find_free_slots(
        store,
        ["priya"],
        "2026-11-03",
        duration_minutes=30,
        earliest="09:30",
        latest="10:00",
    )

    assert slots == [{"start": "09:30", "end": "10:00"}]


def test_meeting_can_start_exactly_when_busy_block_ends():
    store = Store(
        people=[{"id": "priya", "name": "Priya Raman"}],
        calendars={"priya": {"2026-11-03": [{"start": "10:00", "end": "10:30"}]}},
    )

    slots = find_free_slots(
        store,
        ["priya"],
        "2026-11-03",
        duration_minutes=30,
        earliest="10:30",
        latest="11:00",
    )

    assert slots == [{"start": "10:30", "end": "11:00"}]


def test_find_free_slots_returns_no_results_when_duration_exceeds_working_day():
    slots = find_free_slots(
        Store.load(), ["priya"], "2026-11-03", duration_minutes=8 * 60 + 15
    )
    assert slots == []


def test_find_free_slots_rejects_attendees_outside_directory():
    with pytest.raises(UnknownPersonError, match="employee directory: ghost"):
        find_free_slots(
            Store.load(),
            ["priya", "ghost"],
            "2026-11-03",
            duration_minutes=30,
        )


def test_find_free_slots_rejects_empty_attendee_list():
    with pytest.raises(ValueError, match="at least one attendee"):
        find_free_slots(
            Store.load(), [], "2026-11-03", duration_minutes=30
        )


def test_find_free_slots_rejects_duplicate_attendee_ids():
    with pytest.raises(ValueError, match="must not contain duplicates"):
        find_free_slots(
            Store.load(), ["priya", "priya"], "2026-11-03", duration_minutes=30
        )


def test_find_free_slots_rejects_date_outside_calendar_coverage():
    with pytest.raises(CalendarDateUnavailableError, match="No calendar data"):
        find_free_slots(
            Store.load(), ["priya", "sam"], "2027-01-01", duration_minutes=30
        )


@pytest.mark.parametrize("duration", [0, -15, 20, 45.0, True])
def test_find_free_slots_rejects_invalid_duration(duration):
    with pytest.raises(ValueError, match="duration_minutes"):
        find_free_slots(
            Store.load(), ["priya"], "2026-11-03", duration_minutes=duration
        )


@pytest.mark.parametrize(
    ("earliest", "latest"),
    [
        ("11:00", "10:00"),
        ("not-a-time", None),
    ],
)
def test_find_free_slots_rejects_invalid_time_bounds(earliest, latest):
    with pytest.raises(ValueError):
        find_free_slots(
            Store.load(),
            ["priya"],
            "2026-11-03",
            duration_minutes=30,
            earliest=earliest,
            latest=latest,
        )


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ({"start": "09:00"}, "include start and end"),
        ({"start": "25:00", "end": "10:00"}, "invalid time"),
        ({"start": "10:00", "end": "10:00"}, "end after it starts"),
        ({"start": "11:00", "end": "10:00"}, "end after it starts"),
    ],
)
def test_store_rejects_malformed_busy_intervals(block, message):
    with pytest.raises(InvalidCalendarDataError, match=message):
        Store(
            people=[{"id": "priya", "name": "Priya Raman"}],
            calendars={"priya": {"2026-11-03": [block]}},
        )


def test_store_rejects_duplicate_directory_ids():
    with pytest.raises(InvalidCalendarDataError, match="duplicate person IDs"):
        Store(
            people=[
                {"id": "priya", "name": "Priya Raman"},
                {"id": "priya", "name": "Priya Raman"},
            ],
            calendars={},
        )


def test_store_rejects_empty_directory_name():
    with pytest.raises(InvalidCalendarDataError, match="non-empty name"):
        Store(people=[{"id": "priya", "name": ""}], calendars={})


def test_store_rejects_calendar_for_unknown_person():
    with pytest.raises(InvalidCalendarDataError, match="unknown person"):
        Store(
            people=[{"id": "priya", "name": "Priya Raman"}],
            calendars={"ghost": {}},
        )
