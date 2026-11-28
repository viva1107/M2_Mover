#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头盔移动、通用 XYZ、整体／粒子缩放、三模式旋转与三轴镜像工具。"""

import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from M2_Mover import M2Error, move_m2, parse_m2
from M2_Resizer import resize_m2, scale_particle_sizes_m2
from M2_Rotator import rotate_m2
from M2_Mirror import mirror_m2
from M2_BoneFix import copy_bone_tracks
from M2_TexAnim import (
    copy_tex_anims, parse_indices, _open_texanim_source, locate_texanim_table)
from M2_EventAdd import add_events, locate_events_table
from M2_TexInsert import insert_textures, parse_texture_entries


M2_TYPES = [("MD20 v264 模型", "*.m2"), ("所有文件", "*.*")]
TABLE_TYPES = [("种族偏移表", "*.csv *.txt"), ("所有文件", "*.*")]
ROTATION_MODES = {
    "o 模式：X 轴，旋转 Y／Z（180° 即 Flip）": "o",
    "- 模式：Z 轴，旋转 X／Y（Flip 基础）": "dash",
    "I 模式：Y 轴，旋转 X／Z": "i",
    "X 镜像：前后翻转": "mirror_x",
    "Y 镜像：左右翻转（左手转右手）": "mirror_y",
    "Z 镜像：上下翻转": "mirror_z",
}
if getattr(sys, "frozen", False):
    CONFIG_PATH = Path(sys.executable).resolve().parent / "头盔种族偏移.json"
else:
    CONFIG_PATH = Path(__file__).resolve().parent / "头盔种族偏移.json"

# 用户提供的 22 组数据：后缀、界面名称、X、Z。
BUILTIN_DATA = [
    ("bef", "BeF（血精灵女性）", -0.000, -0.205),
    ("bem", "BeM（血精灵男性）", -0.080, -0.170),
    ("drf", "DrF（德莱尼女性）", -0.070, -0.200),
    ("drm", "DrM（德莱尼男性）", -0.075, -0.255),
    ("dwf", "DwF（矮人女性）", -0.000, -0.215),
    ("dwm", "DwM（矮人男性）", -0.000, -0.180),
    ("gnf", "GnF（侏儒女性）", -0.030, -0.280),
    ("gnm", "GnM（侏儒男性）", -0.025, -0.260),
    ("gof", "GoF（地精女性）", -0.045, -0.180),
    ("gom", "GoM（地精男性）", -0.045, -0.175),
    ("huf", "HuF（人类女性）", -0.080, -0.190),
    ("hum", "HuM（人类男性）", -0.070, -0.215),
    ("nif", "NiF（暗夜精灵女性）", -0.060, -0.210),
    ("nim", "NiM（暗夜精灵男性）", -0.095, -0.180),
    ("orf", "OrF（兽人女性）", -0.070, -0.175),
    ("orm", "OrM（兽人男性）", -0.150, -0.190),
    ("scf", "ScF（亡灵女性）", -0.010, -0.160),
    ("scm", "ScM（亡灵男性）", -0.125, -0.135),
    ("taf", "TaF（牛头人女性）", -0.200, -0.100),
    ("tam", "TaM（牛头人男性）", -0.225, -0.100),
    ("trf", "TrF（巨魔女性）", -0.090, -0.085),
    ("trm", "TrM（巨魔男性）", -0.120, -0.165),
]
DEFAULT_PROFILES = {name: {"x": x, "z": z} for _, name, x, z in BUILTIN_DATA}
PROFILE_BY_SUFFIX = {suffix: name for suffix, name, _, _ in BUILTIN_DATA}
SUFFIX_PATTERN = re.compile(
    r"(?:^|[_-])(" + "|".join(PROFILE_BY_SUFFIX) + r")$", re.IGNORECASE)


def profile_name_for_path(path):
    match = SUFFIX_PATTERN.search(Path(path).stem)
    return PROFILE_BY_SUFFIX[match.group(1).lower()] if match else None


class HelmetMoverGUI:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        self.last_path = None
        self.config_warning = ""
        self.custom_profiles = self.load_custom_profiles()
        self.profiles = {name: dict(values) for name, values in DEFAULT_PROFILES.items()}
        self.profiles.update(self.custom_profiles)

        self.race_var = tk.StringVar()
        self.x_var = tk.StringVar(value="0")
        self.z_var = tk.StringVar(value="0")
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.batch_input_var = tk.StringVar()
        self.batch_output_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=True)
        self.auto_suffix_var = tk.BooleanVar(value=True)
        self.generic_input_var = tk.StringVar()
        self.generic_output_var = tk.StringVar()
        self.generic_x_var = tk.StringVar(value="0.015")
        self.generic_y_var = tk.StringVar(value="0.000")
        self.generic_z_var = tk.StringVar(value="-0.175")
        self.scale_input_var = tk.StringVar()
        self.scale_output_var = tk.StringVar()
        self.scale_factor_var = tk.StringVar(value="1.30")
        self.particle_input_var = tk.StringVar()
        self.particle_output_var = tk.StringVar()
        self.particle_factor_var = tk.StringVar(value="1.50")
        self.rotation_input_var = tk.StringVar()
        self.rotation_output_var = tk.StringVar()
        self.rotation_mode_var = tk.StringVar(value=list(ROTATION_MODES)[1])
        self.rotation_angle_var = tk.StringVar(value="180")
        self.bonefix_input_var = tk.StringVar()
        self.bonefix_ref_var = tk.StringVar()
        self.bonefix_output_var = tk.StringVar()
        self.texanim_source_var = tk.StringVar()
        self.texanim_target_var = tk.StringVar()
        self.texanim_output_var = tk.StringVar()
        self.texanim_indices_var = tk.StringVar()
        self.texanim_slots_var = tk.StringVar()
        self.texanim_replace_var = tk.BooleanVar(value=False)
        self.eventadd_target_var = tk.StringVar()
        self.eventadd_output_var = tk.StringVar()
        self.eventadd_count_var = tk.StringVar(value="1")
        self.texins_mode_var = tk.StringVar(value="single")
        self.texins_path_var = tk.StringVar()

        root.title("M2 头盔移动、缩放与旋转工具（MD20 v264）")
        root.geometry("1000x900")
        root.minsize(860, 740)

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text=("头盔模式按文件名末尾的 BeF、HuM 等代码自动匹配 X、Z，Y 固定为 0；"
                  "也可手动指定偏移。支持通用 XYZ、整体／粒子缩放、三模式模型旋转和三轴镜像（Y 镜像＝左手转右手）。"),
            wraplength=940,
            foreground="#444",
        ).pack(anchor="w", pady=(0, 10))

        preset = ttk.LabelFrame(outer, text="种族偏移预设（已内置 22 组，可导入或修改）", padding=10)
        preset.pack(fill="x")
        ttk.Label(preset, text="种族：").grid(row=0, column=0, sticky="w")
        self.race_combo = ttk.Combobox(preset, textvariable=self.race_var, width=27)
        self.race_combo.grid(row=0, column=1, sticky="w", padx=(4, 12))
        self.race_combo.bind("<<ComboboxSelected>>", self.on_race_selected)
        ttk.Label(preset, text="X：").grid(row=0, column=2, sticky="w")
        ttk.Entry(preset, textvariable=self.x_var, width=14).grid(
            row=0, column=3, sticky="w", padx=(4, 12))
        ttk.Label(preset, text="Z：").grid(row=0, column=4, sticky="w")
        ttk.Entry(preset, textvariable=self.z_var, width=14).grid(
            row=0, column=5, sticky="w", padx=(4, 12))
        ttk.Label(preset, text="Y：0（头盔模式固定）", foreground="#666").grid(
            row=0, column=6, sticky="w")
        ttk.Button(preset, text="保存／更新预设", command=self.save_preset).grid(
            row=1, column=1, sticky="w", pady=(10, 0))
        ttk.Button(preset, text="删除自定义／恢复内置", command=self.delete_preset).grid(
            row=1, column=2, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(preset, text="导入 TXT／CSV", command=self.import_profiles).grid(
            row=1, column=4, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(
            preset,
            text="自定义内容保存在 EXE 同目录的“头盔种族偏移.json”；导入表每行：后缀,X,Z。",
            foreground="#666",
        ).grid(row=2, column=0, columnspan=7, sticky="w", pady=(8, 0))
        self.refresh_races(select=DEFAULT_PROFILES and PROFILE_BY_SUFFIX["bef"])
        self.on_race_selected()

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="x", pady=(10, 0))
        single = ttk.Frame(notebook, padding=12)
        batch = ttk.Frame(notebook, padding=12)
        generic = ttk.Frame(notebook, padding=12)
        scale_page = ttk.Frame(notebook, padding=12)
        particle_page = ttk.Frame(notebook, padding=12)
        rotation_page = ttk.Frame(notebook, padding=12)
        bonefix_page = ttk.Frame(notebook, padding=12)
        texanim_page = ttk.Frame(notebook, padding=12)
        eventadd_page = ttk.Frame(notebook, padding=12)
        texins_page = ttk.Frame(notebook, padding=12)
        notebook.add(single, text="单个头盔 X／Z")
        notebook.add(batch, text="批量头盔自动后缀")
        notebook.add(generic, text="通用 XYZ 移动")
        notebook.add(scale_page, text="模型整体缩放")
        notebook.add(particle_page, text="粒子大小缩放")
        notebook.add(rotation_page, text="模型旋转")
        notebook.add(bonefix_page, text="骨骼轨道修复")
        notebook.add(texanim_page, text="TexAnim 复制")
        notebook.add(eventadd_page, text="Events 添加")
        notebook.add(texins_page, text="纹理插入")
        for page in (single, batch, generic, scale_page, particle_page, rotation_page, bonefix_page, texanim_page, eventadd_page, texins_page):
            page.columnconfigure(1, weight=1)

        self.add_file_row(single, 0, "输入头盔 M2：", self.input_var, self.pick_input)
        self.add_file_row(single, 1, "输出头盔 M2：", self.output_var, self.pick_output)
        ttk.Label(
            single,
            text="选择文件时自动识别末尾后缀并填入预设；未识别时使用上方当前 X、Z。",
            foreground="#666",
        ).grid(row=2, column=1, sticky="w", padx=8, pady=(4, 10))
        self.single_button = ttk.Button(
            single, text="按当前 X／Z 移动并另存", command=self.run_single)
        self.single_button.grid(row=3, column=1, sticky="w", padx=8)

        self.add_file_row(batch, 0, "头盔文件夹：", self.batch_input_var, self.pick_batch_input)
        self.add_file_row(batch, 1, "输出文件夹：", self.batch_output_var, self.pick_batch_output)
        ttk.Radiobutton(
            batch, text="按每个文件名后缀自动匹配（未识别文件跳过）",
            variable=self.auto_suffix_var, value=True,
        ).grid(row=2, column=1, sticky="w", padx=8, pady=(4, 0))
        ttk.Radiobutton(
            batch, text="全部使用上方当前的同一组 X／Z（保留原手动批量功能）",
            variable=self.auto_suffix_var, value=False,
        ).grid(row=3, column=1, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(
            batch, text="包含所有子文件夹（保持原目录结构输出）", variable=self.recursive_var
        ).grid(row=4, column=1, sticky="w", padx=8, pady=4)
        ttk.Label(
            batch,
            text="默认输出到头盔文件夹内的“头盔已移动”子文件夹，不覆盖原头盔。",
            foreground="#666",
        ).grid(row=5, column=1, sticky="w", padx=8, pady=(0, 10))
        self.batch_button = ttk.Button(
            batch, text="批量移动头盔到新文件夹", command=self.run_batch)
        self.batch_button.grid(row=6, column=1, sticky="w", padx=8)

        self.add_file_row(
            generic, 0, "输入 M2：", self.generic_input_var, self.pick_generic_input)
        self.add_file_row(
            generic, 1, "输出 M2：", self.generic_output_var, self.pick_generic_output)
        ttk.Label(generic, text="X：").grid(row=2, column=0, sticky="w", pady=5)
        generic_values = ttk.Frame(generic)
        generic_values.grid(row=2, column=1, sticky="w", padx=8, pady=5)
        for label, variable in (
                ("X", self.generic_x_var), ("Y", self.generic_y_var), ("Z", self.generic_z_var)):
            ttk.Label(generic_values, text=label + "：").pack(side="left")
            ttk.Entry(generic_values, textvariable=variable, width=14).pack(
                side="left", padx=(4, 16))
        ttk.Label(
            generic,
            text="与原 M2 Mover 相同：可自由设置 X、Y、Z，另存新模型，不修改输入文件。",
            foreground="#666",
        ).grid(row=3, column=1, sticky="w", padx=8, pady=(4, 10))
        self.generic_button = ttk.Button(
            generic, text="按 XYZ 移动并另存", command=self.run_generic)
        self.generic_button.grid(row=4, column=1, sticky="w", padx=8)

        self.add_file_row(
            scale_page, 0, "输入 M2：", self.scale_input_var, self.pick_scale_input)
        self.add_file_row(
            scale_page, 1, "输出 M2：", self.scale_output_var, self.pick_scale_output)
        ttk.Label(scale_page, text="缩放倍率：").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(scale_page, textvariable=self.scale_factor_var, width=14).grid(
            row=2, column=1, sticky="w", padx=8, pady=5)
        ttk.Label(
            scale_page,
            text="1.30＝放大 30％；2.00＝两倍；0.80＝缩小到 80％。另存新模型，不修改输入文件。",
            foreground="#666",
        ).grid(row=3, column=1, sticky="w", padx=8, pady=(4, 0))
        ttk.Label(
            scale_page,
            text="按原脚本有效代码缩放；其中已注释的骨骼动画轨道不处理。",
            foreground="#666",
        ).grid(row=4, column=1, sticky="w", padx=8, pady=(4, 10))
        self.scale_button = ttk.Button(
            scale_page, text="按倍率缩放并另存", command=self.run_scale)
        self.scale_button.grid(row=5, column=1, sticky="w", padx=8)

        self.add_file_row(
            particle_page, 0, "输入 M2：", self.particle_input_var, self.pick_particle_input)
        self.add_file_row(
            particle_page, 1, "输出 M2：", self.particle_output_var, self.pick_particle_output)
        ttk.Label(particle_page, text="粒子倍率：").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(particle_page, textvariable=self.particle_factor_var, width=14).grid(
            row=2, column=1, sticky="w", padx=8, pady=5)
        ttk.Label(
            particle_page,
            text="默认 1.50＝粒子放大 50％。只缩放 ScaleTrack 的 X、Y 关键帧。",
            foreground="#666",
        ).grid(row=3, column=1, sticky="w", padx=8, pady=(4, 0))
        ttk.Label(
            particle_page,
            text="不移动粒子位置，不修改 ScaleVary、颜色、透明度或其他参数；另存新模型。",
            foreground="#666",
        ).grid(row=4, column=1, sticky="w", padx=8, pady=(4, 10))
        self.particle_button = ttk.Button(
            particle_page, text="按倍率缩放粒子并另存", command=self.run_particle_scale)
        self.particle_button.grid(row=5, column=1, sticky="w", padx=8)

        self.add_file_row(
            rotation_page, 0, "输入 M2：", self.rotation_input_var, self.pick_rotation_input)
        self.add_file_row(
            rotation_page, 1, "输出 M2：", self.rotation_output_var, self.pick_rotation_output)
        ttk.Label(rotation_page, text="旋转模式：").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Combobox(
            rotation_page, textvariable=self.rotation_mode_var, state="readonly",
            values=list(ROTATION_MODES), width=38,
        ).grid(row=2, column=1, sticky="w", padx=8, pady=5)
        ttk.Label(rotation_page, text="旋转角度：").grid(row=3, column=0, sticky="w", pady=5)
        self.rotation_angle_entry = ttk.Entry(
            rotation_page, textvariable=self.rotation_angle_var, width=14)
        self.rotation_angle_entry.grid(row=3, column=1, sticky="w", padx=8, pady=5)
        ttk.Label(
            rotation_page,
            text="默认 180°；沿用三个 Rotation 脚本的公式，支持正角、负角和其他角度。",
            foreground="#666", wraplength=740, justify="left",
        ).grid(row=4, column=1, sticky="w", padx=8, pady=(4, 0))
        ttk.Label(
            rotation_page,
            text=("旋转以 Flip 范围处理顶点、法线、碰撞、骨骼、粒子、事件和附件，并更新包围盒；"
                  "内部骨骼平移／旋转轨道同步处理；外部 .anim 不修改。"
                  "镜像则对位置、法线、骨骼 Pivot 和动画轨道做轴对称取反，"
                  "四元数按镜像规则变换，包围盒同步更新；"
                  "Y 镜像＝左手装备转右手；"
                  "同时自动查找并反转同目录下对应 SKIN 文件的三角形绕序。"),
            foreground="#666", wraplength=740, justify="left",
        ).grid(row=5, column=1, sticky="w", padx=8, pady=(4, 10))
        self.rotation_button = ttk.Button(
            rotation_page, text="按模式和角度旋转并另存", command=self.run_rotation)
        self.rotation_button.grid(row=6, column=1, sticky="w", padx=8)

        self.add_file_row(
            bonefix_page, 0, "输入 264 M2：", self.bonefix_input_var, self.pick_bonefix_input)
        self.add_file_row(
            bonefix_page, 1, "参考 274 M2：", self.bonefix_ref_var, self.pick_bonefix_ref)
        self.add_file_row(
            bonefix_page, 2, "输出 264 M2：", self.bonefix_output_var, self.pick_bonefix_output)
        ttk.Label(
            bonefix_page,
            text=("把 274 模型每根骨骼的 Translation／Rotation／Scaling 三条轨道（时间戳＋键值）"
                  "完整复制到 264 模型，用于修复 274→264 转换时损坏的骨骼轨道；"
                  "按骨骼索引一一对应，两边骨骼数量必须一致。"),
            foreground="#666", wraplength=740, justify="left",
        ).grid(row=3, column=1, sticky="w", padx=8, pady=(4, 0))
        ttk.Label(
            bonefix_page,
            text="另存新模型，不修改输入文件；264 原有轨道数据保留在文件内但不再被引用。",
            foreground="#666", wraplength=740, justify="left",
        ).grid(row=4, column=1, sticky="w", padx=8, pady=(4, 10))
        self.bonefix_button = ttk.Button(
            bonefix_page, text="从 274 复制骨骼轨道并另存", command=self.run_bonefix)
        self.bonefix_button.grid(row=5, column=1, sticky="w", padx=8)

        self.add_file_row(
            texanim_page, 0, "源 M2（264 或 274）：", self.texanim_source_var, self.pick_texanim_source)
        self.add_file_row(
            texanim_page, 1, "目标 M2（264）：", self.texanim_target_var, self.pick_texanim_target)
        self.add_file_row(
            texanim_page, 2, "输出 M2：", self.texanim_output_var, self.pick_texanim_output)
        ttk.Label(
            texanim_page, text="复制哪些（源文件中的索引，从 0 开始）：",
        ).grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(texanim_page, textvariable=self.texanim_indices_var, width=30).grid(
            row=3, column=1, sticky="w", padx=8)
        ttk.Label(
            texanim_page,
            text="留空＝全部；支持逗号分隔（0,2,5）和区间（1-3）。",
            foreground="#666", wraplength=740, justify="left",
        ).grid(row=4, column=1, sticky="w", padx=8)
        ttk.Label(
            texanim_page, text="目标槽位（仅替换模式，与源索引一一对应）：",
        ).grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(texanim_page, textvariable=self.texanim_slots_var, width=30).grid(
            row=5, column=1, sticky="w", padx=8)
        ttk.Label(
            texanim_page,
            text="例：源索引填 1,3、槽位填 0,2 → 目标[0]←源[1]，目标[2]←源[3]，其余槽位不变。",
            foreground="#666", wraplength=740, justify="left",
        ).grid(row=6, column=1, sticky="w", padx=8)
        ttk.Radiobutton(
            texanim_page, text="追加复制（保留目标已有 TexAnim，追加到末尾）",
            variable=self.texanim_replace_var, value=False,
        ).grid(row=7, column=1, sticky="w", padx=8)
        ttk.Radiobutton(
            texanim_page, text="替换（按槽位覆盖目标指定位置，总数与材质索引不变）",
            variable=self.texanim_replace_var, value=True,
        ).grid(row=8, column=1, sticky="w", padx=8)
        ttk.Label(
            texanim_page,
            text="另存新模型，不修改目标文件。",
            foreground="#666", wraplength=740, justify="left",
        ).grid(row=9, column=1, sticky="w", padx=8, pady=(4, 10))
        self.texanim_button = ttk.Button(
            texanim_page, text="复制 TexAnim 并另存", command=self.run_texanim)
        self.texanim_button.grid(row=10, column=1, sticky="w", padx=8)

        self.add_file_row(
            eventadd_page, 0, "目标 M2（264）：", self.eventadd_target_var, self.pick_eventadd_target)
        self.add_file_row(
            eventadd_page, 1, "输出 M2：", self.eventadd_output_var, self.pick_eventadd_output)
        ttk.Label(
            eventadd_page, text="添加数量（空白事件）：",
        ).grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(eventadd_page, textvariable=self.eventadd_count_var, width=10).grid(
            row=2, column=1, sticky="w", padx=8)
        ttk.Label(
            eventadd_page,
            text=("向目标追加 N 条全零空白事件（Identifier／Data／挂点／位置／Timer 均为 0），"
                  "供你在 010 中手工填写；原有事件保持不变。"
                  "例：目标原有 24 条，填 1 → 变 25 条，填 2 → 变 26 条。"),
            foreground="#666", wraplength=740, justify="left",
        ).grid(row=3, column=1, sticky="w", padx=8, pady=(4, 0))
        ttk.Label(
            eventadd_page,
            text="另存新模型，不修改目标文件。",
            foreground="#666", wraplength=740, justify="left",
        ).grid(row=4, column=1, sticky="w", padx=8, pady=(4, 10))
        self.eventadd_button = ttk.Button(
            eventadd_page, text="添加空白事件并另存", command=self.run_eventadd)
        self.eventadd_button.grid(row=5, column=1, sticky="w", padx=8)

        ttk.Radiobutton(
            texins_page, text="单个 M2", variable=self.texins_mode_var,
            value="single", command=self.update_texins_path_label,
        ).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Radiobutton(
            texins_page, text="批量（文件夹内全部 M2）", variable=self.texins_mode_var,
            value="batch", command=self.update_texins_path_label,
        ).grid(row=0, column=1, sticky="w", padx=8)
        self.texins_path_label = ttk.Label(texins_page, text="输入 M2：")
        self.texins_path_label.grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(texins_page, textvariable=self.texins_path_var, width=72).grid(
            row=1, column=1, sticky="w", padx=8)
        ttk.Button(texins_page, text="浏览…", command=self.pick_texins_path).grid(
            row=1, column=2, sticky="w")
        ttk.Label(
            texins_page, text="纹理入口（每行一条：type,路径.blp）：",
        ).grid(row=2, column=0, sticky="nw", pady=4)
        self.texins_text = tk.Text(texins_page, width=95, height=8, wrap="none")
        self.texins_text.grid(row=2, column=1, sticky="w", padx=8)
        ttk.Label(
            texins_page,
            text=("type 用数字（0～26）或枚举名（WEAPON_HANDLE、UNK_SL 等）；"
                  "匹配该 type 的纹理记录会被填入路径，type 统一改为 HARDCODED(0)。"
                  "同一 type 多行时后写的生效。批量模式输出到输入旁的命名子文件夹。"
                  "示例：4,item\\xx\\xx.blp 或 WEAPON_HANDLE,item\\xx\\xx.blp"),
            foreground="#666", wraplength=740, justify="left",
        ).grid(row=3, column=1, sticky="w", padx=8, pady=(4, 0))
        self.texins_button = ttk.Button(
            texins_page, text="插入纹理并另存", command=self.run_texins)
        self.texins_button.grid(row=4, column=1, sticky="w", padx=8, pady=(8, 0))

        self.open_button = ttk.Button(
            outer, text="打开输出位置", command=self.open_output, state="disabled")
        self.open_button.pack(anchor="w", pady=(12, 0))

        log_box = ttk.LabelFrame(outer, text="处理日志", padding=6)
        log_box.pack(fill="both", expand=True, pady=(8, 0))
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log = tk.Text(log_box, wrap="word", font=("Microsoft YaHei UI", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        instructions = (
            "自动后缀示例：Helm_Leather_RaidDruid_Q_01_BeF.m2 → BeF 血精灵女性。\n"
            "后缀不区分大小写；必须是文件名末尾的完整代码，例如 _BeF、_HuM、_TrM。\n"
            "批量自动模式下，每个文件读取自己的种族数据，未识别后缀会跳过。\n"
            "手动头盔模式仍使用上方当前 X／Z；通用 XYZ 模式可另外设置 Y。\n"
            "模型整体缩放默认倍率为 1.30；粒子大小缩放默认倍率为 1.50。\n"
            "粒子功能只处理 ScaleTrack 二维关键帧，不移动粒子位置。\n"
            "模型旋转提供 o、-、I 三种模式，默认 180°；Y 镜像＝左右翻转（左手转右手）。\n"
            "镜像不需要角度，直接选镜像模式执行即可；SKIN 文件会自动同步处理。\n"
            "骨骼轨道修复：从 274 模型复制每根骨骼的 Translation／Rotation／Scaling 轨道到 264 模型。\n"
            "TexAnim 复制：从 264 或 274 模型复制纹理动画到 264 模型，支持全部／指定索引，可追加或替换。\n"
            "Events 添加：向 264 模型追加 N 条空白事件记录（原有事件不变，记录表重建为前移引用）。\n"
            "纹理插入：按 type 匹配改写纹理记录为 HARDCODED(0)+路径，支持单个或批量。\n\n"
            "导入表支持：BeF,-0.000,-0.205，也支持中文逗号、分号或制表符分隔。"
        )
        if self.config_warning:
            instructions = self.config_warning + "\n\n" + instructions
        self.set_log(instructions)
        self.root.after(100, self.poll_events)

    def load_custom_profiles(self):
        if not CONFIG_PATH.exists():
            return {}
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            source = raw.get("profiles", {})
            if not isinstance(source, dict):
                raise ValueError("profiles 不是对象")
            profiles = {}
            ignored = 0
            for name, values in source.items():
                try:
                    x = float(values["x"])
                    z = float(values["z"])
                    if not str(name).strip() or not math.isfinite(x) or not math.isfinite(z):
                        raise ValueError
                    profiles[str(name)] = {"x": x, "z": z}
                except (KeyError, TypeError, ValueError):
                    ignored += 1
            if ignored:
                self.config_warning = "配置中有 %d 条无效自定义预设，已忽略。" % ignored
            return profiles
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.config_warning = "无法读取自定义预设配置：%s" % exc
            return {}

    def save_custom_profiles(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        try:
            temporary.write_text(
                json.dumps({"profiles": self.custom_profiles},
                           ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(str(temporary), str(CONFIG_PATH))
        finally:
            if temporary.exists():
                temporary.unlink()

    def refresh_races(self, select=None):
        names = sorted(self.profiles, key=str.casefold)
        self.race_combo.configure(values=names)
        chosen = select or self.race_var.get().strip()
        if chosen in self.profiles:
            self.race_var.set(chosen)

    def on_race_selected(self, _event=None):
        name = self.race_var.get().strip()
        if name in self.profiles:
            values = self.profiles[name]
            self.x_var.set("%g" % values["x"])
            self.z_var.set("%g" % values["z"])

    def parse_offsets(self):
        try:
            x = float(self.x_var.get().strip())
            z = float(self.z_var.get().strip())
        except ValueError:
            raise M2Error("X 和 Z 必须是有效数字，例如 0.015 或 -0.175。")
        if not math.isfinite(x) or not math.isfinite(z):
            raise M2Error("X 和 Z 不能是无穷大或 NaN。")
        return x, z

    def save_preset(self):
        try:
            entered = self.race_var.get().strip()
            if not entered:
                raise M2Error("请输入种族名称或后缀，例如 BeF。")
            name = PROFILE_BY_SUFFIX.get(entered.lower(), entered)
            x, z = self.parse_offsets()
            values = {"x": x, "z": z}
            self.custom_profiles[name] = values
            self.profiles[name] = dict(values)
            self.save_custom_profiles()
            self.refresh_races(select=name)
            self.race_var.set(name)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法保存预设", str(exc), parent=self.root)
            return
        self.set_log("已保存自定义预设：%s\nX＝%g，Z＝%g\n\n配置文件：%s" % (
            name, x, z, CONFIG_PATH))

    def delete_preset(self):
        entered = self.race_var.get().strip()
        name = PROFILE_BY_SUFFIX.get(entered.lower(), entered)
        if name not in self.custom_profiles:
            if name in DEFAULT_PROFILES:
                messagebox.showinfo("内置预设", "该种族仍使用内置默认值，没有自定义修改需要删除。", parent=self.root)
            else:
                messagebox.showerror("无法删除", "当前名称不是已保存的自定义预设。", parent=self.root)
            return
        if not messagebox.askyesno(
                "删除自定义预设", "确定删除“%s”的自定义数值？" % name, parent=self.root):
            return
        try:
            del self.custom_profiles[name]
            self.save_custom_profiles()
        except OSError as exc:
            messagebox.showerror("无法保存配置", str(exc), parent=self.root)
            return
        if name in DEFAULT_PROFILES:
            self.profiles[name] = dict(DEFAULT_PROFILES[name])
            result = "已删除自定义修改，恢复内置默认值。"
        else:
            self.profiles.pop(name, None)
            result = "已删除自定义种族预设。"
        self.refresh_races(select=name if name in self.profiles else PROFILE_BY_SUFFIX["bef"])
        self.on_race_selected()
        self.set_log(name + "：" + result)

    @staticmethod
    def parse_profile_table(text):
        profiles = {}
        first_content_row = True
        for line_number, raw_line in enumerate(text.splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in re.split(r"[,，;；\t]", line)]
            if len(parts) < 3:
                raise M2Error("第 %d 行格式错误，应为：后缀,X,Z。" % line_number)
            name = PROFILE_BY_SUFFIX.get(parts[0].lower(), parts[0])
            try:
                x = float(parts[1])
                z = float(parts[2])
                if not name or not math.isfinite(x) or not math.isfinite(z):
                    raise ValueError
            except ValueError:
                header = parts[1].lower() == "x" and parts[2].lower() == "z"
                if first_content_row and header:
                    first_content_row = False
                    continue
                raise M2Error("第 %d 行的 X、Z 不是有效数字。" % line_number)
            profiles[name] = {"x": x, "z": z}
            first_content_row = False
        if not profiles:
            raise M2Error("导入表中没有有效的种族偏移数据。")
        return profiles

    def import_profiles(self):
        path = filedialog.askopenfilename(title="导入种族 X／Z 偏移表", filetypes=TABLE_TYPES)
        if not path:
            return
        try:
            raw = Path(path).read_bytes()
            text = None
            for encoding in ("utf-8-sig", "gb18030"):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    pass
            if text is None:
                raise M2Error("无法识别导入表编码，请使用 UTF-8 或 ANSI。")
            imported = self.parse_profile_table(text)
            self.custom_profiles.update(imported)
            for name, values in imported.items():
                self.profiles[name] = dict(values)
            self.save_custom_profiles()
            first = next(iter(imported))
            self.refresh_races(select=first)
            self.on_race_selected()
        except (M2Error, OSError) as exc:
            messagebox.showerror("导入失败", str(exc), parent=self.root)
            return
        self.set_log("成功导入 %d 个自定义预设；同名数据已更新。\n\n配置已保存：%s" % (
            len(imported), CONFIG_PATH))

    def add_file_row(self, parent, row, label, variable, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=8, pady=5)
        ttk.Button(parent, text="浏览…", command=command).grid(row=row, column=2, pady=5)

    @staticmethod
    def default_output_in_named_subfolder(source):
        """输出默认放到输入所在文件夹下的同名子文件夹，例如 head/head/xxx.m2。"""
        source = Path(source)
        return str(source.parent / source.parent.name / source.name)

    def pick_input(self):
        path = filedialog.askopenfilename(title="选择头盔 M2", filetypes=M2_TYPES)
        if not path:
            return
        self.input_var.set(path)
        source = Path(path)
        self.output_var.set(self.default_output_in_named_subfolder(source))
        profile = profile_name_for_path(path)
        if profile:
            self.race_var.set(profile)
            self.on_race_selected()
            values = self.profiles[profile]
            self.set_log("已识别文件后缀：%s\nX＝%g，Z＝%g，Y＝0。\n\n可修改数值后再处理。" % (
                profile, values["x"], values["z"]))
        else:
            self.set_log("未能从文件名末尾识别种族后缀，将使用上方当前 X、Z。\n\n文件：%s" % path)

    def pick_output(self):
        path = filedialog.asksaveasfilename(
            title="保存移动后的头盔", defaultextension=".m2", filetypes=M2_TYPES)
        if path:
            self.output_var.set(path)

    def pick_batch_input(self):
        path = filedialog.askdirectory(title="选择头盔文件夹")
        if path:
            self.batch_input_var.set(path)
            folder = Path(path)
            self.batch_output_var.set(str(folder / "头盔已移动"))

    def pick_batch_output(self):
        path = filedialog.askdirectory(title="选择批量输出文件夹")
        if path:
            self.batch_output_var.set(path)

    def pick_generic_input(self):
        path = filedialog.askopenfilename(title="选择要通用移动的 M2", filetypes=M2_TYPES)
        if path:
            self.generic_input_var.set(path)
            source = Path(path)
            self.generic_output_var.set(self.default_output_in_named_subfolder(source))

    def pick_generic_output(self):
        path = filedialog.asksaveasfilename(
            title="保存移动后的 M2", defaultextension=".m2", filetypes=M2_TYPES)
        if path:
            self.generic_output_var.set(path)

    def pick_scale_input(self):
        path = filedialog.askopenfilename(title="选择要整体缩放的 M2", filetypes=M2_TYPES)
        if path:
            self.scale_input_var.set(path)
            source = Path(path)
            self.scale_output_var.set(self.default_output_in_named_subfolder(source))

    def pick_scale_output(self):
        path = filedialog.asksaveasfilename(
            title="保存缩放后的 M2", defaultextension=".m2", filetypes=M2_TYPES)
        if path:
            self.scale_output_var.set(path)

    def pick_particle_input(self):
        path = filedialog.askopenfilename(title="选择要缩放粒子的 M2", filetypes=M2_TYPES)
        if path:
            self.particle_input_var.set(path)
            source = Path(path)
            self.particle_output_var.set(self.default_output_in_named_subfolder(source))

    def pick_particle_output(self):
        path = filedialog.asksaveasfilename(
            title="保存粒子缩放后的 M2", defaultextension=".m2", filetypes=M2_TYPES)
        if path:
            self.particle_output_var.set(path)

    def pick_rotation_input(self):
        path = filedialog.askopenfilename(title="选择要旋转的 M2", filetypes=M2_TYPES)
        if path:
            self.rotation_input_var.set(path)
            source = Path(path)
            self.rotation_output_var.set(self.default_output_in_named_subfolder(source))

    def pick_rotation_output(self):
        path = filedialog.asksaveasfilename(
            title="保存旋转后的 M2", defaultextension=".m2", filetypes=M2_TYPES)
        if path:
            self.rotation_output_var.set(path)

    def pick_bonefix_input(self):
        path = filedialog.askopenfilename(title="选择待修复的 264 M2", filetypes=M2_TYPES)
        if path:
            self.bonefix_input_var.set(path)
            source = Path(path)
            self.bonefix_output_var.set(self.default_output_in_named_subfolder(source))

    def pick_bonefix_ref(self):
        path = filedialog.askopenfilename(title="选择参考 274 M2", filetypes=M2_TYPES)
        if path:
            self.bonefix_ref_var.set(path)

    def pick_bonefix_output(self):
        path = filedialog.asksaveasfilename(
            title="保存修复后的 264 M2", defaultextension=".m2", filetypes=M2_TYPES)
        if path:
            self.bonefix_output_var.set(path)

    def pick_texanim_source(self):
        path = filedialog.askopenfilename(title="选择源 M2（264 或 274）", filetypes=M2_TYPES)
        if path:
            self.texanim_source_var.set(path)

    def pick_texanim_target(self):
        path = filedialog.askopenfilename(title="选择目标 264 M2", filetypes=M2_TYPES)
        if path:
            self.texanim_target_var.set(path)
            source = Path(path)
            self.texanim_output_var.set(self.default_output_in_named_subfolder(source))

    def pick_texanim_output(self):
        path = filedialog.asksaveasfilename(
            title="保存复制后的 M2", defaultextension=".m2", filetypes=M2_TYPES)
        if path:
            self.texanim_output_var.set(path)

    def pick_eventadd_target(self):
        path = filedialog.askopenfilename(title="选择目标 264 M2", filetypes=M2_TYPES)
        if path:
            self.eventadd_target_var.set(path)
            source = Path(path)
            self.eventadd_output_var.set(self.default_output_in_named_subfolder(source))

    def pick_eventadd_output(self):
        path = filedialog.asksaveasfilename(
            title="保存添加事件后的 M2", defaultextension=".m2", filetypes=M2_TYPES)
        if path:
            self.eventadd_output_var.set(path)

    def update_texins_path_label(self):
        if self.texins_mode_var.get() == "batch":
            self.texins_path_label.config(text="输入文件夹：")
        else:
            self.texins_path_label.config(text="输入 M2：")

    def pick_texins_path(self):
        if self.texins_mode_var.get() == "batch":
            path = filedialog.askdirectory(title="选择包含 M2 的文件夹")
        else:
            path = filedialog.askopenfilename(title="选择目标 264 M2", filetypes=M2_TYPES)
        if path:
            self.texins_path_var.set(path)

    def _update_angle_visibility(self):
        """根据当前旋转模式，显示或隐藏角度输入框。镜像模式不需要角度。"""
        mode_label = self.rotation_mode_var.get()
        mode = ROTATION_MODES.get(mode_label, "")
        if mode.startswith("mirror_"):
            self.rotation_angle_entry.configure(state="disabled")
        else:
            self.rotation_angle_entry.configure(state="normal")

    @staticmethod
    def find_m2_files(folder, recursive):
        root = Path(folder)
        if recursive:
            candidates = (
                Path(current) / filename
                for current, _, filenames in os.walk(str(root))
                for filename in filenames if filename.lower().endswith(".m2")
            )
        else:
            candidates = (
                path for path in root.iterdir()
                if path.is_file() and path.suffix.lower() == ".m2"
            )
        return sorted((path for path in candidates if path.is_file()),
                      key=lambda path: str(path).lower())

    def set_log(self, text):
        self.log.delete("1.0", "end")
        self.log.insert("1.0", text)

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.single_button.configure(state=state)
        self.batch_button.configure(state=state)
        self.generic_button.configure(state=state)
        self.scale_button.configure(state=state)
        self.particle_button.configure(state=state)
        self.rotation_button.configure(state=state)

    @staticmethod
    def normalized(path):
        return os.path.normcase(os.path.abspath(str(path)))

    def run_single(self):
        if self.busy:
            return
        try:
            source = self.input_var.get().strip()
            output = self.output_var.get().strip()
            if not source or not Path(source).is_file():
                raise M2Error("请选择有效的输入头盔 M2。")
            if not output or Path(output).suffix.lower() != ".m2":
                raise M2Error("请选择有效的 .m2 输出位置。")
            if self.normalized(source) == self.normalized(output):
                raise M2Error("为保护原头盔，输出不能覆盖输入文件。")
            x, z = self.parse_offsets()
            if Path(output).exists() and not messagebox.askyesno(
                    "输出文件已存在", "是否覆盖输出文件？\n输入原模型不会修改。", parent=self.root):
                return
            Path(output).parent.mkdir(parents=True, exist_ok=True)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法开始移动", str(exc), parent=self.root)
            return

        race = self.race_var.get().strip() or "手动数值"
        self.set_busy(True)
        self.set_log("正在按 X＝%g、Y＝0、Z＝%g 移动头盔，请稍候……" % (x, z))
        threading.Thread(
            target=self.single_worker,
            args=(source, output, race, x, z), daemon=True).start()

    def single_worker(self, source, output, race, x, z):
        try:
            stats = move_m2(source, output, x, 0.0, z)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "头盔移动失败",
                "message": str(exc), "path": None,
            })
            return
        message = (
            "头盔移动完成。\n\n种族／方式：%s\n移动量：X＝%g，Y＝0，Z＝%g\n"
            "顶点：%d；骨骼：%d；动画：%d；附件：%d；粒子：%d\n"
            "共更新 %d 个浮点坐标值。\n\n输出文件：\n%s\n\n输入原模型未修改。"
            % (race, x, z, stats["vertices"], stats["bones"], stats["animations"],
               stats["attachments"], stats["particles"], stats["moved_floats"], output)
        )
        self.events.put({
            "kind": "success", "title": "头盔移动完成", "message": message, "path": output,
        })

    def run_batch(self):
        if self.busy:
            return
        try:
            source_folder = self.batch_input_var.get().strip()
            output_folder = self.batch_output_var.get().strip()
            if not source_folder or not Path(source_folder).is_dir():
                raise M2Error("请选择有效的头盔输入文件夹。")
            if not output_folder:
                raise M2Error("请选择批量输出文件夹。")
            source_root = Path(source_folder).resolve()
            output_root = Path(output_folder).resolve()
            recursive = self.recursive_var.get()
            if source_root == output_root:
                raise M2Error("批量输出文件夹不能与输入文件夹相同。")
            files = self.find_m2_files(str(source_root), recursive)
            if recursive and output_root.is_relative_to(source_root):
                # 默认输出位于输入目录内；重复运行时排除旧输出，避免二次移动。
                files = [path for path in files if not path.is_relative_to(output_root)]
            if not files:
                raise M2Error("所选范围内没有找到 M2 文件。")
            automatic = self.auto_suffix_var.get()
            x, z = (None, None) if automatic else self.parse_offsets()
            candidates = [
                path for path in files
                if not automatic or profile_name_for_path(path) is not None
            ]
            overwrite_count = sum(
                (output_root / path.relative_to(source_root)).exists() for path in candidates)
            if automatic:
                method = "按每个文件的后缀自动匹配；未识别文件跳过。"
            else:
                method = "全部使用当前 X＝%g、Y＝0、Z＝%g。" % (x, z)
            prompt = (
                "找到 %d 个 M2 文件。\n%s\n\n输出到：%s\n原文件保持不变。"
                % (len(files), method, output_root)
            )
            if overwrite_count:
                prompt += "\n\n输出目录中有 %d 个同名文件将被覆盖。" % overwrite_count
            if not messagebox.askyesno("确认批量移动", prompt, parent=self.root):
                return
            output_root.mkdir(parents=True, exist_ok=True)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法开始批量移动", str(exc), parent=self.root)
            return

        profile_snapshot = {name: dict(values) for name, values in self.profiles.items()}
        race = self.race_var.get().strip() or "手动数值"
        self.set_busy(True)
        self.set_log("正在批量处理 %d 个头盔文件，请稍候……" % len(files))
        threading.Thread(
            target=self.batch_worker,
            args=(source_root, output_root, files, race, x, z, automatic, profile_snapshot),
            daemon=True,
        ).start()

    def batch_worker(self, source_root, output_root, files, current_race,
                     manual_x, manual_z, automatic, profiles):
        completed = 0
        skipped = 0
        failed = 0
        lines = []
        for source in files:
            relative = source.relative_to(source_root)
            output = output_root / relative
            if automatic:
                profile = profile_name_for_path(source)
                if not profile or profile not in profiles:
                    skipped += 1
                    lines.append("%s：未识别末尾种族后缀，已跳过" % relative)
                    continue
                race = profile
                x = profiles[profile]["x"]
                z = profiles[profile]["z"]
            else:
                race = current_race
                x, z = manual_x, manual_z
            try:
                move_m2(source, output, x, 0.0, z)
                completed += 1
                lines.append("%s：完成（%s；X＝%g，Z＝%g）" % (relative, race, x, z))
            except Exception as exc:
                failed += 1
                lines.append("%s：失败（%s）" % (relative, exc))
        summary = (
            "批量移动结束：成功 %d 个，未识别跳过 %d 个，失败 %d 个。\n"
            "模式：%s\n输出目录：%s\n原头盔文件未修改。"
            % (completed, skipped, failed,
               "按文件后缀自动匹配" if automatic else "全部使用手动 X／Z", output_root)
        )
        self.events.put({
            "kind": "warning" if failed else "success",
            "title": "批量移动完成",
            "message": summary + "\n\n" + "\n".join(lines),
            "path": str(output_root),
        })

    def parse_generic_deltas(self):
        try:
            values = (
                float(self.generic_x_var.get().strip()),
                float(self.generic_y_var.get().strip()),
                float(self.generic_z_var.get().strip()),
            )
        except ValueError:
            raise M2Error("通用模式的 X、Y、Z 必须是有效数字。")
        if not all(math.isfinite(value) for value in values):
            raise M2Error("通用模式的 X、Y、Z 不能是无穷大或 NaN。")
        return values

    def run_generic(self):
        if self.busy:
            return
        try:
            source = self.generic_input_var.get().strip()
            output = self.generic_output_var.get().strip()
            if not source or not Path(source).is_file():
                raise M2Error("请选择有效的输入 M2。")
            if not output or Path(output).suffix.lower() != ".m2":
                raise M2Error("请选择有效的 .m2 输出位置。")
            if self.normalized(source) == self.normalized(output):
                raise M2Error("为保护原模型，输出不能覆盖输入文件。")
            deltas = self.parse_generic_deltas()
            if Path(output).exists() and not messagebox.askyesno(
                    "输出文件已存在", "是否覆盖输出文件？\n输入原模型不会修改。", parent=self.root):
                return
            Path(output).parent.mkdir(parents=True, exist_ok=True)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法开始通用移动", str(exc), parent=self.root)
            return

        self.set_busy(True)
        self.set_log("正在按 X＝%g、Y＝%g、Z＝%g 移动模型，请稍候……" % deltas)
        threading.Thread(
            target=self.generic_worker,
            args=(source, output, deltas), daemon=True).start()

    def generic_worker(self, source, output, deltas):
        try:
            stats = move_m2(source, output, *deltas)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "通用移动失败",
                "message": str(exc), "path": None,
            })
            return
        message = (
            "通用 XYZ 移动完成。\n\nX＝%g，Y＝%g，Z＝%g\n"
            "顶点：%d；骨骼：%d；动画：%d；附件：%d；粒子：%d\n"
            "共更新 %d 个浮点坐标值。\n\n输出文件：\n%s\n\n输入原模型未修改。"
            % (deltas[0], deltas[1], deltas[2], stats["vertices"], stats["bones"],
               stats["animations"], stats["attachments"], stats["particles"],
               stats["moved_floats"], output)
        )
        self.events.put({
            "kind": "success", "title": "通用移动完成", "message": message, "path": output,
        })

    def parse_scale_factor(self):
        try:
            multiplier = float(self.scale_factor_var.get().strip())
        except ValueError:
            raise M2Error("缩放倍率必须是有效数字，例如 1.30 或 0.80。")
        if not math.isfinite(multiplier) or multiplier <= 0:
            raise M2Error("缩放倍率必须是大于 0 的有限数值。")
        return multiplier

    def run_scale(self):
        if self.busy:
            return
        try:
            source = self.scale_input_var.get().strip()
            output = self.scale_output_var.get().strip()
            if not source or not Path(source).is_file():
                raise M2Error("请选择有效的输入 M2。")
            if not output or Path(output).suffix.lower() != ".m2":
                raise M2Error("请选择有效的 .m2 输出位置。")
            if self.normalized(source) == self.normalized(output):
                raise M2Error("为保护原模型，输出不能覆盖输入文件。")
            multiplier = self.parse_scale_factor()
            if Path(output).exists() and not messagebox.askyesno(
                    "输出文件已存在", "是否覆盖输出文件？\n输入原模型不会修改。", parent=self.root):
                return
            Path(output).parent.mkdir(parents=True, exist_ok=True)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法开始缩放", str(exc), parent=self.root)
            return

        self.set_busy(True)
        self.set_log("正在按 %g 倍整体缩放模型，请稍候……" % multiplier)
        threading.Thread(
            target=self.scale_worker,
            args=(source, output, multiplier), daemon=True).start()

    def scale_worker(self, source, output, multiplier):
        try:
            stats = resize_m2(source, output, multiplier)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "模型缩放失败",
                "message": str(exc), "path": None,
            })
            return
        message = (
            "模型整体缩放完成。\n\n缩放倍率：%g\n"
            "顶点：%d；骨骼：%d；动画：%d；附件：%d；相机：%d；粒子：%d\n"
            "共缩放 %d 个浮点数值。\n\n输出文件：\n%s\n\n输入原模型未修改。\n"
            "原脚本中已注释的骨骼动画轨道未处理。"
            % (multiplier, stats["vertices"], stats["bones"], stats["animations"],
               stats["attachments"], stats["cameras"], stats["particles"],
               stats["scaled_floats"], output)
        )
        self.events.put({
            "kind": "success", "title": "模型缩放完成", "message": message, "path": output,
        })

    def run_bonefix(self):
        if self.busy:
            return
        try:
            source = self.bonefix_input_var.get().strip()
            reference = self.bonefix_ref_var.get().strip()
            output = self.bonefix_output_var.get().strip()
            if not source or Path(source).suffix.lower() != ".m2":
                raise M2Error("请选择有效的 264 输入 M2。")
            if not reference or Path(reference).suffix.lower() != ".m2":
                raise M2Error("请选择有效的 274 参考 M2。")
            if not output or Path(output).suffix.lower() != ".m2":
                raise M2Error("请选择有效的 .m2 输出位置。")
            if self.normalized(source) == self.normalized(output):
                raise M2Error("为保护原模型，输出不能覆盖输入文件。")
            if Path(output).exists() and not messagebox.askyesno(
                    "输出文件已存在", "是否覆盖输出文件？\n输入原模型不会修改。", parent=self.root):
                return
            Path(output).parent.mkdir(parents=True, exist_ok=True)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法开始修复", str(exc), parent=self.root)
            return

        self.set_busy(True)
        self.set_log("正在从 274 参考模型复制骨骼轨道，请稍候……")
        threading.Thread(
            target=self.bonefix_worker,
            args=(source, reference, output), daemon=True).start()

    def bonefix_worker(self, source, reference, output):
        try:
            stats = copy_bone_tracks(source, reference, output)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "骨骼轨道修复失败",
                "message": str(exc), "path": None,
            })
            return
        message = (
            "骨骼轨道修复完成。\n\n骨骼：%d 根\n复制轨道：%d 条（Translation／Rotation／Scaling）\n"
            "复制键值：%d 个\n\n输出文件：\n%s\n\n输入原模型未修改。"
            % (stats["bones"], stats["tracks"], stats["keyframes"], output)
        )
        self.events.put({
            "kind": "success", "title": "骨骼轨道修复完成", "message": message, "path": output,
        })

    def run_texanim(self):
        if self.busy:
            return
        try:
            source = self.texanim_source_var.get().strip()
            target = self.texanim_target_var.get().strip()
            output = self.texanim_output_var.get().strip()
            if not source or Path(source).suffix.lower() != ".m2":
                raise M2Error("请选择有效的源 M2（264 或 274）。")
            if not target or Path(target).suffix.lower() != ".m2":
                raise M2Error("请选择有效的目标 264 M2。")
            if not output or Path(output).suffix.lower() != ".m2":
                raise M2Error("请选择有效的 .m2 输出位置。")
            if self.normalized(target) == self.normalized(output):
                raise M2Error("为保护原模型，输出不能覆盖目标文件。")
            replace = bool(self.texanim_replace_var.get())
            if Path(output).exists() and not messagebox.askyesno(
                    "输出文件已存在", "是否覆盖输出文件？\n目标原模型不会修改。", parent=self.root):
                return
            Path(output).parent.mkdir(parents=True, exist_ok=True)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法开始复制", str(exc), parent=self.root)
            return

        self.set_busy(True)
        self.set_log("正在复制 TexAnim，请稍候……")
        threading.Thread(
            target=self.texanim_worker,
            args=(source, target, output,
                  self.texanim_indices_var.get(),
                  self.texanim_slots_var.get(), replace), daemon=True).start()

    def texanim_worker(self, source, target, output, indices_text, slots_text, replace):
        try:
            # 先读取两边数量，用于按真实范围校验索引/槽位
            _, _, src_table = _open_texanim_source(source)
            _, _, dst_table = locate_texanim_table(
                Path(target).read_bytes(), 0, 0)
            dst_count = dst_table["count"]
            indices = parse_indices(indices_text, src_table["count"])
            if replace:
                slots = parse_indices(slots_text, dst_count)
                if indices is None or slots is None:
                    raise M2Error("替换模式必须同时填写源索引和目标槽位。")
                if len(indices) != len(slots):
                    raise M2Error("源索引与目标槽位个数不一致（%d 个源，%d 个槽位）。"
                                  % (len(indices), len(slots)))
            else:
                slots = None
            stats = copy_tex_anims(source, target, output, indices, slots)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "TexAnim 复制失败",
                "message": str(exc), "path": None,
            })
            return
        if stats["replace_slots"] is not None:
            mode = "替换槽位 " + ",".join(map(str, stats["replace_slots"]))
        else:
            mode = "追加复制"
        message = (
            "TexAnim 复制完成（%s）。\n\n源文件 TexAnim：%d 条，选中复制：%d 条\n"
            "复制轨道：%d 条（Translation／Rotation／Scaling）\n复制键值：%d 个\n"
            "目标 TexAnim：%d 条 → %d 条\n\n输出文件：\n%s\n\n目标原模型未修改。"
            % (mode, stats["source_total"], stats["copied"],
               stats["tracks"], stats["keyframes"],
               stats["target_previous"], stats["target_now"], output)
        )
        self.events.put({
            "kind": "success", "title": "TexAnim 复制完成", "message": message, "path": output,
        })

    def run_eventadd(self):
        if self.busy:
            return
        try:
            target = self.eventadd_target_var.get().strip()
            output = self.eventadd_output_var.get().strip()
            if not target or Path(target).suffix.lower() != ".m2":
                raise M2Error("请选择有效的目标 264 M2。")
            if not output or Path(output).suffix.lower() != ".m2":
                raise M2Error("请选择有效的 .m2 输出位置。")
            if self.normalized(target) == self.normalized(output):
                raise M2Error("为保护原模型，输出不能覆盖目标文件。")
            try:
                count = int(self.eventadd_count_var.get().strip())
            except ValueError:
                raise M2Error("添加数量必须是正整数。")
            if count < 1:
                raise M2Error("添加数量必须是正整数。")
            _, _, table = locate_events_table(Path(target).read_bytes())
            if Path(output).exists() and not messagebox.askyesno(
                    "输出文件已存在", "是否覆盖输出文件？\n目标原模型不会修改。", parent=self.root):
                return
            Path(output).parent.mkdir(parents=True, exist_ok=True)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法开始添加", str(exc), parent=self.root)
            return

        self.set_busy(True)
        self.set_log("正在添加空白事件，请稍候……")
        threading.Thread(
            target=self.eventadd_worker,
            args=(target, output, count, table["count"]), daemon=True).start()

    def eventadd_worker(self, target, output, count, previous):
        try:
            stats = add_events(target, output, count)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "Events 添加失败",
                "message": str(exc), "path": None,
            })
            return
        message = (
            "Events 添加完成。\n\n原有事件：%d 条\n新增空白事件：%d 条\n"
            "现共：%d 条\n\n新记录位于事件表末尾，"
            "各字段为 0，可在 010 中直接填写。\n\n输出文件：\n%s\n\n目标原模型未修改。"
            % (stats["previous"], stats["added"], stats["now"], output)
        )
        self.events.put({
            "kind": "success", "title": "Events 添加完成",
            "message": message, "path": output,
        })

    def run_texins(self):
        if self.busy:
            return
        try:
            mode = self.texins_mode_var.get()
            path = self.texins_path_var.get().strip()
            if not path:
                raise M2Error("请选择输入 M2 或文件夹。")
            if mode == "single":
                if Path(path).suffix.lower() != ".m2":
                    raise M2Error("单个模式请选择 .m2 文件。")
                outputs = [self.default_output_in_named_subfolder(Path(path))]
            else:
                if not Path(path).is_dir():
                    raise M2Error("批量模式请选择文件夹。")
            entries_text = self.texins_text.get("1.0", "end")
            entries = parse_texture_entries(entries_text)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法开始插入", str(exc), parent=self.root)
            return

        self.set_busy(True)
        self.set_log("正在插入纹理，请稍候……")
        threading.Thread(
            target=self.texins_worker,
            args=(mode, path, entries), daemon=True).start()

    def texins_worker(self, mode, path, entries):
        results = []
        errors = []
        if mode == "single":
            jobs = [(path, self.default_output_in_named_subfolder(Path(path)))]
        else:
            folder = Path(path)
            out_dir = folder / "Export"
            jobs = []
            for f in sorted(folder.glob("*.m2")):
                jobs.append((str(f), str(out_dir / f.name)))
        for src, dst in jobs:
            try:
                stats = insert_textures(src, dst, entries)
                results.append((Path(src).name, stats))
            except Exception as exc:
                errors.append((Path(src).name, str(exc)))
        if mode == "single" and errors:
            self.events.put({
                "kind": "error", "title": "纹理插入失败",
                "message": errors[0][1], "path": None,
            })
            return
        if not results:
            self.events.put({
                "kind": "error", "title": "纹理插入失败",
                "message": "没有成功处理任何文件。" if not errors else
                          "；".join("%s：%s" % e for e in errors[:3]),
                "path": None,
            })
            return
        lines = []
        matched_total = 0
        for name, stats in results:
            matched_total += stats["matched"]
            lines.append("%s：匹配 %d/%d 条（索引 %s）" % (
                name, stats["matched"], stats["total"],
                ",".join(map(str, stats["indices"])) or "无"))
        if errors:
            lines.append("")
            lines.append("失败 %d 个：" % len(errors))
            lines.extend("%s：%s" % e for e in errors[:5])
        message = (
            "纹理插入完成。\n\n成功 %d 个文件，共改写 %d 条纹理记录（type→HARDCODED(0)）。"
            "\n\n%s\n\n原模型未修改。"
            % (len(results), matched_total, "\n".join(lines))
        )
        self.events.put({
            "kind": "success", "title": "纹理插入完成",
            "message": message, "path": None,
        })

    def parse_particle_factor(self):
        try:
            multiplier = float(self.particle_factor_var.get().strip())
        except ValueError:
            raise M2Error("粒子缩放倍率必须是有效数字，例如 1.50 或 0.80。")
        if not math.isfinite(multiplier) or multiplier <= 0:
            raise M2Error("粒子缩放倍率必须是大于 0 的有限数值。")
        return multiplier

    def run_particle_scale(self):
        if self.busy:
            return
        try:
            source = self.particle_input_var.get().strip()
            output = self.particle_output_var.get().strip()
            if not source or not Path(source).is_file():
                raise M2Error("请选择有效的输入 M2。")
            if not output or Path(output).suffix.lower() != ".m2":
                raise M2Error("请选择有效的 .m2 输出位置。")
            if self.normalized(source) == self.normalized(output):
                raise M2Error("为保护原模型，输出不能覆盖输入文件。")
            multiplier = self.parse_particle_factor()
            if Path(output).exists() and not messagebox.askyesno(
                    "输出文件已存在", "是否覆盖输出文件？\n输入原模型不会修改。", parent=self.root):
                return
            Path(output).parent.mkdir(parents=True, exist_ok=True)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法开始粒子缩放", str(exc), parent=self.root)
            return

        self.set_busy(True)
        self.set_log("正在按 %g 倍缩放粒子 ScaleTrack，请稍候……" % multiplier)
        threading.Thread(
            target=self.particle_scale_worker,
            args=(source, output, multiplier), daemon=True).start()

    def particle_scale_worker(self, source, output, multiplier):
        try:
            stats = scale_particle_sizes_m2(source, output, multiplier)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "粒子缩放失败",
                "message": str(exc), "path": None,
            })
            return
        message = (
            "粒子大小缩放完成。\n\n缩放倍率：%g\n"
            "粒子发射器：%d；含缩放关键帧的发射器：%d\n"
            "ScaleTrack 关键帧：%d 组；共缩放 %d 个 X／Y 浮点值。\n\n"
            "输出文件：\n%s\n\n输入原模型未修改。\n"
            "粒子位置、ScaleVary 和其他参数均未修改。"
            % (multiplier, stats["particles"], stats["emitters_with_keys"],
               stats["keyframe_pairs"], stats["scaled_floats"], output)
        )
        self.events.put({
            "kind": "success", "title": "粒子缩放完成", "message": message, "path": output,
        })

    def run_rotation(self):
        if self.busy:
            return
        try:
            source = self.rotation_input_var.get().strip()
            output = self.rotation_output_var.get().strip()
            mode_label = self.rotation_mode_var.get().strip()
            if not source or not Path(source).is_file():
                raise M2Error("请选择有效的输入 M2。")
            if not output or Path(output).suffix.lower() != ".m2":
                raise M2Error("请选择有效的 .m2 输出位置。")
            if self.normalized(source) == self.normalized(output):
                raise M2Error("为保护原模型，输出不能覆盖输入文件。")
            if mode_label not in ROTATION_MODES:
                raise M2Error("请选择 o、-、I 或镜像模式。")
            mode = ROTATION_MODES[mode_label]
            if mode.startswith("mirror_"):
                degrees = None
            else:
                try:
                    degrees = float(self.rotation_angle_var.get().strip())
                except ValueError:
                    raise M2Error("旋转角度必须是有效数字，例如 180 或 -90。")
                if not math.isfinite(degrees):
                    raise M2Error("旋转角度不能是无穷大或 NaN。")
            if Path(output).exists() and not messagebox.askyesno(
                    "输出文件已存在", "是否覆盖输出文件？\n输入原模型不会修改。", parent=self.root):
                return
            Path(output).parent.mkdir(parents=True, exist_ok=True)
        except (M2Error, OSError) as exc:
            messagebox.showerror("无法开始旋转", str(exc), parent=self.root)
            return

        mode = ROTATION_MODES[mode_label]
        self.set_busy(True)
        if degrees is None:
            self.set_log("正在按 %s 镜像模型，请稍候……" % mode_label)
        else:
            self.set_log("正在按 %s、%g° 旋转模型，请稍候……" % (mode_label, degrees))
        threading.Thread(
            target=self.rotation_worker,
            args=(source, output, mode, mode_label, degrees), daemon=True).start()

    def rotation_worker(self, source, output, mode, mode_label, degrees):
        if mode.startswith("mirror_"):
            self.mirror_worker(source, output, mode, mode_label)
            return
        try:
            stats = rotate_m2(source, output, mode, degrees)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "模型旋转失败",
                "message": str(exc), "path": None,
            })
            return
        message = (
            "模型旋转完成。\n\n模式：%s\n角度：%g°\n"
            "顶点：%d；碰撞点：%d；骨骼：%d；粒子：%d；事件：%d；附件：%d\n"
            "内部骨骼平移关键帧：%d；旋转关键帧：%d。\n\n输出文件：\n%s\n\n"
            "输入原模型未修改。"
            % (mode_label, degrees, stats["vertices"], stats["collision_positions"],
               stats["bones"], stats["particles"], stats["events"], stats["attachments"],
               stats["translation_keyframes"], stats["rotation_keyframes"], output)
        )
        if stats["external_tracks"]:
            message += (
                "\n\n注意：%d 条外部动画轨道（共 %d 个关键帧）位于 .anim，未修改。"
                % (stats["external_tracks"], stats["external_keyframes"])
            )
        self.events.put({
            "kind": "success", "title": "模型旋转完成", "message": message, "path": output,
        })

    def mirror_worker(self, source, output, mode, mode_label):
        axis = mode[7:]
        try:
            stats = mirror_m2(source, output, axis)
        except Exception as exc:
            self.events.put({
                "kind": "error", "title": "模型镜像失败",
                "message": str(exc), "path": None,
            })
            return
        message = (
            "模型镜像完成。\n\n模式：%s\n" % mode_label
            + "顶点：%d；碰撞点：%d；碰撞法线：%d；骨骼：%d；粒子：%d；事件：%d；附件：%d\n"
            "骨骼平移关键帧：%d；旋转关键帧：%d。\n\n输出文件：\n%s\n\n输入原模型未修改。"
            % (stats["vertices"], stats["collision_positions"], stats["collision_normals"],
               stats["bones"], stats["particles"], stats["events"], stats["attachments"],
               stats["translation_keyframes"], stats["rotation_keyframes"], output)
        )
        if stats["external_tracks"]:
            message += (
                "\n\n注意：%d 条外部动画轨道（共 %d 个关键帧）位于 .anim，未修改。"
                % (stats["external_tracks"], stats["external_keyframes"])
            )
        # 碰撞索引
        coll_tri = stats.get("collision_triangles", 0)
        if coll_tri:
            message += "\n\nM2 碰撞索引：%d 个三角形绕序已反转。" % coll_tri

        # Lights / Ribbons
        n_lights = stats.get("lights", 0)
        n_ribbons = stats.get("ribbons", 0)
        if n_lights or n_ribbons:
            parts = []
            if n_lights:
                parts.append("%d 个灯光" % n_lights)
            if n_ribbons:
                parts.append("%d 个丝带发射器" % n_ribbons)
            message += "\n\n" + "、".join(parts) + "位置已镜像。"

        # SKIN
        skin_stats = stats.get("skin", {})
        if skin_stats.get("found"):
            lines = ["\n\nSKIN 文件（三角形绕序已反转）："]
            for r in skin_stats["results"]:
                if r["status"] == "ok":
                    lines.append("  %s：%d 个三角形已反转"
                                 % (r["name"], r["triangles"]))
                else:
                    lines.append("  %s：失败（%s）" % (r["name"], r["message"]))
            message += "\n".join(lines)
        else:
            message += "\n\n未找到对应的 .skin 文件。"

        # .phys
        phys_stats = stats.get("phys", {})
        if phys_stats.get("found"):
            for r in phys_stats["results"]:
                if r.get("found"):
                    message += ("\n\n.phys 文件：%s，%d 个胶囊碰撞体已镜像。"
                               % (r["name"], r["capsules"]))
                elif "error" in r:
                    message += "\n\n.phys 文件 %s：失败（%s）" % (r["name"], r["error"])
                else:
                    message += "\n\n.phys 文件 %s：未找到 CAPS 数据。" % r["name"]
        else:
            message += "\n\n未找到对应的 .phys 文件。"
        self.events.put({
            "kind": "success", "title": "模型镜像完成", "message": message, "path": output,
        })

    def poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                self.set_busy(False)
                self.set_log(event["message"])
                path = event.get("path")
                if path and Path(path).exists():
                    self.last_path = Path(path)
                    self.open_button.configure(state="normal")
                if event["kind"] == "error":
                    messagebox.showerror(event["title"], event["message"], parent=self.root)
                # 处理完成或批量部分失败时只更新日志，不再弹出完成提示框。
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def open_output(self):
        if not self.last_path:
            return
        folder = self.last_path if self.last_path.is_dir() else self.last_path.parent
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)


def main():
    root = tk.Tk()
    HelmetMoverGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
