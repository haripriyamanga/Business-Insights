"""End-to-end smoke tests that run the Streamlit app headlessly."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")
TIMEOUT = 60


def run_app() -> AppTest:
    app = AppTest.from_file(APP_PATH, default_timeout=TIMEOUT)
    app.run()
    return app


def test_app_renders_with_sample_data():
    app = run_app()
    assert not app.exception
    assert app.title[0].value == "📊 Business Insights"
    assert [metric.label for metric in app.metric] == [
        "Total Revenue", "Total Orders", "Units Sold", "Average Order Value",
    ]
    assert any(subheader.value == "💡 Business Insights" for subheader in app.subheader)


def test_category_filter_updates_kpis():
    app = run_app()
    total_before = app.metric[0].value
    app.multiselect(key="filter_categories").select("Books").run()
    assert not app.exception
    assert app.metric[0].value != total_before


def test_empty_filter_result_shows_message_and_reset_works():
    app = run_app()
    # The only order on the first day of the sample data is an Electronics item,
    # so Books on that day matches nothing.
    app.multiselect(key="filter_categories").select("Books").run()
    app.date_input(key="filter_dates").set_value(("2024-01-02", "2024-01-02")).run()
    assert not app.exception
    assert len(app.metric) == 0
    assert any("No sales match" in warning.value for warning in app.warning)

    app.button(key="reset_filters_empty").click().run()
    assert not app.exception
    assert app.multiselect(key="filter_categories").value == []
    assert len(app.metric) == 4


def test_product_choices_follow_selected_categories():
    app = run_app()
    app.multiselect(key="filter_categories").select("Books").run()
    app.multiselect(key="filter_products").select("Data Science Handbook").run()
    # Switching category drops the book from the product selection instead of erroring.
    app.multiselect(key="filter_categories").unselect("Books").select("Clothing").run()
    assert not app.exception
    assert app.multiselect(key="filter_products").value == []
    assert "Data Science Handbook" not in app.multiselect(key="filter_products").options


def test_all_tabs_and_granularities_render():
    app = run_app()
    for granularity in ("Daily", "Weekly", "Monthly"):
        app.radio(key="trend_granularity").set_value(granularity).run()
        assert not app.exception
