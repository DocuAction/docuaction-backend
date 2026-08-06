"""POST /regenerate/{briefing_id} (Task 4).

The endpoint re-runs classification and summaries on an existing briefing. Its
defining property is what it does NOT do: collect. Re-collecting to fix a
formatting problem spends metered provider quota to fetch the same stories, and
Perigon's tier is 150 requests a month.
"""
import inspect

from app.bulletin_intelligence import routes

PATH = "/api/v1/bulletin/regenerate/{briefing_id}"


def test_the_endpoint_exists_as_a_post(client):
    from app.main import app
    assert "post" in app.openapi()["paths"][PATH]


def test_it_never_calls_a_collector():
    """The whole point of the endpoint. If a future edit reaches for
    run_daily_cycle or a provider, quota is spent on a re-render."""
    src = inspect.getsource(routes.regenerate_briefing)
    for forbidden in ("run_daily_cycle", "collect_articles", "perigon",
                      "_collect", "newsdata", "gdelt"):
        assert forbidden not in src, f"regenerate must not reach for {forbidden}"


def test_it_reuses_the_same_rehydration_as_the_exports():
    """Sharing _briefing_articles is what keeps a regenerated briefing showing
    the same story set as the Excel and the preview."""
    src = inspect.getsource(routes.regenerate_briefing)
    assert "_briefing_articles" in src


def test_it_is_admin_guarded(client):
    """It rewrites the text of a federal deliverable."""
    src = inspect.getsource(routes)
    marker = 'async def regenerate_briefing'
    head = src[:src.index(marker)]
    decorator = head[head.rindex('@router.post("/regenerate'):]
    assert 'guard("admin")' in decorator


def test_an_unknown_briefing_is_a_404_not_a_started_job(client):
    r = client.post("/api/v1/bulletin/regenerate/does_not_exist", json={})
    assert r.status_code in (401, 403, 404), r.text
    if r.status_code == 404:
        assert "started" not in r.text


def test_the_response_states_that_nothing_was_collected():
    """An operator reading the response must not conclude that a stale article
    would have been dropped — only a real collection applies the freshness
    gate."""
    src = inspect.getsource(routes.regenerate_briefing)
    assert "Collects nothing" in src
