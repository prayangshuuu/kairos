#!/bin/bash
uv run coverage run -m pytest apps/
uv run coverage report --include="apps/scheduling/intervals.py,apps/scheduling/engine.py,apps/bookings/services.py" --fail-under=80
