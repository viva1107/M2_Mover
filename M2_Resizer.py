#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转换 AlastorScript31_M2_Resizer 当前实际启用的 MD20 v264 缩放逻辑。"""

import math
import os
import struct
from pathlib import Path

from M2_Mover import M2Error, parse_m2


CAMERA_COUNT_POSITION = 0x110
CAMERA_OFFSET_POSITION = 0x114
CAMERA_RECORD_SIZE = 0x64
CAMERA_POSITION_BASE = 0x24
PARTICLE_SCALE_VALUE_COUNT = 0x12C
PARTICLE_SCALE_VALUE_OFFSET = 0x130
PARTICLE_SCALE_VALUE_SIZE = 0x08


def scale_float(data, position, multiplier, label="数值"):
    old_value = struct.unpack_from("<f", data, position)[0]
    new_value = old_value * multiplier
    try:
        encoded = struct.pack("<f", new_value)
        unpacked = struct.unpack("<f", encoded)[0]
    except (OverflowError, struct.error) as exc:
        raise M2Error("%s缩放后无法转换为 32 位浮点数，偏移 0x%X：%s" % (
            label, position, exc))
    if not math.isfinite(unpacked):
        raise M2Error("%s缩放后超出 32 位浮点范围，偏移 0x%X。" % (label, position))
    data[position:position + 4] = encoded


def scale_xyz(data, position, multiplier, label="坐标"):
    for axis in range(3):
        scale_float(data, position + axis * 4, multiplier, label)


def camera_table(data):
    count, offset = struct.unpack_from(
        "<II", data, CAMERA_COUNT_POSITION)
    if count:
        if offset >= len(data):
            raise M2Error("相机表的起始偏移超出文件末尾。")
        if count > (len(data) - offset) // CAMERA_RECORD_SIZE:
            raise M2Error(
                "相机表超出文件末尾：数量 %d，单条大小 0x%X，偏移 0x%X。"
                % (count, CAMERA_RECORD_SIZE, offset))
    return {"count": count, "offset": offset, "size": CAMERA_RECORD_SIZE}


def resize_m2(input_path, output_path, multiplier):
    if (not isinstance(multiplier, (int, float))
            or not math.isfinite(multiplier) or multiplier <= 0):
        raise M2Error("缩放倍率必须是大于 0 的有限数值。")

    source = os.path.normcase(os.path.abspath(str(input_path)))
    target = os.path.normcase(os.path.abspath(str(output_path)))
    if source == target:
        raise M2Error("为保护原模型，输出文件不能与输入 M2 相同。")

    original = Path(input_path).read_bytes()
    info = parse_m2(original)
    cameras = camera_table(original)
    data = bytearray(original)

    # 顶点 Position。
    table = info["tables"]["vertices"]
    for index in range(table["count"]):
        scale_xyz(data, table["offset"] + index * table["size"], multiplier, "顶点")

    # 每段动画的 BoundingBoxMin、BoundingBoxMax 和 BoundingBoxRadius。
    table = info["tables"]["animations"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        scale_xyz(data, position + 0x20, multiplier, "动画包围盒")
        scale_xyz(data, position + 0x2C, multiplier, "动画包围盒")
        scale_float(data, position + 0x38, multiplier, "动画包围盒半径")

    # 附件 Position。
    table = info["tables"]["attachments"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        scale_xyz(data, position + 0x08, multiplier, "附件位置")

    # 相机 position_base，即原 010 模板中的 Camera.Pos。
    for index in range(cameras["count"]):
        position = cameras["offset"] + index * cameras["size"]
        scale_xyz(data, position + CAMERA_POSITION_BASE, multiplier, "相机位置")

    # 骨骼 PivotPoint。
    table = info["tables"]["bones"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        scale_xyz(data, position + 0x4C, multiplier, "骨骼 Pivot")

    # 粒子发射器 Pos。
    table = info["tables"]["particles"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        scale_xyz(data, position + 0x08, multiplier, "粒子位置")

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
    result["cameras"] = cameras["count"]
    result["output_size"] = len(data)
    result["scaled_floats"] = (
        info["tables"]["vertices"]["count"] * 3
        + info["tables"]["animations"]["count"] * 7
        + info["tables"]["attachments"]["count"] * 3
        + cameras["count"] * 3
        + info["tables"]["bones"]["count"] * 3
        + info["tables"]["particles"]["count"] * 3
    )
    return result


def scale_particle_sizes_m2(input_path, output_path, multiplier):
    """只缩放 Particle_scale.1sc 对应的 ScaleTrack 二维关键帧。"""
    if (not isinstance(multiplier, (int, float))
            or not math.isfinite(multiplier) or multiplier <= 0):
        raise M2Error("粒子缩放倍率必须是大于 0 的有限数值。")

    source = os.path.normcase(os.path.abspath(str(input_path)))
    target = os.path.normcase(os.path.abspath(str(output_path)))
    if source == target:
        raise M2Error("为保护原模型，输出文件不能与输入 M2 相同。")

    original = Path(input_path).read_bytes()
    info = parse_m2(original)
    data = bytearray(original)
    table = info["tables"]["particles"]
    emitters_with_keys = 0
    keyframe_pairs = 0

    for index in range(table["count"]):
        record = table["offset"] + index * table["size"]
        count, offset = struct.unpack_from(
            "<II", original, record + PARTICLE_SCALE_VALUE_COUNT)
        if not count:
            continue
        if not offset or offset >= len(original):
            raise M2Error("粒子 %d 的 ScaleTrack 值偏移无效。" % index)
        if count > (len(original) - offset) // PARTICLE_SCALE_VALUE_SIZE:
            raise M2Error(
                "粒子 %d 的 ScaleTrack 值表超出文件末尾：数量 %d，偏移 0x%X。"
                % (index, count, offset))
        emitters_with_keys += 1
        keyframe_pairs += count
        for keyframe in range(count):
            position = offset + keyframe * PARTICLE_SCALE_VALUE_SIZE
            scale_float(data, position, multiplier, "粒子 ScaleTrack.X")
            scale_float(data, position + 4, multiplier, "粒子 ScaleTrack.Y")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(str(temporary), str(output))
    finally:
        if temporary.exists():
            temporary.unlink()

    return {
        "particles": table["count"],
        "emitters_with_keys": emitters_with_keys,
        "keyframe_pairs": keyframe_pairs,
        "scaled_floats": keyframe_pairs * 2,
        "output_size": len(data),
    }
