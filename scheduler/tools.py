"""Tools the agent can call. Each tool takes the Store plus keyword arguments
and returns JSON-serialisable data."""

from .slots import (
    DAY_END_MINUTES,
    DAY_START_MINUTES,
    SLOT_MINUTES,
    availability_from_busy,
    combine_availability,
    find_free_windows,
    slot_to_time,
    time_to_minutes,
)
from .store import Store


class UnknownPersonError(ValueError):
    """Raised when a tool request names someone outside the employee directory."""


class CalendarDateUnavailableError(ValueError):
    """Raised when the sample calendars contain no data for a requested date."""


class AmbiguousPersonError(ValueError):
    """Raised when a directory name matches more than one person."""


def resolve_attendee_ids(store: Store, person_ids: list[str]) -> list[str]:
    people_by_alias: dict[str, set[str]] = {}
    for person in store.people:
        person_id = person["id"]
        aliases = {person_id, person["name"]}
        first_name = person["name"].split()[0]
        aliases.add(first_name)
        for alias in aliases:
            people_by_alias.setdefault(alias.casefold(), set()).add(person_id)

    resolved = []
    for supplied in person_ids:
        if not isinstance(supplied, str) or not supplied.strip():
            raise ValueError("attendee IDs must be non-empty strings")
        matches = people_by_alias.get(supplied.strip().casefold(), set())
        if not matches:
            resolved.append(supplied)
        elif len(matches) > 1:
            raise AmbiguousPersonError(
                f"The attendee name {supplied!r} matches multiple people; "
                "use a unique directory ID."
            )
        else:
            resolved.append(next(iter(matches)))
    return resolved


def _require_known_people(store: Store, person_ids: list[str]) -> None:
    if not person_ids:
        raise ValueError("at least one attendee is required")
    if any(not isinstance(person_id, str) or not person_id for person_id in person_ids):
        raise ValueError("attendee IDs must be non-empty strings")
    person_ids = resolve_attendee_ids(store, person_ids)
    if len(person_ids) != len(set(person_ids)):
        raise ValueError("attendee IDs must not contain duplicates")
    known_ids = {person["id"] for person in store.people}
    unknown_ids = sorted(set(person_ids) - known_ids)
    if unknown_ids:
        names = ", ".join(unknown_ids)
        raise UnknownPersonError(
            f"I couldn't find these people in the employee directory: {names}."
        )


def _require_calendar_date(store: Store, date: str) -> None:
    available_dates = {
        calendar_date
        for person_calendar in store.calendars.values()
        for calendar_date in person_calendar
    }
    if date not in available_dates:
        raise CalendarDateUnavailableError(
            f"No calendar data is available for {date}."
        )


def list_people(store: Store) -> list[dict]:
    """Return only the directory fields needed to identify attendees."""
    return [
        {"id": person["id"], "name": person["name"]}
        for person in store.people
    ]


def get_busy_times(store: Store, person: str, date: str) -> list[dict]:
    """Return only busy intervals for a person on a date (YYYY-MM-DD)."""
    resolved_person = resolve_attendee_ids(store, [person])[0]
    _require_known_people(store, [resolved_person])
    _require_calendar_date(store, date)
    return [
        {"start": block["start"], "end": block["end"]}
        for block in store.calendars.get(resolved_person, {}).get(date, [])
    ]


def find_free_slots(
    store: Store,
    attendees: list[str],
    date: str,
    duration_minutes: int,
    earliest: str | None = None,
    latest: str | None = None,
) -> list[dict]:
    """Find every time on `date` when all attendees are free for `duration_minutes`.

    `earliest` and `latest` optionally narrow the search to part of the day
    (e.g. earliest="12:00" for "afternoon").
    """
    attendees = resolve_attendee_ids(store, attendees)
    _require_known_people(store, attendees)
    _require_calendar_date(store, date)
    if (
        not isinstance(duration_minutes, int)
        or isinstance(duration_minutes, bool)
        or duration_minutes <= 0
        or duration_minutes % SLOT_MINUTES != 0
    ):
        raise ValueError(
            f"duration_minutes must be a positive multiple of {SLOT_MINUTES}"
        )

    def bound_to_slot(clock: str, *, earliest_bound: bool) -> int:
        minutes = time_to_minutes(clock)
        minutes = min(max(minutes, DAY_START_MINUTES), DAY_END_MINUTES)
        offset = minutes - DAY_START_MINUTES
        if earliest_bound:
            return -(-offset // SLOT_MINUTES)
        return offset // SLOT_MINUTES

    calendars = []
    for person in attendees:
        busy = store.calendars.get(person, {}).get(date, [])
        calendars.append(availability_from_busy(busy))

    free = combine_availability(calendars)
    slots_needed = duration_minutes // SLOT_MINUTES
    start_slot = (
        bound_to_slot(earliest, earliest_bound=True)
        if earliest is not None
        else 0
    )
    end_slot = (
        bound_to_slot(latest, earliest_bound=False)
        if latest is not None
        else None
    )
    if end_slot is not None and start_slot > end_slot:
        raise ValueError("earliest must not be later than latest")

    starts = find_free_windows(free, slots_needed, start_slot, end_slot)
    return [
        {"start": slot_to_time(s), "end": slot_to_time(s + slots_needed)}
        for s in starts
    ]


TOOLS = {
    "list_people": list_people,
    "get_busy_times": get_busy_times,
    "find_free_slots": find_free_slots,
}
