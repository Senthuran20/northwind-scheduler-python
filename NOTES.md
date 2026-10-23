# Notes

## Problems found

- **Incorrect or missed slots:** Boundary checks found a missed
  16:30–17:00 opening and duration rounding (45 minutes could become 30).
  Scheduling now uses exact durations on a 15-minute grid and includes
  openings ending at 17:00. **Example:** a 45-minute request returns a
  45-minute window, not a 30-minute one.
- **Invalid people or calendar data:** Direct tool/store tests found empty or
  duplicate attendee lists, unresolved names, unsupported dates, and
  malformed records. These now fail deliberately; supported name forms are
  resolved consistently. **Example:** `find_free_slots(["Priya"])` resolves
  the known first name, while an unknown name is rejected.
- **Model/request mismatches:** FakeModel tests showed that a response could
  omit/invent attendees or recommend an unsearched time. The agent checks
  structured selections against attendees, explicit constraints, and search
  results, then renders the answer in application code. Model/tool responses
  are validated (including malformed JSON, non-string, deeply nested, and
  oversized replies), and tool calls are capped at eight. **Example:** a
  recommendation naming Ghost, or a time absent from search results, is not
  presented as available.
- **Misleading request handling:** Tests exposed weekday and period
  mismatches and early stopping after an empty search. Weekdays are matched
  by name, range phrases are not treated as exact-day requests, and an empty
  search does not prevent trying another date when the request allows it.
  Retries must still satisfy an explicit requested date or weekday.
  **Example:** a request without a specified day may try Monday after an
  empty Tuesday search; the regression test confirms “next Tuesday” rejects
  a Monday fallback.
- **Unnecessary data exposure:** Inspection showed event titles and personal
  directory details were not needed. Model-facing data is limited to
  scheduling fields. **Example:** busy-time results contain interval
  boundaries, not event titles, phone numbers, or email addresses.
- **CLI mismatch:** The default request and scripted demo used different
  durations. Both now request one hour; CLI smoke tests also check a Friday
  request against the Tuesday-only demo. **Example:** a Friday request does
  not return the demo's Tuesday slot.

### Changed test expectations

- These are updates to the original assertions, not additional bug
  descriptions:
- **Afternoon expected list:** added **16:30–17:00**; the prior list encoded
  the old boundary behavior. **Example:** the test now expects the final
  half-hour when it is free.
- **Duration assertion:** changed from accepting a shortened interval to
  requiring the requested length. **Example:** a 45-minute request must
  assert a 45-minute result, not 13:00–13:30.
- **Busy-time result shape:** removed event titles from expected values;
  tests now compare only the interval fields needed by the tool. **Example:**
  assert start/end, not a meeting title.
- **Missing calendar entry:** changed the expectation to match the sample
  policy and its warning. **Example:** a missing entry is not asserted as
  confirmed availability.
- Other cases (such as bound rounding and weekday phrasing) are covered by
  added regression tests rather than changed legacy expectations.

## Part B: multiple meeting options

- **What:** Return up to three options validated against searched results.
  **Example:** show 13:00–14:00 and 14:00–15:00 when both were searched and
  validated.
- **Who it helps:** Employees comparing alternatives instead of receiving
  only one suggestion. **Example:** a user can choose a later option to
  accommodate their own preference.
- **Why this:** It is a small user-facing improvement; a UI and booking flow
  are out of scope. Options are capped and validated, but may overlap.
  **Example:** two valid options may start 15 minutes apart and overlap.

## Calendar-data assumption

- On a covered date, a missing entry for a known person means **no busy
  blocks were recorded**—not verified free time. The app warns users when a
  recommendation depends on this assumption. **Verified example:** Mei has
  no November 4 entry; the scheduling tool can find a slot, and the agent's
  recommendation includes a warning to confirm availability.
- Dates outside the sample's covered range are rejected. A real integration
  should track calendar coverage and sync status explicitly. **Example:** a
  missing Monday entry and a confirmed empty Monday calendar should be
  distinguishable in a production system.

## AI-tool use

- I used AI assistance to inspect the code and assignment, explore cases,
  implement changes, and draft tests and notes. I did not delegate work to
  separate agents. I checked suggestions against code, data, and test
  results. **Example:** I ran the regression suite and CLI instead of relying
  only on generated explanations.
- **Corrected suggestion:** rejecting final answers just because they contain
  a time or availability wording would also reject valid answers. I used
  structured recommendations checked against search evidence and
  application-side rendering instead. **Example:** “13:00 works for both”
  should only be accepted when it identifies a searched window.
- Tests use `FakeModel` for repeatability; it does not interpret arbitrary
  user requests. **Example:** tests script a tool call and response rather
  than expecting FakeModel to understand a new sentence.

## What I would do next

- Add an agent-level test for how `ModelRequestError` is handled; current
  coverage tests the adapter, not that agent path. **Example:** simulate the
  adapter raising the error and assert the agent returns a controlled
  failure.
- Add boundary tests for the model-response size cap and for ambiguous
  legacy `final` text. **Examples:** test a reply just over the configured
  size limit, and “13:00 works for both” when multiple searched windows
  could match.
- Improve clarification for ambiguous requests and offer a user-approved
  broader search. Currently, the model may try another date within the
  eight-tool-call limit, provided the next date satisfies an explicit
  requested date or weekday; if no usable result is found, the app reports no
  availability. **Example:** after an empty Tuesday search for “next
  Tuesday,” a Monday retry is rejected; for a request with no specified day,
  another sample day can be tried.
- Track calendar coverage and synchronization before treating missing data as
  known free time. **Example:** store whether each person's Tuesday calendar
  was successfully synced, rather than inferring it from a missing entry.
- I deliberately left UI, booking/calendar writes, time zones, recurring
  meetings, authentication, deployment, and live-model integration out of
  scope, as requested by the assignment. **Examples:** the CLI reports
  suggested times but does not send invitations or write calendar events.
