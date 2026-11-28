"""key_url in the catalog: free-provider key pages are exposed through the
providers API and seeded into the DB (with a startup migration for old DBs)."""
from conftest import signup


def test_providers_api_exposes_key_url(app_client):
    cl = app_client()
    signup(cl, "keyhelp@example.com")
    cat = cl.ok("get", "/api/v1/providers")["catalog"]
    by_id = {g["id"]: g for g in cat}
    assert by_id["hf_space_ltx"]["key_url"] == "https://huggingface.co/settings/tokens"
    assert by_id["replicate"]["key_url"] == "https://replicate.com/account/api-tokens"
    # mock/manual providers need no key, so they carry none
    assert not by_id["manual"]["key_url"]


def test_key_url_column_survives_existing_db(env):
    """The startup ALTER adds key_url to a DB created before the column existed."""
    from app.database.session import init_db, new_session
    from app.database.models import GenerationProvider
    init_db()          # runs create_all + the additive ALTER
    db = new_session()
    try:
        row = db.get(GenerationProvider, "hf_space_ltx")
        assert row is not None and "huggingface.co/settings/tokens" in (row.key_url or "")
    finally:
        db.close()