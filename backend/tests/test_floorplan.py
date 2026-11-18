from __future__ import annotations

from sqlalchemy import select

from labkeeper import services as sv
from labkeeper.floorplan import normalise, parse_plan_file
from labkeeper.models import FloorPlan, Rack
from labkeeper.schemas import FloorCell, FloorPlanIn

GRID = """,01,02,03,04
A,R:1.7,R,E,U*1x2
B,B*2,,P,
C,R:SPECIAL,S,.,D
"""


def test_grid_codes_spans_metrics_and_labels():
    plan = parse_plan_file("lab.csv", GRID.encode())
    assert plan.rows == ["A", "B", "C"] and plan.columns == ["01", "02", "03", "04"]
    cells = {(c.row, c.col): c for c in plan.cells}
    assert cells[("A", "01")].metric == 1.7 and cells[("A", "01")].type == "rack"
    assert cells[("A", "03")].type == "empty_rack"
    assert cells[("A", "04")].span_rows == 2
    assert cells[("B", "01")].type == "bench" and cells[("B", "01")].span_cols == 2
    assert cells[("B", "03")].type == "pillar"
    assert cells[("C", "01")].label == "SPECIAL"
    assert cells[("C", "04")].type == "door"
    assert ("C", "03") not in cells


def test_yaml_plan_and_normalise_drops_cells_outside_grid():
    text = b"""
name: Room
rows: [A, B]
columns: ["01", "02"]
cells:
  - {row: A, col: "1", type: rack}
  - {row: Z, col: "01", type: rack}
  - {row: B, col: "02", type: bench, span_cols: 5}
"""
    plan = parse_plan_file("room.yaml", text)
    assert [(c.row, c.col) for c in plan.cells] == [("A", "01"), ("B", "02")]
    assert plan.cells[1].span_cols == 1  # clamped to the grid


def test_racks_link_by_prefix_row_col(session):
    session.add_all([Rack(name="B1_C03"), Rack(name="B1_D5")])
    session.flush()
    data = normalise(
        FloorPlanIn(
            name="Lab", rack_prefix="B1_", rows=["C", "D"], columns=["03", "05"],
            cells=[FloorCell(row="C", col="03", type="rack"), FloorCell(row="D", col="05", type="rack")],
        )
    )
    plan = FloorPlan(name=data.name, rack_prefix="B1_", rows=data.rows, columns=data.columns, cells=[c.model_dump() for c in data.cells])
    session.add(plan)
    session.flush()
    sv.link_floor_racks(session, plan)
    session.flush()
    linked = {(r.name, r.floor_row, r.floor_col) for r in session.scalars(select(Rack)) if r.floor_plan_id}
    assert linked == {("B1_C03", "C", "03"), ("B1_D5", "D", "05")}  # D05 also matches unpadded D5
    out = sv.floor_plan_out(session, plan)
    assert sum(1 for link in out.racks if link.rack) == 2
