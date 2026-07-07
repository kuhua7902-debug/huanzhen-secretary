#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
qwen3-vl-plus 连通性最小测试

不依赖截屏、不依赖 pyautogui，只用 PIL 生成一张测试图，
直接调用配置中的视觉模型，验证：
1. API 能否连通（网络/鉴权）
2. 模型名 qwen3-vl-plus 是否被服务端接受
3. 视觉输入能否正常处理（image_url）
4. 能否返回结构化 JSON

用法：
    cd d:\about_python\Keji_Nanobot\Keji-agent-main
    venv\Scripts\python.exe scripts\test_vl_connect.py
"""

import base64
import io
import os
import sys
import time
import json
import traceback

# 让脚本能 import 项目内的 core 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont


def make_test_image() -> bytes:
    """生成一张测试图：白底，中间画一个红方块并写文字 'TARGET'。"""
    img = Image.new("RGB", (640, 480), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    # 画一个红方块（目标），中心 (320, 240)，大小 120x120
    draw.rectangle([260, 180, 380, 300], fill=(220, 30, 30), outline=(0, 0, 0), width=3)
    # 写文字
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    draw.text((280, 215), "TARGET", fill=(255, 255, 255), font=font)
    # 画一些干扰元素
    draw.rectangle([40, 40, 140, 140], fill=(30, 120, 220), outline=(0, 0, 0), width=2)
    draw.text((60, 80), "BLUE", fill=(255, 255, 255), font=font)
    draw.ellipse([460, 320, 600, 460], fill=(30, 180, 60), outline=(0, 0, 0), width=2)
    draw.text((500, 370), "GREEN", fill=(255, 255, 255), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main():
    print("=" * 60)
    print("qwen3-vl-plus 连通性测试")
    print("=" * 60)

    # 1. 加载配置
    try:
        import yaml
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.yaml",
        )
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except Exception as e:
        print(f"[FAIL] 加载 config.yaml 失败: {e}")
        return

    models_cfg = cfg.get("models", {})
    vl_cfg = models_cfg.get("qwen_vl")
    if not vl_cfg:
        print("[FAIL] config.yaml 中未找到 models.qwen_vl 配置段")
        return

    # 展开 ${ENV_VAR}
    import re
    def expand(v):
        if isinstance(v, str):
            m = re.match(r"^\$\{(\w+)\}$", v.strip())
            if m:
                return os.environ.get(m.group(1), "")
        return v

    base_url = expand(vl_cfg.get("base_url", ""))
    api_key = expand(vl_cfg.get("api_key", ""))
    model = vl_cfg.get("model", "")
    timeout = vl_cfg.get("timeout", 60)

    print(f"\n[配置]")
    print(f"  base_url : {base_url}")
    print(f"  api_key  : {api_key[:8]}{'*' * (len(api_key)-12) if len(api_key) > 12 else '***'}")
    print(f"  model    : {model}")
    print(f"  timeout  : {timeout}s")
    print(f"  vision   : {vl_cfg.get('vision')}")

    if not api_key:
        print("\n[FAIL] api_key 为空，请检查环境变量 DASHSCOPE_API_KEY 是否已设置")
        return
    if not model:
        print("\n[FAIL] model 字段为空")
        return

    # 2. 生成测试图
    print("\n[1/3] 生成测试图...")
    img_bytes = make_test_image()
    img_b64 = base64.b64encode(img_bytes).decode("ascii")
    print(f"  测试图大小: {len(img_bytes)} bytes (640x480)")

    # 3. 构造 messages
    prompt = (
        "这张图里有三个色块：左上角蓝色方块(标注BLUE)、中间红色方块(标注TARGET)、"
        "右下角绿色圆形(标注GREEN)。\n"
        "请找到标注为 TARGET 的红色方块，返回它中心的坐标。\n"
        "图片尺寸 640x480，左上角(0,0)，右下角(639,479)。\n"
        "严格只返回 JSON：{\"found\": true, \"x\": 320, \"y\": 240, \"confidence\": \"high\"}"
    )
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
        ],
    }]

    # 4. 调用 API
    print(f"\n[2/3] 调用 {model} ...")
    import requests
    api_url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "stream": False,
    }

    t0 = time.time()
    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
        elapsed = time.time() - t0
        print(f"  HTTP 状态码: {resp.status_code}  (耗时 {elapsed:.2f}s)")
    except requests.exceptions.Timeout:
        print(f"\n[FAIL] 请求超时（{timeout}s）")
        return
    except requests.exceptions.ConnectionError as e:
        print(f"\n[FAIL] 连接失败: {e}")
        return
    except Exception as e:
        print(f"\n[FAIL] 请求异常: {type(e).__name__}: {e}")
        return

    # 5. 解析响应
    print(f"\n[3/3] 解析响应...")
    if resp.status_code != 200:
        print(f"[FAIL] HTTP {resp.status_code}")
        print(f"响应体: {resp.text[:1000]}")
        # 常见错误提示
        if resp.status_code == 401:
            print("\n>> 401 鉴权失败：检查 DASHSCOPE_API_KEY 是否正确")
        elif resp.status_code == 404:
            print("\n>> 404 路径错误：检查 base_url 是否正确")
            print(f"   当前: {api_url}")
        elif resp.status_code == 400:
            txt = resp.text
            if "model" in txt.lower() and "not" in txt.lower():
                print("\n>> 模型名错误：服务端不接受 model=" + model)
                print("   常见可用名: qwen-vl-plus / qwen-vl-max / qwen2.5-vl-72b-instruct")
        return

    try:
        data = resp.json()
    except Exception as e:
        print(f"[FAIL] 响应不是 JSON: {e}")
        print(f"响应体: {resp.text[:1000]}")
        return

    # 打印完整响应（截断）
    print(f"\n[完整响应片段]")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])

    # 提取 content
    try:
        msg = data["choices"][0]["message"]
        content = msg.get("content") or msg.get("reasoning_content") or ""
        actual_model = data.get("model", "?")
        usage = data.get("usage", {})
        print(f"\n[模型信息]")
        print(f"  请求模型 : {model}")
        print(f"  实际模型 : {actual_model}")
        print(f"  token用量: prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')} total={usage.get('total_tokens')}")
        print(f"\n[模型返回内容]")
        print(content)
    except (KeyError, IndexError) as e:
        print(f"[FAIL] 响应结构异常: {e}")
        print(f"响应: {json.dumps(data, ensure_ascii=False)[:1000]}")
        return

    # 6. 判定
    print("\n" + "=" * 60)
    print("[结论]")
    print(f"  ✅ API 连通正常")
    print(f"  ✅ 模型名 '{model}' 被接受（实际返回: {actual_model}）")
    print(f"  ✅ 视觉输入处理正常（成功响应）")
    print(f"  ✅ 耗时 {elapsed:.2f}s")

    # 尝试解析 JSON
    try:
        # 去除可能的 markdown 包裹
        clean = content.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
        parsed = json.loads(clean)
        print(f"\n[JSON 解析成功]")
        print(f"  found     : {parsed.get('found')}")
        print(f"  x, y      : ({parsed.get('x')}, {parsed.get('y')})  (期望: 320, 240)")
        print(f"  confidence: {parsed.get('confidence')}")
        dx = abs(parsed.get("x", -999) - 320)
        dy = abs(parsed.get("y", -999) - 240)
        print(f"  偏移      : dx={dx}px, dy={dy}px")
        if dx <= 20 and dy <= 20:
            print("  ✅ 定位精度良好（偏移 ≤ 20px）")
        else:
            print("  ⚠️ 定位偏移较大（> 20px）")
    except Exception as e:
        print(f"\n[WARN] 返回内容无法解析为 JSON: {e}")
        print("       但 API 连通性本身是 OK 的，只是模型未严格遵循 JSON 格式")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断")
    except Exception as e:
        print(f"\n[未捕获异常] {type(e).__name__}: {e}")
        traceback.print_exc()
