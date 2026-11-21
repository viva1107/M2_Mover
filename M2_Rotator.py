#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以 LyswhScript_M2_Flip 的范围为基础，加入三种 M2_o/-/_I_Rotation 模式。"""

import math
import os
import struct
from pathlib import Path

from M2_Mover import M2Error, parse_m2


VERTEX_NORMAL_OFFSET = 0x14
EVENT_POSITION_OFFSET = 0x0C
EVENT_RECORD_SIZE = 0x24
BONE_TRANSLATION_TRACK = 0x10
BONE_ROTATION_TRACK = 0x24


def snapped(value):
    if abs(value) < 1e-15:
        return 0.0
    if abs(value - 1.0) < 1e-15:
        return 1.0
    if abs(value + 1.0) < 1e-15:
        return -1.0
    return value


def rotation_matrix(mode, degrees):
    if mode not in ("o", "dash", "i"):
        raise M2Error("未知旋转模式。")
    if not isinstance(degrees, (int, float)) or not math.isfinite(degrees):
        raise M2Error("旋转角度必须是有限数值。")
    cosine = snapped(math.cos(math.radians(degrees)))
    sine = snapped(math.sin(math.radians(degrees)))
    if mode == "o":       # M2_o_Rotation：Y、Z。
        return ((1.0, 0.0, 0.0), (0.0, cosine, -sine), (0.0, sine, cosine))
    if mode == "dash":    # M2_-_Rotation：X、Y，也是 Flip 的 180° Z 轴基础。
        return ((cosine, -sine, 0.0), (sine, cosine, 0.0), (0.0, 0.0, 1.0))
    # M2_I_Rotation：严格沿用原脚本的 X、Z 公式。
    return ((cosine, 0.0, -sine), (0.0, 1.0, 0.0), (sine, 0.0, cosine))


def rotation_quaternion(mode, degrees):
    """返回与 rotation_matrix 等价的世界坐标旋转四元数。"""
    half = math.radians(degrees) / 2.0
    sine = snapped(math.sin(half))
    cosine = snapped(math.cos(half))
    if mode == "o":
        return (sine, 0.0, 0.0, cosine)
    if mode == "dash":
        return (0.0, 0.0, sine, cosine)
    if mode == "i":
        return (0.0, -sine, 0.0, cosine)
    raise M2Error("未知旋转模式。")


def rotated(values, matrix):
    x, y, z = values
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z,
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z,
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z,
    )


def write_float(data, position, value, label):
    try:
        encoded = struct.pack("<f", value)
        unpacked = struct.unpack("<f", encoded)[0]
    except (OverflowError, struct.error) as exc:
        raise M2Error("%s旋转后无法转换为 32 位浮点数，偏移 0x%X：%s" % (
            label, position, exc))
    if not math.isfinite(unpacked):
        raise M2Error("%s旋转后超出 32 位浮点范围，偏移 0x%X。" % (label, position))
    data[position:position + 4] = encoded


def rotate_xyz(data, position, matrix, label):
    values = struct.unpack_from("<fff", data, position)
    result = rotated(values, matrix)
    for axis, value in enumerate(result):
        write_float(data, position + axis * 4, value, label)


def rotate_aabb(data, minimum_position, maximum_position, matrix, label):
    minimum = struct.unpack_from("<fff", data, minimum_position)
    maximum = struct.unpack_from("<fff", data, maximum_position)
    corners = [
        rotated((x, y, z), matrix)
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    ]
    new_minimum = tuple(min(point[axis] for point in corners) for axis in range(3))
    new_maximum = tuple(max(point[axis] for point in corners) for axis in range(3))
    for axis in range(3):
        write_float(data, minimum_position + axis * 4, new_minimum[axis], label)
        write_float(data, maximum_position + axis * 4, new_maximum[axis], label)


def read_table(data, count_position, offset_position, record_size, label):
    count, offset = struct.unpack_from("<II", data, count_position)
    if count:
        if offset >= len(data):
            raise M2Error("%s的起始偏移超出文件末尾。" % label)
        if count > (len(data) - offset) // record_size:
            raise M2Error("%s超出文件末尾：数量 %d，偏移 0x%X。" % (label, count, offset))
    return {"count": count, "offset": offset, "size": record_size}


def quaternion_multiply(left, right):
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def decode_compressed_quaternion(raw):
    return tuple(value / 32767.0 - 1.0 for value in raw)


def encode_compressed_quaternion(values):
    encoded = []
    for value in values:
        if not math.isfinite(value):
            raise M2Error("骨骼旋转四元数转换结果不是有限数值。")
        compressed = int(round((value + 1.0) * 32767.0))
        encoded.append(max(0, min(65535, compressed)))
    return tuple(encoded)


def rotate_compressed_quaternion(data, position, world_rotation):
    raw = struct.unpack_from("<HHHH", data, position)
    local = decode_compressed_quaternion(raw)
    inverse = tuple(-value for value in world_rotation[:3]) + (world_rotation[3],)
    result = quaternion_multiply(quaternion_multiply(world_rotation, local), inverse)
    struct.pack_into("<HHHH", data, position, *encode_compressed_quaternion(result))


def transform_bone_track(original, data, bone_record, track_offset, value_size,
                         animation_flags, world_rotation, matrix, kind, stats):
    """处理 M2 内部轨道；外部 .anim 序列只计数并跳过。"""
    global_sequence = struct.unpack_from("<H", original, bone_record + track_offset + 2)[0]
    outer_count, outer_offset = struct.unpack_from(
        "<II", original, bone_record + track_offset + 0x0C)
    if not outer_count:
        return
    if outer_offset >= len(original) or outer_count > (len(original) - outer_offset) // 8:
        raise M2Error("骨骼 %s 的序列值表超出文件末尾。" % kind)

    for sequence_index in range(outer_count):
        descriptor = outer_offset + sequence_index * 8
        count, offset = struct.unpack_from("<II", original, descriptor)
        if not count:
            continue
        if global_sequence == 0xFFFF:
            internal = (
                sequence_index < len(animation_flags)
                and bool(animation_flags[sequence_index] & 0x20)
            )
            if not internal:
                stats["external_tracks"] += 1
                stats["external_keyframes"] += count
                continue
        if not offset or offset >= len(original):
            raise M2Error("骨骼 %s 的值偏移无效。" % kind)
        if count > (len(original) - offset) // value_size:
            raise M2Error("骨骼 %s 的值表超出文件末尾。" % kind)

        for keyframe in range(count):
            position = offset + keyframe * value_size
            if kind == "平移轨道":
                rotate_xyz(data, position, matrix, "骨骼平移")
                stats["translation_keyframes"] += 1
            else:
                rotate_compressed_quaternion(data, position, world_rotation)
                stats["rotation_keyframes"] += 1


def save_output(output_path, data):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(str(temporary), str(output))
    finally:
        if temporary.exists():
            temporary.unlink()


def rotate_m2(input_path, output_path, mode, degrees):
    matrix = rotation_matrix(mode, degrees)
    world_rotation = rotation_quaternion(mode, degrees)
    source = os.path.normcase(os.path.abspath(str(input_path)))
    target = os.path.normcase(os.path.abspath(str(output_path)))
    if source == target:
        raise M2Error("为保护原模型，输出文件不能与输入 M2 相同。")

    original = Path(input_path).read_bytes()
    info = parse_m2(original)
    data = bytearray(original)
    collision_positions = read_table(original, 0xE0, 0xE4, 0x0C, "碰撞顶点表")
    collision_normals = read_table(original, 0xE8, 0xEC, 0x0C, "碰撞法线表")
    events = read_table(original, 0x100, 0x104, EVENT_RECORD_SIZE, "事件表")

    # Flip 基础：顶点位置和法线。
    vertices = info["tables"]["vertices"]
    for index in range(vertices["count"]):
        position = vertices["offset"] + index * vertices["size"]
        rotate_xyz(data, position, matrix, "顶点位置")
        rotate_xyz(data, position + VERTEX_NORMAL_OFFSET, matrix, "顶点法线")

    for table, label in ((collision_positions, "碰撞顶点"), (collision_normals, "碰撞法线")):
        for index in range(table["count"]):
            rotate_xyz(data, table["offset"] + index * table["size"], matrix, label)

    # 旋转后的轴对齐包围盒必须重新取 Min／Max；半径不因旋转而改变。
    rotate_aabb(data, 0xA0, 0xAC, matrix, "整体包围盒")
    rotate_aabb(data, 0xBC, 0xC8, matrix, "碰撞包围盒")
    animations = info["tables"]["animations"]
    animation_flags = []
    for index in range(animations["count"]):
        position = animations["offset"] + index * animations["size"]
        animation_flags.append(struct.unpack_from("<I", original, position + 0x0C)[0])
        rotate_aabb(data, position + 0x20, position + 0x2C, matrix, "动画包围盒")

    stats = {
        "vertices": vertices["count"],
        "collision_positions": collision_positions["count"],
        "collision_normals": collision_normals["count"],
        "bones": info["tables"]["bones"]["count"],
        "particles": info["tables"]["particles"]["count"],
        "events": events["count"],
        "attachments": info["tables"]["attachments"]["count"],
        "animations": animations["count"],
        "translation_keyframes": 0,
        "rotation_keyframes": 0,
        "external_tracks": 0,
        "external_keyframes": 0,
    }

    # 骨骼 Pivot；同时处理内部平移轨道和压缩旋转四元数。
    bones = info["tables"]["bones"]
    for index in range(bones["count"]):
        position = bones["offset"] + index * bones["size"]
        rotate_xyz(data, position + 0x4C, matrix, "骨骼 Pivot")
        transform_bone_track(
            original, data, position, BONE_TRANSLATION_TRACK, 0x0C,
            animation_flags, world_rotation, matrix, "平移轨道", stats)
        transform_bone_track(
            original, data, position, BONE_ROTATION_TRACK, 0x08,
            animation_flags, world_rotation, matrix, "旋转轨道", stats)

    table = info["tables"]["particles"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        rotate_xyz(data, position + 0x08, matrix, "粒子位置")

    for index in range(events["count"]):
        position = events["offset"] + index * events["size"]
        rotate_xyz(data, position + EVENT_POSITION_OFFSET, matrix, "事件位置")

    table = info["tables"]["attachments"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        rotate_xyz(data, position + 0x08, matrix, "附件位置")

    save_output(output_path, data)
    stats["output_size"] = len(data)
    return stats
