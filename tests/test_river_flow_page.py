"""The River Water Watch page renders from the bundled snapshot, with no network needed."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_river_water_watch_page_renders():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.radio(key="page").set_value("river-flow").run()
    assert not at.exception
    assert [h.value for h in at.header] == ["📈 River Water Watch"]
    assert [t.label for t in at.tabs] == ["Modelled flow", "Yearly record", "Satellite width", "Field readings",
                                          "Check and limits"]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Yearly water (1991-2020 mean)"].startswith("113.")
    assert any("Modelled, not measured" in w.value for w in at.warning)
    assert "Our rain-forecast model (experimental)" in [h.value for h in at.subheader]
    assert any("Judged against GEOGLOWS" in w.value for w in at.warning)


def test_yearly_record_tab_renders():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.radio(key="page").set_value("river-flow").run()
    at.session_state["fw_tab"] = "Yearly record"
    at.run()
    assert not at.exception
    subheaders = [h.value for h in at.subheader]
    assert "Flow at Khajuria ghat" in subheaders and "Monsoon rain at Baheri" in subheaders
    metrics = {m.label: m.value for m in at.metric}
    assert any(label.startswith("May ") and value.endswith("m³/s") for label, value in metrics.items())
    assert any(label.startswith("IMD, June-October") for label in metrics)
