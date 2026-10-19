"""生成应用图标（assets/icon.ico）与页面 logo（app/assets/logo.png）。

纯几何绘制，不依赖外部素材，随时可重新生成：

    .\\.venv\\Scripts\\python.exe tools\\make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ICON_PATH = ROOT / "assets" / "icon.ico"
LOGO_PATH = ROOT / "app" / "assets" / "logo.png"

BLUE_TOP = (0, 161, 214)      # B 站蓝
BLUE_BOTTOM = (35, 173, 229)
PINK = (251, 114, 153)        # B 站粉
WHITE = (255, 255, 255, 255)


def rounded_gradient(size: int, radius_ratio: float = 0.24) -> Image.Image:
    """圆角方形 + 蓝色渐变底。"""
    gradient = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(gradient)
    for y in range(size):
        ratio = y / max(size - 1, 1)
        color = tuple(
            int(BLUE_TOP[channel] + (BLUE_BOTTOM[channel] - BLUE_TOP[channel]) * ratio)
            for channel in range(3)
        ) + (255,)
        draw.line([(0, y), (size, y)], fill=color)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=255
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(gradient, (0, 0), mask)
    return canvas


def draw_chart(image: Image.Image) -> Image.Image:
    """画柱状图与粉色数据点，表达「数据分析」。"""
    size = image.size[0]
    draw = ImageDraw.Draw(image)

    baseline = size * 0.76
    bar_width = size * 0.11
    gap = size * 0.075
    heights = (0.22, 0.36, 0.28, 0.46)
    start_x = (size - (len(heights) * bar_width + (len(heights) - 1) * gap)) / 2

    for index, height_ratio in enumerate(heights):
        left = start_x + index * (bar_width + gap)
        top = baseline - size * height_ratio
        draw.rounded_rectangle(
            [left, top, left + bar_width, baseline],
            radius=bar_width / 2,
            fill=WHITE,
        )

    # 数据点：最后一个柱子顶部加一个粉色圆点，呼应品牌色
    last_left = start_x + (len(heights) - 1) * (bar_width + gap)
    dot_radius = size * 0.055
    center_x = last_left + bar_width / 2
    center_y = baseline - size * heights[-1] - dot_radius * 1.1
    draw.ellipse(
        [center_x - dot_radius, center_y - dot_radius, center_x + dot_radius, center_y + dot_radius],
        fill=PINK,
    )
    return image


def build_logo(size: int = 512) -> Image.Image:
    return draw_chart(rounded_gradient(size))


def main() -> None:
    ICON_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOGO_PATH.parent.mkdir(parents=True, exist_ok=True)

    logo = build_logo(512)
    logo.save(LOGO_PATH)

    # 图标提供多档尺寸，保证任务栏/资源管理器小图标清晰
    icon_source = build_logo(256)
    icon_source.save(
        ICON_PATH,
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("已生成:", LOGO_PATH)
    print("已生成:", ICON_PATH)


if __name__ == "__main__":
    main()
