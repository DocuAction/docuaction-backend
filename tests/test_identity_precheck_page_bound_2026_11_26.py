"""The identity pre-check page must be able to return within the App Service gateway timeout (2026-11-26).

Measured on DEV against the real November delivery: ~6 s per call + ~0.20 s per member. A page of 2,000 needed
~410 s, so the response never reached the caller (230 s front-end timeout) while the server kept working. These
tests pin the bound on both the service function and the route so neither can drift back above it. They do not
touch the identity logic itself; paging coverage is pinned by
test_review_cycle_identity_precheck_2026_11_25.test_paging_covers_every_member_exactly_once_and_the_pages_add_up.
"""
import inspect

from app.tefca_registry import review_cycle as rc
from app.tefca_registry import workflow_routes as wr

#: Observed binding limit: the browser re-sent a GET that had no response after ~60 s (two server executions
#: logged 60 s apart). App Service's own front-end timeout is 230 s. Measured DEV cost model below.
GATEWAY_TIMEOUT_S = 60
FIXED_S, PER_MEMBER_S = 6.0, 0.20


def _route_limit_param():
    return inspect.signature(wr.review_cycle_identity_precheck_route).parameters["limit"].default


def _bound(field, name):
    for item in getattr(field, "metadata", []) or []:
        if hasattr(item, name):
            return getattr(item, name)
    return getattr(field, name, None)


def test_service_bounds_fit_the_gateway_timeout():
    assert 1 <= rc.PRECHECK_DEFAULT_LIMIT <= rc.PRECHECK_MAX_LIMIT
    assert FIXED_S + PER_MEMBER_S * rc.PRECHECK_MAX_LIMIT < GATEWAY_TIMEOUT_S
    # the default leaves a quarter of the limit as headroom for a loaded instance
    assert FIXED_S + PER_MEMBER_S * rc.PRECHECK_DEFAULT_LIMIT < GATEWAY_TIMEOUT_S * 0.75


def test_route_default_and_maximum_match_the_service():
    field = _route_limit_param()
    assert field.default == rc.PRECHECK_DEFAULT_LIMIT
    assert _bound(field, "le") == rc.PRECHECK_MAX_LIMIT
    assert _bound(field, "ge") == 1
