from tools.episode_queue import filter_by_manual_exclusions


def test_default_allow_queues_identifiable_titles_except_manual_denylist():
    manifest = [
        {"title": "Allowed Saga - 01 (1080p)", "episode": 1},
        {"title": "Blocked Saga - 02 (720p)", "episode": 2},
        {"title": "Allowed Saga - 03 (480p)", "episode": 3},
        {"title": "Unclear title without episode metadata"},
    ]

    allowed, counts = filter_by_manual_exclusions(manifest, ["Blocked Saga"])

    assert [item["title"] for item in allowed] == [
        "Allowed Saga - 01 (1080p)",
        "Allowed Saga - 03 (480p)",
        "Unclear title without episode metadata",
    ]
    assert counts == {
        "eligible": 3,
        "excluded": 1,
        "invalid": 0,
    }


def test_manual_exclusion_removes_matching_series():
    manifest = [{"title": "Blocked Saga - 01 (1080p)", "episode": 1}]

    allowed, counts = filter_by_manual_exclusions(manifest, ["Blocked Saga"])

    assert allowed == []
    assert counts["excluded"] == 1
