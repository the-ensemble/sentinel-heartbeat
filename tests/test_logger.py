import heartbeat_logger as hl


RIPESTAT_SAMPLE = {
    "data": {
        "visibility": {
            "v4": {"ris_peers_seeing": 310, "total_ris_peers": 325},
            "v6": {"ris_peers_seeing": 13, "total_ris_peers": 315},
        },
        "announced_space": {"v4": {"prefixes": 2390, "ips": 2461696}, "v6": {"prefixes": 2, "48s": 2}},
        "observed_neighbours": 41,
    }
}


def test_parse_bgp_reads_visibility_and_prefixes():
    out = hl.parse_bgp(RIPESTAT_SAMPLE)
    assert out["v4_visibility"] == 0.954
    assert out["v4_peers_seeing"] == 310
    assert out["v4_total_peers"] == 325
    assert out["announced_prefixes"] == 2390
    assert out["announced_prefixes_v6"] == 2
    assert out["v6_visibility"] == 0.041
    assert out["observed_neighbours"] == 41


def test_parse_bgp_handles_missing_fields():
    out = hl.parse_bgp({"data": {}})
    assert out["v4_visibility"] == 0
    assert out["announced_prefixes"] == 0


def test_summarize_aircraft_counts_military_and_gps_degraded():
    ac = [
        {"hex": "a", "nic": 8},
        {"hex": "b", "nic": 5, "dbFlags": 1},
        {"hex": "c", "nic": 0},
        {"hex": "d"},  # MLAT-only, no NIC
        {"hex": "e", "nic": 9, "dbFlags": 3},
    ]
    out = hl.summarize_aircraft(ac)
    assert out == {
        "aircraft_count": 5,
        "military_count": 2,
        "gps_sampled": 4,
        "gps_degraded_count": 2,
    }


def test_summarize_aircraft_empty():
    assert hl.summarize_aircraft([])["aircraft_count"] == 0


def test_section_summary_counts_ok_and_errors():
    results = [{"status": "ok"}, {"status": "error"}, {"status": "ok"}]
    assert hl.section_summary(results) == {"total": 3, "ok": 2, "errors": 1}


def test_zone_configs_have_valid_radius():
    for name, z in hl.FLIGHT_ZONES.items():
        assert 0 < z["radius_nm"] <= 250, name
