import json
from collections import Counter

from app.db.security_catalog import DESTINATION, FOUNDATION, manifest


def test_independent_policy_and_grant_inventories() -> None:
    baseline = [json.loads(row) for row in manifest(FOUNDATION)]
    assert {r[2] for r in baseline if r[0] == "policies"} == {"audit_insert", "audit_admin_read"}
    destination = [json.loads(row) for row in manifest(DESTINATION)]
    policies = [r for r in destination if r[0] == "policies"]
    assert len(policies) == 43
    assert Counter(r[1] for r in policies) == {
        "users": 3,
        "user_profiles": 3,
        "user_sessions": 4,
        "email_verification_tokens": 4,
        "password_reset_tokens": 4,
        "email_outbox": 4,
        "locations": 4,
        "institutions": 4,
        "amenities": 4,
        "property_categories": 4,
        "platform_settings": 3,
        "audit_logs": 2,
    }
    assert {r[2] for r in policies if r[1] == "audit_logs"} == {
        "audit_logs_insert",
        "audit_logs_select",
    }
    for row in destination:
        if row[0] == "relations" and row[3] == "r" and row[2] != "alembic_version":
            assert row[5:7] == [True, True]
