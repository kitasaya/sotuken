"""依存ライブラリなしでOSM PBFから局所的なhighway wayを抽出する分析用ヘルパー。

GraphHopper用に保存済みのPBFを三回走査する。
1) 判定点近傍のnode ID、2) それらを含むhighway way、3) 選択wayの全node座標。
サービスコードやPBF自体は変更しない。
"""

from __future__ import annotations

import math
from pathlib import Path
import struct
import zlib


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7
        if shift > 70:
            raise ValueError("invalid protobuf varint")


def _zigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _signed64(value: int) -> int:
    return value - (1 << 64) if value >= (1 << 63) else value


def _fields(data: bytes):
    pos = 0
    while pos < len(data):
        key, pos = _varint(data, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(data, pos)
            yield number, wire, value
        elif wire == 1:
            yield number, wire, data[pos:pos + 8]
            pos += 8
        elif wire == 2:
            length, pos = _varint(data, pos)
            yield number, wire, data[pos:pos + length]
            pos += length
        elif wire == 5:
            yield number, wire, data[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported protobuf wire type: {wire}")


def _packed_varints(data: bytes, *, zigzag: bool = False) -> list[int]:
    values = []
    pos = 0
    while pos < len(data):
        value, pos = _varint(data, pos)
        values.append(_zigzag(value) if zigzag else value)
    return values


def _read_blob_header(data: bytes) -> tuple[str, int]:
    blob_type = ""
    data_size = 0
    for number, _, value in _fields(data):
        if number == 1:
            blob_type = bytes(value).decode("ascii")
        elif number == 3:
            data_size = int(value)
    return blob_type, data_size


def _read_blob(data: bytes) -> bytes:
    raw = None
    compressed = None
    raw_size = None
    for number, _, value in _fields(data):
        if number == 1:
            raw = bytes(value)
        elif number == 2:
            raw_size = int(value)
        elif number == 3:
            compressed = bytes(value)
    if raw is not None:
        return raw
    if compressed is not None:
        result = zlib.decompress(compressed)
        if raw_size is not None and len(result) != raw_size:
            raise ValueError("OSM PBF blob raw_size mismatch")
        return result
    raise ValueError("unsupported OSM PBF blob encoding")


def _data_blocks(path: Path):
    with path.open("rb") as stream:
        block_index = 0
        while True:
            size_bytes = stream.read(4)
            if not size_bytes:
                return
            if len(size_bytes) != 4:
                raise ValueError("truncated OSM PBF block header")
            header_size = struct.unpack(">I", size_bytes)[0]
            blob_type, data_size = _read_blob_header(stream.read(header_size))
            blob = stream.read(data_size)
            if len(blob) != data_size:
                raise ValueError("truncated OSM PBF blob")
            if blob_type == "OSMData":
                block_index += 1
                yield block_index, _read_blob(blob)


def _primitive_block(data: bytes) -> tuple[list[bytes], list[bytes], int, int, int]:
    strings: list[bytes] = []
    groups: list[bytes] = []
    granularity = 100
    lat_offset = 0
    lon_offset = 0
    for number, _, value in _fields(data):
        if number == 1:
            strings = [bytes(v) for n, _, v in _fields(bytes(value)) if n == 1]
        elif number == 2:
            groups.append(bytes(value))
        elif number == 17:
            granularity = int(value)
        elif number == 19:
            lat_offset = _signed64(int(value))
        elif number == 20:
            lon_offset = _signed64(int(value))
    return strings, groups, granularity, lat_offset, lon_offset


def _dense_nodes(group: bytes, granularity: int, lat_offset: int, lon_offset: int):
    dense_messages = [bytes(value) for number, _, value in _fields(group) if number == 2]
    for dense in dense_messages:
        ids: list[int] = []
        lats: list[int] = []
        lons: list[int] = []
        for number, _, value in _fields(dense):
            if number == 1:
                ids = _packed_varints(bytes(value), zigzag=True)
            elif number == 8:
                lats = _packed_varints(bytes(value), zigzag=True)
            elif number == 9:
                lons = _packed_varints(bytes(value), zigzag=True)
        node_id = lat_value = lon_value = 0
        for did, dlat, dlon in zip(ids, lats, lons):
            node_id += did
            lat_value += dlat
            lon_value += dlon
            lat = (lat_offset + granularity * lat_value) * 1e-9
            lon = (lon_offset + granularity * lon_value) * 1e-9
            yield node_id, lat, lon


def _ways(group: bytes, strings: list[bytes]):
    for number, _, value in _fields(group):
        if number != 3:
            continue
        way_id = None
        keys: list[int] = []
        vals: list[int] = []
        refs_delta: list[int] = []
        for field_no, _, field_value in _fields(bytes(value)):
            if field_no == 1:
                way_id = int(field_value)
            elif field_no == 2:
                keys = _packed_varints(bytes(field_value))
            elif field_no == 3:
                vals = _packed_varints(bytes(field_value))
            elif field_no == 8:
                refs_delta = _packed_varints(bytes(field_value), zigzag=True)
        tags = {
            strings[key].decode("utf-8", errors="replace"): strings[val].decode("utf-8", errors="replace")
            for key, val in zip(keys, vals)
        }
        ref = 0
        refs = []
        for delta in refs_delta:
            ref += delta
            refs.append(ref)
        yield way_id, tags, refs


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(min(1.0, a)))


def _target_grid(points: list[tuple[float, float]], cell_deg: float):
    grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for lat, lon in points:
        key = (math.floor(lat / cell_deg), math.floor(lon / cell_deg))
        grid.setdefault(key, []).append((lat, lon))
    return grid


def extract_local_highways(
    pbf_path: Path,
    points: list[tuple[float, float]],
    *,
    node_buffer_m: float = 300.0,
) -> list[dict]:
    """判定点近傍に存在するhighway wayをPBFから抽出する。"""
    cell_deg = 0.005
    grid = _target_grid(points, cell_deg)
    near_nodes: set[int] = set()

    print(f"PBF pass 1/3: nodes within {node_buffer_m:g}m of analysis points", flush=True)
    for block_index, block in _data_blocks(pbf_path):
        _, groups, granularity, lat_offset, lon_offset = _primitive_block(block)
        for group in groups:
            for node_id, lat, lon in _dense_nodes(group, granularity, lat_offset, lon_offset):
                cell = (math.floor(lat / cell_deg), math.floor(lon / cell_deg))
                nearby = []
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        nearby.extend(grid.get((cell[0] + di, cell[1] + dj), ()))
                if any(_haversine_m(lat, lon, plat, plon) <= node_buffer_m for plat, plon in nearby):
                    near_nodes.add(node_id)
        if block_index % 250 == 0:
            print(f"  blocks={block_index}, near_nodes={len(near_nodes)}", flush=True)

    print(f"PBF pass 2/3: highway ways touching {len(near_nodes)} nearby nodes", flush=True)
    selected: dict[int, dict] = {}
    all_refs: set[int] = set()
    for block_index, block in _data_blocks(pbf_path):
        strings, groups, _, _, _ = _primitive_block(block)
        for group in groups:
            for way_id, tags, refs in _ways(group, strings):
                if "highway" not in tags or not any(ref in near_nodes for ref in refs):
                    continue
                selected[way_id] = {"id": way_id, "tags": tags, "refs": refs}
                all_refs.update(refs)
        if block_index % 250 == 0:
            print(f"  blocks={block_index}, ways={len(selected)}, refs={len(all_refs)}", flush=True)

    print(f"PBF pass 3/3: coordinates for {len(all_refs)} selected refs", flush=True)
    coordinates: dict[int, tuple[float, float]] = {}
    for block_index, block in _data_blocks(pbf_path):
        _, groups, granularity, lat_offset, lon_offset = _primitive_block(block)
        for group in groups:
            for node_id, lat, lon in _dense_nodes(group, granularity, lat_offset, lon_offset):
                if node_id in all_refs:
                    coordinates[node_id] = (lon, lat)
        if block_index % 250 == 0:
            print(f"  blocks={block_index}, coordinates={len(coordinates)}", flush=True)

    result = []
    for way in selected.values():
        geometry = [coordinates[ref] for ref in way["refs"] if ref in coordinates]
        if geometry:
            result.append({"id": way["id"], "tags": way["tags"], "geometry": geometry})
    print(f"Local PBF extraction complete: {len(result)} highway ways", flush=True)
    return result
