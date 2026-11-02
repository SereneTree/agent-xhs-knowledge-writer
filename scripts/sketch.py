#!/usr/bin/env python3
"""生成 rough.js 风格的手绘 SVG 图元。固定种子，输出可复现。

不依赖任何第三方库，只用标准库 random + math。
"""
import math
import random


class Sketch:
    def __init__(self, seed=20260920):
        self.rng = random.Random(seed)

    def _jit(self, amount):
        return self.rng.uniform(-amount, amount)

    def line_path(self, x1, y1, x2, y2, rough=1.8, bow=1.0):
        """一条手绘线：两个控制点带抖动，形成轻微弓形。"""
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy) or 1
        # 抖动量随长度略增，但有上限
        r = min(rough * (1 + length / 400), rough * 2.2)
        offset = bow * r
        # 垂直方向的法向量，用于制造弓形
        nx, ny = -dy / length, dx / length
        mid_bow = self._jit(offset)
        c1x = x1 + dx * 0.35 + nx * mid_bow + self._jit(r)
        c1y = y1 + dy * 0.35 + ny * mid_bow + self._jit(r)
        c2x = x1 + dx * 0.68 + nx * mid_bow * 0.7 + self._jit(r)
        c2y = y1 + dy * 0.68 + ny * mid_bow * 0.7 + self._jit(r)
        sx, sy = x1 + self._jit(r * 0.6), y1 + self._jit(r * 0.6)
        ex, ey = x2 + self._jit(r * 0.6), y2 + self._jit(r * 0.6)
        return f"M{sx:.1f},{sy:.1f} C{c1x:.1f},{c1y:.1f} {c2x:.1f},{c2y:.1f} {ex:.1f},{ey:.1f}"

    def line(self, x1, y1, x2, y2, stroke="#3A3A3A", width=2.6, rough=1.8, passes=2, marker=None):
        """手绘线，默认画两遍模拟重描。"""
        out = []
        for i in range(passes):
            d = self.line_path(x1, y1, x2, y2, rough=rough * (1 + i * 0.35))
            m = f' marker-end="url(#{marker})"' if (marker and i == passes - 1) else ""
            op = 1.0 if i == 0 else 0.55
            out.append(
                f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{width:.1f}" '
                f'stroke-linecap="round" opacity="{op}"{m}/>'
            )
        return "\n".join(out)

    def rect(self, x, y, w, h, stroke="#3A3A3A", fill="none", width=2.8, rough=2.0, passes=2, radius=6):
        """手绘矩形：先填充（规整路径），再用抖动线描边两遍。"""
        out = []
        if fill != "none":
            out.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                f'rx="{radius}" fill="{fill}" stroke="none"/>'
            )
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        for i in range(4):
            a, b = corners[i], corners[(i + 1) % 4]
            # 边角略微超出，模拟手绘出头
            ox = self._jit(3.0)
            oy = self._jit(3.0)
            out.append(self.line(a[0], a[1], b[0] + ox * 0.3, b[1] + oy * 0.3,
                                 stroke=stroke, width=width, rough=rough, passes=passes))
        return "\n".join(out)

    def diamond(self, cx, cy, w, h, stroke="#3A3A3A", fill="none", width=2.8, rough=2.0, passes=2):
        """手绘菱形。"""
        out = []
        pts = [(cx, cy - h / 2), (cx + w / 2, cy), (cx, cy + h / 2), (cx - w / 2, cy)]
        if fill != "none":
            poly = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
            out.append(f'<polygon points="{poly}" fill="{fill}" stroke="none"/>')
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            out.append(self.line(a[0], a[1], b[0], b[1],
                                 stroke=stroke, width=width, rough=rough, passes=passes))
        return "\n".join(out)

    def underline(self, x1, x2, y, stroke="#D9843B", width=6, rough=2.4):
        """标题下的手绘双下划线。"""
        out = [self.line(x1, y, x2, y, stroke=stroke, width=width, rough=rough, passes=1)]
        out.append(self.line(x1 + 6, y + 13, x2 - 40, y + 11,
                             stroke=stroke, width=width * 0.55, rough=rough * 1.2, passes=1))
        return "\n".join(out)

    def arrow_head(self, x, y, angle_deg, stroke="#3A3A3A", width=2.6, size=15):
        """手绘箭头：两笔短线，不用 marker。"""
        a = math.radians(angle_deg)
        out = []
        for spread in (150, -150):
            b = a + math.radians(spread)
            ex = x + size * math.cos(b) + self._jit(1.2)
            ey = y + size * math.sin(b) + self._jit(1.2)
            out.append(self.line(x, y, ex, ey, stroke=stroke, width=width, rough=1.0, passes=1))
        return "\n".join(out)

    def arrow(self, x1, y1, x2, y2, stroke="#3A3A3A", width=2.6, rough=1.8):
        """带手绘箭头的连线。"""
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        return (self.line(x1, y1, x2, y2, stroke=stroke, width=width, rough=rough, passes=2)
                + "\n" + self.arrow_head(x2, y2, angle, stroke=stroke, width=width))

    def curve_arrow(self, pts, stroke="#3A3A3A", width=2.6, rough=1.6):
        """折线式手绘箭头，pts 为途经点列表，最后一段带箭头。"""
        out = []
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            if i == len(pts) - 2:
                out.append(self.arrow(a[0], a[1], b[0], b[1], stroke=stroke, width=width, rough=rough))
            else:
                out.append(self.line(a[0], a[1], b[0], b[1], stroke=stroke, width=width, rough=rough))
        return "\n".join(out)
