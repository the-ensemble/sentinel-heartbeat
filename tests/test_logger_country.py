import datetime

import heartbeat_logger as hl


IODA_SIGNALS_SAMPLE = {
    "data": [[
        {"datasource": "bgp", "from": 1000, "step": 300, "values": [100, 110, None, 120, None]},
        {"datasource": "ping-slash24", "from": 1000, "step": 600, "values": [50, None]},
        {"datasource": "merit-nt", "from": 1000, "step": 300, "values": [None, None]},
        {"datasource": "gtr-norm", "from": 1000, "step": 1800, "values": [0.31]},
        {"datasource": "ping-slash24-loss", "from": 1000, "step": 600, "values": [[{"agg_values": {}}]]},
    ]]
}


def test_parse_ioda_signals_takes_last_non_null_value_with_timestamp():
    out = hl.parse_ioda_signals(IODA_SIGNALS_SAMPLE)
    assert out["bgp"] == {"value": 120, "as_of": "1970-01-01T00:31:40Z"}  # 1000 + 3*300
    assert out["ping"] == {"value": 50, "as_of": "1970-01-01T00:16:40Z"}
    assert out["telescope"] is None
    assert out["gtr_norm"] == {"value": 0.31, "as_of": "1970-01-01T00:16:40Z"}
    assert "ping-slash24-loss" not in out


def test_parse_ioda_signals_empty():
    assert hl.parse_ioda_signals({"data": []}) == {"bgp": None, "ping": None, "telescope": None, "gtr_norm": None}


IODA_ALERTS_SAMPLE = {
    "data": [
        {"datasource": "merit-nt", "level": "critical", "time": 1788822240, "condition": "< 0.25"},
        {"datasource": "merit-nt", "level": "normal", "time": 1788822900, "condition": "normal"},
        {"datasource": "bgp", "level": "warning", "time": 1788823000, "condition": "< 0.9"},
        {"datasource": "gtr", "level": "critical", "time": 1788823500, "condition": "< 0.8"},
    ]
}


def test_parse_ioda_alerts_reports_active_datasources_and_worst_level():
    out = hl.parse_ioda_alerts(IODA_ALERTS_SAMPLE)
    assert out["count"] == 4
    assert out["latest"]["merit-nt"]["level"] == "normal"
    assert out["latest"]["bgp"]["level"] == "warning"
    assert out["active"] == {"bgp": "warning", "gtr": "critical"}
    assert out["worst"] == "critical"


def test_parse_ioda_alerts_none_active():
    out = hl.parse_ioda_alerts({"data": [{"datasource": "bgp", "level": "normal", "time": 1}]})
    assert out["active"] == {}
    assert out["worst"] is None


COUNTRY_RIS_SAMPLE = {
    "data": {
        "stats": [
            {"stats_date": "2026-10-01T00:00:00", "asns_ris": 244, "v4_prefixes_ris": 9419, "v6_prefixes_ris": 1210},
            {"stats_date": "2026-10-02T00:00:00", "asns_ris": 244, "v4_prefixes_ris": 9403, "v6_prefixes_ris": 1212},
            {"stats_date": "2026-10-03T00:00:00", "asns_ris": 243, "v4_prefixes_ris": 9406.5, "v6_prefixes_ris": 1215},
        ]
    }
}


def test_parse_country_ris_latest_and_previous_day():
    out = hl.parse_country_ris(COUNTRY_RIS_SAMPLE)
    assert out["stats_date"] == "2026-10-03"
    assert out["asns"] == 243
    assert out["v4_prefixes"] == 9406.5
    assert out["v6_prefixes"] == 1215
    assert out["prev"] == {"stats_date": "2026-10-02", "asns": 244, "v4_prefixes": 9403, "v6_prefixes": 1212}


def test_parse_country_ris_single_point_has_no_prev():
    one = {"data": {"stats": COUNTRY_RIS_SAMPLE["data"]["stats"][-1:]}}
    assert hl.parse_country_ris(one)["prev"] is None


def test_hourly_gate():
    assert hl.is_hourly_sample(datetime.datetime(2026, 10, 3, 4, 2))
    assert hl.is_hourly_sample(datetime.datetime(2026, 10, 3, 4, 4, 59))
    assert not hl.is_hourly_sample(datetime.datetime(2026, 10, 3, 4, 5))
    assert not hl.is_hourly_sample(datetime.datetime(2026, 10, 3, 4, 37))


def test_build_tasks_includes_country_routing_only_on_hourly_runs():
    sections_hourly = {t[0] for t in hl.build_tasks(hourly=True)}
    sections_other = {t[0] for t in hl.build_tasks(hourly=False)}
    assert "ioda" in sections_hourly and "ioda" in sections_other
    assert "country_routing" in sections_hourly
    assert "country_routing" not in sections_other


def test_taipei_endpoint_registered():
    assert "ap-east-2" in hl.ENDPOINTS
