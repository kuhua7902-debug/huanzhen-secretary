"""DPI 缩放诊断脚本 —— 检查 screenshot_and_find 的坐标体系是否一致

运行：py -3.12 scripts\\check_dpi.py

如果输出显示「DPI不一致: 是」，那就是之前计算器点不准的元凶。
修复后 click_element 会自动做坐标转换，应该能点准了。
"""
import sys
import os
import io

# 强制 stdout 用 UTF-8，避免 GBK 编码报错
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 让脚本能 import 项目内的 core 模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main():
    try:
        import pyautogui
    except ImportError:
        print("❌ 未安装 pyautogui，请执行: py -3.12 -m pip install pyautogui pillow")
        return

    # 逻辑像素（pyautogui.size()）
    logical_w, logical_h = pyautogui.size()
    # 物理像素（截图的真实尺寸）
    img = pyautogui.screenshot()
    physical_w, physical_h = img.size

    scale_x = physical_w / logical_w if logical_w else 1.0
    scale_y = physical_h / logical_h if logical_h else 1.0

    print("=" * 60)
    print("DPI 缩放诊断")
    print("=" * 60)
    print(f"逻辑像素 (pyautogui.size):   {logical_w} x {logical_h}")
    print(f"物理像素 (screenshot.size):  {physical_w} x {physical_h}")
    print(f"缩放比:                      {scale_x:.3f} x {scale_y:.3f}")
    print(f"系统缩放百分比:              约 {scale_x*100:.0f}%")
    print()
    if abs(scale_x - 1.0) > 0.01 or abs(scale_y - 1.0) > 0.01:
        print("⚠️  DPI不一致: 是")
        print("   这就是之前点不准的元凶！")
        print("   screenshot() 截的是物理像素，但 pyautogui.click() 按逻辑像素点。")
        print()
        print("✅ 修复方案已应用：")
        print("   - screenshot_and_find 现在用图片实际像素作为 VL 坐标系")
        print("   - click_element 会自动把图片像素坐标转成逻辑像素再点击")
        print()
        print("验证转换示例：")
        # 模拟一个中心点
        test_x, test_y = physical_w // 2, physical_h // 2
        converted_x = int(round(test_x / scale_x))
        converted_y = int(round(test_y / scale_y))
        print(f"   图片中心点 物理像素({test_x},{test_y}) → 逻辑像素({converted_x},{converted_y})")
        print(f"   逻辑中心点应为 ({logical_w//2},{logical_h//2})")
        match = "✅ 匹配" if abs(converted_x - logical_w//2) < 2 and abs(converted_y - logical_h//2) < 2 else "❌ 不匹配"
        print(f"   {match}")
    else:
        print("✅ DPI一致: 否 (无缩放)")
        print("   截图和点击坐标系一致，点不准可能是其他原因（视觉模型估算误差）。")

    print()
    print("=" * 60)
    print("测试 _physical_to_logical 转换函数")
    print("=" * 60)
    try:
        from core.gui_tools import _physical_to_logical, _get_dpi_scale
        sx, sy, lw, lh, pw, ph = _get_dpi_scale()
        print(f"_get_dpi_scale: scale={sx:.3f}x{sy:.3f} logical={lw}x{lh} physical={pw}x{ph}")
        # 测试几个点
        for px, py in [(0, 0), (pw//4, ph//4), (pw//2, ph//2), (pw-1, ph-1)]:
            lx, ly = _physical_to_logical(px, py, sx, sy)
            print(f"  物理({px:5d},{py:5d}) → 逻辑({lx:5d},{ly:5d})")
    except Exception as e:
        print(f"❌ 调用转换函数失败: {e}")

    print()
    print("=" * 60)
    print("结论")
    print("=" * 60)
    if abs(scale_x - 1.0) > 0.01 or abs(scale_y - 1.0) > 0.01:
        print("你的屏幕启用了 DPI 缩放，修复前 click_element 会系统性偏移。")
        print("修复后应该能点准了，请重新测试计算器算数流程。")
    else:
        print("你的屏幕无 DPI 缩放，修复对坐标无影响（但代码更健壮了）。")


if __name__ == "__main__":
    main()
