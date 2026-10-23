# Notes

## Problems found

- **Incorrect slots:** Boundary tests found a missed 16:30–17:00 opening and
  45-minute meetings shortened to 30. Scheduling now uses exact durations on
  a 15-minute grid. **Example:** 45 requested minutes returns a 45-minute
  window.
- **Invalid people/calendar data:** Direct tool and store tests found empty
  or duplicate attendees, unknown names, unsupported dates, and malformed
  entries. These fail clearly; known aliases resolve to directory IDs.
  **Example:** `Priya` resolves; an unknown `Ghost` is rejected.
- **Untrusted model output:** FakeModel tests exposed invented attendees,
  unsearched times, and malformed responses. Structured selections are
  validated against attendees, request constraints, and search results;
  application code renders the answer. Responses are validated and tool use
  is capped at eight calls. **Example:** a recommendation for `Ghost` or an
  unsearched time is not presented.
- **Request/search mismatches:** Tests found weekday and period conflicts and
  early stopping after empty searches. Searches must honor explicit dates or
  weekdays; an undated request may try another date. **Example:** an empty
  Tuesday search may be followed by Monday for an undated request, but not
  for “next Tuesday.”
- **Privacy and CLI:** Inspection showed event titles and personal details
  were unnecessary, so model-facing data is limited to scheduling fields.
  The CLI and scripted demo now both request one hour. **Example:** a Friday
  request cannot return the demo's Tuesday slot.

### Changed test expectations

- The afternoon expectation now includes **16:30–17:00**; the old assertion
  omitted a valid window ending at 17:00. **Example:** the final half-hour is
  expected when nobody is busy.
- Duration assertions now require the requested length. **Example:** a
  45-minute request must not pass with a 30-minute result.
- Busy-time assertions check interval fields, not event titles.
  **Example:** compare `start` and `end`, not the meeting subject.
- Missing-calendar assertions reflect the sample assumption and warning.
  **Example:** Mei has no November 4 entry; the tool can find a slot, while
  the agent warns the user to confirm availability.
- Other changes, such as weekday matching and bound validation, are covered
  by added regression tests rather than altered legacy expectations.
  **Example:** “next Tuesday” cannot fall back to Monday.

## Part B: multiple meeting options

- The assistant offers up to three validated options so employees can
  compare times rather than receive only one. **Example:** show two searched
  openings when both meet the request. I chose this over automatically
  returning only the earliest slot because employees may have different
  preferences; the list stays capped, though options may overlap.

## AI-tool use

- I used AI assistance to inspect, implement, and test; I did not delegate
  work to separate agents. I verified suggestions against code, data, tests,
  and CLI behavior. **Example:** FakeModel scripts a tool call and response;
  it does not understand arbitrary new requests.
- I rejected a suggestion to reject every final answer containing a time,
  because that would block valid answers. **Example:** “13:00 works” should
  be accepted only if it matches a searched window; structured
  recommendations are checked before application-side rendering.

## What I would do next

- Add agent-level handling coverage for `ModelRequestError`, response-size
  boundary tests, and ambiguous legacy `final` tests. **Examples:** simulate
  the model error in the agent, test a response just over the cap, and test
  “13:00 works” when multiple windows could match.
- Improve clarification and offer user-approved broader searches. Empty
  searches may be followed by another search only when the next date respects
  the user's request. If searches find no valid option, the assistant returns
  a controlled no-availability response; if the call limit is reached while
  usable windows exist but no recommendation is completed, it returns a
  controlled limit failure. **Example:** do not search Monday for “next
  Tuesday.”
- Track calendar coverage and sync status so a missing entry differs from a
  confirmed empty calendar. **Example:** record whether Mei's November 4
  calendar was successfully synced rather than inferring coverage from the
  missing entry.
