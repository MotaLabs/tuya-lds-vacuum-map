import dataclasses
import io
import os
from pathlib import Path

from PIL import Image, ImageFont

from support.maps import CODES, HEIGHT, WIDTH, build_cells, cell_center_mm, cloud_payload
from tuya_lds_vacuum_map.lds import commands
from tuya_lds_vacuum_map.lds.messages import Area, Room
from tuya_lds_vacuum_map.map import render as render_module
from tuya_lds_vacuum_map.map.grid import GridMap
from tuya_lds_vacuum_map.map.render import Renderer, Scene, Style, calibration

STYLE = Style()
SCALE = STYLE.scale
UNNAMED = (Room(1, ""), Room(2, ""))
COLORS = {1: (200, 40, 40), 2: (40, 200, 40)}
REFERENCE = Path(__file__).with_name("render_reference.png")


def render(scene: Scene) -> Image.Image:
    image, _ = Renderer(CODES, STYLE).render(scene)
    return Image.open(io.BytesIO(image)).convert("RGB")


def pixel_of_cell(col: int, row: int) -> tuple[int, int]:
    return col * SCALE + SCALE // 2, (HEIGHT - 1 - row) * SCALE + SCALE // 2


def to_map(points: list[dict], x: float, y: float) -> tuple[int, int]:
    origin, along_x, along_y = points
    map_x = origin["map"]["x"] + (x - origin["vacuum"]["x"]) * (along_x["map"]["x"] - origin["map"]["x"]) / (
        along_x["vacuum"]["x"] - origin["vacuum"]["x"]
    )
    map_y = origin["map"]["y"] + (y - origin["vacuum"]["y"]) * (along_y["map"]["y"] - origin["map"]["y"]) / (
        along_y["vacuum"]["y"] - origin["vacuum"]["y"]
    )
    return round(map_x), round(map_y)


def test_image_size_and_calibration(payload):
    grid = GridMap.from_cloud(payload)
    image, points = Renderer(CODES, STYLE).render(Scene(grid))
    assert Image.open(io.BytesIO(image)).size == (WIDTH * SCALE, HEIGHT * SCALE)
    assert points == calibration(grid, SCALE)
    assert points == [
        {"vacuum": {"x": -1000, "y": -500}, "map": {"x": 0, "y": HEIGHT * SCALE}},
        {"vacuum": {"x": 1000, "y": -500}, "map": {"x": WIDTH * SCALE, "y": HEIGHT * SCALE}},
        {"vacuum": {"x": -1000, "y": 1000}, "map": {"x": 0, "y": 0}},
    ]


def test_north_is_up():
    cells = build_cells()
    cells[25 * WIDTH + 5] = 255
    image = render(Scene(GridMap.from_cloud(cloud_payload(cells)), rooms=UNNAMED, room_colors=COLORS))
    assert image.getpixel(pixel_of_cell(5, 25)) == STYLE.obstacle
    assert image.getpixel((5 * SCALE + 2, 25 * SCALE + 2)) == COLORS[1]


def test_cell_colors(payload):
    image = render(Scene(GridMap.from_cloud(payload), rooms=UNNAMED, room_colors={1: COLORS[1]}))
    assert image.getpixel(pixel_of_cell(10, 14)) == COLORS[1]
    assert image.getpixel(pixel_of_cell(30, 14)) == STYLE.floor
    assert image.getpixel(pixel_of_cell(20, 14)) == STYLE.wall
    assert image.getpixel((0, 0)) == STYLE.background[:3]


def test_overlays_land_where_the_calibration_says(payload):
    grid = GridMap.from_cloud(payload)
    robot = cell_center_mm(10, 20)
    image = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, robot=robot))
    assert image.getpixel(to_map(calibration(grid, SCALE), *robot)) == (60, 60, 60)


def test_dock_is_drawn(payload):
    grid = GridMap.from_cloud(payload)
    dock = cell_center_mm(30, 20)
    x, y = to_map(calibration(grid, SCALE), *dock)
    image = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, dock=dock))
    assert image.getpixel((x - 7, y - 7)) == STYLE.dock[:3]


def test_spot_is_tinted(payload):
    grid = GridMap.from_cloud(payload)
    spot = tuple(tuple(vertex) for vertex in commands.spot_square(*cell_center_mm(30, 14), 500))
    plain = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS)).getpixel(pixel_of_cell(30, 14))
    tinted = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, spot=spot)).getpixel(pixel_of_cell(30, 14))
    outside = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, spot=spot)).getpixel(pixel_of_cell(30, 20))
    assert tinted[0] > plain[0]
    assert tinted[1] < plain[1]
    assert outside == COLORS[2]


def test_the_path_shows_over_the_spot(payload):
    grid = GridMap.from_cloud(payload)
    spot = tuple(tuple(vertex) for vertex in commands.spot_square(*cell_center_mm(10, 14), 500))
    path = [cell_center_mm(8, 14), cell_center_mm(12, 14)]
    image = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, path=path, spot=spot))
    assert min(image.getpixel(pixel_of_cell(10, 14))) > 200


def test_the_spot_border_shows_on_a_room_of_its_color(payload):
    grid = GridMap.from_cloud(payload)
    spot = tuple(tuple(vertex) for vertex in commands.spot_square(*cell_center_mm(10, 14), 500))
    image = render(Scene(grid, rooms=UNNAMED, room_colors={1: STYLE.spot[:3], 2: COLORS[2]}, spot=spot))
    row = pixel_of_cell(10, 14)[1]
    assert any(min(image.getpixel((x, row))) > 200 for x in range(3 * SCALE, 20 * SCALE))


def test_the_spot_border_grows_with_the_map_scale(payload):
    grid = GridMap.from_cloud(payload)
    spot = tuple(tuple(vertex) for vertex in commands.spot_square(*cell_center_mm(10, 14), 500))
    for scale in (4, 8):
        png, points = Renderer(CODES, Style(scale=scale)).render(
            Scene(grid, rooms=UNNAMED, room_colors=COLORS, spot=spot)
        )
        image = Image.open(io.BytesIO(png)).convert("RGB")
        x, y = to_map(points, *spot[0])
        column = [image.getpixel((x + 3, y + dy)) for dy in range(-scale, scale + 1)]
        assert sum(1 for pixel in column if pixel == STYLE.spot[:3]) >= scale


def test_a_spot_preview_is_tinted_apart_from_a_running_spot(payload):
    grid = GridMap.from_cloud(payload)
    square = tuple(tuple(vertex) for vertex in commands.spot_square(*cell_center_mm(30, 14), 500))
    plain = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS)).getpixel(pixel_of_cell(31, 15))
    running = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, spot=square)).getpixel(pixel_of_cell(31, 15))
    preview = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, preview_square=square)).getpixel(
        pixel_of_cell(31, 15)
    )
    assert preview[2] > plain[2]
    assert preview[0] < plain[0] + 5
    assert running[0] > preview[0]


def test_a_spot_preview_marks_the_tapped_point(payload):
    grid = GridMap.from_cloud(payload)
    center = cell_center_mm(30, 14)
    x, y = to_map(calibration(grid, SCALE), *center)
    image = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, preview_center=center))
    radius = 2 * SCALE
    assert image.getpixel((x, y)) == STYLE.preview[:3]
    assert image.getpixel((x + radius - 1, y)) == STYLE.preview[:3]
    assert min(image.getpixel((x + radius + 1, y))) > 200
    assert image.getpixel((x + radius + 4, y)) == COLORS[2]


def test_the_preview_border_shows_on_a_room_of_its_color(payload):
    grid = GridMap.from_cloud(payload)
    square = tuple(tuple(vertex) for vertex in commands.spot_square(*cell_center_mm(10, 14), 500))
    image = render(Scene(grid, rooms=UNNAMED, room_colors={1: STYLE.preview[:3], 2: COLORS[2]}, preview_square=square))
    row = pixel_of_cell(10, 14)[1]
    assert any(min(image.getpixel((x, row))) > 200 for x in range(3 * SCALE, 20 * SCALE))
    assert any(image.getpixel((x, row)) == STYLE.preview[:3] for x in range(3 * SCALE, 20 * SCALE))


def test_an_area_under_the_spot_keeps_its_border(payload):
    grid = GridMap.from_cloud(payload)
    area = Area(1, "area", "normal", "", ((-750, 50), (-250, 50), (-250, 550), (-750, 550)))
    spot = tuple(tuple(vertex) for vertex in commands.spot_square(*cell_center_mm(10, 14), 500))
    plain = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, areas=(area,)))
    with_spot = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, areas=(area,), spot=spot))
    border = [
        (x, y) for x in range(WIDTH * SCALE) for y in range(HEIGHT * SCALE) if plain.getpixel((x, y)) == (40, 40, 40)
    ]
    inside = [(x, y) for x, y in border if 7 * SCALE < x < 14 * SCALE]
    assert inside
    assert all(sum(with_spot.getpixel(p)) < 200 for p in inside)


def test_names_that_draw_nothing_do_not_break_the_map(payload):
    grid = GridMap.from_cloud(payload)
    far = ((0, 21825), (500, 21825), (500, 22325), (0, 22325))
    rooms = (Room(1, "\u200b", pos=far[0]), Room(2, "\xad", pos=far[2]))
    area = Area(1, "area", "normal", "\u200b", far)
    image = render(Scene(grid, rooms=rooms, room_colors=COLORS, areas=(area,)))
    assert image.size == (WIDTH * SCALE, HEIGHT * SCALE)


def test_forbidden_area_is_red(payload):
    grid = GridMap.from_cloud(payload)
    x, y = cell_center_mm(30, 20)
    forbid = Area(4, "", "forbid", "", ((x - 200, y - 200), (x + 200, y - 200), (x + 200, y + 200), (x - 200, y + 200)))
    image = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, areas=(forbid,)))
    assert image.getpixel(pixel_of_cell(30, 20)) == STYLE.forbidden[:3]


def test_settings_pills_follow_the_switch(payload):
    grid = GridMap.from_cloud(payload)
    rooms = (Room(1, "Left", pos=cell_center_mm(10, 14), fan="quiet"),)
    labels = {"quiet": "Quiet"}
    hidden = render(Scene(grid, rooms=rooms, fan_labels=labels))
    shown = render(Scene(grid, rooms=rooms, fan_labels=labels, show_settings=True))
    unlabeled = render(Scene(grid, rooms=rooms, show_settings=True))
    assert shown.tobytes() != hidden.tobytes()
    assert unlabeled.tobytes() == hidden.tobytes()


def test_a_listed_room_shows_its_place_in_the_list(payload):
    grid = GridMap.from_cloud(payload)
    rooms = (Room(1, "Left", pos=cell_center_mm(10, 14)),)
    plain = render(Scene(grid, rooms=rooms))
    second = render(Scene(grid, rooms=rooms, room_order={1: 2}))
    third = render(Scene(grid, rooms=rooms, room_order={1: 3}))
    _, y = to_map(calibration(grid, SCALE), *cell_center_mm(10, 14))
    marked = [x for x in range(WIDTH * SCALE) if second.getpixel((x, y)) == STYLE.preview[:3]]
    assert marked
    assert all(plain.getpixel((x, y)) != STYLE.preview[:3] for x in marked)
    assert second.tobytes() != third.tobytes()
    assert render(Scene(grid, rooms=rooms, room_order={2: 1})).tobytes() == plain.tobytes()


def test_room_names_are_drawn(payload):
    grid = GridMap.from_cloud(payload)
    named = render(Scene(grid, rooms=(Room(1, "Left", pos=cell_center_mm(10, 14)),)))
    unnamed = render(Scene(grid, rooms=(Room(1, "", pos=cell_center_mm(10, 14)),)))
    assert named.tobytes() != unnamed.tobytes()


def test_a_map_too_large_for_the_scale_is_drawn_smaller(monkeypatch, payload):
    monkeypatch.setattr(render_module, "MAX_IMAGE_PIXELS", WIDTH * HEIGHT * 4)
    grid = GridMap.from_cloud(payload)
    image, points = Renderer(CODES, STYLE).render(Scene(grid, rooms=UNNAMED, room_colors=COLORS))
    drawn = Image.open(io.BytesIO(image)).convert("RGB")
    assert drawn.size == (WIDTH * 2, HEIGHT * 2)
    assert points == calibration(grid, 2)
    assert drawn.getpixel(to_map(points, *cell_center_mm(10, 14))) == COLORS[1]


def test_a_map_with_more_cells_than_pixels_is_drawn_one_pixel_per_cell(monkeypatch, payload):
    monkeypatch.setattr(render_module, "MAX_IMAGE_PIXELS", WIDTH * HEIGHT // 2)
    image, points = Renderer(CODES, STYLE).render(Scene(GridMap.from_cloud(payload)))
    assert Image.open(io.BytesIO(image)).size == (WIDTH, HEIGHT)
    assert points == calibration(GridMap.from_cloud(payload), 1)


def test_a_spot_or_preview_of_three_vertices_is_drawn(payload):
    grid = GridMap.from_cloud(payload)
    triangle = tuple(tuple(vertex) for vertex in commands.spot_square(*cell_center_mm(30, 14), 500))[:3]
    plain = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS))
    spot = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, spot=triangle))
    preview = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, preview_square=triangle))
    assert spot.tobytes() != plain.tobytes()
    assert preview.tobytes() != plain.tobytes()


def test_an_area_of_two_vertices_draws_its_dashed_line(payload):
    grid = GridMap.from_cloud(payload)
    line = Area(1, "area", "normal", "", (cell_center_mm(5, 10), cell_center_mm(15, 10)))
    image = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, areas=(line,)))
    _, y = pixel_of_cell(10, 10)
    assert any(image.getpixel((x, y)) == (40, 40, 40) for x in range(5 * SCALE, 16 * SCALE))


def test_overlays_land_where_the_calibration_says_far_from_the_origin(payload):
    grid = dataclasses.replace(GridMap.from_cloud(payload), x_min=-80.0, y_min=-60.6)
    points = calibration(grid, SCALE)
    assert points == [
        {"vacuum": {"x": -80000, "y": -60600}, "map": {"x": 0, "y": HEIGHT * SCALE}},
        {"vacuum": {"x": -78000, "y": -60600}, "map": {"x": WIDTH * SCALE, "y": HEIGHT * SCALE}},
        {"vacuum": {"x": -80000, "y": -59100}, "map": {"x": 0, "y": 0}},
    ]
    robot = (-80000 + 525, -60600 + 1025)
    image = render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, robot=robot))
    assert image.getpixel(to_map(points, *robot)) == (60, 60, 60)


def test_overlays_land_where_the_calibration_says_on_a_map_drawn_smaller(monkeypatch, payload):
    monkeypatch.setattr(render_module, "MAX_IMAGE_PIXELS", WIDTH * HEIGHT * 4)
    grid = GridMap.from_cloud(payload)
    robot = cell_center_mm(10, 20)
    image, points = Renderer(CODES, STYLE).render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, robot=robot))
    assert Image.open(io.BytesIO(image)).convert("RGB").getpixel(to_map(points, *robot)) == (60, 60, 60)


def test_labels_are_drawn_with_the_configured_font(monkeypatch, payload):
    paths: list[str | None] = []

    def font(size: int, path: str | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        paths.append(path)
        return ImageFont.load_default(size=size)

    monkeypatch.setattr(render_module, "load_font", font)
    grid = GridMap.from_cloud(payload)
    Renderer(CODES, Style(font_path="/fonts/labels.ttf")).render(
        Scene(grid, rooms=(Room(1, "Left", pos=cell_center_mm(10, 14)),))
    )
    assert paths
    assert set(paths) == {"/fonts/labels.ttf"}


def test_a_fan_setting_shows_a_fan_and_a_water_setting_a_drop(payload):
    grid = GridMap.from_cloud(payload)

    def colors(room: Room) -> set:
        image = render(
            Scene(grid, rooms=(room,), show_settings=True, fan_labels={"quiet": "Quiet"}, water_labels={1: "Low"})
        )
        return {STYLE.fan[:3], STYLE.water[:3]} & set(image.get_flattened_data())

    pos = cell_center_mm(10, 14)
    assert colors(Room(1, "Left", pos=pos, fan="quiet")) == {STYLE.fan[:3]}
    assert colors(Room(1, "Left", pos=pos, water=1)) == {STYLE.water[:3]}
    assert colors(Room(1, "Left", pos=pos, fan="quiet", water=1)) == {STYLE.fan[:3], STYLE.water[:3]}


def test_the_spot_border_is_never_thinner_than_two_pixels(payload):
    grid = GridMap.from_cloud(payload)
    spot = tuple(tuple(vertex) for vertex in commands.spot_square(*cell_center_mm(10, 14), 500))
    png, points = Renderer(CODES, Style(scale=2)).render(Scene(grid, rooms=UNNAMED, room_colors=COLORS, spot=spot))
    image = Image.open(io.BytesIO(png)).convert("RGB")
    x, y = to_map(points, *spot[0])
    column = [image.getpixel((x + 3, y + dy)) for dy in range(-6, 7)]
    assert sum(1 for pixel in column if pixel == STYLE.spot[:3]) == 2


def reference_scene(grid: GridMap) -> Scene:
    def square(col: int, row: int, size: int) -> tuple:
        return tuple(tuple(vertex) for vertex in commands.spot_square(*cell_center_mm(col, row), size))

    def cells(*pairs: tuple[int, int]) -> tuple:
        return tuple(cell_center_mm(col, row) for col, row in pairs)

    rooms = (
        Room(1, "Left", pos=cell_center_mm(10, 21), fan="quiet", water=1),
        Room(2, "junk", pos=cell_center_mm(29, 21), water=0),
    )
    areas = (
        Area(1, "area", "normal", "Nook", cells((4, 4), (9, 4), (9, 9), (4, 9))),
        Area(2, "", "forbid", "", cells((32, 4), (35, 4), (35, 7))),
        Area(3, "area", "normal", "", cells((23, 4), (24, 4), (24, 5), (23, 5))),
    )
    return Scene(
        grid,
        rooms=rooms,
        areas=areas,
        path=list(cells((5, 15), (17, 15), (17, 12), (25, 12), (25, 16))),
        robot=cell_center_mm(25, 16),
        dock=cell_center_mm(35, 24),
        spot=square(15, 7, 400),
        preview_square=square(27, 8, 300),
        preview_center=cell_center_mm(27, 8),
        room_order={1: 1, 2: 12},
        show_settings=True,
        fan_labels={"quiet": "Quiet"},
        water_labels={0: "Off", 1: "Low"},
        room_colors={1: (0, 120, 200), 2: (40, 200, 40)},
    )


def test_the_drawing_matches_the_reference(monkeypatch, payload):
    monkeypatch.setattr(render_module, "FONT_CANDIDATES", ())
    render_module.load_font.cache_clear()
    try:
        png, _ = Renderer(CODES, Style(scale=8)).render(reference_scene(GridMap.from_cloud(payload)))
    finally:
        render_module.load_font.cache_clear()
    drawn = Image.open(io.BytesIO(png)).convert("RGBA")
    if os.environ.get("UPDATE_RENDER_REFERENCE"):
        drawn.save(REFERENCE)
    reference = Image.open(REFERENCE).convert("RGBA")
    if drawn.size != reference.size or drawn.tobytes() != reference.tobytes():
        Path("reports").mkdir(exist_ok=True)
        drawn.save("reports/render_drawn.png")
        raise AssertionError(
            "the drawing differs from tests/map/render_reference.png; it is in reports/render_drawn.png, and "
            "UPDATE_RENDER_REFERENCE=1 uv run pytest tests/map/test_render.py rewrites the reference"
        )
