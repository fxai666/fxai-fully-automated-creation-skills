# -*- coding: utf-8 -*-
"""永久素材索引：本地图片文件 <-> 微信永久素材 media_id/url 的映射账本。

上传成功后记账，建稿上传前先查账本：命中直接复用，同一张图只上传一次。

索引文件位置
------------
1) 环境变量 `WX_MATERIAL_MAP` 指定了就用它（全局索引，跨项目共用）；
2) 否则默认 `<wx_api.py 所在目录>/material_map.json`；
3) 记录时若知道图所在目录，同时写一份到 `<目录>/素材索引.json`（项目级）。
两处都读，同一文件项目级优先。

数据结构
--------
项目级索引（`<图片目录>/素材索引.json`）key = 文件名：

{
  "schema": 2,
  "updated": "2026-10-01T07:25:00",
  "items": {
    "八张照片-书廊.jpg": {
      "media_id": "kiMq...",
      "url": "http://mmecoa.qpic.cn/...",
      "size": "1056x1440",
      "bytes": 415308,
      "uploaded_at": "2026-10-01T07:20:58",
      "verified": true,        # true=本次上传确认过；false=未逐张校验，不可当准
      "note": ""
    }
  }
}

全局账本（`<scripts>/material_map.json`）key = 绝对路径。

对外 `load()/lookup()/ids_for()` 返回的 items 一律用绝对路径做 key。
"""
from __future__ import annotations
import json, os, time, sys
from pathlib import Path

SCHEMA = 2
_FOLDER_TAG = "素材索引.json"
_GLOBAL_NAME = "material_map.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _abs(fp) -> str:
    """绝对路径字符串（对外统一用这个做 key）。"""
    return str(Path(fp).resolve())


def _name(fp) -> str:
    """文件名（写进项目级索引的 key）。"""
    return Path(fp).name


def _rel(fp, base) -> str:
    """把文件转成相对 base 的 posix 路径；跨目录（无共同基准）则退化为绝对路径。"""
    try:
        return Path(fp).resolve().relative_to(Path(base).resolve()).as_posix()
    except ValueError:
        return _abs(fp)


def _entry(fp: Path, media_id, url, size, verified, note, extra) -> dict:
    e = {
        "media_id": media_id,
        "url": url or "",
        "size": size or "",
        "bytes": fp.stat().st_size if fp.exists() else 0,
        "uploaded_at": _now(),
        "verified": bool(verified),
        "note": note,
    }
    if extra:
        e.update(extra)
    return e


def _atomic_write(path: Path, data: dict) -> None:
    """UTF-8 原子写：先写同目录 tmp 再 replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def global_map_path() -> Path:
    env = os.environ.get("WX_MATERIAL_MAP")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / _GLOBAL_NAME


def project_map_path(image_dir) -> Path:
    d = Path(image_dir)
    return d / _FOLDER_TAG


def _read(path: Path) -> dict:
    if not path.exists():
        return {"schema": SCHEMA, "updated": "", "items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": SCHEMA, "updated": "", "items": {}}
    data.setdefault("items", {})
    return data


def _read_with_base(path: Path) -> dict:
    """读一本账本，key 统一还原成绝对路径（相对 key × 该本所在目录 = 绝对）。对外 items 永远以绝对路径为 key。"""
    data = _read(path)
    out = {}
    for k, v in data.get("items", {}).items():
        if not v or not v.get("media_id"):
            continue
        fp = Path(k)
        if not fp.is_absolute():
            fp = path.parent / fp
        out[_abs(fp)] = {**v, "_rel": k}
    out["_updated"] = data.get("updated", "")
    return out


def load(image_dir=None) -> dict:
    """读账本：全局 + 项目级合并（同一文件项目级优先）。对外 items 的 key 一律绝对路径。"""
    merged = {"schema": SCHEMA, "updated": "", "items": {}}
    gp = global_map_path()
    if gp.exists():
        g = _read_with_base(gp)
        merged["items"].update({k: v for k, v in g.items() if k != "_updated"})
        merged["updated"] = g.get("_updated", "") or merged["updated"]
    if image_dir:
        pp = project_map_path(image_dir)
        if pp.exists():
            p = _read_with_base(pp)
            merged["items"].update({k: v for k, v in p.items() if k != "_updated"})
            merged["updated"] = p.get("_updated", "") or merged["updated"]
    return merged


def lookup(local_path, image_dir=None) -> dict | None:
    """按本地文件路径查是否已上传过。返回条目 dict 或 None。

    绝对路径 / 相对路径 / 带 `./` 的写法都能命中（统一 resolve 后比对）。
    """
    fp = Path(local_path).resolve()
    m = load(image_dir if image_dir else fp.parent)
    return m["items"].get(_abs(fp))


def record(local_path, media_id, url="", image_dir=None, verified=True, size="", note="", extra=None) -> dict:
    """上传成功后记账（全局 + 项目级各写一份）：项目级 key = 文件名，全局 key = 绝对路径。"""
    if not media_id:
        return {}
    fp = Path(local_path).resolve()
    abs_key = _abs(fp)
    entry = _entry(fp, media_id, url, size, verified, note, extra)

    # 项目级 key 恒为文件名：用 _name()（_rel() 跨目录会退化成绝对路径）
    targets = []
    pdir = Path(image_dir).resolve() if image_dir else fp.parent
    pm = project_map_path(pdir)
    key_proj = _name(fp)
    if pm != global_map_path():
        targets.append((pm, key_proj))
    if pm != project_map_path(fp.parent):
        targets.append((project_map_path(fp.parent), _name(fp)))
    targets.append((global_map_path(), abs_key))

    seen = set()
    for path, key in targets:
        if path is None or path in seen:
            continue
        seen.add(path)
        data = _read(path)
        data["items"][key] = entry
        data["updated"] = entry["uploaded_at"]
        _atomic_write(path, data)
    return entry


def revert(local_path) -> None:
    """删掉某条映射（例如素材作废时用它，防止下次误复用）。"""
    fp = Path(local_path).resolve()
    abs_key = _abs(fp)
    for path in {global_map_path(), project_map_path(fp.parent)}:
        if not path.exists():
            continue
        data = _read(path)
        items = data.get("items", {})
        key = abs_key if abs_key in items else _rel(fp, path.parent)
        if items.pop(key, None) is not None:
            data["updated"] = _now()
            _atomic_write(path, data)


def ids_for(local_paths) -> list:
    """给一串本地路径取 media_id 列表：命中的复用、没命中的返回 None 占位（占位是否传给接口由调用方定）。"""
    m = load(Path(local_paths[0]).resolve().parent if local_paths else None)
    out = []
    for p in local_paths:
        e = m["items"].get(_abs(p))
        out.append(e["media_id"] if e else None)
    return out


def migrate(image_dir) -> dict:
    """把绝对路径 key 的账本就地改写成文件名 key，幂等。全局账本 key 是绝对路径，不动。"""
    path = project_map_path(image_dir)
    if not path.exists():
        return {"migrated": 0, "file": str(path)}
    data = _read(path)
    base = path.parent
    items = data.get("items", {})
    changed, final = 0, {}
    for k, v in items.items():
        new_key = _rel(base / k, base) if Path(k).is_absolute() else k
        final[new_key] = v
        changed += 1 if new_key != k else 0
    data["items"] = final
    data["schema"] = SCHEMA
    data["updated"] = data.get("updated") or _now()
    _atomic_write(path, data)
    return {"migrated": changed, "total": len(final), "file": str(path)}


def paths_for(image_dir, only_verified=False) -> list:
    """列出账本里所有本地文件的绝对路径（用于自动补录新图/批量校验）。"""
    m = load(image_dir)
    out = []
    for k, v in m["items"].items():
        if only_verified and not v.get("verified"):
            continue
        p = Path(k)
        out.append(p if p.exists() else None)
    return [p for p in out if p]


def dump(image_dir=None, only_verified=False) -> str:
    """打印账本，方便一眼看清哪些图已上传、还差哪几张。"""
    m = load(image_dir)
    items = m["items"]
    if only_verified:
        items = {k: v for k, v in items.items() if v.get("verified")}
    if not items:
        return "素材索引为空（还没上传过任何图）"
    rows = []
    for k, v in sorted(items.items()):
        mark = "OK " if v.get("verified") else "?? "
        rows.append(f" [{mark}]{v.get('size', ''):>11} {v['media_id'][-8:]} {Path(k).name}")
    return f"素材索引共 {len(items)} 条：\n" + "\n".join(rows)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg == "--migrate":
        print(json.dumps(migrate(arg and sys.argv[2] or "."), ensure_ascii=False))
    elif arg == "--paths":
        for p in paths_for(arg and sys.argv[2] or None):
            print(p)
    else:
        print(dump(arg))
