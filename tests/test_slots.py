import pytest

from scheduler.slots import (
    SLOT_MINUTES,
    SLOTS_PER_DAY,
    availability_from_busy,
    combine_availability,
    find_free_windows,
    slot_to_time,
    time_to_slot,
)


def test_slot_to_time():
    assert slot_to_time(0) == "09:00"
    assert slot_to_time(6) == "10:30"


def test_time_to_slot():
    assert time_to_slot("09:00") == 0
    assert time_to_slot("10:45") == 7


def test_availability_marks_partial_overlap_busy():
    free = availability_from_busy([{"start": "10:00", "end": "10:45"}])
    assert free[4:7] == [False, False, False]
    assert free[7] is True


def test_availability_rounds_partial_quarter_hour_overlap_outward():
    free = availability_from_busy([{"start": "10:07", "end": "10:22"}])
    assert free[3:6] == [True, False, False]
    assert free[6] is True


def test_combine_availability():
    a = [True, True, False, True]
    b = [True, False, True, True]
    assert combine_availability([a, b])[:4] == [True, False, False, True]


def test_find_free_windows():
    free = [False, True, True, True, False, True] + [False] * 10
    assert find_free_windows(free, 2) == [1, 2]


def test_find_free_windows_includes_last_window_that_ends_at_day_boundary():
    starts = find_free_windows([True] * SLOTS_PER_DAY, 2)
    assert starts[-1] == SLOTS_PER_DAY - 2


@pytest.mark.parametrize("slots_needed", [0, -1, True])
def test_find_free_windows_rejects_invalid_duration(slots_needed):
    with pytest.raises(ValueError, match="slots_needed"):
        find_free_windows([True] * SLOTS_PER_DAY, slots_needed)


def test_find_free_windows_rejects_invalid_bounds():
    with pytest.raises(ValueError, match="slot bounds"):
        find_free_windows([True] * SLOTS_PER_DAY, 1, start_slot=2, end_slot=1)


def test_time_to_minutes_rejects_malformed_or_invalid_clocks():
    from scheduler.slots import time_to_minutes

    for clock in ("9:00", "09:0", "24:00", "12:60"):
        with pytest.raises(ValueError):
            time_to_minutes(clock)


def test_slot_size_supports_quarter_hour_intervals():
    assert SLOT_MINUTES == 15
