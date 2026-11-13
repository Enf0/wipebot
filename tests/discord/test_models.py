"""/wipebot setup model: the models a server's own key reaches, and choosing one for its answers."""
import httpx
import pytest

from wipebot.discord import commands, keys, models

OPENROUTER = [
    {"id": "openai/gpt-small", "name": "OpenAI: GPT Small", "supported_parameters": ["tools"], "created": 5,
     "pricing": {"prompt": "0.00000025", "completion": "0.000002"}},
    {"id": "anthropic/claude-old", "name": "Anthropic: Claude Old", "supported_parameters": ["tools"], "created": 1,
     "pricing": {"prompt": "0.000004", "completion": "0.00002"}},
    {"id": "anthropic/claude-new", "name": "Anthropic: Claude New", "supported_parameters": ["tools"], "created": 9,
     "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}},
    {"id": "anthropic/claude-new:batch", "name": "Anthropic: Claude New (batch)", "supported_parameters": ["tools"]},
    {"id": "some/no-tools", "name": "No tools", "supported_parameters": ["temperature"]},
    {"id": "some/painter", "name": "Painter", "supported_parameters": ["tools"],
     "architecture": {"output_modalities": ["image"]}},
]
KEY = keys.ServerKey("openrouter", "sk-or-v1-test")


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    """The providers' model lists, answered here; what was asked for."""
    asked = []

    class Http:
        def __init__(self, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): ...

        async def get(self, url, **kw):
            asked.append((url, kw["headers"]))
            if "broken" in str(kw["headers"]):
                raise httpx.ConnectError("no route")
            data = OPENROUTER if "openrouter" in url else [{"id": "claude-haiku-9", "display_name": "Claude Haiku 9"}]
            return httpx.Response(200, json={"data": data}, request=httpx.Request("GET", url))
    monkeypatch.setattr(models.httpx, "AsyncClient", Http)
    models._kept.clear()
    return asked


async def test_an_openrouter_key_lists_the_models_that_call_tools_claudes_first(provider):
    found = await models.available(KEY)
    assert [m.id for m in found] == ["anthropic/claude-new", "anthropic/claude-old", "openai/gpt-small"]
    assert found[0].label() == "Anthropic: Claude New ($0.10 in, $0.50 out per million tokens)"
    assert found[1].label() == "Anthropic: Claude Old ($4 in, $20 out per million tokens)"
    assert provider[0][1] == {"Authorization": "Bearer sk-or-v1-test"}
    await models.available(KEY)
    assert len(provider) == 1  # kept: the option asks again at every letter typed


async def test_an_anthropic_key_lists_anthropics_models():
    found = await models.available(keys.ServerKey("anthropic", "sk-ant-test"))
    assert [(m.id, m.label()) for m in found] == [("claude-haiku-9", "Claude Haiku 9")]


async def test_the_option_shows_the_default_then_what_matches_the_typing(store):
    store.save_server_key(1001, KEY, 7)
    shown = await commands.model_choices(store, 1001, "", "opus")
    assert [(c.name, c.value) for c in shown][:2] == [
        ("My default (opus)", "default"),
        ("Anthropic: Claude New ($0.10 in, $0.50 out per million tokens)", "anthropic/claude-new")]
    assert [c.value for c in await commands.model_choices(store, 1001, "gpt small", "opus")] == ["openai/gpt-small"]


async def test_the_option_says_why_it_has_no_models(store):
    assert "own AI key first" in (await commands.model_choices(store, 1001, "", "opus"))[0].name
    store.save_server_key(1001, keys.ServerKey("openrouter", "sk-or-broken"), 7)
    assert "Couldn't ask OpenRouter" in (await commands.model_choices(store, 1001, "", "opus"))[0].name


async def test_a_chosen_model_is_the_servers_until_default_or_the_key_goes(store):
    assert await commands.choose_model(store, 1001, "openai/gpt-small", "opus") == commands.NO_KEY_FOR_MODEL
    store.save_server_key(1001, KEY, 7)
    assert "reaches no model `made/up`" in await commands.choose_model(store, 1001, "made/up", "opus")
    assert store.model(1001) is None

    told = await commands.choose_model(store, 1001, "anthropic/claude-new", "opus")
    assert "now run on **Anthropic: Claude New**" in told and "only tested" not in told
    assert store.model(1001) == "anthropic/claude-new"
    key = store.server_key(1001)
    assert key is not None and key.env(store.model(1001))[keys.MODEL_ENV] == "anthropic/claude-new"
    assert keys.MODEL_ENV not in key.env(None)

    assert "only tested on the Claude models" in await commands.choose_model(store, 1001, "openai/gpt-small", "opus")
    assert "default model again (opus)" in await commands.choose_model(store, 1001, "Default", "opus")
    assert store.model(1001) is None

    await commands.choose_model(store, 1001, "openai/gpt-small", "opus")
    store.save_server_key(1001, KEY, 7)  # the same provider's key again: its models are named the same
    assert store.model(1001) == "openai/gpt-small"
    store.save_server_key(1001, keys.ServerKey("anthropic", "sk-ant-test"), 7)
    assert store.model(1001) is None
    await commands.choose_model(store, 1001, "claude-haiku-9", "opus")
    store.drop_server_key(1001)
    assert store.model(1001) is None


HAIKU = {"id": "anthropic/claude-haiku-5.5", "pricing": {
    "prompt": "0.0000001", "completion": "0.0000005", "input_cache_read": "0.00000001",
    "input_cache_write": "0.000000125",
    "overrides": [{"min_prompt_tokens": 100000, "prompt": "0.0000005", "completion": "0.0000025",
                   "input_cache_read": "0.00000005", "input_cache_write": "0.000000625"}]}}


def used(read=0, written=0, cache_read=0, cache_written=0, cost=0.0) -> dict:
    return {"inputTokens": read, "outputTokens": written, "cacheReadInputTokens": cache_read,
            "cacheCreationInputTokens": cache_written, "costUSD": cost}


def test_an_answer_costs_its_own_tokens_at_openrouters_price_for_the_model():
    """Claude Code counted this answer on Haiku at $0.25: it knows no price for a model under OpenRouter's name."""
    rates = models.rates_from_openrouter([HAIKU, {"id": "openrouter/auto", "pricing": {"prompt": "-1",
                                                                                         "completion": "-1"}}])
    assert list(rates) == ["anthropic/claude-haiku-5.5"]
    before = {"anthropic/claude-haiku-5.5": used(1000, 500, 20_000, 30_000, cost=0.4)}
    now = {"anthropic/claude-haiku-5.5": used(1100, 800, 80_000, 32_000, cost=0.65),
           "claude-haiku-4-5": used(10, 10, cost=0.001)}  # one Claude Code prices itself
    cost = models.cost(now, before, rates)
    assert cost == pytest.approx(100 * 1e-7 + 300 * 5e-7 + 60_000 * 1e-8 + 2000 * 1.25e-7 + 0.001)
    # a prompt past the model's long tier costs that tier's rates, and counts that start over are the answer's own
    assert models.cost(now, before, rates, 120_000) == pytest.approx(5 * (cost - 0.001) + 0.001)
    assert models.cost(before, now, rates) == pytest.approx(models.cost(before, {}, rates))
