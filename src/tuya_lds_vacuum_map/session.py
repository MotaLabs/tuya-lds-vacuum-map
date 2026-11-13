import asyncio
import hashlib
import json
import logging
import random
import time
from pathlib import Path
from typing import Any

from .cloud_sync import CloudMapSync
from .config import RobotSettings, Settings
from .gestures import START_DELAY_RANGE, RoomPicker, SpotPicker, TapController, TapMode, start_delay
from .i18n import Texts
from .integrations.homeassistant import HomeAssistant, HomeAssistantError
from .integrations.localtuya import LocalTuyaLink
from .integrations.mqtt import MqttBridge, Topics, discovery_configs, statistic_configs
from .integrations.tuya_cloud import ERRORS, TuyaCloud
from .lds.messages import Room, as_object, point_at
from .map.colors import Color, format_color, parse_color, random_color
from .map.coverage import Coverage
from .map.model import MapModel
from .map.render import Renderer, Scene, Style
from .profiles import Profile
from .robot import STATISTICS, Robot
from .storage import MapSource, Snapshot, Storage

log = logging.getLogger(__name__)

ROOM_SETTINGS_EVENT = "tuya_lds_vacuum_map_room_settings"
SWITCH = {"ON": True, "OFF": False}


class RobotSession:
    def __init__(
        self,
        config: RobotSettings,
        profile: Profile,
        settings: Settings,
        texts: Texts,
        ha: HomeAssistant,
        mqtt: MqttBridge,
        cloud: TuyaCloud | None,
    ) -> None:
        self.config = config
        self.device_id = config.device_id
        self.label = config.name or config.device_id
        self.profile = profile
        self.settings = settings
        self.texts = texts
        self.ha = ha
        self.mqtt = mqtt
        self.topics = Topics(settings.robot_topic(config.device_id))
        self.storage = Storage(Path(settings.data_dir) / config.device_id / "state.json")
        self.snapshot = self.storage.load()
        _check_start_settings(self.snapshot)
        geometry = profile.geometry
        coverage = Coverage(profile.cells, geometry.coverage_cell_mm, geometry.cleaning_radius_mm)
        self.map = MapModel(coverage, self.snapshot.cloud_map)
        coverage.load_list(self.snapshot.coverage)
        self.robot = Robot(profile, self.snapshot.room_message, self.snapshot.robot_state, self.snapshot.room_mode)
        try:
            self.robot.path.restore(self.snapshot.path_id, self.snapshot.path, self.snapshot.path_start)
        except ValueError as error:
            log.warning("%s: the saved path could not be read: %s", self.label, error)
        self.renderer = Renderer(profile.cells, Style(scale=settings.map_scale, font_path=settings.font_path))
        self.dirty = asyncio.Event()
        link = LocalTuyaLink(ha, config.device_id, profile.data_points.raw)
        self.spots = SpotPicker(
            self.robot, link, geometry.spot_size_mm, self.auto_start_delay, settings.timing.spot_preview, self.dirty.set
        )
        self.rooms = RoomPicker(link, self.auto_start_delay, self.dirty.set)
        self.taps = TapController(
            self.robot,
            self.map,
            link,
            self.open_room_settings,
            settings.timing.double_tap,
            self.spots,
            self.rooms,
        )
        self.cloud = cloud
        self._catch_up = asyncio.Event()
        self.reported_ms: dict[int, int] = {}
        self.cloud_sync = (
            CloudMapSync(
                cloud,
                config.device_id,
                settings.timing,
                enabled=lambda: self.snapshot.source is MapSource.AUTO,
                moving=lambda: self.robot.moving,
                on_payload=self.on_cloud_payload,
            )
            if cloud
            else None
        )
        levels = self.texts.levels
        self.fan_labels = {value: levels.get(level, value) for value, level in profile.levels.fan.items()}
        self.water_labels = {value: levels.get(level, str(value)) for value, level in profile.levels.water.items()}
        self.state_labels = {code: self.texts.states.get(name, code) for code, name in profile.states.names.items()}
        self.unsaved = False
        self.rng = random.Random()
        self.room_colors = _load_colors(self.snapshot.room_colors)
        self.color_new_rooms()

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self.render_loop())
            group.create_task(self.save_loop())
            if self.cloud_sync:
                group.create_task(self.cloud_sync.run())
            if self.cloud:
                group.create_task(self.catch_up_loop())

    def catch_up(self) -> None:
        self._catch_up.set()

    async def on_available(self, _trigger: Any) -> None:
        self.catch_up()

    async def catch_up_loop(self) -> None:
        while True:
            await self._catch_up.wait()
            self._catch_up.clear()
            if self.snapshot.source is MapSource.AUTO:
                try:
                    await self.catch_up_from_cloud()
                except Exception:
                    log.exception("%s: could not catch up with the robot", self.label)

    async def catch_up_from_cloud(self) -> None:
        try:
            reported = await self.cloud.data_points(self.device_id)
        except ERRORS as error:
            log.warning("%s: the robot's status is not available from the cloud: %s", self.label, error)
            return
        newer = {
            dp: point
            for dp, point in reported.items()
            if dp in self.robot.status_dps and (dp not in self.reported_ms or point.at_ms > self.reported_ms[dp])
        }
        log.info("%s: caught up with %d data points from the cloud", self.label, len(newer))
        self.reported_ms.update({dp: point.at_ms for dp, point in newer.items()})
        await self.apply({dp: point.value for dp, point in newer.items()})

    async def on_data_points(self, data_points: dict[int, Any]) -> None:
        self.reported_ms.update(dict.fromkeys(data_points, int(time.time() * 1000)))
        await self.apply(data_points)

    async def apply(self, data_points: dict[int, Any]) -> None:
        update = self.robot.apply(data_points)
        await self.publish_statistics(sorted(update.statistics))
        if update.new_path:
            self.map.coverage.paint(update.new_path)
        if update.rooms_changed:
            self.color_new_rooms()
            self.taps.follow_map()
        if update.rooms_renamed:
            asyncio.get_running_loop().create_task(self.publish_discovery())
        if self.cloud_sync and update.started_moving:
            self.cloud_sync.wake()
        if self.cloud_sync and update.stopped_moving:
            self.cloud_sync.wake_after_cleaning()
        if update.redraw:
            self.unsaved = True
            self.dirty.set()

    def on_cloud_payload(self, payload: dict[str, Any]) -> None:
        try:
            changed = self.map.set_cloud(payload)
        except ValueError as error:
            log.warning("%s: cloud map could not be decoded: %s", self.label, error)
            return
        if changed:
            log.info("%s: cloud map updated (%sx%s)", self.label, self.map.cloud.width, self.map.cloud.height)
            self.taps.follow_map()
            self.unsaved = True
            self.dirty.set()

    async def on_connect(self) -> None:
        await self.publish_discovery()
        await self.publish_states()
        self.dirty.set()

    async def on_message(self, topic: str, payload: str) -> None:
        topics = self.topics
        try:
            if topic == topics.tap:
                data = as_object(json.loads(payload))
                x, y = point_at(data.get("x"), data.get("y"))
                await self.taps.tap(TapMode(data.get("mode", TapMode.ROOM)), x, y)
                self.dirty.set()
            elif topic == topics.map_source_set:
                self.snapshot.source = MapSource.LOCAL if payload == self.texts.source_local else MapSource.AUTO
                self.unsaved = True
                self.dirty.set()
                await self.publish_states()
                if self.cloud_sync:
                    self.cloud_sync.wake()
            elif topic == topics.room_settings_set:
                self.snapshot.selected_room = None if payload == self.texts.no_room else payload
                self.unsaved = True
                await self.publish_states()
            elif topic == topics.room_color_set:
                await self.recolor_selected_room(payload)
            elif topic == topics.room_color_random:
                await self.recolor_selected_room(None)
            elif topic == topics.auto_start_set:
                self.snapshot.auto_start = SWITCH[payload]
                await self.start_settings_changed()
            elif topic == topics.start_delay_set:
                self.snapshot.start_delay = start_delay(payload)
                await self.start_settings_changed()
            elif topic == topics.start:
                await self.taps.start()
            elif topic == topics.clear:
                self.taps.clear()
        except (ValueError, KeyError, TypeError, HomeAssistantError) as error:
            log.warning("%s: could not handle %s: %s", self.label, topic, error)

    def auto_start_delay(self) -> float | None:
        return self.snapshot.start_delay if self.snapshot.auto_start else None

    async def start_settings_changed(self) -> None:
        self.unsaved = True
        self.taps.reschedule()
        await self.publish_states()

    def color_new_rooms(self) -> None:
        for room in self.robot.rooms.rooms if self.robot.rooms else ():
            if room.id not in self.room_colors:
                self.room_colors[room.id] = random_color(self.room_colors.values(), self.rng)
                self.unsaved = True

    def selected_room(self) -> Room | None:
        return self.robot.rooms.room_named(self.snapshot.selected_room) if self.robot.rooms else None

    async def recolor_selected_room(self, color_text: str | None) -> None:
        room = self.selected_room()
        if room is None:
            raise ValueError("no room selected")
        if color_text:
            self.room_colors[room.id] = parse_color(color_text)
        else:
            self.room_colors[room.id] = random_color(self.room_colors.values(), self.rng)
        self.unsaved = True
        self.dirty.set()
        await self.publish_states()

    async def open_room_settings(self, room: Room) -> None:
        self.snapshot.selected_room = room.name
        self.unsaved = True
        await self.publish_states()
        await self.ha.fire_event(
            ROOM_SETTINGS_EVENT, {"device_id": self.device_id, "room_id": room.id, "room_name": room.name}
        )

    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": [f"tuya_lds_vacuum_map_{self.device_id}"],
            "name": self.config.name or f"Vacuum map {self.device_id[-6:]}",
            "manufacturer": "tuya-lds-vacuum-map",
            "model": self.profile.name,
        }

    async def publish_discovery(self) -> None:
        texts = self.texts
        configs = discovery_configs(
            self.topics,
            self.settings.availability_topic,
            f"tlvm_{self.device_id}",
            self.device_info(),
            {
                "map": texts.map,
                "map_source": texts.map_source,
                "room_settings": texts.room_settings,
                "room_color": texts.room_color,
                "random_room_color": texts.random_room_color,
                "auto_start": texts.auto_start,
                "start_delay": texts.start_delay,
                "start": texts.start,
                "clear": texts.clear,
            },
            [texts.source_auto, texts.source_local],
            [texts.no_room, *(self.robot.rooms.names if self.robot.rooms else [])],
            START_DELAY_RANGE,
        )
        configs |= statistic_configs(
            self.topics,
            self.settings.availability_topic,
            f"tlvm_{self.device_id}",
            self.device_info(),
            {name: getattr(texts, name) for name in STATISTICS},
            {"time": self.profile.units.time, "area": self.profile.units.area},
            [name for name in STATISTICS if name in self.robot.statistic_dps.values()],
        )
        await self.mqtt.publish_discovery(configs)

    async def publish_states(self) -> None:
        texts, topics = self.texts, self.topics
        source = texts.source_local if self.snapshot.source is MapSource.LOCAL else texts.source_auto
        await self.mqtt.publish(topics.map_source, source)
        await self.mqtt.publish(topics.room_settings, self.snapshot.selected_room or texts.no_room)
        room = self.selected_room()
        color = self.room_colors.get(room.id) if room else None
        await self.mqtt.publish(topics.room_color, format_color(color) if color else "")
        await self.mqtt.publish(topics.auto_start, "ON" if self.snapshot.auto_start else "OFF")
        await self.mqtt.publish(topics.start_delay, f"{self.snapshot.start_delay:g}")
        await self.publish_statistics(sorted(self.robot.statistics))

    async def publish_statistics(self, names: list[str]) -> None:
        for name in names:
            await self.mqtt.publish(self.topics.statistic(name), str(self.robot.statistics[name]))

    def scene(self) -> Scene | None:
        rooms = self.robot.rooms
        grid = self.map.compose(rooms.rooms if rooms else ())
        if grid is None:
            return None
        preview = self.spots.preview
        return Scene(
            grid=grid,
            rooms=rooms.rooms if rooms else (),
            areas=rooms.areas if rooms else (),
            path=list(self.robot.path.points),
            robot=self.robot.path.position,
            dock=self.map.dock,
            spot=self.robot.spot,
            preview_square=preview.square if preview else None,
            preview_center=preview.center if preview else None,
            room_order={room_id: order for order, room_id in enumerate(self.rooms.ids, 1)},
            show_settings=bool(self.robot.room_mode),
            fan_labels=self.fan_labels,
            water_labels=self.water_labels,
            room_colors=self.room_colors,
        )

    async def render_loop(self) -> None:
        while True:
            await self.dirty.wait()
            self.dirty.clear()
            try:
                await self.redraw()
            except Exception:
                log.exception("%s: the map could not be drawn", self.label)
            await asyncio.sleep(self.settings.timing.render_interval)

    async def redraw(self) -> None:
        scene = self.scene() if self.mqtt.connected else None
        if scene is None:
            return
        image, calibration = await asyncio.to_thread(self.renderer.render, scene)
        position = self.robot.path.position
        attributes = {
            "image_digest": hashlib.sha256(image).hexdigest()[:16],
            "calibration_points": calibration,
            "robot_state": self.robot.state,
            "robot_state_name": self.state_labels.get(self.robot.state, self.robot.state),
            "robot_position": {"x": position[0], "y": position[1]} if position else None,
            "map_source": self.snapshot.source.value,
            "selection": "rooms" if self.rooms.ids else "spot" if self.spots.preview else "none",
            "rooms": [
                {"id": room.id, "name": room.name, "color": format_color(self.room_colors[room.id])}
                for room in scene.rooms
            ],
        }
        if not await self.mqtt.publish_map(self.topics, image, attributes):
            self.dirty.set()

    async def save_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.timing.save_interval)
            if self.unsaved:
                self.save()

    def save(self) -> None:
        try:
            self.storage.save(
                Snapshot(
                    room_message=self.robot.room_message,
                    robot_state=self.robot.state,
                    room_mode=self.robot.room_mode,
                    cloud_map=self.map.cloud_payload,
                    source=self.snapshot.source,
                    selected_room=self.snapshot.selected_room,
                    coverage=self.map.coverage.to_list(),
                    path_id=self.robot.path.path_id,
                    path=[list(point) for point in self.robot.path.points],
                    path_start=self.robot.path.offset,
                    room_colors={str(room_id): format_color(color) for room_id, color in self.room_colors.items()},
                    auto_start=self.snapshot.auto_start,
                    start_delay=self.snapshot.start_delay,
                )
            )
        except Exception as error:
            log.warning(
                "%s: the state could not be saved in %s: %s: %s",
                self.label,
                self.storage.path,
                type(error).__name__,
                error,
            )
            return
        self.unsaved = False


def _check_start_settings(snapshot: Snapshot) -> None:
    defaults = Snapshot()
    if not isinstance(snapshot.auto_start, bool):
        log.warning("ignoring saved automatic start %r", snapshot.auto_start)
        snapshot.auto_start = defaults.auto_start
    try:
        snapshot.start_delay = start_delay(snapshot.start_delay)
    except ValueError:
        log.warning("ignoring saved start delay %r", snapshot.start_delay)
        snapshot.start_delay = defaults.start_delay


def _load_colors(saved: dict[str, str]) -> dict[int, Color]:
    colors = {}
    for room_id, text in saved.items():
        try:
            colors[int(room_id)] = parse_color(text)
        except ValueError:
            log.warning("ignoring saved color %r for room %s", text, room_id)
    return colors
