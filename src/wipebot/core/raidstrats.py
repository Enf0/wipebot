"""raidstrats.gg: a raid plan an officer saves by its link, fetched so nobody pastes its export.

raidstrats.gg's plan REST API reads any plan that isn't a guild's own, Viserio's defaults included, with a key
raidstrats.gg gives out (RSGG_REST_API_KEY). The link Viserio's assignment note embeds names the plan and the night's
raiders on its spots, so `from_links` fetches the plan and writes those names on it, and it is saved as a pasted
plan. A guild's own plan is saved by pasting its export. Facts verified live are in docs/reference/sources.md.
"""
import logging
import os

import httpx

from .report import FactError
from .report import plan_export as plans

log = logging.getLogger(__name__)

BASE = "https://raidstrats.gg"
TIMEOUT_S = 10
UA = {"User-Agent": "Wipebot"}


def key() -> str | None:
    """The plan REST API's key, or None where it isn't set up."""
    return os.environ.get("RSGG_REST_API_KEY") or None


REFUSED = {  # why the plan REST API didn't give a plan, by its code
    "guild_plan": "it's a guild's own plan, which only a login reads: paste its export instead (the planner's "
                  "Export button)",
    "not_found": "raidstrats.gg has no such plan: copy the link again",
    "addon_export_missing": "it has no export yet: open it on raidstrats.gg and save it once",
}


async def from_links(text: str) -> str:
    """A paste of raidstrats.gg plan links as the plans' exports, the raiders each link names written on its
    spots, for saving as a pasted plan; any other text as it is. A link with no raiders gives the plan as saved
    there. Raises FactError, for the officer, where there's no key or a plan can't be read."""
    if plans.EXPORT.search(text) or not (links := plans.links(text)):
        return text
    api_key = key()
    if not api_key:
        raise FactError("this Wipebot can't fetch a plan by its link (whoever runs it sets RSGG_REST_API_KEY): "
                        "paste the plan's export instead, the section's RSGG Addon button in Viserio")
    ids = list(dict.fromkeys(link.plan_id for link in links))[:50]
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT_S,
                                     headers={**UA, "Authorization": f"Bearer {api_key}"}) as http:
            r = await http.post("/api/plans/addon-export", json={"planIds": ids})
        refused = r.status_code in (401, 403)
        if not refused:
            r.raise_for_status()
            results = {str(x.get("planId")): x for x in r.json().get("results") or []}
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as e:
        log.warning("fetching plans by link from raidstrats.gg failed: %r", e)
        raise FactError(f"raidstrats.gg didn't answer ({type(e).__name__}): try again, or paste the plan's "
                        "export") from e
    if refused:
        log.warning("raidstrats.gg refused the plan REST API key: %s", r.status_code)
        raise FactError("raidstrats.gg refused this Wipebot's key, so the plan wasn't fetched: paste its export "
                        "instead, the section's RSGG Addon button in Viserio")
    exports = []
    for link in links:
        result = results.get(link.plan_id) or {}
        if not (result.get("ok") and result.get("addonExport")):
            why = REFUSED.get(str(result.get("code")), "raidstrats.gg didn't give it")
            raise FactError(f"the plan at {BASE}/planner?view={link.plan_id} wasn't fetched: {why}")
        exports.append(plans.with_names(result["addonExport"], link))
    return "\n".join(exports)
