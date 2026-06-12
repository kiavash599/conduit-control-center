# SPDX-License-Identifier: MIT
"""
backend.traffic
---------------
P0 Traffic Persistence Collector.

This package owns CCC's persistent, aggregate-only traffic accounting:
Conduit's Prometheus byte counters are cumulative and reset on restart, so
lifetime totals and history must live in CCC. The package is organised as:

  - schema.py      schema DDL + bootstrap (Step 1) — landed dormant.
  - models.py      typed records (Step 2).
  - accounting.py  pure delta/epoch/reset state machine (Step 2).
  - repository.py  all SQL for the traffic_* tables (Step 3).
  - collector.py   the in-process collector task (Step 3) — runs only when
                   the ``traffic_collector_enabled`` flag is set.

Step 1 introduces the schema only. No collector runs and no production
behaviour changes until the feature flag is explicitly enabled.
"""
