#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TexAnim 复制：把源 M2（264 或 274）的纹理动画（M2TextureAnim）复制到目标 264 M2。

每条 TexAnim 记录（0x3C 字节）包含三条 M2Track：
  Translation @ +0x00（键值 C3Vector，12 字节）
  Rotation    @ +0x14（键值压缩四元数，8 字节）
  Scaling     @ +0x28（键值 float，4 字节）

表位置：nTexAnims @ 0x64，ofsTexAnims @ 0x68。

模式：
  追加复制（replace_slots=None）：保留目标已有 TexAnim，把选中的源 TexAnim
    追加到末尾（新记录接在原表之后，原索引不变）；
  替换（replace_slots 为列表）：把选中的源 TexAnim 覆盖到目标指定的槽位，
    目标其余槽位保持不变，TexAnim 总数与表位置不变，材质索引引用不受影响。

indices：None 表示全部；否则为源文件中的索引列表（从 0 开始）。
replace_slots：目标文件中的槽位列表，与 indices 一一对应（len 必须相等）。
"""

import os
import struct
from pathlib import Path

from M2_Mover import M2Error, parse_m2
from M2_BoneFix import find_md20_pos, resolve_md21_base
from M2_TrackIO import BlockWriter, read_track, write_track

TEXANIM_COUNT_POSITION = 0x64        # 经典 WotLK v264 头部字段位置
TEXANIM_OFFSET_POSITION = 0x68
TEXANIM_COUNT_POSITION_ALT = 0x60    # Cata+/Legion 布局（0x44 处 num_skin_profiles 为单字段，整体前移 4 字节）
TEXANIM_OFFSET_POSITION_ALT = 0x64
TEXANIM_RECORD_SIZE = 0x3C
MAX_TEXANIM_COUNT = 4096

TEXANIM_TRACKS = (
    (0x00, 12, "TexAnim 平移轨道"),
    (0x14, 8, "TexAnim 旋转轨道"),
    (0x28, 12, "TexAnim 缩放轨道"),
)


def parse_indices(text, count):
    """解析索引文本（如 \"0,2\"、\"1-3,5\"），返回排序去重后的索引列表；
    空文本返回 None（表示全部）。索引必须在 [0, count) 内。"""
    text = (text or "").strip()
    if not text:
        return None
    result = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            pieces = part.split("-")
            if len(pieces) != 2 or not pieces[0].strip().isdigit() \
                    or not pieces[1].strip().isdigit():
                raise M2Error("无法解析索引片段：%s。" % part)
            start, end = int(pieces[0]), int(pieces[1])
            if end < start:
                raise M2Error("索引区间无效：%s。" % part)
            result.update(range(start, end + 1))
        else:
            if not part.isdigit():
                raise M2Error("无法解析索引片段：%s。" % part)
            result.add(int(part))
    if not result:
        return None
    bad = [i for i in result if i >= count or i < 0]
    if bad:
        raise M2Error("索引超出范围：%s（源文件共有 %d 条 TexAnim，合法索引 0～%d）。"
                      % (", ".join(map(str, sorted(bad))), count, count - 1))
    return sorted(result)


def _validate_texanim_at(data, fields_base, off_base, count_pos, offset_pos):
    """校验某头部位置是否存在结构合法的 TexAnim 表，通过则返回表信息。"""
    try:
        count, offset = struct.unpack_from(
            "<II", data, fields_base + count_pos)
    except struct.error:
        return None
    if count > MAX_TEXANIM_COUNT:
        return None
    if count:
        absolute = off_base + offset
        if absolute >= len(data) \
                or count > (len(data) - absolute) // TEXANIM_RECORD_SIZE:
            return None
        record = offset
        for track_offset, key_size, label in TEXANIM_TRACKS:
            try:
                read_track(data, record, track_offset,
                           off_base, key_size, label)
            except (struct.error, M2Error):
                return None
    return {"count": count, "offset": offset, "size": TEXANIM_RECORD_SIZE}


def locate_texanim_table(data, fields_base, off_base, prefer_alt=False):
    """自动探测 TexAnim 表头部位置。

    部分 274→264 转换文件把 Cata+/Legion 头部布局（TexAnim @ 0x60/0x64）
    塞进了 MD20 v264 壳里；经典 WotLK 文件则在 0x64/0x68。
    两个候选位置都做结构校验，优先取 prefer_alt 指定的布局。
    返回 (count_pos, offset_pos, table)。
    """
    layouts = ((TEXANIM_COUNT_POSITION_ALT, TEXANIM_OFFSET_POSITION_ALT),
               (TEXANIM_COUNT_POSITION, TEXANIM_OFFSET_POSITION)) \
        if prefer_alt else \
        ((TEXANIM_COUNT_POSITION, TEXANIM_OFFSET_POSITION),
         (TEXANIM_COUNT_POSITION_ALT, TEXANIM_OFFSET_POSITION_ALT))
    found = []
    for count_pos, offset_pos in layouts:
        table = _validate_texanim_at(
            data, fields_base, off_base, count_pos, offset_pos)
        if table is not None:
            found.append((count_pos, offset_pos, table))
    if not found:
        raise M2Error("找不到结构合法的纹理动画表，文件可能已损坏。")
    return found[0]


def _open_texanim_source(path):
    """打开源文件（264 或 274），返回 (data, off_base, texanims 表信息)。"""
    data = Path(path).read_bytes()
    if data[:4] == b"MD21":
        pos = find_md20_pos(data)
        fields_base, off_base = resolve_md21_base(data, pos)
        _, _, table = locate_texanim_table(
            data, fields_base, off_base, prefer_alt=True)
        return data, off_base, table
    if data[:4] == b"MD20":
        parse_m2(data)  # 校验整体结构
        _, _, table = locate_texanim_table(data, 0, 0)
        return data, 0, table
    raise M2Error("源文件既不是 MD20 也不是 MD21 格式。")


def _build_record(source_data, off_base, record, writer):
    """读取一条 TexAnim 记录（源内偏移 off_base 解释），用前移引用重建到 writer。

    返回 (0x3C 字节的新记录, 键值总数)。所有轨道数据追加到输出文件末尾，
    保证 010 模板解析时所有偏移引用向前（模板不支持记录表回溯引用）。
    """
    new_record = bytearray(TEXANIM_RECORD_SIZE)
    keyframes = 0
    for track_offset, key_size, label in TEXANIM_TRACKS:
        (interpolation, global_sequence,
         times_descs, keys_descs) = read_track(
            source_data, record, track_offset, off_base, key_size, label)
        write_track(
            new_record, 0, track_offset, writer,
            interpolation, global_sequence,
            times_descs, keys_descs)
        keyframes += sum(count for count, _ in keys_descs)
    return new_record, keyframes


def copy_tex_anims(source_path, target_path, output_path,
                   indices=None, replace_slots=None):
    """把源 M2 中选定的 TexAnim 复制到目标 264 M2，另存为新文件。

    indices：None＝全部；列表＝源文件中的索引（从 0 开始）。
    replace_slots：None＝追加到目标末尾；列表＝目标文件中的槽位，
    与 indices 一一对应，就地覆盖。

    无论哪种模式，整个 TexAnim 记录表都会重建：
    被替换/新增的槽位写入源数据，其余槽位把目标原有轨道数据
    复制到文件末尾并修正引用。这保证所有偏移引用严格向前——
    010 的 M2.bt 模板在解析记录表时不支持向后回溯的偏移
    （否则会报 "Ending position was in front of starting position"）。
    """
    source = os.path.normcase(os.path.abspath(str(target_path)))
    target = os.path.normcase(os.path.abspath(str(output_path)))
    if source == target:
        raise M2Error("为保护原模型，输出文件不能与目标 M2 相同。")

    src_data, src_off_base, src_table = \
        _open_texanim_source(source_path)

    if not src_table["count"]:
        raise M2Error("源文件中没有 TexAnim 记录。")

    selected = list(range(src_table["count"])) \
        if indices is None else list(indices)
    if not selected:
        raise M2Error("未选择任何 TexAnim。")
    bad = [i for i in selected if i < 0 or i >= src_table["count"]]
    if bad:
        raise M2Error("源索引超出范围：%s（源文件共有 %d 条，合法索引 0～%d）。"
                      % (", ".join(map(str, bad)), src_table["count"],
                         src_table["count"] - 1))

    md20 = Path(target_path).read_bytes()
    parse_m2(md20)
    dst_count_pos, dst_offset_pos, dst_table = locate_texanim_table(md20, 0, 0)

    data = bytearray(md20)
    writer = BlockWriter(len(data))
    copied_keyframes = 0

    if replace_slots is None:
        # 追加模式：重建完整连续记录表（原记录 + 新记录）
        mapping = {dst_table["count"] + position: src_index
                   for position, src_index in enumerate(selected)}
        table_count = dst_table["count"] + len(selected)
        records_start = writer.reserve(TEXANIM_RECORD_SIZE * table_count)
        new_count = table_count
        new_offset = records_start if table_count else 0
        mode = "追加"
    else:
        # 替换模式：表位置与总数不变，所有槽位就地重建
        slots = list(replace_slots)
        if not slots:
            raise M2Error("未指定要替换的目标槽位。")
        if len(slots) != len(selected):
            raise M2Error("源索引与目标槽位个数不一致（%d 个源，%d 个槽位）。"
                          % (len(selected), len(slots)))
        bad = [i for i in slots if i < 0 or i >= dst_table["count"]]
        if bad:
            raise M2Error("目标槽位超出范围：%s（目标共有 %d 条，合法槽位 0～%d）。"
                          % (", ".join(map(str, bad)), dst_table["count"],
                             dst_table["count"] - 1))
        mapping = dict(zip(slots, selected))
        table_count = dst_table["count"]
        records_start = None
        new_count = table_count
        new_offset = dst_table["offset"]
        mode = "替换"

    for slot in range(table_count):
        if slot in mapping:
            src_rec = (src_table["offset"] +
                       mapping[slot] * TEXANIM_RECORD_SIZE)
            new_record, kf = _build_record(
                src_data, src_off_base, src_rec, writer)
            copied_keyframes += kf
        else:
            dst_rec = dst_table["offset"] + slot * TEXANIM_RECORD_SIZE
            try:
                new_record, kf = _build_record(md20, 0, dst_rec, writer)
                copied_keyframes += kf
            except (M2Error, struct.error) as exc:
                raise M2Error(
                    "目标第 %d 条 TexAnim 的轨道数据无法解析（%s），"
                    "无法安全%s。" % (slot, exc, mode))
        if replace_slots is None:
            writer.fill(records_start + slot * TEXANIM_RECORD_SIZE,
                        bytes(new_record))
        else:
            record_pos = dst_table["offset"] + slot * TEXANIM_RECORD_SIZE
            data[record_pos:record_pos + TEXANIM_RECORD_SIZE] = \
                bytes(new_record)

    struct.pack_into("<II", data, dst_count_pos, new_count, new_offset)
    data.extend(writer.appended)

    # 写出前完整重解析校验：所有记录必须能按结构读通
    try:
        _, _, out_table = locate_texanim_table(bytes(data), 0, 0)
        for i in range(out_table["count"]):
            rec = out_table["offset"] + i * TEXANIM_RECORD_SIZE
            for track_offset, key_size, label in TEXANIM_TRACKS:
                read_track(bytes(data), rec, track_offset, 0, key_size, label)
    except (M2Error, struct.error) as exc:
        raise M2Error("内部错误：写出前校验失败（%s），未写出文件。" % exc)

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
        "source_total": src_table["count"],
        "selected": selected,
        "copied": len(selected),
        "tracks": out_table["count"] * 3,
        "keyframes": copied_keyframes,
        "target_previous": dst_table["count"],
        "target_now": new_count,
        "replace_slots": list(replace_slots) if replace_slots is not None else None,
        "output_size": len(data),
    }
