# ref 取值解析速查

**何时读**：传参考图前查 role / 关键字到服务端 rel 的映射时

- `None` / 空 / 假值            → `""`（空场景：不含人物参考；MV 多参考 multiref 用）
- `"woman"` / `"女"` / `"女性"`  → `avatar/000.png`（`REF_WOMAN`）
- `"man"` / `"男"` / `"男性"`    → `avatar/001.png`（`REF_MAN`）
- `"both"` / `"couple"` / `"双人"` / `"男女"` → `avatar/001.png,avatar/000.png`
- 其它任意非空字符串            → 原样返回（自定义参考图：角色三 / 四视图相对路径，或单张五官参考图）
