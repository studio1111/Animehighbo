from tools.episode_queue import filter_by_publication_approval


def test_default_allow_queues_identifiable_titles_except_manual_denylist():
    manifest = [
        {"title": "Allowed Saga - 01 (1080p)", "episode": 1},
        {"title": "Blocked Saga - 02 (720p)", "episode": 2},
        {"title": "Allowed Saga - 03 (480p)", "episode": 3},
        {"title": "Unclear title without episode metadata"},
    ]

    allowed, counts = filter_by_publication_approval(manifest, {
        "policy_version": 1,
        "default_decision": "allow",
        "approved_series": [],
        "denied_series": ["Blocked Saga"],
    })

    assert [item["title"] for item in allowed] == [
        "Allowed Saga - 01 (1080p)",
        "Allowed Saga - 03 (480p)",
    ]
    assert counts == {
        "approved": 2,
        "pending": 0,
        "denied": 1,
        "ambiguous": 1,
    }


def test_manual_deny_overrides_legacy_approval():
    manifest = [{"title": "Blocked Saga - 01 (1080p)", "episode": 1}]

    allowed, counts = filter_by_publication_approval(manifest, {
        "default_decision": "allow",
        "approved_series": ["Blocked Saga"],
        "denied_series": ["Blocked Saga"],
    })

    assert allowed == []
    assert counts["denied"] == 1
