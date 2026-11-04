"""A stopgap for warcraft_cli 0.6.0's Wowhead client (wowhead_cli): its item tooltip is the base item, but a worn
piece's item level and secondary stats come from its bonus IDs (an upgrade track, a crafted piece's stats, a
Catalyst piece), which Wowhead's tooltip takes as `bonus=a:b:c`. `patch()` gives `wowhead entity item` a
`--bonus a:b:c` option that adds them; `fetch` applies it in the Wowhead command's process. Remove this module once
a warcraft_cli release takes bonus IDs."""
import sys


def bonus_arg(argv: list[str]) -> str | None:
    """Takes `--bonus a:b:c` out of the command line, and gives its value."""
    if "--bonus" not in argv:
        return None
    i = argv.index("--bonus")
    value = argv[i + 1] if i + 1 < len(argv) else ""
    del argv[i:i + 2]
    return value or None


def patch() -> None:
    bonus = bonus_arg(sys.argv)
    if not bonus:
        return
    from wowhead_cli.wowhead_client import WowheadClient

    tooltip = WowheadClient.tooltip_with_metadata
    request = WowheadClient._request_with_retries

    def tooltip_with_metadata(self, entity_type, entity_id, **kwargs):
        self._bonus = bonus if entity_type == "item" else None
        return tooltip(self, entity_type, entity_id, **kwargs)

    def _request_with_retries(self, url, *, params=None):
        if getattr(self, "_bonus", None) and "/tooltip/item/" in url:
            params = {**(params or {}), "bonus": self._bonus}
        return request(self, url, params=params)

    WowheadClient.tooltip_with_metadata = tooltip_with_metadata  # type: ignore[method-assign]
    WowheadClient._request_with_retries = _request_with_retries  # type: ignore[method-assign]
    # The client's own caches key an item by its id alone: with bonus IDs, the answer is kept by `fetch` instead
    WowheadClient._read_cache = lambda self, key: None  # type: ignore[method-assign]
    WowheadClient._write_cache = lambda self, key, payload, *, ttl_seconds: None  # type: ignore[method-assign]
