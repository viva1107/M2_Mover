#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 AlastorScript33_M2_Mover.1sc 的 MD20 v264 坐标移动逻辑转换为 Python。"""

import math
import os
import struct
from pathlib import Path


TABLE_LAYOUTS = {
    "animations": (0x1C, 0x20, 0x40),
    "bones": (0x2C, 0x30, 0x58),
    "vertices": (0x3C, 0x40, 0x30),
    "attachments": (0xF0, 0xF4, 0x28),
    "particles": (0x128, 0x12C, 0x1DC),
}

TABLE_NAMES_CN = {
    "animations": "动画序列",
    "bones": "骨骼",
    "vertices": "顶点",
    "attachments": "附件",
    "particles": "粒子发射器",
}


class M2Error(Exception):
    pass


def parse_m2(data):
    if len(data) < 0x130:
        raise M2Error("文件过小，M2 文件头不完整。")
    if data[:4] != b"MD20":
        raise M2Error("仅支持与原脚本一致的 MD20 格式，不支持 MD21 或其他文件。")
    version = struct.unpack_from("<I", data, 4)[0]
    if version != 264:
        raise M2Error("仅支持 MD20 v264，当前文件版本为 %d。" % version)

    tables = {}
    for name, (count_pos, offset_pos, record_size) in TABLE_LAYOUTS.items():
        count, offset = struct.unpack_from("<II", data, count_pos)
        if count:
            if offset >= len(data):
                raise M2Error("%s表的起始偏移超出文件末尾。" % TABLE_NAMES_CN[name])
            if count > (len(data) - offset) // record_size:
                raise M2Error(
                    "%s表超出文件末尾：数量 %d，单条大小 0x%X，偏移 0x%X。"
                    % (TABLE_NAMES_CN[name], count, record_size, offset)
                )
        tables[name] = {"count": count, "offset": offset, "size": record_size}
    return {"version": version, "tables": tables, "size": len(data)}


def inspect_m2(path):
    data = Path(path).read_bytes()
    info = parse_m2(data)
    info["path"] = str(path)
    return info


def move_xyz(data, position, deltas):
    for axis in range(3):
        offset = position + axis * 4
        old_value = struct.unpack_from("<f", data, offset)[0]
        new_value = old_value + deltas[axis]
        try:
            encoded = struct.pack("<f", new_value)
            unpacked = struct.unpack("<f", encoded)[0]
        except (OverflowError, struct.error) as exc:
            raise M2Error("坐标转换失败，偏移 0x%X：%s" % (offset, exc))
        if not math.isfinite(unpacked):
            raise M2Error("坐标移动后超出 32 位浮点范围，偏移 0x%X。" % offset)
        data[offset:offset + 4] = encoded


def move_m2(input_path, output_path, x, y, z):
    deltas = (x, y, z)
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in deltas):
        raise M2Error("X、Y、Z 移动量必须是有限数值。")

    source = os.path.normcase(os.path.abspath(str(input_path)))
    target = os.path.normcase(os.path.abspath(str(output_path)))
    if source == target:
        raise M2Error("为保护原模型，输出文件不能与输入 M2 相同。")

    original = Path(input_path).read_bytes()
    info = parse_m2(original)
    data = bytearray(original)

    # 整体 Bounding Box 与 Collision Box 的 Min／Max；半径不受平移影响。
    for minimum in (0xA0, 0xBC):
        move_xyz(data, minimum, deltas)
        move_xyz(data, minimum + 0x0C, deltas)

    # 每个动画序列自己的 Bounding Box Min／Max。
    table = info["tables"]["animations"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        move_xyz(data, position + 0x20, deltas)
        move_xyz(data, position + 0x30, deltas)

    # 模型顶点坐标，每条记录 0x30 字节。
    table = info["tables"]["vertices"]
    for index in range(table["count"]):
        move_xyz(data, table["offset"] + index * table["size"], deltas)

    # 附件 Pivot。
    table = info["tables"]["attachments"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        move_xyz(data, position + 0x08, deltas)

    # 骨骼 Pivot。
    table = info["tables"]["bones"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        move_xyz(data, position + 0x4C, deltas)

    # 粒子发射器位置，记录布局与原 010 脚本一致。
    table = info["tables"]["particles"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        move_xyz(data, position + 0x08, deltas)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(str(temporary), str(output))
    finally:
        if temporary.exists():
            temporary.unlink()

    result = {name: values["count"] for name, values in info["tables"].items()}
    result["output_size"] = len(data)
    result["moved_floats"] = (
        12 + info["tables"]["animations"]["count"] * 6
        + info["tables"]["vertices"]["count"] * 3
        + info["tables"]["attachments"]["count"] * 3
        + info["tables"]["bones"]["count"] * 3
        + info["tables"]["particles"]["count"] * 3
    )
    return result
