"""Offline tests of remote-read failure handling: error classification, whole-read retries with backoff and
fresh URLs, SAS re-signing, message sanitising and skipped-scene accounting. No network access; the raster
read itself is replaced by a fake."""
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from rasterio.errors import RasterioIOError

from src import eo, river

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
PC_HREF = ("https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/44/R/LS/2026/01/25/x/T44RLS_B03_10m.tif"
           "?st=2026-09-30T11%3A00%3A00Z&se={se}&sp=rl&sv=2024-05-04&sr=c&sig=c2VjcmV0c2lnbmF0dXJl%3D")


def pc_href(expires):
    return PC_HREF.format(se=expires.strftime("%Y-%m-%dT%H%%3A%M%%3A%SZ"))


def failure(message, cause=None):
    """A rasterio read error as rasterio raises it: 'Read failed' with the GDAL error as its cause."""
    exc = RasterioIOError(message)
    if cause:
        exc.__cause__ = RuntimeError(cause)
    return exc


def scene_item(day, n=0):
    return SimpleNamespace(id=f"S2_{day:%Y%m%d}_{n}", datetime=day, properties={},
                           assets={b: SimpleNamespace(href=f"https://example.org/{b}.tif") for b in ("SCL", "B04")})


# ---------------------------------------------------------------------------------------- classification

@pytest.mark.parametrize("exc, messages, kind, status", [
    (failure("Read failed. See previous exception for details.",
             "zqk7i%3D, band 1: IReadBlock failed at X offset 9, Y offset 11: TIFFReadEncodedTile() failed."),
     ["CPLE_AppDefined:Request for 176155804-176609179 failed with response_code=206"], "truncated", 206),
    (failure("Read failed."), ["VSICURL: ReadMultiRange(x.tif), 1-2: response_code=206, msg=Operation timed out "
                               "after 30001 milliseconds with 293882 out of 454970 bytes received"], "timeout", 206),
    (failure("Read failed."), ["response_code=206, msg=Operation too slow. Less than 1024 bytes/sec"], "stalled", 206),
    (failure("Read failed.", "TIFFFillTile:Read error at row 4294967295, col 4294967295, tile 251; got 216922 "
                             "bytes, expected 453364"), [], "truncated", None),
    (failure("Read failed."), ["CPLE_AppDefined:Request for 1-2 failed with response_code=0"], "network", 0),
    (failure("Read failed."), ["CURL error: Could not resolve host: sentinel2l2a01.blob.core.windows.net"],
     "network", None),
    (failure("HTTP response code: 409"), [], "forbidden", 409),
    (failure("HTTP response code: 404"), ["Request for 1-2 failed with response_code=206"], "not_found", 404),
    (failure("Read failed."), ["HTTP error code: 503 - ServerBusy. Retrying again in 2.0 secs"], "throttled", 503),
    (failure("Read failed."), ["HTTP error code for x.tif range 0-99: 502. Retrying again in 2.0 secs"],
     "server", 502),
    (failure("Read failed.", "ZIPDecode:Decoding error at scanline 0"), [], "decode", None),
    (failure("x.tif: not recognized as a supported file format."), [], "io", None),
    (ValueError("operands could not be broadcast together"), [], "error", None),
])
def test_classify_read_error(exc, messages, kind, status):
    assert eo.classify_read_error(exc, messages) == (kind, status)


def test_classify_expired_token_only_when_the_href_has_expired():
    refused = failure("HTTP response code: 403")
    assert eo.classify_read_error(refused, href=pc_href(NOW - timedelta(minutes=1)), now=NOW) == ("expired", 403)
    assert eo.classify_read_error(refused, href=pc_href(NOW + timedelta(hours=1)), now=NOW) == ("forbidden", 403)
    # An expired token explains a silent failure too, but not a 503.
    assert eo.classify_read_error(failure("Read failed."), href=pc_href(NOW - timedelta(minutes=1)),
                                  now=NOW)[0] == "expired"
    busy = failure("Read failed.", "HTTP error code: 503 - ServerBusy")
    assert eo.classify_read_error(busy, href=pc_href(NOW - timedelta(minutes=1)), now=NOW)[0] == "throttled"


def test_clean_drops_urls_and_sas_fragments():
    text = eo._clean(f"GDAL on {pc_href(NOW)} said: zqk7iAbC%2Bx%3D, band 1: IReadBlock failed at X offset 9")
    assert "T44RLS_B03_10m.tif" in text
    for secret in ("sig", "se=", "%3D", "%2B", "blob.core", "zqk7i", "?"):
        assert secret not in text
    assert eo._clean("zqk7iAbC%3D, band 1: IReadBlock failed").startswith("band 1: IReadBlock failed")
    assert len(eo._clean("x" * 500)) == 200


# ----------------------------------------------------------------------------------------------- retries

@pytest.fixture
def fake_io(monkeypatch):
    """Replace the raster read with a scripted fake; record URLs and backoff sleeps."""
    calls, sleeps, script = [], [], []

    def read_once(href, grid, resampling):
        calls.append(href)
        step = script.pop(0) if script else "ok"
        if step == "ok":
            return np.ones((2, 2), dtype="float32")
        message, logged = step
        if logged:
            logging.getLogger("rasterio._err").warning(logged)
        raise failure(message)

    monkeypatch.setattr(eo, "_read_once", read_once)
    monkeypatch.setattr(eo.time, "sleep", sleeps.append)
    monkeypatch.setattr(eo.random, "uniform", lambda a, b: 1.0)
    return SimpleNamespace(calls=calls, sleeps=sleeps, script=script)


def test_retry_recovers_with_backoff_and_a_fresh_url(fake_io):
    truncated = ("Read failed.", "CPLE_AppDefined:Request for 1-2 failed with response_code=206")
    fake_io.script += [truncated, truncated]
    before = dict(eo.READ_STATS)
    out = eo._read_on_grid("https://example.org/a.tif", None, None)
    assert out.shape == (2, 2)
    assert fake_io.calls[0] == "https://example.org/a.tif"
    assert all("?khetos_retry=" in u for u in fake_io.calls[1:]) and len(set(fake_io.calls)) == 3
    assert fake_io.sleeps == [2.0, 6.0]
    assert eo.READ_STATS["retried"] - before["retried"] == 2
    assert eo.READ_STATS["recovered"] - before["recovered"] == 1


def test_retry_gives_up_with_a_clean_read_error(fake_io):
    fake_io.script += [("Read failed.", "HTTP error code: 503 - ServerBusy")] * 5
    href = "https://example.org/a.tif?token=s3cr3t"
    with pytest.raises(eo.ReadError) as info:
        eo._read_on_grid(href, None, None)
    err = info.value
    assert (err.kind, err.status, err.attempts, err.file) == ("throttled", 503, eo.READ_ATTEMPTS, "a.tif")
    assert fake_io.sleeps == [2.0, 6.0, 18.0]
    assert "s3cr3t" not in str(err) and str(err).startswith("a.tif: server busy")
    assert all("&khetos_retry=" in u for u in fake_io.calls[1:])  # appended to the existing query
    assert isinstance(err, RasterioIOError)


def test_missing_file_is_not_retried(fake_io):
    fake_io.script.append(("HTTP response code: 404", None))
    with pytest.raises(eo.ReadError) as info:
        eo._read_on_grid("https://example.org/missing.tif", None, None)
    assert info.value.kind == "not_found" and len(fake_io.calls) == 1 and not fake_io.sleeps


def test_local_reads_are_not_wrapped(fake_io):
    fake_io.script.append(("Read failed.", None))
    with pytest.raises(RasterioIOError) as info:
        eo._read_on_grid("C:/data/local.tif", None, None)
    assert not isinstance(info.value, eo.ReadError) and len(fake_io.calls) == 1


def test_refused_token_is_resigned_without_sleeping(fake_io, monkeypatch):
    resigned = []

    def resign(href, force=False):
        resigned.append(force)
        return pc_href(datetime.now(timezone.utc) + timedelta(hours=1)).replace("c2VjcmV0", "bmV3")

    monkeypatch.setattr(eo, "_resign", resign)
    fake_io.script.append(("HTTP response code: 403", None))
    eo._read_on_grid(pc_href(datetime.now(timezone.utc) + timedelta(hours=1)), None, None)
    assert resigned == [True] and not fake_io.sleeps
    assert "sig=bmV3" in fake_io.calls[1] and "khetos_retry=" in fake_io.calls[1]


def test_token_about_to_expire_is_resigned_before_reading(fake_io, monkeypatch):
    fresh = pc_href(datetime.now(timezone.utc) + timedelta(hours=1))
    monkeypatch.setattr(eo, "_resign", lambda href, force=False: fresh)
    eo._read_on_grid(pc_href(datetime.now(timezone.utc) + timedelta(seconds=30)), None, None)
    assert fake_io.calls == [fresh]


def test_resign_evicts_a_refused_cached_token(monkeypatch):
    import planetary_computer
    from planetary_computer import sas
    key = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel2l2a01/sentinel2-l2"
    monkeypatch.setattr(sas, "TOKEN_CACHE", {key: "stale"})
    monkeypatch.setattr(planetary_computer, "sign",
                        lambda url: url + ("?se=x&sig=old" if key in sas.TOKEN_CACHE else "?se=y&sig=new"))
    href = PC_HREF.split("?")[0] + "?se=x&sig=old"
    assert eo._resign(href).endswith("sig=old")  # not forced: the cached token is reused
    assert eo._resign(href, force=True).endswith("sig=new")
    assert key not in sas.TOKEN_CACHE


# ------------------------------------------------------------------------------------ skip accounting

def test_safe_collects_skip_records():
    skipped = []

    def read(it):
        if it.id.endswith("_2"):
            raise eo.ReadError("B04.tif: transfer cut short (HTTP 206) after 4 attempts", "truncated", 206, 4)
        return it.id

    items = [scene_item(datetime(2026, 3, d, tzinfo=timezone.utc), d) for d in (1, 2, 3)]
    assert [r for r in eo._pmap(eo._safe(read, skipped, "bands"), items) if r] == ["S2_20260301_1", "S2_20260303_3"]
    assert skipped == [{"scene": "S2_20260302_2", "date": "2026-03-02", "stage": "bands", "reason": "truncated",
                        "detail": "B04.tif: transfer cut short (HTTP 206) after 4 attempts"}]
    note = eo.skip_note(skipped)
    assert note.startswith("1 Sentinel-2 scene could not be read and was left out (1 transfer cut short; 2026-03-02)")
    assert eo.skip_note([]) is None
    summary = eo.skip_summary(skipped)
    assert summary["skipped_scenes"] == 1 and summary["skipped_reasons"] == "1 transfer cut short"
    assert eo.skip_summary(None)["skipped_scenes"] == 0


def _fake_s2(monkeypatch, items, bad_screen=(), bad_bands=()):
    """Stub the Sentinel-2 search, cloud screen and band read around eo's own control flow."""
    grid = SimpleNamespace(res=20)
    aoi = np.ones((2, 2), dtype=bool)
    monkeypatch.setattr(eo, "make_grid", lambda *a, **k: grid)
    monkeypatch.setattr(eo, "geometry_pixels", lambda *a, **k: aoi)
    monkeypatch.setattr(eo, "cropland_mask", lambda g: None)
    monkeypatch.setattr(eo, "search_s2", lambda bbox, start, end, **k: [i for i in items
                                                                        if start <= i.datetime <= end])
    monkeypatch.setattr(eo, "_one_per_day", lambda its, bbox: sorted(its, key=lambda i: i.datetime, reverse=True))

    def screen(it, g, a):
        if it.id in bad_screen:
            raise eo.ReadError("SCL.tif: network error", "network", 0)
        return eo.S2Scene(it.id, it.datetime, 5.0, g, a, a.copy(), 1.0, 1.0), it

    def bands(it, g, names=("B04", "B08", "B11")):
        if it.id in bad_bands:
            raise eo.ReadError("B08.tif: transfer timed out (HTTP 206) after 4 attempts", "timeout", 206, 4)
        return {"B04": np.full((2, 2), 0.05, "float32"), "B08": np.full((2, 2), 0.4, "float32"),
                "B11": np.full((2, 2), 0.2, "float32")}

    monkeypatch.setattr(eo, "_screen_s2", screen)
    monkeypatch.setattr(eo, "_read_s2_bands", bands)


def test_latest_clear_scene_falls_back_past_an_unreadable_one(monkeypatch):
    now = datetime.now(timezone.utc)
    items = [scene_item(now - timedelta(days=d), d) for d in (2, 7, 12)]
    _fake_s2(monkeypatch, items, bad_bands={items[0].id})
    scene = eo.run_analysis(eo.latest_clear_s2, (79.0, 28.0, 79.1, 28.1))
    assert scene.id == items[1].id
    assert [(s["scene"], s["stage"], s["reason"]) for s in scene.skipped] == [(items[0].id, "bands", "timeout")]
    prev = eo.run_analysis(eo.previous_clear_s2, scene, (79.0, 28.0, 79.1, 28.1))
    assert prev.id == items[2].id


def test_latest_clear_scene_error_names_unreadable_scenes(monkeypatch):
    now = datetime.now(timezone.utc)
    items = [scene_item(now - timedelta(days=2))]
    _fake_s2(monkeypatch, items, bad_screen={items[0].id})
    with pytest.raises(RuntimeError, match="1 Sentinel-2 scene could not be read"):
        eo.run_analysis(eo.latest_clear_s2, (79.0, 28.0, 79.1, 28.1))


def test_trend_series_reports_skipped_scenes_with_their_stage(monkeypatch):
    now = datetime.now(timezone.utc)
    items = [scene_item(now - timedelta(days=d), d) for d in (3, 13, 23, 33)]
    _fake_s2(monkeypatch, items, bad_screen={items[0].id}, bad_bands={items[2].id})
    skipped = []
    df = eo.run_analysis(eo.trend_series, (79.0, 28.0, 79.1, 28.1), 6, 35, skipped=skipped)
    assert len(df) == 2
    assert sorted((s["scene"], s["stage"]) for s in skipped) == sorted([(items[0].id, "screen"),
                                                                        (items[2].id, "bands")])
    assert "2 Sentinel-2 scenes could not be read and were left out" in eo.skip_note(skipped)


def test_river_caveats_name_unread_scenes():
    seasons = {2020: {"year": 2020, "quality": "good", "peak_scenes": 3, "skipped_scenes": 2,
                      "skipped_reasons": "2 transfer cut short"},
               2021: {"year": 2021, "quality": "good", "peak_scenes": 3, "skipped_scenes": 0}}
    caveats = river._season_caveats(seasons)
    assert len(caveats) == 1 and "2020: 2 (2 transfer cut short)" in caveats[0]
    lost = [{"scene": "a", "date": "2026-02-01", "stage": "bands", "reason": "throttled", "detail": ""}]
    looks = river._looks_caveats({"skipped": lost}, {"skipped": []}, "Jan-Mar 2026", "Jan-Mar 2025")
    assert len(looks) == 1 and looks[0].startswith("Jan-Mar 2026: 1 Sentinel-2 scene could not be read")
