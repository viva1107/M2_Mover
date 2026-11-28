#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 物理文件 (.phys) 镜像处理。

.phys 文件是 chunk-based 格式，包含碰撞体（胶囊、盒子等）和关节约束的位置数据。
镜像改变手性后，物理碰撞体位置也需要同步取反。

当前处理范围：
- CAPS chunk（胶囊碰撞体）：Bottom 和 Top 位置
- 其他 chunk 待扩展
"""

import math
import os
import struct
from pathlib import Path

from M2_Mover import M2Error


class PhysError(M2Error):
    pass


def _read_chunk_at(data, offset):
    """读取 chunk 的 magic 和 size。"""
    if offset + 8 > len(data):
        return None, 0
    magic = data[offset:offset+4]
    size = struct.unpack_from("<I", data, offset + 4)[0]
    return magic, size


def _mirror_float3(data, offset, axis, label):
    """将 offset 处的三分量浮点向量的指定轴分量取反。"""
    off = offset + axis * 4
    old = struct.unpack_from("<f", data, off)[0]
    if not math.isfinite(old):
        raise PhysError("%s 的 %s 分量不是有限数值。" % (label, "XYZ"[axis]))
    struct.pack_into("<f", data, off, -old)


def mirror_phys_caps(data, chunk_offset, chunk_size, axis):
    """处理 CAPS chunk 中的胶囊位置。每个 capsule 28 字节。"""
    # chunk 头 8 字节，数据从 chunk_offset + 8 开始
    data_start = chunk_offset + 8
    n_capsules = chunk_size // 28
    count = 0
    for i in range(n_capsules):
        pos = data_start + i * 28
        if pos + 28 > len(data):
            break
        _mirror_float3(data, pos, axis, "CapsuleBottom")
        _mirror_float3(data, pos + 12, axis, "CapsuleTop")
        count += 1
    return count


def mirror_phys(input_path, output_path, axis):
    """对 .phys 文件做镜像处理。"""
    original = Path(input_path).read_bytes()
    data = bytearray(original)

    offset = 0
    caps_count = 0
    while offset < len(data):
        magic, size = _read_chunk_at(data, offset)
        if magic is None:
            break
        # 检查 magic 是否看起来像有效 chunk（可打印 ASCII）
        if not all(32 <= b <= 126 or b == 0 for b in magic):
            break
        if magic == b"CAPS":
            caps_count += mirror_phys_caps(data, offset, size, axis)
        # 后续可扩展 BOXS、JOIN 等 chunk
        offset += 8 + size
        if size == 0:
            offset += 1  # 避免死循环

    if caps_count == 0:
        return {"found": False, "capsules": 0}

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(str(temporary), str(output))
    finally:
        if temporary.exists():
            temporary.unlink()

    return {"found": True, "capsules": caps_count, "output_size": len(data)}


def find_phys_file(m2_path):
    """查找与 M2 文件同目录下同名的 .phys 文件。"""
    source = Path(m2_path)
    phys = source.with_suffix(".phys")
    if phys.is_file():
        return phys
    # 某些命名格式：xxx00.phys
    for child in sorted(source.parent.iterdir() if source.parent.exists() else []):
        if child.is_file() and child.suffix.lower() == ".phys":
            if child.stem == source.stem or child.stem.startswith(source.stem):
                return child
    return None


def mirror_all_phys(m2_input, m2_output, axis):
    """对 M2 对应的所有 .phys 文件做镜像。"""
    phys = find_phys_file(m2_input)
    if not phys:
        return {"found": 0, "results": []}

    m2_out = Path(m2_output)
    out = m2_out.parent / phys.name
    try:
        stats = mirror_phys(str(phys), str(out), axis)
        return {"found": 1, "results": [{"name": phys.name, **stats}]}
    except PhysError as exc:
        return {"found": 1, "results": [{"name": phys.name, "found": False, "error": str(exc)}]}
