"""视觉定位精度验证脚本 —— 测试 screenshot_and_find 找按钮的准确性

运行：py -3.12 scripts\\test_vision_accuracy.py

脚本会：
1. 截屏
2. 让你输入想找的元素描述（如"数字7按钮"、"加号按钮"）
3. 调用 screenshot_and_find 找坐标
4. 在截图上用红十字标记找到的坐标，保存到 marked_*.png
5. 自动打开标记后的图片，让你直观看到 VL 找得准不准

这样就能判断：是 VL 找错了，还是坐标转换错了，还是点击错了。
"""
import sys
import os
import io
import json

# 强制 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main():
    from core.gui_tools import screenshot_and_find, screenshot_screen
    from PIL import Image, ImageDraw

    print("=" * 60)
    print("视觉定位精度验证")
    print("=" * 60)
    print()
    print("请先打开计算器（或任何想测试的窗口），让它显示在屏幕上。")
    print()

    # 1. 先截一张图看看当前屏幕
    print("正在截屏...")
    img_path = screenshot_screen()
    if img_path.startswith("错误"):
        print(f"截屏失败: {img_path}")
        return
    print(f"截图已保存: {img_path}")

    # 2. 让用户输入想找的元素
    print()
    target = input("请输入想找的元素描述（如 '数字7按钮'、'加号 +'、'等号 =按钮'）: ").strip()
    if not target:
        target = "数字7按钮"
        print(f"未输入，使用默认: {target}")

    print()
    print(f"正在用视觉模型查找「{target}」...")
    print("(这可能需要几秒钟)")
    print()

    # 3. 调用 screenshot_and_find
    result_json = screenshot_and_find(target, return_all=False)
    try:
        parsed = json.loads(result_json)
    except json.JSONDecodeError:
        print(f"返回非 JSON: {result_json[:500]}")
        return

    print("视觉模型返回:")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    print()

    if not parsed.get("found"):
        print("未找到元素。")
        return

    elements = parsed.get("elements", [])
    if not elements:
        print("元素列表为空。")
        return

    el = elements[0]
    x, y = el.get("x"), el.get("y")
    norm_x, norm_y = el.get("norm_x"), el.get("norm_y")
    confidence = el.get("confidence", "unknown")
    scale_x = parsed.get("scale_x", 1.0)
    scale_y = parsed.get("scale_y", 1.0)
    image_size = parsed.get("image_size", "?")

    print(f"找到「{target}」于图片坐标 ({x},{y}) [置信度:{confidence}]")
    if norm_x is not None:
        print(f"  归一化坐标: ({norm_x},{norm_y}) → 映射回 {image_size} 像素坐标 ({x},{y})")
    print(f"scale_x={scale_x}, scale_y={scale_y}")

    # 4. 在截图上标记坐标
    marked_path = img_path.replace(".png", "_marked.png")
    try:
        img = Image.open(img_path)
        draw = ImageDraw.Draw(img)

        # 画十字标记
        cx, cy = int(x), int(y)
        # 红色十字
        draw.line([(cx - 30, cy), (cx + 30, cy)], fill="red", width=3)
        draw.line([(cx, cy - 30), (cx, cy + 30)], fill="red", width=3)
        # 画圆圈
        r = 20
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="red", width=3)
        # 标注文字
        label = f"({cx},{cy}) {target}"
        draw.text((cx + 25, cy + 25), label, fill="red")

        img.save(marked_path)
        print()
        print(f"已保存标记图: {marked_path}")
    except Exception as e:
        print(f"标记图片失败: {e}")
        return

    # 5. 自动打开图片
    print()
    print("正在打开标记图，请查看红十字标记的位置是否准确...")
    print("如果红十字正好落在目标按钮中心 → VL 找准了，问题在点击环节")
    print("如果红十字偏了 → VL 估算不准，需要换更强的视觉模型或优化 prompt")
    try:
        os.startfile(marked_path)
    except Exception as e:
        print(f"自动打开失败: {e}，请手动打开: {marked_path}")

    # 6. 计算转换后的逻辑坐标
    if scale_x != 1.0 or scale_y != 1.0:
        from core.gui_tools import _physical_to_logical
        lx, ly = _physical_to_logical(float(x), float(y), scale_x, scale_y)
        print()
        print(f"DPI 转换: 图片像素({x},{y}) → 逻辑像素({lx},{ly})")
        print(f"click_position 会用逻辑坐标 ({lx},{ly}) 点击")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消")
    except Exception as e:
        print(f"错误: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
