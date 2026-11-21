#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 SKIN 文件三角形绕序反转。

镜像会改变手性（det=-1），三角形顶点顺序必须反转才能保持正面朝外。
对 SKIN 文件中的每个三角形索引三元组 (a, b, c) 改为 (a, c, b)。

SKIN 格式（MD20 v264 对应）：
  0x00: magic "SKIN"
  0x04: uint32 nIndices
  0x08: uint32 offsetIndices
  ...
"""

import os
import struct
from pathlib import Path

from M2_Mover import M2Error


class SkinError(M2Error):
    pass


def mirror_skin_indices(skin_path, output_path):
    original = Path(skin_path).read_bytes()
    if len(original) < 0x10:
        raise SkinError("SKIN 文件过小。")
    if original[:4] != b"SKIN":
        raise SkinError("文件头不是 SKIN：%r" % original[:4])

    data = bytearray(original)
    n_indices = struct.unpack_from("<I", data, 0x04)[0]
    offset_indices = struct.unpack_from("<I", data, 0x08)[0]

    if offset_indices >= len(data):
        raise SkinError("索引表偏移 0x%X 超出文件末尾。" % offset_indices)
    if n_indices > (len(data) - offset_indices) // 2:
        raise SkinError(
            "索引表超出文件末尾：数量 %d，偏移 0x%X，文件大小 %d。"
            % (n_indices, offset_indices, len(data)))
    if n_indices % 3 != 0:
        raise SkinError("索引数量 %d 不是 3 的倍数。" % n_indices)

    count = 0
    for i in range(0, n_indices, 3):
        idx = offset_indices + i * 2
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
    """查找与 M2 文件同目录下所有对应的 .skin 文件（如 xxx00.skin、xxx01.skin）。"""
    source = Path(m2_path)
    folder = source.parent
    stem = source.stem
    skins = []
    for child in sorted(folder.iterdir()):
        if child.is_file() and child.suffix.lower() == ".skin":
            name = child.stem
            # 匹配 xxx00、xxx01 等，或者 xxx.skin（不带数字后缀）
            if name == stem or (name.startswith(stem) and name[len(stem):].isdigit()):
                skins.append(child)
    return skins


def mirror_all_skins(m2_input, m2_output, skin_output_folder=None):
    """对 M2 输入对应的所有 SKIN 文件做绕序反转。

    输出到与 m2_output 同目录（如果未指定 skin_output_folder），
    保持原文件名。
    """
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
