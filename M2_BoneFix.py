#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""骨骼轨道修复：从 MD21 (v274) 模型复制 Translation/Rotation/Scaling 轨道到 MD20 (v264) 模型。

264 转换过程中骨骼的 M2Track 数据可能损坏；本模块以 274 模型为基准，
把每根骨骼的三条轨道（平移/旋转/缩放）的完整数据复制到 264 模型中。

M2Track 真实布局（0x14 字节，按动画序列分块存储）：
  +0x00 u16 interpolationType
  +0x02 u16 globalSequence
  +0x04 u32 nTimes      （时间戳描述符个数，通常＝动画序列数）
  +0x08 u32 ofsTimes    → nTimes 个描述符，每个 8 字节 {u32 count, u32 offset}
                          offset 指向该序列的 uint32 时间戳数组
  +0x0C u32 nKeys       （键值描述符个数，通常＝nTimes）
  +0x10 u32 ofsKeys     → nKeys 个描述符，每个 8 字节 {u32 count, u32 offset}
                          offset 指向该序列的键值数组

骨骼记录（0x58 字节）内轨道偏移：
  Translation @ +0x10（键值 C3Vector，12 字节）
  Rotation    @ +0x24（键值压缩四元数，8 字节）
  Scaling     @ +0x38（键值 float，4 字节）

MD21 文件结构：文件头 "MD21"（8 字节：魔数 + uint32），其后是 "MD20" chunk；
MD20 chunk 无独立 size 字段，chunk 数据直接以版本号开头；
chunk 内所有偏移相对于 MD20 魔数所在位置（本模块自动检测偏移基准）。
"""

import os
import struct
from pathlib import Path

from M2_Mover import M2Error, parse_m2
from M2_TrackIO import BlockWriter, read_track as _io_read_track, write_track

BONE_COUNT_POSITION = 0x2C
BONE_OFFSET_POSITION = 0x30
BONE_RECORD_SIZE = 0x58

TRACK_TRANSLATION_OFFSET = 0x10
TRACK_ROTATION_OFFSET = 0x24
TRACK_SCALING_OFFSET = 0x38
TRACK_SIZE = 0x14

TIMESTAMP_SIZE = 4
KEY_SIZES = {
    TRACK_TRANSLATION_OFFSET: 12,   # C3Vector
    TRACK_ROTATION_OFFSET: 8,       # 压缩四元数 4 x u16
    TRACK_SCALING_OFFSET: 12,       # C3Vector（M2.bt 模板 sBone 定义）
}
TRACK_LABELS = {
    TRACK_TRANSLATION_OFFSET: "平移轨道",
    TRACK_ROTATION_OFFSET: "旋转轨道",
    TRACK_SCALING_OFFSET: "缩放轨道",
}
MAX_SEQUENCE_COUNT = 4096


def find_md20_pos(data):
    """在 MD21 文件中定位内嵌 MD20 chunk 的魔数位置。"""
    if len(data) < 0x130:
        raise M2Error("274 文件过小，不是有效的 MD21 文件。")
    if data[:4] != b"MD21":
        raise M2Error("参考文件不是 MD21 格式（缺少 MD21 文件头）。")
    pos = data.find(b"MD20", 4)
    if pos < 0:
        raise M2Error("在 274 文件中找不到内嵌的 MD20 数据块。")
    if pos + 0x130 > len(data):
        raise M2Error("274 文件中的 MD20 数据块不完整。")
    version = struct.unpack_from("<I", data, pos + 4)[0]
    if version != 274:
        raise M2Error("参考文件版本为 %d，预期 MD20 v274。" % version)
    return pos


def _read_track(source, record, track_offset, off_base, key_size=None, label=None):
    """读取一条骨骼轨道的全部数据（封装通用轨道读取）。"""
    if key_size is None:
        key_size = KEY_SIZES[track_offset]
    if label is None:
        label = TRACK_LABELS[track_offset]
    return _io_read_track(source, record, track_offset, off_base,
                          key_size, label)


# (count位置, 偏移位置, 记录大小, 记录内轨道[(偏移, 键值大小)])
_VALIDATION_TABLES = (
    (0x2C, 0x30, 0x58, ((0x10, 12), (0x24, 8), (0x38, 4))),   # 骨骼表
    (0x60, 0x64, 0x3C, ((0x00, 12), (0x14, 8), (0x28, 4))),   # TexAnim 表（Legion v274 位置）
)


def _check_table(data, fields_base, off_base,
                 count_position, offset_position, record_size):
    """校验某组基准下单表是否可读，返回表信息或 None。"""
    try:
        count, offset = struct.unpack_from(
            "<II", data, fields_base + count_position)
    except struct.error:
        return None
    if count == 0 or count > MAX_SEQUENCE_COUNT * 16:
        return None
    absolute = off_base + offset
    if absolute >= len(data):
        return None
    if count > (len(data) - absolute) // record_size:
        return None
    return {"count": count, "offset": offset, "size": record_size}


def _check_bones_table(data, fields_base, off_base):
    """校验某组基准下骨骼表是否可读，返回骨骼表信息或 None。"""
    return _check_table(data, fields_base, off_base,
                        BONE_COUNT_POSITION, BONE_OFFSET_POSITION,
                        BONE_RECORD_SIZE)


def _check_track(data, record, track_offset, base, off_base, key_size=None):
    """抽样校验记录内一条轨道在指定基准下结构是否合理。"""
    if key_size is None:
        key_size = KEY_SIZES[track_offset]
    try:
        _read_track(data, record, track_offset, off_base, key_size,
                    TRACK_LABELS.get(track_offset, "轨道"))
    except (struct.error, M2Error):
        return False
    return True


def resolve_md21_base(data, pos):
    """自动检测 274 文件的基准组合，返回 (fields_base, off_base)。

    fields_base：头部字段的读取位置（MD20 魔数位置，或魔数+8 的变体）；
    off_base：文件内偏移值的解释基准（魔数位置或文件头 0）。
    依次用骨骼表、TexAnim 表做结构校验，任一通过即可。
    """
    candidates = []
    for fields_base in (pos, pos + 8):
        for off_base in (fields_base, 0):
            if (fields_base, off_base) in candidates:
                continue
            for (count_pos, offset_pos, record_size,
                 track_layout) in _VALIDATION_TABLES:
                table = _check_table(
                    data, fields_base, off_base,
                    count_pos, offset_pos, record_size)
                if not table:
                    continue
                record = table["offset"]
                if all(
                        _check_track(
                            data, record, track_offset,
                            fields_base, off_base, key_size)
                        for track_offset, key_size in track_layout):
                    candidates.append((fields_base, off_base))
                    break
    if not candidates:
        raise M2Error("无法识别 274 文件的偏移结构，文件可能不是标准 MD21 v274。")
    return candidates[0]


def copy_bone_tracks(md20_path, md21_path, output_path):
    """把 274 模型的骨骼轨道复制进 264 模型，另存为新文件。"""
    source = os.path.normcase(os.path.abspath(str(md20_path)))
    target = os.path.normcase(os.path.abspath(str(output_path)))
    if source == target:
        raise M2Error("为保护原模型，输出文件不能与输入 M2 相同。")

    md20 = Path(md20_path).read_bytes()
    info = parse_m2(md20)
    bones264 = info["tables"]["bones"]

    md21 = Path(md21_path).read_bytes()
    pos = find_md20_pos(md21)
    fields_base, off_base = resolve_md21_base(md21, pos)
    bones274 = _check_bones_table(md21, fields_base, off_base)

    if bones264["count"] != bones274["count"]:
        raise M2Error(
            "两个模型的骨骼数量不一致（264：%d 根，274：%d 根），无法按索引复制。"
            % (bones264["count"], bones274["count"]))
    if not bones264["count"]:
        raise M2Error("模型中没有骨骼，无需修复。")

    data = bytearray(md20)
    writer = BlockWriter(len(data))
    copied_tracks = 0
    copied_keyframes = 0

    for index in range(bones264["count"]):
        record264 = bones264["offset"] + index * bones264["size"]
        record274 = bones274["offset"] + index * bones274["size"]
        for track_offset in (TRACK_TRANSLATION_OFFSET,
                             TRACK_ROTATION_OFFSET,
                             TRACK_SCALING_OFFSET):
            label = TRACK_LABELS[track_offset]
            (interpolation, global_sequence,
             times_descs, keys_descs) = _read_track(
                md21, record274, track_offset, off_base, label=label)

            write_track(
                data, record264, track_offset, writer,
                interpolation, global_sequence,
                times_descs, keys_descs)
            copied_tracks += 1
            copied_keyframes += sum(count for count, _ in keys_descs)

    data.extend(writer.appended)

    # 写出前最终校验：所有写回的轨道偏移必须落在文件内。
    for index in range(bones264["count"]):
        record264 = bones264["offset"] + index * bones264["size"]
        for track_offset in (TRACK_TRANSLATION_OFFSET,
                             TRACK_ROTATION_OFFSET,
                             TRACK_SCALING_OFFSET):
            _, _, n_times, ofs_times, n_keys, ofs_keys = \
                struct.unpack_from("<HHIIII", data, record264 + track_offset)
            if n_times and ofs_times + n_times * 8 > len(data):
                raise M2Error("内部错误：时间戳表偏移越界，未写出文件。")
            if n_keys and ofs_keys + n_keys * 8 > len(data):
                raise M2Error("内部错误：键值表偏移越界，未写出文件。")

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
        "bones": bones264["count"],
        "tracks": copied_tracks,
        "keyframes": copied_keyframes,
        "output_size": len(data),
    }
