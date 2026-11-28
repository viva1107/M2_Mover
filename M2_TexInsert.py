#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纹理插入（按 type 改写）：把匹配指定 type 的纹理记录改写为 HARDCODED(0) 并填入路径。

纹理记录（0x10 字节，M2.bt 模板 sTexture）：
  +0x00 u32 type（E_TEXTURETYPES：0=HARDCODED … 4=WEAPON_HANDLE …）
  +0x04 u32 flags
  +0x08 u32 FileName_length（含结尾 NUL）
  +0x0C u32 FileName_offset（文件绝对偏移）

输入入口每行一条：type,路径.blp
  例：4,Item\\ObjectComponents\\Weapon\\my.blp
  type 可用数字或枚举名（WEAPON_HANDLE、UNK_SL 等，不区分大小写）。
  同一 type 多行时，后写的行生效；一行 type 匹配表中所有同 type 记录。

表位置自动探测（经典 0x54/0x58 或前移 4 字节的转换文件布局 0x50/0x54）。
路径字符串追加到文件末尾（去重、4 字节对齐），偏移天然向前。
"""

import os
import struct
from pathlib import Path

from M2_Mover import M2Error, parse_m2

TEXTURE_RECORD_SIZE = 0x10
MAX_TEXTURES = 4096
MAX_NAME_LENGTH = 1024

# (count位置, offset位置)
_TEXTURE_LAYOUTS = (
    (0x54, 0x58),   # 经典 WotLK v264
    (0x50, 0x54),   # Cata+/Legion 头部布局（0x44 单字段，整体前移 4 字节）
)

TEXTURE_TYPES = {
    "HARDCODED": 0, "SKIN": 1, "OBJECT_SKIN": 2, "WEAPON_BLADE": 3,
    "WEAPON_HANDLE": 4, "ENVIRONMENT": 5, "CHAR_HAIR": 6,
    "CHAR_FACIAL_HAIR": 7, "SKIN_EXTRA": 8, "UI_SKIN": 9, "TAUREN_MANE": 10,
    "MONSTER_SKIN_1": 11, "MONSTER_SKIN_2": 12, "MONSTER_SKIN_3": 13,
    "ITEM_ICON": 14, "GUILD_BG_COL": 15, "GUILD_EMBLEM_COL": 16,
    "GUILD_BORDER_COL": 17, "GUILD_EMBLEM": 18, "EYE_COL": 19,
    "JEWEL": 20, "SECOND_SKIN": 21, "SECOND_HAIR": 22,
    "SECOND_ARMOR": 23, "UNK_SL": 24, "UNK_DF": 25, "UNK1_DF": 26,
}


def parse_texture_entries(text):
    """解析纹理入口文本，返回 [(type值, 路径), ...]（后写的同 type 生效）。"""
    entries = []
    for lineno, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if "," not in line:
            raise M2Error("第 %d 行格式错误：应为 type,路径.blp" % lineno)
        type_text, path = line.split(",", 1)
        type_text = type_text.strip().upper()
        path = path.strip()
        if type_text.isdigit():
            type_value = int(type_text)
        elif type_text in TEXTURE_TYPES:
            type_value = TEXTURE_TYPES[type_text]
        else:
            raise M2Error(
                "第 %d 行无法识别的 type：%s（可用数字 0～26 或枚举名如 WEAPON_HANDLE）"
                % (lineno, type_text))
        if type_value < 0 or type_value > 0xFFFF:
            raise M2Error("第 %d 行 type 超出范围：%d" % (lineno, type_value))
        if not path:
            raise M2Error("第 %d 行路径为空。" % lineno)
        try:
            path.encode("ascii")
        except UnicodeEncodeError:
            raise M2Error("第 %d 行路径含非 ASCII 字符。" % lineno)
        entries.append((type_value, path))
    if not entries:
        raise M2Error("未填写任何纹理入口（格式：type,路径.blp，每行一条）。")
    return entries


def _texture_record_ok(data, rec_abs):
    tex_type, flags, name_len, name_ofs = struct.unpack_from(
        "<IIII", data, rec_abs)
    if tex_type > 4096 or flags > 0xFFFF:
        return False
    if name_len:
        if name_len > MAX_NAME_LENGTH:
            return False
        if name_ofs + name_len > len(data):
            return False
    return True


def locate_textures_table(data):
    """自动探测纹理表位置，返回 (count_pos, offset_pos, table)。"""
    for count_pos, offset_pos in _TEXTURE_LAYOUTS:
        try:
            count, offset = struct.unpack_from("<II", data, count_pos)
        except struct.error:
            continue
        if count > MAX_TEXTURES:
            continue
        if offset + count * TEXTURE_RECORD_SIZE > len(data):
            continue
        if count and not all(
                _texture_record_ok(data, offset + i * TEXTURE_RECORD_SIZE)
                for i in range(min(count, 3))):
            continue
        return count_pos, offset_pos, {"count": count, "offset": offset}
    raise M2Error("找不到结构合法的纹理表，文件可能已损坏。")


def insert_textures(target_path, output_path, entries):
    """把匹配 entries 中 type 的纹理记录改写为 HARDCODED(0)+路径，另存为新文件。"""
    source = os.path.normcase(os.path.abspath(str(target_path)))
    target = os.path.normcase(os.path.abspath(str(output_path)))
    if source == target:
        raise M2Error("为保护原模型，输出文件不能与目标 M2 相同。")

    md20 = Path(target_path).read_bytes()
    parse_m2(md20)
    count_pos, offset_pos, table = locate_textures_table(md20)

    # 同 type 后写的行生效
    wanted = {}
    for type_value, path in entries:
        wanted[type_value] = path

    matched = []
    for i in range(table["count"]):
        rec = table["offset"] + i * TEXTURE_RECORD_SIZE
        tex_type = struct.unpack_from("<I", md20, rec)[0]
        if tex_type in wanted:
            matched.append((i, rec, wanted[tex_type]))

    data = bytearray(md20)
    appended = bytearray()
    appended_base = len(data)
    string_cache = {}

    def alloc_string(path):
        """追加路径字符串（NUL 结尾，length 含 NUL），去重，4 字节对齐。"""
        if path in string_cache:
            return string_cache[path]
        payload = path.encode("ascii") + b"\x00"
        aligned = (appended_base + len(appended) + 3) & ~3
        appended.extend(b"\x00" * (aligned - (appended_base + len(appended))))
        appended.extend(payload)
        result = (len(payload), aligned)
        string_cache[path] = result
        return result

    for i, rec, path in matched:
        name_len, name_ofs = alloc_string(path)
        struct.pack_into("<IIII", data, rec,
                         0,                      # type → HARDCODED
                         struct.unpack_from("<I", data, rec + 4)[0],
                         name_len, name_ofs)

    data.extend(appended)

    # 写出前校验
    try:
        _, _, out_table = locate_textures_table(bytes(data))
        for i, rec, path in matched:
            t, fl, nl, no = struct.unpack_from(
                "<IIII", data,
                out_table["offset"] + i * TEXTURE_RECORD_SIZE)
            if t != 0 or data[no:no + nl - 1].decode("ascii") != path:
                raise M2Error("记录 %d 校验不符" % i)
    except (M2Error, struct.error, UnicodeDecodeError) as exc:
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
        "total": table["count"],
        "matched": len(matched),
        "indices": [i for i, _, _ in matched],
        "output_size": len(data),
    }
