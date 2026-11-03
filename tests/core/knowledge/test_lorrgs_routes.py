"""The stopgap for Lorrgs' routes under a game version (knowledge/lorrgs_routes.py)."""
from wipebot.core.knowledge.lorrgs_routes import route


def test_game_data_moves_under_the_game_version_and_reports_stay():
    assert route("/api/specs/evoker-preservation/spells") == "/api/retail/specs/evoker-preservation/spells"
    assert route("/api/specs") == "/api/retail/specs"
    assert route("/api/spec_ranking/evoker-preservation/the-coiled-altar") == \
        "/api/retail/spec_ranking/evoker-preservation/the-coiled-altar"
    assert route("/api/user_reports/abc/fights") == "/api/user_reports/abc/fights"
    assert route("/api/retail/specs") == "/api/retail/specs"
    assert route("/api/specsx") == "/api/specsx"
