import base64
from datetime import date

import httpx
import pytest

from sprout.sources.companies_house import CompaniesHouseClient, make_http_client, parse_hit
from sprout.sources.http import FetchError

ADVANCED_ITEM = {
    "company_name": "ACME MACHINE LEARNING LTD",
    "company_number": "12345678",
    "company_status": "active",
    "date_of_creation": "2019-03-04",
    "sic_codes": ["62012"],
    "registered_office_address": {"locality": "London", "postal_code": "EC1A 1BB"},
}
SEARCH_ITEM = {
    "title": "BETA AI LTD",
    "company_number": "sc123456",
    "company_status": "active",
    "date_of_creation": "2021-01-01",
    "address": {"locality": "Edinburgh"},
}


def test_parse_hit_handles_both_response_shapes():
    advanced = parse_hit(ADVANCED_ITEM)
    assert (advanced.number, advanced.incorporated, advanced.sic_codes) == (
        "12345678",
        date(2019, 3, 4),
        ("62012",),
    )
    assert advanced.locality == "London"
    assert advanced.url.endswith("/company/12345678")

    search = parse_hit(SEARCH_ITEM)
    assert (search.number, search.name, search.sic_codes) == ("SC123456", "BETA AI LTD", ())


def test_parse_hit_skips_items_without_identity_and_bad_dates():
    assert parse_hit({"company_name": "No number"}) is None
    assert parse_hit({**ADVANCED_ITEM, "date_of_creation": "not-a-date"}).incorporated is None


def test_advanced_search_sends_filters(make_http):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [ADVANCED_ITEM]})

    client = CompaniesHouseClient(make_http(handler))
    hits = client.advanced_search("machine learning", incorporated_from=date(2012, 1, 1), size=5)
    assert [h.number for h in hits] == ["12345678"]
    params = dict(seen[0].url.params)
    assert params["company_name_includes"] == "machine learning"
    assert params["company_status"] == "active"
    assert params["incorporated_from"] == "2012-01-01"
    assert params["size"] == "5"


def test_pagination_stops_at_limit_or_short_page(make_http):
    starts = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start_index"])
        starts.append(start)
        size = int(request.url.params["size"])
        items = [{**ADVANCED_ITEM, "company_number": f"{start + i:08d}"} for i in range(size)]
        return httpx.Response(200, json={"items": items})

    client = CompaniesHouseClient(make_http(handler))
    hits = list(
        client.iter_advanced_search(
            "ai", incorporated_from=date(2012, 1, 1), limit=250, page_size=100
        )
    )
    assert len(hits) == 250
    assert starts == [0, 100, 200]


def test_pagination_stops_when_results_run_out(make_http):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [ADVANCED_ITEM]})

    client = CompaniesHouseClient(make_http(handler))
    hits = list(client.iter_advanced_search("ai", incorporated_from=date(2012, 1, 1), limit=500))
    assert len(hits) == 1


def test_no_results_404_is_an_empty_list(make_http):
    client = CompaniesHouseClient(make_http(lambda r: httpx.Response(404, json={})))
    assert client.advanced_search("zzz", incorporated_from=date(2012, 1, 1)) == []
    assert client.get_company("12345678") is None


def test_company_profile_and_filing_history_urls(make_http):
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"ok": 1})

    client = CompaniesHouseClient(make_http(handler))
    client.get_company("12345678")
    client.filing_history("sc123456")
    assert paths == ["/company/12345678", "/company/SC123456/filing-history"]


@pytest.mark.parametrize("number", ["../etc/passwd", "123", "1234567/x", ""])
def test_invalid_company_numbers_are_rejected_before_any_request(make_http, number):
    client = CompaniesHouseClient(make_http(lambda r: pytest.fail("no request expected")))
    with pytest.raises(ValueError):
        client.get_company(number)


def test_invalid_json_is_a_fetch_error(make_http):
    client = CompaniesHouseClient(make_http(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(FetchError, match="invalid JSON"):
        client.get_company("12345678")


def test_api_key_is_sent_as_basic_auth(tmp_path):
    http = make_http_client("secret-key", tmp_path)
    request = http._client.build_request("GET", "https://api.company-information.service.gov.uk/")
    http._client.auth.sync_auth_flow(request).__next__()
    token = base64.b64decode(request.headers["Authorization"].split()[1]).decode()
    assert token == "secret-key:"
    http.close()
