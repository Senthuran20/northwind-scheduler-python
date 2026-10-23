"""Loads the people directory and calendars from JSON files."""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .slots import time_to_minutes

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class InvalidCalendarDataError(ValueError):
    """Raised when directory or calendar data is malformed or inconsistent."""


@dataclass
class Store:
    people: list[dict]
    calendars: dict[str, dict[str, list[dict]]]

    def __post_init__(self) -> None:
        if not isinstance(self.people, list):
            raise InvalidCalendarDataError("people must be a list")
        person_ids: list[str] = []
        for person in self.people:
            if not isinstance(person, dict):
                raise InvalidCalendarDataError("each person must be an object")
            person_id = person.get("id")
            if not isinstance(person_id, str) or not person_id:
                raise InvalidCalendarDataError(
                    "each person must have a non-empty string id"
                )
            person_name = person.get("name")
            if not isinstance(person_name, str) or not person_name.strip():
                raise InvalidCalendarDataError(
                    f"person {person_id!r} must have a non-empty name"
                )
            person_ids.append(person_id)
        if len(person_ids) != len(set(person_ids)):
            raise InvalidCalendarDataError("directory contains duplicate person IDs")

        if not isinstance(self.calendars, dict):
            raise InvalidCalendarDataError("calendars must be an object")
        known_ids = set(person_ids)
        for person_id, person_calendar in self.calendars.items():
            if person_id not in known_ids:
                raise InvalidCalendarDataError(
                    f"calendar data references unknown person {person_id!r}"
                )
            if not isinstance(person_calendar, dict):
                raise InvalidCalendarDataError(
                    f"calendar for {person_id!r} must be an object"
                )
            for calendar_date, busy_blocks in person_calendar.items():
                try:
                    parsed_date = date.fromisoformat(calendar_date)
                except (TypeError, ValueError) as exc:
                    raise InvalidCalendarDataError(
                        f"invalid calendar date {calendar_date!r}"
                    ) from exc
                if parsed_date.isoformat() != calendar_date:
                    raise InvalidCalendarDataError(
                        f"invalid calendar date {calendar_date!r}"
                    )
                if not isinstance(busy_blocks, list):
                    raise InvalidCalendarDataError(
                        f"busy blocks for {person_id!r} on {calendar_date} "
                        "must be a list"
                    )
                for index, block in enumerate(busy_blocks):
                    if not isinstance(block, dict):
                        raise InvalidCalendarDataError(
                            f"busy block {index} for {person_id!r} must be an object"
                        )
                    if "start" not in block or "end" not in block:
                        raise InvalidCalendarDataError(
                            f"busy block {index} for {person_id!r} must include "
                            "start and end"
                        )
                    try:
                        start = time_to_minutes(block["start"])
                        end = time_to_minutes(block["end"])
                    except ValueError as exc:
                        raise InvalidCalendarDataError(
                            f"busy block {index} for {person_id!r} has an invalid time"
                        ) from exc
                    if start >= end:
                        raise InvalidCalendarDataError(
                            f"busy block {index} for {person_id!r} must end after it starts"
                        )

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "Store":
        with open(data_dir / "people.json") as f:
            people = json.load(f)
        with open(data_dir / "calendars.json") as f:
            calendars = json.load(f)
        return cls(people=people, calendars=calendars)
