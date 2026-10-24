"""The scheduling agent: a validated, bounded tool-calling loop."""

import json
import re
from calendar import month_abbr, month_name
from dataclasses import dataclass
from datetime import date, timedelta

from .model import Model, ModelRequestError
from .prompts import build_system_prompt
from .slots import SLOT_MINUTES, time_to_minutes
from .store import Store
from .tools import (
    TOOLS,
    AmbiguousPersonError,
    CalendarDateUnavailableError,
    UnknownPersonError,
    resolve_attendee_ids,
)

MAX_TOOL_CALLS = 8
MAX_MODEL_RESPONSE_CHARS = 100_000
_CLOCK_PATTERN = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)")
_TIME_RANGE_PATTERN = re.compile(
    r"(?<!\d)((?:[01]?\d|2[0-3]):[0-5]\d)\s*"
    r"(?:-|–|—|\bto\b)\s*"
    r"((?:[01]?\d|2[0-3]):[0-5]\d)(?!\d)",
    re.IGNORECASE,
)
_DURATION_PATTERN = re.compile(
    r"\b(half an hour|an hour and a half|one and a half hours|"
    r"an hour|one hour|\d+(?:\.\d+)?\s*(?:minutes?|mins?|hours?|hrs?))\b",
    re.IGNORECASE,
)
_ATTENDEE_STOP_PATTERN = re.compile(
    r"\b(?:on|next|this|today|tomorrow|in|asap|at|from|before|after|"
    r"morning|afternoon|evening|"
    r"Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|"
    r"Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?|"
    + "|".join(
        re.escape(name)
        for month in range(1, 13)
        for name in (month_name[month], month_abbr[month])
    )
    + r"|\d{1,2})\b",
    re.IGNORECASE,
)
_NAME_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z'-]*")
_WEEKDAY_PATTERN = re.compile(
    r"\b(Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|"
    r"Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b",
    re.IGNORECASE,
)
_MONTH_DATE_PATTERN = re.compile(
    r"\b(?:(January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+(\d{1,2})(?:,?\s+(\d{4}))?|"
    r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|"
    r"Sep|Sept|Oct|Nov|Dec)(?:\s+(\d{4}))?)\b",
    re.IGNORECASE,
)
_PERIOD_PATTERN = re.compile(
    r"\b(morning|afternoon|evening)\b", re.IGNORECASE
)
_AVAILABILITY_CLAIM_PATTERN = re.compile(
    r"\b(?:free|available|open|works?|can meet|could meet|fits?|"
    r"availability|opening|slot)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SearchContext:
    attendees: frozenset[str]
    date: str
    duration_minutes: int
    windows: frozenset[tuple[str, str]]
    missing_calendar_attendees: frozenset[str]


class InvalidModelResponse(ValueError):
    """Raised when model output does not match one supported action schema."""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidModelResponse(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_iso_date(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a date in YYYY-MM-DD format")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a date in YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be a date in YYYY-MM-DD format")


def _validate_tool_arguments(name: str, args: object) -> dict:
    if not isinstance(args, dict):
        raise ValueError("tool args must be a JSON object")

    schemas = {
        "list_people": (set(), set()),
        "get_busy_times": ({"person", "date"}, set()),
        "find_free_slots": (
            {"attendees", "date", "duration_minutes"},
            {"earliest", "latest"},
        ),
    }
    required, optional = schemas[name]
    keys = set(args)
    missing = required - keys
    unexpected = keys - required - optional
    if missing:
        raise ValueError(f"{name} is missing required argument(s): {', '.join(sorted(missing))}")
    if unexpected:
        raise ValueError(f"{name} received unexpected argument(s): {', '.join(sorted(unexpected))}")

    if name == "get_busy_times":
        if not isinstance(args["person"], str) or not args["person"]:
            raise ValueError("person must be a non-empty string")
        _validate_iso_date(args["date"], "date")
    elif name == "find_free_slots":
        attendees = args["attendees"]
        if (
            not isinstance(attendees, list)
            or not attendees
            or any(not isinstance(person, str) or not person for person in attendees)
        ):
            raise ValueError("attendees must be a non-empty list of person IDs")
        _validate_iso_date(args["date"], "date")
        duration = args["duration_minutes"]
        if not isinstance(duration, int) or isinstance(duration, bool):
            raise ValueError("duration_minutes must be an integer")
        for bound in ("earliest", "latest"):
            if args.get(bound) is None:
                args.pop(bound, None)
            elif not isinstance(args[bound], str):
                raise ValueError(f"{bound} must be an HH:MM string")

    return args


def _mentioned_people(text: str, people: list[dict]) -> set[str]:
    mentioned: set[str] = set()
    for person in people:
        person_id = person["id"]
        full_name = person.get("name", "")
        names = {person_id, full_name}
        if full_name:
            names.add(full_name.split()[0])
        if any(
            re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE)
            for name in names
        ):
            mentioned.add(person_id)
    return mentioned


def _unknown_requested_attendees(text: str, people: list[dict]) -> set[str]:
    participant_clause = re.search(
        r"\b(?:with|between|among)\s+(.+)", text, re.IGNORECASE
    )
    if participant_clause is None:
        participant_clause = re.search(
            r"\b(?:schedule|book|arrange)\s+(?:a\s+meeting\s+)?(.+)",
            text,
            re.IGNORECASE,
        )
    if participant_clause is None:
        participant_clause = re.search(
            r"\bfor\s+(.+)", text, re.IGNORECASE
        )
    if participant_clause is None:
        return set()

    clause = re.split(
        r"[?.!;]|\bfor\s+(?:\d|an?\s+hour|half\s+an?\s+hour)"
        r"|\b(?:in\s+)?(?:the\s+)?next\s+\d+\s+(?:minutes?|mins?|hours?|hrs?)",
        participant_clause.group(1),
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    clause = _ATTENDEE_STOP_PATTERN.split(clause, maxsplit=1)[0]
    aliases = {
        alias.lower(): person["id"]
        for person in people
        for alias in (
            person["id"],
            person.get("name", "").split()[0] if person.get("name") else "",
            person.get("name", ""),
        )
        if alias
    }
    generic_names = {
        "colleagues",
        "everyone",
        "group",
        "team",
        "us",
        "me",
        "myself",
        "people",
    }
    unknown: set[str] = set()
    for candidate in re.split(
        r"\s*(?:,|&|\band\b|\bor\b|\bplus\b)\s*",
        clause,
        flags=re.IGNORECASE,
    ):
        candidate = candidate.strip()
        if not candidate:
            continue
        normalized = candidate.lower().strip()
        if normalized in aliases or normalized in generic_names:
            continue
        words = _NAME_WORD_PATTERN.findall(candidate)
        if len(words) == 1 or words and all(word[0].isupper() for word in words):
            unknown.add(candidate)
    return unknown


def _has_ambiguous_attendee_choice(text: str, people: list[dict]) -> bool:
    participant_clause = re.search(
        r"\b(?:with|between|among)\s+(.+)", text, re.IGNORECASE
    )
    if participant_clause is None:
        return False
    clause = _ATTENDEE_STOP_PATTERN.split(
        participant_clause.group(1), maxsplit=1
    )[0]
    return bool(
        re.search(r"\bor\b", clause, re.IGNORECASE)
        and len(_mentioned_people(clause, people)) > 1
    )


def _requested_duration_minutes(text: str) -> int | None:
    durations: set[int] = set()
    matches = list(_DURATION_PATTERN.finditer(text))
    for match in matches:
        before = text[: match.start()]
        after = text[match.end() :]
        preceding_action = re.search(
            r"\b(?:find|schedule|book|arrange|need|want)\b([^.!?]*)$",
            before,
            re.IGNORECASE,
        )
        if preceding_action:
            gap = preceding_action.group(1)
            if re.search(r"\b(?:next|in|within|during|over)\b", gap, re.IGNORECASE):
                preceding_action = None
            elif len(gap.split()) > 5:
                preceding_action = None
        followed_by_participant = bool(
            re.match(r"\s+(?:with|for)\b", after, re.IGNORECASE)
        )
        preceded_by_duration_for = bool(
            re.search(r"\bfor\s*$", before, re.IGNORECASE)
        )
        if not (preceding_action or followed_by_participant or preceded_by_duration_for):
            continue

        phrase = match.group(1).lower()
        if phrase in {"half an hour", "an hour and a half", "one and a half hours"}:
            value = 30 if phrase == "half an hour" else 90
        elif phrase in {"an hour", "one hour"}:
            value = 60
        else:
            amount_match = re.match(
                r"(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)", phrase
            )
            if amount_match is None:
                continue
            amount = float(amount_match.group(1))
            factor = 60 if amount_match.group(2).startswith(("hour", "hr")) else 1
            exact_minutes = amount * factor
            if not exact_minutes.is_integer():
                raise ValueError(
                    "the requested duration must be a whole number of minutes"
                )
            value = int(exact_minutes)
        durations.add(value)

    duration_choice = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)?\s+or\s+"
        r"(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)\b",
        text,
        re.IGNORECASE,
    )
    if duration_choice:
        first_amount, first_unit, second_amount, second_unit = duration_choice.groups()
        first_unit = first_unit or second_unit
        factor1 = 60 if first_unit.lower().startswith(("hour", "hr")) else 1
        factor2 = 60 if second_unit.lower().startswith(("hour", "hr")) else 1
        durations.add(int(float(first_amount) * factor1))
        durations.add(int(float(second_amount) * factor2))

    if len(durations) > 1:
        raise ValueError(
            "the request contains conflicting meeting durations; please clarify"
        )
    return next(iter(durations)) if durations else None


def _requested_date(text: str, today: str) -> date | None:
    dates: set[date] = set()
    for iso_date in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text):
        try:
            dates.add(date.fromisoformat(iso_date))
        except ValueError as exc:
            raise ValueError("the requested date is invalid; please clarify") from exc

    reference = date.fromisoformat(today)
    relative_dates = {
        "today": reference,
        "tomorrow": reference + timedelta(days=1),
        "yesterday": reference - timedelta(days=1),
    }
    for word, value in relative_dates.items():
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):
            dates.add(value)

    for match in _MONTH_DATE_PATTERN.finditer(text):
        month_name_text, day_text, year_text, day_first, month_day_first, year_day_first = (
            match.groups()
        )
        month_text = month_name_text or month_day_first
        day_value = int(day_text or day_first)
        year_value = int(year_text or year_day_first or reference.year)
        month_lookup = {
            name.casefold(): month
            for month in range(1, 13)
            for name in (month_name[month], month_abbr[month])
        }
        month_lookup["sept"] = 9
        month_value = month_lookup[month_text.casefold()]
        try:
            dates.add(date(year_value, month_value, day_value))
        except ValueError as exc:
            raise ValueError("the requested date is invalid; please clarify") from exc

    if len(dates) > 1:
        raise ValueError(
            "the request contains conflicting dates; please clarify the requested day"
        )
    return next(iter(dates)) if dates else None


def _requested_weekday(text: str) -> tuple[str, int] | None:
    weekday_matches = list(_WEEKDAY_PATTERN.finditer(text))
    if weekday_matches:
        weekday_token = (
            r"Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|"
            r"Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?"
        )
        weekday_range_patterns = (
            rf"\b(?:before|after|by|until)\s+(?:{weekday_token})\b",
            rf"\b(?:{weekday_token})\s+(?:through|to)\s+(?:{weekday_token})\b",
            rf"\bbetween\s+(?:{weekday_token})\s+and\s+(?:{weekday_token})\b",
        )
        if any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in weekday_range_patterns
        ):
            return None

        choice_match = re.search(
            r"\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|"
            r"Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\s+or\s+"
            r"(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|"
            r"Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b",
            text,
            re.IGNORECASE,
        )
        if choice_match:
            raise ValueError(
                "the requested weekday is ambiguous; please choose one day"
            )
        anchored_matches = [
            match
            for match in weekday_matches
            if re.search(
                r"\b(?:on|next|this)\s+$",
                text[max(0, match.start() - 16) : match.start()],
                re.IGNORECASE,
            )
        ]
        relevant_matches = anchored_matches or [
            match
            for match in weekday_matches
            if not re.search(
                r"\b(?:after|before|since|until)\s+$",
                text[max(0, match.start() - 16) : match.start()],
                re.IGNORECASE,
            )
        ]
        relevant_matches = relevant_matches or weekday_matches
        relevant_weekdays = {
            match.group(1).casefold()[:3] for match in relevant_matches
        }
        if len(relevant_weekdays) > 1:
            raise ValueError(
                "the request contains conflicting weekdays; please clarify"
            )
        weekday_text = relevant_matches[0].group(1).casefold()[:3]
        weekdays = {
            "mon": 0,
            "tue": 1,
            "wed": 2,
            "thu": 3,
            "fri": 4,
            "sat": 5,
            "sun": 6,
        }
        weekday_name = relevant_matches[0].group(1).casefold()
        return weekday_name, weekdays[weekday_text]
    return None


def _requested_period(text: str) -> str | None:
    periods = {value.casefold() for value in _PERIOD_PATTERN.findall(text)}
    if len(periods) > 1:
        raise ValueError(
            "the request contains conflicting times of day; please clarify"
        )
    return next(iter(periods)) if periods else None


def _validate_request_constraints(
    user_request: str,
    tool_name: str,
    args: dict,
    people: list[dict],
    today: str,
) -> None:
    if tool_name != "find_free_slots":
        return

    requested_date = _requested_date(user_request, today)
    requested_weekday = _requested_weekday(user_request)
    if requested_date is not None and requested_date.isoformat() != args["date"]:
        raise ValueError(
            f"the requested date {requested_date.isoformat()} does not match "
            f"the search date {args['date']}"
        )

    search_date = date.fromisoformat(args["date"])
    if requested_weekday is not None and search_date.weekday() != requested_weekday[1]:
        requested = requested_weekday[0].title()
        raise ValueError(
            f"the requested weekday ({requested}) does not match the "
            f"search date {args['date']} ({search_date.strftime('%A')})"
        )

    requested_period = _requested_period(user_request)
    if requested_period == "evening":
        raise ValueError(
            "evening availability is outside the supported 09:00–17:00 workday"
        )
    if requested_period == "morning" and args.get("earliest") is not None:
        if time_to_minutes(args["earliest"]) >= 12 * 60:
            raise ValueError(
                "the requested morning period does not match the search bounds"
            )
    if requested_period == "afternoon" and args.get("latest") is not None:
        if time_to_minutes(args["latest"]) <= 12 * 60:
            raise ValueError(
                "the requested afternoon period does not match the search bounds"
            )

    requested_duration = _requested_duration_minutes(user_request)
    if (
        requested_duration is not None
        and requested_duration != args["duration_minutes"]
    ):
        raise ValueError(
            "the requested meeting duration does not match the search duration"
        )

    known_attendees = {person["id"] for person in people}
    supplied_attendees = set(args["attendees"])
    unknown_attendees = _unknown_requested_attendees(user_request, people)
    if unknown_attendees and supplied_attendees <= known_attendees:
        raise UnknownPersonError(
            "I couldn't find these people in the employee directory: "
            f"{', '.join(sorted(unknown_attendees))}."
        )

    if _has_ambiguous_attendee_choice(user_request, people):
        raise ValueError(
            "the requested attendees are ambiguous; please clarify who should attend"
        )

    requested_attendees = _mentioned_people(user_request, people)
    if (
        requested_attendees
        and supplied_attendees <= known_attendees
        and requested_attendees != supplied_attendees
    ):
        raise ValueError(
            "the requested attendees do not match the attendees in the search"
        )


def _window_matches_period(window: tuple[str, str], period: str | None) -> bool:
    if period is None:
        return True
    start = time_to_minutes(window[0])
    end = time_to_minutes(window[1])
    noon = 12 * 60
    if period == "morning":
        return start < noon and end <= noon
    if period == "afternoon":
        return start >= noon
    return False


def _join_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _no_availability_message(
    searches: list[SearchContext], people: list[dict], period: str | None
) -> str:
    if not searches:
        return "No matching availability was found in the calendar searches."

    attendee_sets = {context.attendees for context in searches}
    durations = {context.duration_minutes for context in searches}
    if len(attendee_sets) != 1 or len(durations) != 1:
        return "No matching availability was found in the calendar searches."

    attendee_ids = next(iter(attendee_sets))
    names = {
        person["id"]: person.get("name", person["id"])
        for person in people
    }
    attendee_names = _join_names(
        [names[person_id] for person_id in names if person_id in attendee_ids]
    )
    dates = sorted({context.date for context in searches})
    date_text = (
        dates[0]
        if len(dates) == 1
        else f"the searched dates ({', '.join(dates)})"
    )
    period_text = f" in the {period}" if period else ""
    return (
        f"No matching availability was found for {attendee_names}"
        f"{period_text} on {date_text} for {next(iter(durations))} minutes."
    )


def _validate_recommendation(
    selection: object, searches: list[SearchContext], people: list[dict]
) -> str | None:
    if not isinstance(selection, dict):
        return None
    required = {"attendees", "date", "duration_minutes", "start", "end"}
    if set(selection) != required:
        return None

    attendees = selection["attendees"]
    if (
        not isinstance(attendees, list)
        or not attendees
        or any(not isinstance(person, str) for person in attendees)
        or len(attendees) != len(set(attendees))
    ):
        return None
    _validate_iso_date(selection["date"], "date")
    duration = selection["duration_minutes"]
    if (
        not isinstance(duration, int)
        or isinstance(duration, bool)
        or duration <= 0
        or duration % SLOT_MINUTES != 0
    ):
        return None
    try:
        start = time_to_minutes(selection["start"])
        end = time_to_minutes(selection["end"])
    except ValueError:
        return None
    if end - start != duration:
        return None

    selected_attendees = frozenset(attendees)
    selected_window = (selection["start"], selection["end"])
    matching_context = next(
        (
            context
            for context in searches
            if selected_attendees == context.attendees
            and selection["date"] == context.date
            and duration == context.duration_minutes
            and selected_window in context.windows
        ),
        None,
    )
    if matching_context is None:
        return None

    names = {person["id"]: person.get("name", person["id"]) for person in people}
    attendee_names_list = [
        names[person["id"]]
        for person in people
        if person["id"] in selected_attendees
    ]
    attendee_names = _join_names(attendee_names_list)
    result = (
        f"{attendee_names} can meet on {selection['date']} from "
        f"{selection['start']} to {selection['end']} ({duration} minutes)."
    )
    if matching_context.missing_calendar_attendees:
        missing_names = [
            names[person["id"]]
            for person in people
            if person["id"] in matching_context.missing_calendar_attendees
        ]
        missing_group = _join_names(missing_names)
        result += (
            f" Note: No calendar entry was recorded for {missing_group} on "
            "this date, so this result assumes no busy blocks were recorded; "
            "please confirm availability."
        )
    return result


def _render_recommendation_list(
    selections: object, searches: list[SearchContext], people: list[dict]
) -> str | None:
    if not isinstance(selections, list) or not 1 <= len(selections) <= 3:
        return None
    rendered = []
    selection_keys = set()
    for selection in selections:
        answer = _validate_recommendation(selection, searches, people)
        if answer is None:
            return None
        key = (
            tuple(selection["attendees"]),
            selection["date"],
            selection["duration_minutes"],
            selection["start"],
            selection["end"],
        )
        if key in selection_keys:
            return None
        selection_keys.add(key)
        rendered.append(answer)
    first = selections[0]
    if any(
        selection["attendees"] != first["attendees"]
        or selection["date"] != first["date"]
        or selection["duration_minutes"] != first["duration_minutes"]
        for selection in selections[1:]
    ):
        return None
    return "Available options:\n" + "\n".join(
        f"{index}. {answer}" for index, answer in enumerate(rendered, start=1)
    )


def _render_supported_final(
    text: str,
    searches: list[SearchContext],
    people: list[dict],
    today: str,
) -> str | None:
    if not searches:
        return None
    try:
        mentioned_date = _requested_date(text, today)
        mentioned_weekday = _requested_weekday(text)
        mentioned_duration = _requested_duration_minutes(text)
    except ValueError:
        return None
    mentioned_people = _mentioned_people(text, people)

    candidates: set[tuple[SearchContext, tuple[str, str]]] = set()
    ranges = list(_TIME_RANGE_PATTERN.finditer(text))
    remaining = _TIME_RANGE_PATTERN.sub("", text)
    points = [match.group(0) for match in _CLOCK_PATTERN.finditer(remaining)]
    for context in searches:
        if mentioned_people and not mentioned_people <= context.attendees:
            continue
        if mentioned_date is not None and context.date != mentioned_date.isoformat():
            continue
        if (
            mentioned_weekday is not None
            and date.fromisoformat(context.date).weekday() != mentioned_weekday[1]
        ):
            continue
        if (
            mentioned_duration is not None
            and context.duration_minutes != mentioned_duration
        ):
            continue
        for window in context.windows:
            if ranges and any(
                (match.group(1), match.group(2)) == window for match in ranges
            ):
                candidates.add((context, window))
            elif not ranges and points and all(
                point in window for point in points
            ):
                candidates.add((context, window))
    if len(candidates) != 1:
        return None
    context, window = next(iter(candidates))
    selection = {
        "attendees": sorted(context.attendees),
        "date": context.date,
        "duration_minutes": context.duration_minutes,
        "start": window[0],
        "end": window[1],
    }
    return _validate_recommendation(selection, searches, people)


class SchedulingAgent:
    def __init__(self, model: Model, store: Store, today: str = "2026-11-02"):
        self.model = model
        self.store = store
        self.today = today

    def run(self, user_request: str) -> str:
        """Handle one user request and return the assistant's final answer."""
        messages = [
            {"role": "system", "content": build_system_prompt(self.store.people, self.today)},
            {"role": "user", "content": user_request},
        ]
        tool_calls = 0
        searches: list[SearchContext] = []

        for _ in range(MAX_TOOL_CALLS + 1):
            try:
                raw = self.model.complete(messages)
            except ModelRequestError as exc:
                return f"I couldn't get a valid response from the model: {exc}"
            if not isinstance(raw, str):
                return "I couldn't process the model response, so I stopped without making a recommendation."
            if len(raw) > MAX_MODEL_RESPONSE_CHARS:
                return "I couldn't process the model response because it was too large."
            try:
                action = json.loads(raw, object_pairs_hook=_unique_json_object)
            except (
                json.JSONDecodeError,
                InvalidModelResponse,
                RecursionError,
                ValueError,
            ):
                return "I couldn't process the model response, so I stopped without making a recommendation."

            if not isinstance(action, dict):
                return "I couldn't process the model response, so I stopped without making a recommendation."
            action_keys = set(action)
            if (
                action_keys in ({"final"}, {"recommendation"}, {"recommendations"})
                and (action_keys != {"final"} or isinstance(action["final"], str))
                and searches
                and not any(context.windows for context in searches)
            ):
                return _no_availability_message(
                    searches, self.store.people, _requested_period(user_request)
                )
            if action_keys == {"final"}:
                answer = action["final"]
                if not isinstance(answer, str):
                    return "I couldn't process the model response, so I stopped without making a recommendation."
                if _AVAILABILITY_CLAIM_PATTERN.search(answer) or _CLOCK_PATTERN.search(answer):
                    rendered = _render_supported_final(
                        answer, searches, self.store.people, self.today
                    )
                    if rendered is not None:
                        return rendered
                    return (
                        "I can't confirm that availability from the calendar results, "
                        "so I won't recommend that time."
                    )
                return answer
            if action_keys == {"recommendation"}:
                try:
                    answer = _validate_recommendation(
                        action["recommendation"], searches, self.store.people
                    )
                except ValueError:
                    answer = None
                if answer is None:
                    return (
                        "I can't confirm that availability from the calendar results, "
                        "so I won't recommend that time."
                    )
                return answer
            if action_keys == {"recommendations"}:
                try:
                    answer = _render_recommendation_list(
                        action["recommendations"], searches, self.store.people
                    )
                except (ValueError, TypeError):
                    answer = None
                if answer is None:
                    return (
                        "I can't confirm those availability options from the "
                        "calendar results, so I won't recommend them."
                    )
                return answer
            if action_keys != {"tool", "args"}:
                return "I couldn't process the model response, so I stopped without making a recommendation."

            tool_name = action["tool"]
            if not isinstance(tool_name, str) or tool_name not in TOOLS:
                return "I couldn't run the requested tool because its name is not recognized."
            if tool_calls >= MAX_TOOL_CALLS:
                if searches and not any(context.windows for context in searches):
                    return _no_availability_message(
                        searches, self.store.people, _requested_period(user_request)
                    )
                return (
                    "I couldn't complete this request within "
                    f"{MAX_TOOL_CALLS} tool calls."
                )
            try:
                args = _validate_tool_arguments(tool_name, action["args"])
                if tool_name == "find_free_slots":
                    args["attendees"] = resolve_attendee_ids(
                        self.store, args["attendees"]
                    )
                elif tool_name == "get_busy_times":
                    args["person"] = resolve_attendee_ids(
                        self.store, [args["person"]]
                    )[0]
                _validate_request_constraints(
                    user_request, tool_name, args, self.store.people, self.today
                )
            except UnknownPersonError as exc:
                return str(exc)
            except AmbiguousPersonError as exc:
                return str(exc)
            except ValueError as exc:
                return f"I couldn't run the requested tool: {exc}."

            messages.append({"role": "assistant", "content": raw})
            try:
                result = TOOLS[tool_name](self.store, **args)
            except UnknownPersonError as exc:
                return str(exc)
            except CalendarDateUnavailableError as exc:
                return str(exc)
            except ValueError as exc:
                return f"I couldn't run the requested tool: {exc}."

            tool_calls += 1
            if tool_name == "find_free_slots":
                period = _requested_period(user_request)
                result = [
                    window
                    for window in result
                    if _window_matches_period(
                        (window["start"], window["end"]), period
                    )
                ]
                searches.append(
                    SearchContext(
                        attendees=frozenset(args["attendees"]),
                        date=args["date"],
                        duration_minutes=args["duration_minutes"],
                        windows=frozenset(
                            (window["start"], window["end"]) for window in result
                        ),
                        missing_calendar_attendees=frozenset(
                            attendee
                            for attendee in args["attendees"]
                            if args["date"]
                            not in self.store.calendars.get(attendee, {})
                        ),
                    )
                )
            messages.append({"role": "tool", "content": json.dumps(result)})

        if searches and not any(context.windows for context in searches):
            return _no_availability_message(
                searches, self.store.people, _requested_period(user_request)
            )
        return (
            "I couldn't complete this request within "
            f"{MAX_TOOL_CALLS} tool calls."
        )
