"""Where gen9-agent's database is (settings.DatabaseSettings): gen9-postgres, or a server of one's
own, reached with TLS when `DATABASE_SSLMODE` asks for it (docs/operations.md, "External
services"). Every connection takes the same URL: SQLAlchemy's, the checkpointer's pool and the
event hub's libpq string."""

import pytest
from pydantic import ValidationError

from gen9_agent.settings import DatabaseSettings


def settings(**env: str) -> DatabaseSettings:
    return DatabaseSettings.model_validate({"database_password": "pw", **env})


def test_tls_when_the_server_offers_it_by_default():
    url = settings().database_url
    assert url.query == {"sslmode": "prefer"}
    assert settings().database_conninfo.endswith("/gen9_agent?sslmode=prefer")


def test_a_server_elsewhere_with_tls_required():
    s = settings(
        database_host="db.example", database_port="6432", database_sslmode="require"
    )
    assert s.database_url.host == "db.example" and s.database_url.port == 6432
    assert (
        s.database_conninfo
        == "postgresql://gen9_agent:pw@db.example:6432/gen9_agent?sslmode=require"
    )


def test_an_unknown_sslmode_is_refused():
    with pytest.raises(ValidationError):
        settings(database_sslmode="on")


def test_its_certificate_checked_against_a_ca_of_ones_own():
    s = settings(
        database_sslmode="verify-full",
        database_sslrootcert="/etc/gen9/certs/db-ca.pem",
    )
    assert s.database_url.query == {
        "sslmode": "verify-full",
        "sslrootcert": "/etc/gen9/certs/db-ca.pem",
    }
    assert s.database_conninfo.endswith(
        "?sslmode=verify-full&sslrootcert=%2Fetc%2Fgen9%2Fcerts%2Fdb-ca.pem"
    )
