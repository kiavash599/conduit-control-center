# SPDX-License-Identifier: MIT
"""
backend/traffic/prefs.py
------------------------
Runtime enable/disable resolution for the traffic persistence collector.

``config.json``'s ``traffic.collector_enabled`` is the **install default**
(ship-dark: ``false``). The operator's *runtime* choice is stored as a
non-secret key in the ``app_settings`` table (``backend/database.py``) so it
survives a restart **without any privileged ``config.json`` write** -- the
config file stays root-owned and read-only to the service user (the v0.3.19
ownership boundary). This keeps the toggle inside the service user's existing,
already-audited write surface (``ccc.db``).

Precedence (the "effective" value):
    app_settings override, when a valid "true"/"false" is stored
    else -> config.json install default (traffic.collector_enabled)

A single resolver, used by application boot, the toggle endpoint, and the
read API, so all three always agree.
"""

from __future__ import annotations

from backend.config import get_app_config
from backend.database import get_setting, set_setting

# Namespaced app_settings key. Non-secret, per the database.py storage contract
# (benign operator-set metadata only -- never secrets/tokens/keys).
TRAFFIC_COLLECTOR_ENABLED_KEY = "traffic_collector_enabled"

_TRUE = "true"
_FALSE = "false"


def _parse(value: str | None) -> bool | None:
    """Parse a stored override into a bool, or None when absent/unrecognised."""
    if value is None:
        return None
    v = value.strip().lower()
    if v == _TRUE:
        return True
    if v == _FALSE:
        return False
    return None  # unrecognised -> treat as absent (fall back to the default)


async def effective_collector_enabled() -> bool:
    """Return the effective collector-enabled state.

    A valid ``app_settings`` override wins; otherwise the ``config.json``
    install default (``traffic.collector_enabled``) applies.
    """
    override = _parse(await get_setting(TRAFFIC_COLLECTOR_ENABLED_KEY))
    if override is not None:
        return override
    return bool(get_app_config().traffic_collector_enabled)


async def set_collector_enabled(enabled: bool) -> None:
    """Persist the operator's runtime choice as a non-secret ``app_settings`` row."""
    await set_setting(TRAFFIC_COLLECTOR_ENABLED_KEY, _TRUE if enabled else _FALSE)
