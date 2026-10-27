import pytest
from review_app.app.components.database_recovery import is_backup_compatible
from review_app.backend.db.migrations import MIGRATIONS


@pytest.mark.parametrize(
    "version,expected",
    [(None, True), (1, True), (len(MIGRATIONS), True), (len(MIGRATIONS) + 1, False)],
)
def test_backup_compatibility(version, expected):
    assert is_backup_compatible({"schema_version": version}) is expected
