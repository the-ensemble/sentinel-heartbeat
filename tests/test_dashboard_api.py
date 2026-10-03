import dashboard_api as api


def make_entry(ts, bgp_ok=True, flights_ok=True, prefixes=100, vis=1.0):
    bgp_status = "ok" if bgp_ok else "error"
    return {
        "timestamp": ts,
        "heartbeats": {
            "results": [{"region": "il-central-1", "reachable": True}],
            "summary": {"total": 3, "healthy": 3, "down": 0},
        },
        "probes": {
            "results": [{"country": "IL", "connected": 40, "disconnected": 5, "status": "ok"}],
            "summary": {"total": 5, "ok": 5, "errors": 0},
        },
        "bgp": {
            "results": [
                {"country": "IL", "asn": "AS8551", "name": "Bezeq", "status": bgp_status,
                 "v4_visibility": vis if bgp_ok else None, "announced_prefixes": prefixes if bgp_ok else None},
                {"country": "IL", "asn": "AS1680", "name": "Partner", "status": bgp_status,
                 "v4_visibility": 0.9 if bgp_ok else None, "announced_prefixes": 50 if bgp_ok else None},
            ],
            "summary": {"total": 2, "ok": 2 if bgp_ok else 0, "errors": 0 if bgp_ok else 2},
        },
        "flights": {
            "results": [
                {"zone": "israel", "status": "ok" if flights_ok else "error",
                 "aircraft_count": 10 if flights_ok else None,
                 "military_count": 1 if flights_ok else None,
                 "gps_sampled": 10 if flights_ok else None,
                 "gps_degraded_count": 2 if flights_ok else None},
            ],
            "summary": {"total": 1, "ok": 1 if flights_ok else 0, "errors": 0 if flights_ok else 1},
        },
        "canaries": {
            "results": [{"region": "me-south-1", "reachable": False}],
            "summary": {"total": 1, "healthy": 0, "down": 1},
        },
    }


def test_slim_entry_keeps_only_summaries_and_compact_metrics():
    slim = api.slim_entry(make_entry("2026-10-03T00:00:00Z"))
    assert slim["timestamp"] == "2026-10-03T00:00:00Z"
    assert slim["heartbeats"] == {"total": 3, "healthy": 3, "down": 0}
    assert slim["probes"] == {"total": 5, "ok": 5, "errors": 0}
    assert slim["bgp"] == {"total": 2, "ok": 2, "errors": 0}
    assert slim["bgp_min_visibility"] == 0.9
    assert slim["flights"] == {"total": 1, "ok": 1, "errors": 0}
    assert slim["flights_totals"] == {"aircraft": 10, "military": 1, "gps_sampled": 10, "gps_degraded": 2}
    assert slim["canaries"] == {"me-south-1": False}
    assert "bgp_detail" not in slim
    assert "probe_detail" not in slim
    assert "azure" not in slim


def test_slim_entry_bgp_min_visibility_none_when_all_errors():
    slim = api.slim_entry(make_entry("2026-10-03T00:00:00Z", bgp_ok=False))
    assert slim["bgp_min_visibility"] is None


def test_slim_entry_tolerates_legacy_entries_without_new_sections():
    legacy = make_entry("2026-10-03T00:00:00Z")
    del legacy["flights"]
    del legacy["canaries"]
    legacy["azure_heartbeats"] = {"summary": {"total": 3, "healthy": 0, "down": 3}}
    slim = api.slim_entry(legacy)
    assert "flights" not in slim
    assert slim["canaries"] == {}


def test_carry_forward_uses_last_successful_flight_sample():
    entries = [
        make_entry("2026-10-03T00:00:00Z", flights_ok=True),
        make_entry("2026-10-03T00:05:00Z", flights_ok=False),
        make_entry("2026-10-03T00:10:00Z", flights_ok=False),
    ]
    current = api.carry_forward_flights(entries)
    assert current["flights"]["summary"]["ok"] == 1
    assert current["flights_as_of"] == "2026-10-03T00:00:00Z"


def test_carry_forward_noop_when_current_sample_succeeded():
    entries = [make_entry("2026-10-03T00:00:00Z"), make_entry("2026-10-03T00:05:00Z")]
    current = api.carry_forward_flights(entries)
    assert "flights_as_of" not in current
    assert current["timestamp"] == "2026-10-03T00:05:00Z"


def test_bgp_baseline_is_median_over_ok_samples():
    entries = [
        make_entry("t1", prefixes=100, vis=1.0),
        make_entry("t2", prefixes=104, vis=0.98),
        make_entry("t3", bgp_ok=False),
        make_entry("t4", prefixes=90, vis=0.97),
    ]
    base = api.bgp_baseline(entries)
    assert base["AS8551"] == {"prefixes": 100, "visibility": 0.98, "samples": 3}
    assert base["AS1680"]["prefixes"] == 50


def test_bgp_baseline_ignores_zero_prefix_counts_from_legacy_entries():
    entries = [make_entry("t1", prefixes=0), make_entry("t2", prefixes=0), make_entry("t3", prefixes=120)]
    base = api.bgp_baseline(entries)
    assert base["AS8551"]["prefixes"] == 120
    assert base["AS8551"]["samples"] == 3  # visibility samples still count


def test_bgp_baseline_empty_when_no_data():
    assert api.bgp_baseline([]) == {}
