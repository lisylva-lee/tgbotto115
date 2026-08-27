import pytest

from core.db import ShareDB


@pytest.fixture()
def db(tmp_path):
    return ShareDB(tmp_path / "share.db")


def test_dirs_crud(db):
    assert db.list_dirs() == {}
    db.add_dir("电影", "111")
    db.add_dir("电视剧", "222")
    assert db.list_dirs() == {"电影": "111", "电视剧": "222"}
    db.remove_dir("电影")
    assert db.list_dirs() == {"电视剧": "222"}


def test_add_dir_replaces_same_name(db):
    db.add_dir("电影", "111")
    db.add_dir("电影", "333")
    assert db.list_dirs() == {"电影": "333"}


def test_default_cids_crud(db):
    assert db.get_default_cids() == {}
    db.set_default_cid("share", "111", "电影")
    db.set_default_cid("offline", "222", "电视剧")
    d = db.get_default_cids()
    assert d["share"] == {"cid": "111", "name": "电影"}
    assert d["offline"] == {"cid": "222", "name": "电视剧"}
    db.remove_default_cid("share")
    assert "share" not in db.get_default_cids()


def test_user_cid_crud(db):
    assert db.get_user_cid(100, "share") is None
    db.set_user_cid(100, "share", "111", "电影")
    assert db.get_user_cid(100, "share") == {"cid": "111", "name": "电影"}
    assert db.get_user_cid(200, "share") is None
    assert db.get_user_all(100) == {"share": {"cid": "111", "name": "电影"}}
    db.remove_user_all(100)
    assert db.get_user_all(100) == {}


def test_links_append(db):
    db.append_links(100, [{"share_code": "aaa", "receive_code": "123"}])
    # 通过 transfer 层读回不做断言，这里只保证不报错且可重复
    db.append_links(200, [{"share_code": "bbb", "receive_code": None}])
    db.append_links(100, [])


def test_transfer_log_upsert_and_load(db):
    db.upsert_transfer_log({
        "key": "aaa_123",
        "kind": "share",
        "share_code": "aaa",
        "receive_code": "123",
        "status": "success",
        "target_cid": "111",
    })
    db.upsert_transfer_log({
        "key": "aaa_123",
        "kind": "share",
        "share_code": "aaa",
        "receive_code": "123",
        "status": "failed",
        "target_cid": "999",
    })
    log = db.load_transfer_log()
    assert log["aaa_123"]["status"] == "failed"
    assert log["aaa_123"]["target_cid"] == "999"


def test_reset_clears_all_tables(db):
    db.add_dir("电影", "111")
    db.set_default_cid("share", "111")
    db.set_user_cid(1, "share", "111")
    db.append_links(1, [{"share_code": "a"}])
    db.upsert_transfer_log({"key": "k1", "kind": "share", "status": "success"})
    db.reset()
    assert db.list_dirs() == {}
    assert db.get_default_cids() == {}
    assert db.get_user_all(1) == {}
    assert db.load_transfer_log() == {}


def test_db_persists_after_reopen(db, tmp_path):
    db.add_dir("电影", "111")
    db2 = ShareDB(tmp_path / "share.db")
    assert db2.list_dirs() == {"电影": "111"}