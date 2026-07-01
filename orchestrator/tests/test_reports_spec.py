from app.reports.spec import Column, ReportSpec, Row, Run, TableSection


def test_row_defaults_to_normal_style():
    row = Row(cells=["a", "b"])
    assert row.style == "normal"


def test_column_defaults_to_text_type_and_weight_one():
    col = Column(header="Name")
    assert col.type == "text"
    assert col.width_weight == 1.0


def test_report_spec_builds_nested_structure():
    spec = ReportSpec(
        title="Test Report",
        lang="en",
        generated_label="Generated: 2026-07-01",
        sections=[
            TableSection(
                heading="Section A",
                columns=[Column(header="Date"), Column(header="Amount", type="number")],
                rows=[Row(cells=["01/07/2026", "100.00 ILS"])],
                totals_row=Row(cells=["Total", "100.00 ILS"], style="total"),
            )
        ],
    )
    assert spec.meta_lines == []
    assert spec.sections[0].heading == "Section A"
    assert spec.sections[0].totals_row.style == "total"


def test_cell_accepts_plain_string_or_run_list():
    row_plain = Row(cells=["hello"])
    row_runs = Row(cells=[[Run(text="bold part", bold=True), Run(text=" plain part")]])
    assert row_plain.cells[0] == "hello"
    assert row_runs.cells[0][0].bold is True
    assert row_runs.cells[0][1].bold is False
