# Testing Kairos

This document outlines the testing philosophy and setup for the Kairos scheduling engine.

## Why the engine takes `now` as a parameter
The scheduling engine relies on the current time to determine availability (e.g., buffering, next available slot). Using an explicit `now` parameter allows tests to precisely control the "current" time without resorting to time-mocking libraries like `freezegun`. `freezegun` intercepts standard library calls, which can cause subtle bugs and instability in complex datetime manipulations and timezone conversions. By passing `now`, we keep the engine pure and easily testable.

## Why concurrency tests need real threads and separate connections
Kairos uses Postgres Exclusion Constraints and row-level locking (`select_for_update`) to prevent double bookings. These mechanisms operate at the database transaction level. Mocking threads or using in-memory databases (like SQLite) does not accurately reproduce the concurrent transaction isolation semantics of Postgres. Real threads and separate connections are necessary to verify that race conditions are correctly handled by the database constraints and application locks.

## Coverage Floors
We enforce a strict test coverage floor on the most critical components of the system. The view layer is allowed lower coverage, but the core engine must be robustly tested.

*   `apps/scheduling/intervals.py`: Handles complex datetime interval math. Bugs here lead to overlapping meetings.
*   `apps/scheduling/engine.py`: The core availability logic. Bugs here lead to showing incorrect slots.
*   `apps/bookings/services.py`: Handles state transitions and booking creation. Bugs here lead to broken bookings or double-booking.

If coverage for any of these modules drops below 95%, the CI build will fail.

## How to run the suite
We use `pytest` as our test runner.

Run all tests:
```bash
make test
```
Or manually:
```bash
pytest
```

Run tests with coverage:
```bash
pytest --cov=apps
```
