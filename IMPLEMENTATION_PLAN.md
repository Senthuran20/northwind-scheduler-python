# Implementation Approach and Outcome

## Goal

The assignment was to take ownership of an AI-assisted scheduler and make its
results correct, its failures deliberate, and its behavior testable. The goal
was not to rewrite the application or add production infrastructure; the work
focused on the deterministic scheduling core, the boundary around model
responses, and a small user-facing improvement.

## Approach

1. **Understand the existing behavior.** Review the scheduler, tools, agent
   loop, sample calendars, tests, and assignment requirements. Use the
   scripted `FakeModel` for repeatable agent tests rather than depending on a
   live model.
2. **Make scheduling results authoritative.** Validate attendees and calendar
   data, resolve supported name forms consistently, reject dates outside the
   sample data, and calculate exact-duration openings on the 15-minute grid.
   Treat a known person with no entry on an otherwise covered date as having
   no recorded busy blocks, while warning that this is a sample-data
   assumption—not proof of real-world availability.
3. **Constrain the model boundary.** Validate tool arguments and responses,
   limit tool execution, continue after empty searches, and ask the model for a
   structured selection. Check that selection against actual search results,
   requested constraints, and attendees; render the user-facing answer in
   application code rather than trusting generated prose.
4. **Add and verify a user-facing improvement.** Provide up to three
   evidence-checked meeting options so a user can compare alternatives. Keep
   the list bounded; options are validated but are not guaranteed to be
   non-overlapping.
5. **Test edge cases and the demo.** Add regression coverage for scheduling
   boundaries, malformed data and model output, identity resolution,
   constraints, and empty-search ordering. Run the complete pytest suite and
   the default CLI demo.

## Outcome and decisions

The final implementation keeps scheduling decisions deterministic even when
the model is involved: the model may request searches and select from their
results, but cannot establish availability or introduce unsupported
attendees. Empty results do not prematurely end a run; if no usable result is
found by the end of the bounded run, the application reports no availability.
Request interpretation remains intentionally limited to constraints the
application can establish reliably. The sample dataset has sparse calendar
entries, so missing entries on covered dates are disclosed as an assumption.

The multi-option list was chosen for Part B because it helps an employee
compare useful alternatives without adding booking, invitations, or a UI.
Privacy minimization and robustness improvements were included as reliability
work, not presented as the Part B addition.

Validation is through the repository's pytest suite and CLI smoke test using
the scripted model. Real-model behavior, time zones, recurring events,
calendar writes, and production calendar-coverage tracking remain out of
scope. The last item would be necessary before treating missing calendar data
as confirmed availability in a real system.
