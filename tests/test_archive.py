import gzip
import io
import json
import urllib.request

from kpi_extractor import archive as archive_module
from kpi_extractor.archive import Archive, exhibits_from_record, exhibits_record
from kpi_extractor.control import ControlError


class Bucket:
    """The control plane and R2 together: links are the keys themselves."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls = []

    def call(self, operation, **payload):
        self.calls.append(operation)
        if operation == "archive_list":
            return {"files": {key: f"get:{key}" for key in self.objects}}
        if operation == "archive_put":
            return {"uploads": {file["key"]: f"put:{file['key']}" for file in payload["files"]}}
        return {}

    def urlopen(self, request, timeout=None):
        if isinstance(request, urllib.request.Request):
            assert request.get_method() == "PUT"
            self.objects[request.full_url.removeprefix("put:")] = request.data
            return io.BytesIO(b"")
        return io.BytesIO(self.objects[request.removeprefix("get:")])


def test_a_filing_archived_once_is_read_back_without_edgar(monkeypatch):
    bucket = Bucket()
    monkeypatch.setattr(archive_module.urllib.request, "urlopen", bucket.urlopen)
    first = Archive(bucket, "NVDA", 1045810, log=lambda *_: None)
    assert first.read_json("0001045810-26-000123", "xbrl.json") is None
    first.write_json("0001045810-26-000123", "xbrl.json", {"parts": {"instance": "<xbrl/>"}})
    assert json.loads(gzip.decompress(bucket.objects["sec/1045810/0001045810-26-000123/xbrl.json.gz"])) == {
        "parts": {"instance": "<xbrl/>"}}
    later = Archive(bucket, "NVDA", 1045810, log=lambda *_: None)
    assert later.read_json("0001045810-26-000123", "xbrl.json") == {"parts": {"instance": "<xbrl/>"}}
    assert later.reads == 1 and bucket.calls.count("archive_list") == 2


def test_an_unreachable_archive_only_means_reading_from_edgar():
    class Down:
        def call(self, operation, **payload):
            raise ControlError("archive_list failed (500)")
    archive = Archive(Down(), "NVDA", 1045810, log=lambda *_: None)
    assert archive.read_json("0001045810-26-000123", "xbrl.json") is None
    archive.write_json("0001045810-26-000123", "xbrl.json", {"parts": {}})  # no error, nothing written
    assert archive.writes == 0


def test_exhibits_round_trip_without_their_attachments():
    class Attachment:
        document, document_type, description, url = "ex99.htm", "EX-99.1", "Press release", "https://www.sec.gov/x/ex99.htm"
        content = "<p>Revenue $1.0 billion</p>"
    restored = exhibits_from_record(json.loads(json.dumps(exhibits_record([Attachment()], 1_000))))
    assert restored[0].content == Attachment.content and restored[0].url == Attachment.url and restored[0].is_html()
