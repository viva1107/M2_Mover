#!/usr/bin/env python3
# -*- coding: utf-8 -*"
"""M2 SKIN 文件三角形绕序反转。

镜像会改变手性（det=-1），三角形顶点顺序必须反转才能保持正面朝外。
对 SKIN 文件中的每个三角形索引三元组 (a, b, c) 改为 (a, c, b)。

SKIN 格式（从 010 Editor 模板 SKIN.bt 确认）：
  0x00: char[4] magic   // "SKIN"
  0x04: uint32 nVertices
  0x08: uint32 ofsVertices
  0x0C: uint32 nIndices
  0x10: uint32 ofsIndices   <-- 三角形索引表（uint16 数组）
  0x14: uint32 nBoneIndices
  0x18: uint32 ofsBoneIndices
  0x1C: uint32 nSubmeshes
  0x20: uint32 ofsSubmeshes
  0x24: uint32 nTextureUnits
  0x28: uint32 ofsTextureUnits
  0x2C: uint32 boneCountMax
  // 条件：如果 ofsIndices/ofsVertices 等不是 0x30，则有 ShadowBatch
  0x30: uint32 nM2ShadowBatch (条件)
  0x34: uint32 ofsM2ShadowBatch (条件)
"""

import os
import struct
from pathlib import Path

from M2_Mover import M2Error


class SkinError(M2Error):
    pass


def _has_shadow_batch(data):
    """检测 SKIN 文件是否包含 ShadowBatch 段（模板中的条件判断）。"""
    if len(data) < 0x30:
        return False
    # 模板条件: ReadInt(0x8)!=0x30 && ReadInt(0x10)!=0x30 && ...
    # 简化为：如果 0x30 位置看起来像有效数据（非零且合理）
    val = struct.unpack_from("<I", data, 0x30)[0]
    return val != 0 and val < 65536


def mirror_skin_indices(skin_path, output_path):
    original = Path(skin_path).read_bytes()
    if len(original) < 0x10:
        raise SkinError("SKIN 文件过小。")
    if original[:4] != b"SKIN":
        raise SkinError("文件头不是 SKIN：%r" % original[:4])

    data = bytearray(original)

    # 正确偏移（来自 SKIN.bt 模板）
    n_vertices = struct.unpack_from("<I", data, 0x04)[0]
    ofs_vertices = struct.unpack_from("<I", data, 0x08)[0]
    n_indices = struct.unpack_from("<I", data, 0x0C)[0]
    ofs_indices = struct.unpack_from("<I", data, 0x10)[0]

    # 安全检查
    if ofs_indices >= len(data):
        raise SkinError("索引表偏移 0x%X 超出文件末尾（文件大小 %d）。" % (ofs_indices, len(data)))
    if n_indices > (len(data) - ofs_indices) // 2:
        raise SkinError(
            "索引表超出文件末尾：数量 %d，偏移 0x%X，文件大小 %d。"
            % (n_indices, ofs_indices, len(data)))
    if n_indices % 3 != 0:
        raise SkinError("索引数量 %d 不是 3 的倍数。" % n_indices)

    # 反转每个三角形的 b 和 c（(a,b,c) → (a,c,b)）
    count = 0
    for i in range(0, n_indices, 3):
        idx = ofs_indices + i * 2
        b = struct.unpack_from("<H", data, idx + 2)[0]
        c = struct.unpack_from("<H", data, idx + 4)[0]
        struct.pack_into("<HH", data, idx + 2, c, b)
        count += 1

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(str(temporary), str(output))
    finally:
        if temporary.exists():
            temporary.unlink()

    return {"triangles": count, "indices": n_indices, "output_size": len(data)}


def find_skin_files(m2_path):
    """查找与 M2 文件同目录下所有对应的 .skin 文件。

    匹配规则：文件名以 M2 的 stem 开头，例如：
      model.m2 → model00.skin、model01.skin、model.skin、model_lod.skin
    """
    source = Path(m2_path)
    folder = source.parent
    stem = source.stem
    skins = []
    for child in sorted(folder.iterdir() if folder.exists() else []):
        if child.is_file() and child.suffix.lower() == ".skin":
            name = child.stem
            if name == stem or name.startswith(stem):
                skins.append(child)
    return skins


def mirror_all_skins(m2_input, m2_output, skin_output_folder=None):
    skins = find_skin_files(m2_input)
    if not skins:
        return {"found": 0, "results": []}

    m2_out = Path(m2_output)
    out_folder = Path(skin_output_folder) if skin_output_folder else m2_out.parent

    results = []
    for skin in skins:
        out = out_folder / skin.name
        try:
            stats = mirror_skin_indices(skin, out)
            results.append({"name": skin.name, "status": "ok", **stats})
        except SkinError as exc:
            results.append({"name": skin.name, "status": "error", "message": str(exc)})
    return {"found": len(skins), "results": results}
