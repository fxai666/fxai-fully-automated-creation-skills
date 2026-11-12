# -*- coding: utf-8 -*-
'\n短剧单集生成模板（可分享、无本机绝对路径）。\n\n用法（在新项目目录下跑，指定 --project）：\n  python examples/episode_template.py --project "<项目目录>/新一集"\n\n完整流程（对应 SKILL.md「项目档案存储约定」）：\n 1) 系列根需有 project_def.json（server_subdir + 角色/场景→server_index 映射）；分集向上查找即可。\n 2) 缺角色三视图/场景图 -> 按 project_def 在本系列独立服务端目录（shortdrama_<slug>）生图：\n    gen_project_assets 调 fxai-fourview-gen / fxai-scene-gen，显式传 subdir=server_subdir、num=server_index，\n    服务端精确落到 shortdrama_<slug>/<server_index>.png，与上传/引用同名对齐。本地备份到 series assets/。\n 3) 写 scenes_manifest.json（资源 rel 路径映射，rel 前缀即本系列 server_subdir）\n 4) 如需从本地备份恢复服务端资源，跑 sync_project_to_comfyui（HTTP 覆盖回传；正常生图流程不必，资源已在服务端生成）\n 5) 组装 shots（时长=提示词时间轴，cap 10；图片用相对服务端 fxai/image/ 的 rel 路径，如 shortdrama_xjz/000.png）\n 6) 提交双采版工作流 + 轮询 merged\n 7) 最终成片路径记回 manifest.final_video\n\n注意：机器相关路径（comfy_url）走全局配置 ~/.workbuddy/fxai_global.json，本文件不写死。\n'
















import sys, os, json, argparse, shutil

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL_DIR)
from drama_pipeline import (build_workflow, submit_workflow, wait_for_merged,
                            sync_project_to_comfyui, save_manifest, project_asset_path,
                            estimate_minutes, check_comfyui, load_project_def,
                            server_subdir_of, gen_project_assets, render_episode,
                            acceptance_burn)

# ---------- 1) 本集角色/场景定义（按项目填写）----------
CHAR_VIEWS = {
    "juzi": "圆滚滚橘猫，啤酒肚，圆框眼镜，浅蓝衬衫",
    "gou": "德国牧羊犬，深色西装领带",
}
SCENES = {
    "scene_office": "现代开放式办公室，明亮忙碌",
    "scene_tuanjian": "户外团建场地，草坪帐篷烧烤架，轻松愉快",
}


# ---------- 2) 缺什么补什么：按 project_def 在本系列独立服务端目录生图 ----------
def ensure_assets(project_root):
    """读 series 的 project_def.json：对每个缺本地备份的角色/场景，在服务器 shortdrama_<slug>/
    下以 num=server_index 精确生成（与上传/引用同名对齐）；返回 manifest（rel 前缀=server_subdir）。"""
    series_root, pd = load_project_def(project_root)
    if not pd:
        print("⚠️ 未找到 project_def.json（向上查找失败）。请先在本系列根建 project_def.json。")
        return {}
    sd = server_subdir_of(project_root)
    # 按 project_def 在服务器生图（缺本地备份才生）；精确落到 shortdrama_<slug>/<server_index>.png
    gen_project_assets(project_root)
    manifest = {}
    for key, info in pd.get("characters", {}).items():
        manifest[f"char_{key}"] = f"{sd}/{int(info.get('server_index', 0)):03d}.png"
    for key, info in pd.get("scenes", {}).items():
        manifest[f"scene_{key}"] = f"{sd}/{int(info.get('server_index', 0)):03d}.png"
    return manifest


# ---------- 3) 本集 shots（时长=提示词时间轴，cap 10；图片用 rel 路径）----------
def build_shots(manifest):
    # TODO: 按本集分镜填写 6 段式提示词；对白用 <d>[Chinese]台词</d>（见 SKILL.md 字幕规范）
    p1 = """主体定义
<Subject 1> 是 <Picture 1> 中的角色。
<Subject 2> 是 <Picture 2> 中的场景。

摘要
[reference generation] 目标视频展示场景，角色说话。

延续分析
<Subject 1> (出现在 [Shot 1]): fully_preserved - 保留人物身份、发型发色、服装样式与颜色、裤装、鞋履。
<Subject 2> (出现在 [Shot 1]): fully_preserved - 保留场景的建筑、陈设与物品。

详细描述
目标视频是场景，自然光，氛围轻松。
[Shot 1] 中景镜头，固定机位，<Subject 1> (S1) 站在画面正中，<Subject 2> 在身后铺开。
<Subject 1> (S1) 对着画面左侧画外沉声道：<d>[Chinese] 第一句台词。</d>。
At 00:02.500, 静场。
At 00:03.500, <Subject 1> (S1) 对着画面左侧画外接着说：<d>[Chinese] 第二句台词。</d>。

整体音效
环境音，轻松音效。

非叙事音乐
轻快的音乐，节奏明快，音量适中。"""
    return [
        {"duration": 6, "prompt": p1,
         "images": [manifest["char_juzi"], manifest["scene_office"]]},
        # 继续添加本集其余镜头 ...
    ]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="短剧单集生成模板")
    ap.add_argument("--project", required=True, help="项目档案根目录")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--audio-mode", default="系统生成", choices=["系统生成", "音色参考", "原音频"])
    ap.add_argument("--ep", type=int, required=True, help="集号（如 8），用于 ep{N}_pid.txt / manifest 字段")
    args = ap.parse_args()

    if not check_comfyui():
        print("⚠️ ComfyUI 未在线，请先启动凤希本地AI控制台。")
        sys.exit(1)

    project_root = os.path.abspath(args.project)
    os.makedirs(os.path.join(project_root, "assets"), exist_ok=True)

    manifest = ensure_assets(project_root)
    manifest_path = save_manifest(project_root, manifest)
    print("manifest:", manifest_path, json.dumps(manifest, ensure_ascii=False))

    shots = build_shots(manifest)
    total = sum(s["duration"] for s in shots)
    print(f"总时长 {total}s，预计约 {estimate_minutes(total)} 分钟（16G 显存）")

    # 按需回传：本地有备份则从 HTTP 覆盖上传到服务端（恢复场景用；正常生图流程资源已在服务端）
    rels = [img for sh in shots for img in sh["images"]]
    missing = sync_project_to_comfyui(project_root, rels)
    if missing:
        print("⚠️ 以下 rel 本地备份缺失（服务端已生成则不影响；如需恢复请先下载）：", missing)

    # 台词真值对齐硬门禁（铁律：台词一字不删；一句装不下拆成独立一镜，禁用精简）
    # 分镜脚本约定放集目录、名为「完整分镜脚本.md」
    story_md = os.path.join(project_root, "完整分镜脚本.md")
    if os.path.isfile(story_md):
        sys.path.insert(0, SKILL_DIR)
        from check_lines_against_storyboard import check_shots_against_storyboard
        ok, problems, rn, sn = check_shots_against_storyboard(shots, story_md)
        if not ok:
            print("✗ 台词真值对齐失败（须拆分镜，禁用精简）—— 问题 %d 条：" % len(problems))
            for p in problems:
                print("  -", p)
            sys.exit(1)
        print("✓ 台词真值对齐：run %d 条 vs 分镜 %d 条，逐字一致，可提交。" % (rn, sn))
    else:
        print("⚠️ 未找到「完整分镜脚本.md」，跳过台词真值对齐（强烈建议补分镜脚本后启用）。")

    # 提交+等待合并+写 ep{N}_pid.txt + 写回 manifest（world_constraint 自动从 project_def 取）
    pid, merged = render_episode(project_root, args.ep, shots,
                                 audio_mode=args.audio_mode, width=args.width, height=args.height)
    print("PROMPT_ID", pid)
    print("MERGED", merged if merged else "TIMEOUT_90MIN")
    if not merged:
        print("⚠️ 渲染超时，请检查 ComfyUI 队列。")
        sys.exit(1)
    # 验收+烧录：drama_pipeline.acceptance_burn(project_root, ep, shots, title_hint="集名")
    print("渲染完成。后续验收烧录请调用：acceptance_burn(project_root, %d, shots, title_hint='集名')" % args.ep)
