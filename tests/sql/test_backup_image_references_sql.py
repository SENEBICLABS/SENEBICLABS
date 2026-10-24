"""
backup_verify.py's image-reference discovery, on a real Postgres, for every form in which an
item can point at an object in the `client-images` bucket. storage_backup.py --expect checks
that every one of these is in the image backup, so a form missed here is an image that could
be missing from a backup without anything saying so.
"""
import json
import re
import uuid

import psycopg
import pytest

from scripts import backup_verify as B

# The query as it was before, kept only to show what it missed.
OLD_IMAGE_REFERENCES = r"""
select distinct coalesce(content->>'_image_key',
         substring(content->>'image' from '/object/(?:sign|public|authenticated)/client-images/([^?]+)'))
  from public.project_items
 where content ? '_image_key' or content->>'image' like '%/client-images/%'
"""

BASE = "https://abc.supabase.co/storage/v1/object"
KEY = re.compile(r"[0-9a-f]{16}/[0-9a-f]{32}\.[a-z0-9]{1,5}")
K = {n: f"{n:x}".rjust(16, "a") + "/" + f"{n:x}".rjust(32, "b") + ".png" for n in range(1, 10)}

# name -> (content, the references it must yield)
FORMS = {
    "portal: key beside a signed URL": (
        {"image": f"{BASE}/sign/client-images/{K[1]}?token=t", "_image_key": K[1]}, {K[1]}),
    "signed URL alone (older portal and operator ingest)": (
        {"image": f"{BASE}/sign/client-images/{K[2]}?token=abc.def"}, {K[2]}),
    "public URL": ({"image": f"{BASE}/public/client-images/{K[3]}"}, {K[3]}),
    "authenticated URL with a fragment": ({"image": f"{BASE}/authenticated/client-images/{K[4]}#x"}, {K[4]}),
    "bare key in image": ({"image": K[5]}, {K[5]}),
    "%-encoded path": ({"image": f"{BASE}/sign/client-images/{K[6].replace('/', '%2F')}?token=t"}, {K[6]}),
    "key and an older URL to a different object": (
        {"image": f"{BASE}/sign/client-images/{K[7]}?token=t", "_image_key": K[8]}, {K[7], K[8]}),
}
NOT_OURS = {
    "a client's own https image": {"image": "https://cdn.client.example/x/1.png"},
    "text review item": {"text": "a scenario", "prediction": "urgent", "scenario": "s"},
    "an image that is not a string": {"image": {"url": f"{BASE}/sign/client-images/{K[9]}"}},
    "empty image": {"image": ""},
    "content that is not an object": [f"{BASE}/sign/client-images/{K[9]}"],
}


@pytest.fixture
def conn(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        yield c


def _project(c):
    pid = str(uuid.uuid4())
    c.execute("insert into project_submissions (id, name, email, company, description)"
              " values (%s, 'P', 'c@x.com', 'Co', 'd')", (pid,))
    return pid


def _items(c, contents, status="queued"):
    pid = _project(c)
    for i, content in enumerate(contents):
        c.execute("insert into project_items (project_id, idx, content, status) values (%s, %s, %s, %s)",
                  (pid, i, json.dumps(content), status))


def _old(c):
    return {r[0] for r in c.execute(OLD_IMAGE_REFERENCES) if r[0]}


@pytest.mark.parametrize("form", list(FORMS))
def test_every_form_of_reference_is_found(conn, form):
    content, refs = FORMS[form]
    _items(conn, [content])
    assert set(B.image_references(conn)) == refs


@pytest.mark.parametrize("form", list(NOT_OURS))
def test_nothing_else_is_taken_for_a_reference(conn, form):
    _items(conn, [NOT_OURS[form]])
    assert B.image_references(conn) == []


def test_what_the_old_query_missed(conn):
    _items(conn, [FORMS["bare key in image"][0]])
    assert _old(conn) == set()
    _items(conn, [FORMS["%-encoded path"][0]])
    assert _old(conn) == {K[6].replace("/", "%2F")}, "the old query returned an encoded, unmatchable key"
    _items(conn, [FORMS["key and an older URL to a different object"][0]])
    assert K[7] not in _old(conn)


def test_everything_the_old_query_found_is_still_found(conn):
    _items(conn, [content for content, _ in FORMS.values()] + list(NOT_OURS.values()))
    old, new = _old(conn), set(B.image_references(conn))
    # Every real key the old query returned is still returned. What else it returned was
    # never a key (an encoded path, a URL fragment, the tail of a JSON object), so it could
    # never have matched an object in the image backup.
    assert {r for r in old if KEY.fullmatch(r)} <= new
    assert {r for r in old if not KEY.fullmatch(r)} == {
        K[6].replace("/", "%2F"), K[4] + "#x", K[9] + '"}'}
    assert new == set().union(*(refs for _, refs in FORMS.values()))


def test_superseded_versions_still_count(conn):
    _items(conn, [FORMS["signed URL alone (older portal and operator ingest)"][0]], status="superseded")
    assert B.image_references(conn) == [K[2]]


def test_each_reference_once_and_sorted(conn):
    _items(conn, [FORMS["bare key in image"][0]] * 3 + [FORMS["public URL"][0]])
    assert B.image_references(conn) == sorted({K[5], K[3]})


def test_an_app_without_items_refers_to_no_images(dsn_before_native):
    with psycopg.connect(dsn_before_native, autocommit=True) as c:
        c.execute("drop table project_items cascade")
        assert B.image_references(c) == []
