import io

from PIL import Image

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
