#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 模型镜像（反射）变换，同步处理外部 .skin 文件。

镜像与旋转不同：旋转保向（det=+1），镜像改变手性（det=-1）。
X 镜像（左右翻转，左手转右手）：位置 X 取反，法线 X 取反；
四元数 (qx, qy, qz, qw) 在 X 镜像下变为 (qx, -qy, -qz, qw)。

现代 M2 v264 的索引数据在外部 .skin 文件中，M2 本身不包含 views。
"""

import math
import os
import struct
from pathlib import Path

from M2_Mover import M2Error, parse_m2
from M2_Rotator import (
    BONE_TRANSLATION_TRACK,
    BONE_ROTATION_TRACK,
    EVENT_POSITION_OFFSET,
    EVENT_RECORD_SIZE,
    decode_compressed_quaternion,
    encode_compressed_quaternion,
    read_table,
    save_output,
)
from M2_SkinMirror import mirror_all_skins
from M2_PhysMirror import mirror_all_phys

VERTEX_NORMAL_OFFSET = 0x14
QUATERNION_VALUE_SIZE = 0x08
BONE_PIVOT_OFFSET = 0x4C


def mirror_axis_index(mode):
    if mode == "x":
        return 0
    if mode == "y":
        return 1
    if mode == "z":
        return 2
    raise M2Error("未知镜像模式。")


def mirror_xyz(data, position, axis, label):
    """将 position 处的三分量浮点向量的指定轴分量取反。"""
    offset = position + axis * 4
    old_value = struct.unpack_from("<f", data, offset)[0]
    if not math.isfinite(old_value):
        raise M2Error("%s的 %s 分量不是有限数值，偏移 0x%X。" % (label, "XYZ"[axis], offset))
    struct.pack_into("<f", data, offset, -old_value)


def mirror_quaternion(data, position, axis):
    """将压缩四元数的指定轴向量分量取反。"""
    raw = struct.unpack_from("<HHHH", data, position)
    values = list(decode_compressed_quaternion(raw))
    for i in range(3):
        if i != axis:
            values[i] = -values[i]
    struct.pack_into("<HHHH", data, position, *encode_compressed_quaternion(values))


def mirror_bone_track(original, data, bone_record, track_offset, value_size,
                       animation_flags, axis, kind, stats):
    """处理骨骼平移/旋转轨道的内部序列；外部 .anim 只计数并跳过。"""
    track = bone_record + track_offset
    global_sequence = struct.unpack_from("<H", original, track + 2)[0]
    outer_count, outer_offset = struct.unpack_from("<II", original, track + 0x0C)
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
                mirror_xyz(data, position, axis, "骨骼平移")
                stats["translation_keyframes"] += 1
            else:
                mirror_quaternion(data, position, axis)
                stats["rotation_keyframes"] += 1


def mirror_aabb(data, minimum_position, maximum_position, axis):
    """镜像后轴对齐包围盒：Min 变 Max、Max 变 Min 需重新计算。"""
    min_axis = struct.unpack_from("<f", data, minimum_position + axis * 4)[0]
    max_axis = struct.unpack_from("<f", data, maximum_position + axis * 4)[0]
    struct.pack_into("<f", data, minimum_position + axis * 4, -max_axis)
    struct.pack_into("<f", data, maximum_position + axis * 4, -min_axis)


def mirror_m2(input_path, output_path, mode):
    axis = mirror_axis_index(mode)
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

    # 顶点位置与法线
    vertices = info["tables"]["vertices"]
    for index in range(vertices["count"]):
        position = vertices["offset"] + index * vertices["size"]
        mirror_xyz(data, position, axis, "顶点位置")
        mirror_xyz(data, position + VERTEX_NORMAL_OFFSET, axis, "顶点法线")

    # 碰撞顶点与法线
    for table, label in ((collision_positions, "碰撞顶点"), (collision_normals, "碰撞法线")):
        for index in range(table["count"]):
            mirror_xyz(data, table["offset"] + index * table["size"], axis, label)

    animations = info["tables"]["animations"]
    animation_flags = []
    for index in range(animations["count"]):
        position = animations["offset"] + index * animations["size"]
        animation_flags.append(struct.unpack_from("<I", original, position + 0x0C)[0])

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

    # 骨骼 Pivot 与动画轨道
    bones = info["tables"]["bones"]
    for index in range(bones["count"]):
        position = bones["offset"] + index * bones["size"]
        mirror_xyz(data, position + BONE_PIVOT_OFFSET, axis, "骨骼 Pivot")
        mirror_bone_track(original, data, position, BONE_TRANSLATION_TRACK, 0x0C,
                           animation_flags, axis, "平移轨道", stats)
        mirror_bone_track(original, data, position, BONE_ROTATION_TRACK, QUATERNION_VALUE_SIZE,
                           animation_flags, axis, "旋转轨道", stats)

    # 粒子发射器位置
    table = info["tables"]["particles"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        mirror_xyz(data, position + 0x08, axis, "粒子位置")

    # 事件位置
    for index in range(events["count"]):
        position = events["offset"] + index * events["size"]
        mirror_xyz(data, position + EVENT_POSITION_OFFSET, axis, "事件位置")

    # 附件位置
    table = info["tables"]["attachments"]
    for index in range(table["count"]):
        position = table["offset"] + index * table["size"]
        mirror_xyz(data, position + 0x08, axis, "附件位置")

    # 整体包围盒、碰撞包围盒、动画包围盒
    mirror_aabb(data, 0xA0, 0xAC, axis)
    mirror_aabb(data, 0xBC, 0xC8, axis)
    for index in range(animations["count"]):
        position = animations["offset"] + index * animations["size"]
        mirror_aabb(data, position + 0x20, position + 0x30, axis)

    # 碰撞索引绕序反转（M2 内部）
    collision_indices = read_table(original, 0xD8, 0xDC, 2, "碰撞索引表")
    if collision_indices["count"] > 0 and collision_indices["count"] % 3 == 0:
        for i in range(0, collision_indices["count"], 3):
            idx = collision_indices["offset"] + i * 2
            b = struct.unpack_from("<H", data, idx + 2)[0]
            c = struct.unpack_from("<H", data, idx + 4)[0]
            struct.pack_into("<HH", data, idx + 2, c, b)
        stats["collision_triangles"] = collision_indices["count"] // 3

    # Lights 处理（sLights 记录大小 0x66=102，Position @ offset 6）
    lights = read_table(original, 0x108, 0x10C, 0x66, "灯光表")
    for i in range(lights["count"]):
        pos_off = lights["offset"] + i * lights["size"] + 6
        if pos_off + 12 <= len(data):
            mirror_xyz(data, pos_off, axis, "灯光位置")
            stats["lights"] = stats.get("lights", 0) + 1

    # RibbonEmitters 处理（sRibbons 记录大小 0x40=64，Pos @ offset 8）
    ribbons = read_table(original, 0x120, 0x124, 0x40, "丝带发射器表")
    for i in range(ribbons["count"]):
        pos_off = ribbons["offset"] + i * ribbons["size"] + 8
        if pos_off + 12 <= len(data):
            mirror_xyz(data, pos_off, axis, "丝带位置")
            stats["ribbons"] = stats.get("ribbons", 0) + 1

    # 这些表包含位置数据，镜像/旋转时也应处理，但需确认各版本结构大小。

    save_output(output_path, data)
    stats["output_size"] = len(data)

    # 同步处理外部 SKIN 文件绕序反转
    skin_stats = mirror_all_skins(input_path, output_path)
    stats["skin"] = skin_stats

    # 同步处理 .phys 物理文件
    phys_stats = mirror_all_phys(input_path, output_path, axis)
    stats["phys"] = phys_stats
    return stats
