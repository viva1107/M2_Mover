#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 轨道（M2Track）读写工具。

与 010 Editor M2.bt (Alastor Strix'Efuartus) 行为一致：
轨道头（0x14 字节）：
  +0x00 u16 interpolationType
  +0x02 i16 globalSequence（LoopID）
  +0x04 u32 nTimestampPairs  → ofsTimestampPairs 指向描述符表
  +0x0C u32 numberOfKeyFramePairs → offsetToKeyFramePairs 指向描述符表
每个描述符 8 字节 {i32 nValues, i32 ofsValues}，ofsValues 为文件绝对偏移
（MD21 内为相对 MD20 chunk 数据起点的偏移，调用方通过 off_base 换算）。
键值大小：Translation=C3Vector(12)，Rotation=压缩四元数(8)，Scaling=C3Vector(12)。
"""

import struct

from M2_Mover import M2Error

TIMESTAMP_SIZE = 4
MAX_SEQUENCE_COUNT = 4096

KEY_SIZES = {
    "translation": 12,
    "rotation": 8,
    "scaling": 12,
}


def read_track(source, record, track_offset, off_base, key_size, label):
    """读取一条轨道的全部数据。

    record 为记录（存储）偏移值，off_base 为文件内偏移的解释基准。
    返回 (interpolation, global_sequence, times_descs, keys_descs)，
    其中 times_descs/keys_descs 为 [(count, bytes), ...] 描述符列表。
    """
    position = off_base + record + track_offset
    interpolation, global_sequence, n_times, ofs_times, n_keys, ofs_keys = \
        struct.unpack_from("<HHIIII", source, position)

    if n_times > MAX_SEQUENCE_COUNT or n_keys > MAX_SEQUENCE_COUNT:
        raise M2Error("%s 的序列数异常（%d/%d）。" % (label, n_times, n_keys))

    times_descs = []
    keys_descs = []

    if n_times:
        table = off_base + ofs_times
        if table + n_times * 8 > len(source):
            raise M2Error("%s 的时间戳描述符表超出文件末尾。" % label)
        for i in range(n_times):
            count, offset = struct.unpack_from("<II", source, table + i * 8)
            if not count:
                times_descs.append((0, b""))
                continue
            absolute = off_base + offset
            if absolute + count * TIMESTAMP_SIZE > len(source):
                raise M2Error("%s 的第 %d 组时间戳超出文件末尾。" % (label, i))
            times_descs.append(
                (count, source[absolute:absolute + count * TIMESTAMP_SIZE]))

    if n_keys:
        table = off_base + ofs_keys
        if table + n_keys * 8 > len(source):
            raise M2Error("%s 的键值描述符表超出文件末尾。" % label)
        for i in range(n_keys):
            count, offset = struct.unpack_from("<II", source, table + i * 8)
            if not count:
                keys_descs.append((0, b""))
                continue
            absolute = off_base + offset
            if absolute + count * key_size > len(source):
                raise M2Error("%s 的第 %d 组键值超出文件末尾。" % (label, i))
            keys_descs.append(
                (count, source[absolute:absolute + count * key_size]))

    return interpolation, global_sequence, times_descs, keys_descs


class BlockWriter:
    """在输出文件末尾追加数据块（4 字节对齐），并修正块内部的绝对偏移引用。"""

    def __init__(self, appended_base):
        self.appended = bytearray()
        self.base = appended_base

    def allocate(self, builder):
        """为 builder 生成的数据块腾出空间，返回块的绝对文件偏移；
        builder 以该绝对偏移为参数，用于修正内部引用。"""
        aligned = (self.base + len(self.appended) + 3) & ~3
        payload = builder(aligned)
        self.appended.extend(b"\x00" * (aligned - (self.base + len(self.appended))))
        self.appended.extend(payload)
        return aligned

    def reserve(self, size):
        """预留 size 字节的连续空间（内容后补），返回块的绝对文件偏移。"""
        aligned = (self.base + len(self.appended) + 3) & ~3
        self.appended.extend(b"\x00" * (aligned - (self.base + len(self.appended))))
        self.appended.extend(b"\x00" * size)
        return aligned

    def fill(self, absolute_start, payload):
        """把内容写入此前 reserve 的空间（按绝对文件偏移）。"""
        relative = absolute_start - self.base
        self.appended[relative:relative + len(payload)] = payload

    @staticmethod
    def build_block(descs, absolute_start):
        """把 [(count, bytes), ...] 重建为描述符表＋数据区，
        描述符中的偏移指向输出文件中的绝对位置。"""
        table_size = len(descs) * 8
        running = table_size
        entries = []
        blobs = []
        for count, payload in descs:
            if count:
                entries.append((count, absolute_start + running))
                blobs.append(payload)
                running += len(payload)
            else:
                entries.append((0, 0))
        block = bytearray()
        for count, offset in entries:
            block.extend(struct.pack("<II", count, offset))
        for payload in blobs:
            block.extend(payload)
        return bytes(block)


def write_track(data, record_position, track_offset, writer,
                interpolation, global_sequence,
                times_descs, keys_descs):
    """把读取到的轨道数据写入输出文件（按描述符表结构重建）。

    data 为输出文件字节数组；record_position + track_offset 为轨道头写入位置；
    writer 为 BlockWriter（轨道数据追加到文件末尾）。
    """
    new_ofs_times = 0
    new_ofs_keys = 0
    if times_descs:
        new_ofs_times = writer.allocate(
            lambda start, d=times_descs: BlockWriter.build_block(d, start))
    if keys_descs:
        new_ofs_keys = writer.allocate(
            lambda start, d=keys_descs: BlockWriter.build_block(d, start))

    struct.pack_into(
        "<HHIIII", data, record_position + track_offset,
        interpolation, global_sequence,
        len(times_descs), new_ofs_times,
        len(keys_descs), new_ofs_keys)
