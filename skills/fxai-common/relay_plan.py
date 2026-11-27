# -*- coding: utf-8 -*-
"""场景续接计划：决定 lines_data 每一行（= 每一段）要不要勾「场景续接」。

口径（2026-11-28 用户定，节点侧已定型）：
- 勾在**接缝的前一段**：本段勾了 → 保存节点把末尾 1 帧写 `<段号+1>.pt`，下一段读它做第 0 帧硬锚定。
- **同一场景跨分镜要续、换场景必须断**：H3 的 K 帧钉整帧，换场景还开续接会把上一段的旧场景带进来
  （2026-11-27 实测：开续接 + 换场景图 → 成片还是上一段的旧房间）。
- **显式优先**：段数据里写 relay / 场景续接 / 续接 就按它来；没写才按「同场景」自动推断。
- 只服务**多参考 / 影视剧场**链；多场景整类不续接（写端显式 False、读端不接段序号），
  调用方自己决定用不用本模块，本模块不做链路判断。

段数据（shot / 行）认这些键：
- 场景标识：`场景` / `场景图` / `scene` / `scene_key`；没有就取 `images`/`素材` 的**最后一张**
  （我们的约定：人物图在前、场景图在后）。
- 显式开关：`relay` / `场景续接` / `启用场景续接` / `续接` / `scene_relay`。

用法：
    import relay_plan
    flags = relay_plan.plan(keys, explicit=[...], auto=True)
    relay_plan.apply(rows, keys)          # rows 就地补第 4 项
"""

_TRUE_WORDS = {"1", "true", "yes", "y", "on", "开", "开启", "是", "要", "续接", "同场景"}

# 显式开关字段（顺序即优先级）
_FLAG_KEYS = ("场景续接", "启用场景续接", "续接", "scene_relay", "relay")
# 场景标识字段
_SCENE_KEYS = ("场景", "场景图", "scene", "scene_key", "scene_image", "背景")
# 图片列表字段
_IMG_KEYS = ("images", "素材", "图片", "imgs")


def to_bool(v):
    """把各种写法的开关值统一成 bool（1/0、"true"、"是"、True…）。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in _TRUE_WORDS
    return bool(v)


def explicit_flag(item):
    """读段数据里的显式续接开关。没写返回 None（= 交给自动推断）。"""
    if not isinstance(item, dict):
        return None
    for k in _FLAG_KEYS:
        if k in item:
            return to_bool(item[k])
    return None


def _images_of(item):
    if isinstance(item, dict):
        for k in _IMG_KEYS:
            v = item.get(k)
            if not v:
                continue
            if isinstance(v, (list, tuple)):
                return [str(x).strip() for x in v if str(x).strip()]
            return [s.strip() for s in str(v).split(",") if s.strip()]
        return []
    if isinstance(item, (list, tuple)):
        return [str(x).strip() for x in item if str(x).strip()]
    if isinstance(item, str):
        return [s.strip() for s in item.split(",") if s.strip()]
    return []


def scene_key(item):
    """取这一段的场景标识。拿不到返回 ""（= 不参与自动续接）。

    - 段数据里有显式场景字段 → 用它（最可靠）；
    - 否则取图片列表**最后一张**（约定：人物图在前、场景图在后）；
    - 图片不足 2 张、或最后一张与第一张相同 → 判不出「哪张是场景」，返回 ""，
      宁可不动（要续就显式写 relay: true），免得把「同一张人物图」当成同场景误续。
    """
    if isinstance(item, dict):
        for k in _SCENE_KEYS:
            v = item.get(k)
            if v:
                return str(v).strip()
    imgs = _images_of(item)
    if len(imgs) < 2 or imgs[-1] == imgs[0]:
        return ""
    return imgs[-1]


def plan(keys, explicit=None, auto=True):
    """算每段要不要勾续接。

    keys: 每段的场景标识（同一条链路上顺序排列，长度 = 段数）。
    explicit: 与 keys 等长的列表，元素 True/False/None；None = 按自动规则。
    auto: False 时只认 explicit（缺省全 False）。

    规则：本段与**下一段**同场景 → 本段勾 True（勾在接缝的前一段）。
    最后一段没有下一段，恒 False。
    """
    keys = list(keys or [])
    n = len(keys)
    flags = [False] * n
    if auto:
        for i in range(n - 1):
            k1, k2 = keys[i], keys[i + 1]
            if k1 and k2 and k1 == k2:
                flags[i] = True
    if explicit:
        for i, v in enumerate(explicit):
            if i < n and v is not None:
                flags[i] = bool(v)
    if n:
        flags[-1] = False   # 最后一段没有下一段，勾了只会白写一个接点
    return flags


def apply(rows, keys, explicit=None, auto=True):
    """把续接标记补成 rows 的第 4 项（就地改，返回同一个 rows）。

    rows 形如 [[时长, 提示词, "图1,图2"], ...]；补完是 [..., True/False]。
    场景加载器（FxAiScreenManager）读第 4 项；老数据只有 3 项按关闭处理，所以这里恒补齐。
    """
    flags = plan(keys, explicit=explicit, auto=auto)
    for r, f in zip(rows, flags):
        while len(r) < 3:
            r.append("")
        if len(r) >= 4:
            r[3] = bool(f)
        else:
            r.append(bool(f))
    return rows
