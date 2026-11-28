#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Events 添加：向 264 M2 追加 N 条空白事件记录。

事件记录（0x24 字节，与 M2.bt 模板 sEvents 一致）：
  +0x00 char[4] Identifier（如 "$CSR"；空白记录为全 0）
  +0x04 u32 Data
  +0x08 u32 ParentBone
  +0x0C C3Vector Position（12 字节）
  +0x18 u16 type + i16 loop + u32 nTimestampPairs + u32 ofsTimestampPairs
       （Timer 轨道仅时间戳块，无键值块）

表位置自动探测（经典 WotLK 0x100/0x104 或前移 4 字节的转换文件布局）。
整个记录表重建到文件末尾，原有事件的 Timer 时间戳一并前移复制，
保证 010 模板解析时所有偏移引用向前。
"""

import os
import struct
from pathlib import Path

from M2_Mover import M2Error, parse_m2
from M2_TrackIO import BlockWriter

EVENT_RECORD_SIZE = 0x24
TIMER_OFFSET = 0x18
TIMESTAMP_SIZE = 4
MAX_EVENTS = 4096
MAX_TIMER_SEQUENCES = 4096

_EVENT_LAYOUTS = (
    (0x100, 0x104),
    (0xFC, 0x100),
)


def _event_record_ok(data, rec_abs, n_bones):
    if data[rec_abs:rec_abs + EVENT_RECORD_SIZE] == b"\x00" * EVENT_RECORD_SIZE:
        return True  # 空白事件记录（添加功能生成的初始状态）合法
    ident = data[rec_abs:rec_abs + 4]
    if any(b < 0x20 or b > 0x7E for b in ident):
        return False
    parent = struct.unpack_from("<I", data, rec_abs + 8)[0]
    if parent > max(n_bones * 8, 0x10000) and parent != 0xFFFFFFFF:
        return False
    n_ts = struct.unpack_from("<I", data, rec_abs + 0x1C)[0]
    if n_ts > MAX_TIMER_SEQUENCES:
        return False
    return True


def locate_events_table(data):
    try:
        n_bones = struct.unpack_from("<I", data, 0x2C)[0]
    except struct.error:
        n_bones = 0
    for count_pos, offset_pos in _EVENT_LAYOUTS:
        try:
            count, offset = struct.unpack_from("<II", data, count_pos)
        except struct.error:
            continue
        if count > MAX_EVENTS:
            continue
        if offset + count * EVENT_RECORD_SIZE > len(data):
            continue
        if count and not all(
                _event_record_ok(data, offset + i * EVENT_RECORD_SIZE, n_bones)
                for i in range(min(count, 3))):
            continue
        return count_pos, offset_pos, {"count": count, "offset": offset}
    raise M2Error("找不到结构合法的事件表，文件可能已损坏。")


def _read_timer(data, rec_abs):
    timer_type, loop, n_ts, ofs_ts = struct.unpack_from(
        "<HHII", data, rec_abs + TIMER_OFFSET)
    if n_ts > MAX_TIMER_SEQUENCES:
        raise M2Error("事件 Timer 序列数异常（%d）。" % n_ts)
    descs = []
    for i in range(n_ts):
        cnt, off = struct.unpack_from("<II", data, ofs_ts + i * 8)
        if cnt:
            if off + cnt * TIMESTAMP_SIZE > len(data):
                raise M2Error("事件第 %d 组时间戳超出文件末尾。" % i)
            descs.append((cnt, data[off:off + cnt * TIMESTAMP_SIZE]))
        else:
            descs.append((0, b""))
    return timer_type, loop, descs


def _build_record(source, rec_abs, writer):
    new_record = bytearray(EVENT_RECORD_SIZE)
    new_record[0:TIMER_OFFSET] = source[rec_abs:rec_abs + TIMER_OFFSET]
    timer_type, loop, descs = _read_timer(source, rec_abs)
    new_ofs = 0
    if descs:
        new_ofs = writer.allocate(
            lambda start, d=descs: BlockWriter.build_block(d, start))
    struct.pack_into("<HHII", new_record, TIMER_OFFSET,
                     timer_type, loop, len(descs), new_ofs)
    return new_record


def add_events(target_path, output_path, count=1):
    source = os.path.normcase(os.path.abspath(str(target_path)))
    target = os.path.normcase(os.path.abspath(str(output_path)))
    if source == target:
        raise M2Error("为保护原模型，输出文件不能与目标 M2 相同。")
    if not isinstance(count, int) or count < 1:
        raise M2Error("添加数量必须是正整数。")
    if count > MAX_EVENTS:
        raise M2Error("单次最多添加 %d 条事件。" % MAX_EVENTS)

    md20 = Path(target_path).read_bytes()
    parse_m2(md20)
    count_pos, offset_pos, table = locate_events_table(md20)

    data = bytearray(md20)
    writer = BlockWriter(len(data))

    new_count = table["count"] + count
    records_start = writer.reserve(EVENT_RECORD_SIZE * new_count)
    for i in range(table["count"]):
        rec = table["offset"] + i * EVENT_RECORD_SIZE
        try:
            new_record = _build_record(md20, rec, writer)
        except (M2Error, struct.error) as exc:
            raise M2Error(
                "目标第 %d 条事件的 Timer 数据无法解析（%s），无法安全添加。"
                % (i, exc))
        writer.fill(records_start + i * EVENT_RECORD_SIZE, bytes(new_record))

    struct.pack_into("<II", data, count_pos, new_count, records_start)
    data.extend(writer.appended)

    try:
        _, _, out_table = locate_events_table(bytes(data))
        if out_table["count"] != new_count:
            raise M2Error("数量不符")
        for i in range(out_table["count"]):
            _read_timer(bytes(data),
                        out_table["offset"] + i * EVENT_RECORD_SIZE)
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
        "previous": table["count"],
        "added": count,
        "now": new_count,
        "output_size": len(data),
    }
