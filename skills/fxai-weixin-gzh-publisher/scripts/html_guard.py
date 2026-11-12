# -*- coding: utf-8 -*-
"""公众号正文 HTML 守卫：提交前规范化 + 硬校验。

微信图文编辑器在渲染/存稿时会做一轮清洗（2026-11 实测），有三条必须绕开的坑：

1. 剥掉 <pre> 与 white-space，`\\n` 换行被折叠成空格
   → 代码块/提示词整段挤成一行。必须一行一个块级元素，靠 DOM 结构换行。
2. style 属性里的引号会提前闭合属性
   → `font-family:Consolas,Monaco,"Courier New",monospace` 被拆成
     `courier="courier" new="new" px="px"` 这种假属性，样式全废。
3. `word-break:break-all` 会在任意字符处断行
   → `<Subject 2>` 被切成 `Subje / ct 2`，读者以为写错了。

处理策略：
- normalize()：能自动修的当场修（pre 转块、清引号、去 white-space 与 break-all）；
- check()：修不掉的（块内裸 `\\n` 靠空白换行）一律报违规；
- guard()：先 normalize 再 check，有违规抛 HtmlGuardError，调用方据此拒绝提交。
"""
import re

# <pre> 内每行都套这个样式；不外设字号行高，继承外层盒子的即可
_LINE_STYLE = 'margin:0;min-height:1em;word-wrap:break-word;word-break:normal;'
# 原 <pre> 没带样式时的兜底盒子（浅灰卡片，跟技能包正文模板一致）
_DEFAULT_BOX = ('font-size:13px;line-height:1.85;color:#2f3542;background:#f7f8fc;'
                'border:1px solid #e6e9f2;border-radius:8px;padding:16px 18px;margin:0 0 20px 0;'
                'font-family:monospace;')
# 这些声明在微信里要么失效要么惹祸，转块时丢掉（font-size 保留，别用 font 前缀一刀切）
_DROP_DECLS = ('white-space', 'font-family', 'overflow', 'word-break', 'word-wrap')

_PRE_RE = re.compile(r'<pre\b([^>]*)>(.*?)</pre\s*>', re.I | re.S)
_STYLE_ATTR_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.I)
# font-family 值里带引号（如 "Courier New"）→ 整条换 monospace，否则去引号后
# 会留下 Courier New 这种含空格的无引号非法值，字体照样不生效
_FONT_FAMILY_RE = re.compile(r'font-family\s*:\s*([^;}]+)', re.I)
# 引号紧跟在 : ( , 之后的，基本是 CSS 值里的字体名/字面量
_QUOTED_VALUE_RE = re.compile(r'([:(,]\s*)["\']([^"\']{1,60})["\']')


class HtmlGuardError(Exception):
    def __init__(self, violations):
        self.violations = violations
        super().__init__('; '.join(v['problem'] for v in violations))


def _fix_font_family(m):
    """字体族里带引号就整条换 monospace：去引号后含空格的字体名照样不生效。"""
    val = m.group(1)
    if '"' in val or "'" in val:
        return 'font-family:monospace'
    return m.group(0)


def _strip_css_quotes(html):
    """去掉 CSS 值里的引号，使 style 属性完整送达微信。"""
    html = _FONT_FAMILY_RE.sub(_fix_font_family, html)
    return _QUOTED_VALUE_RE.sub(lambda m: m.group(1) + m.group(2), html)


def _box_style(pre_style):
    """把原 <pre> 的样式洗成可用的盒子样式：丢失效声明，补安全折行。"""
    kept = []
    for decl in (pre_style or '').split(';'):
        decl = decl.strip()
        if not decl or ':' not in decl:
            continue
        key = decl.split(':', 1)[0].strip().lower()
        if key.startswith(_DROP_DECLS):
            continue
        kept.append(decl)
    if not kept:
        return _DEFAULT_BOX
    kept.append('font-family:monospace')
    kept.append('word-wrap:break-word')
    kept.append('word-break:normal')
    return ';'.join(kept) + ';'


def _pre_to_blocks(m):
    """<pre> 逐行拆成块级 <section>：换行靠结构，不靠 white-space。"""
    attrs = m.group(1) or ''
    inner = m.group(2) or ''
    ms = _STYLE_ATTR_RE.search(attrs)
    box = _box_style(ms.group(1) if ms else '')

    lines = inner.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    parts = ['<section style="%s">' % box]
    for ln in lines:
        if not ln.strip():
            parts.append('<section style="%s">&nbsp;</section>' % _LINE_STYLE)
            continue
        # 前导空白转 &nbsp; 保住缩进（制表符按 4 个空格算）
        body = ln.replace('\t', '    ')
        lead = len(body) - len(body.lstrip(' '))
        indent = '&nbsp;' * lead
        parts.append('<section style="%s">%s%s</section>'
                     % (_LINE_STYLE, indent, body.lstrip(' ')))
    parts.append('</section>')
    return '\n'.join(parts)


def normalize(html):
    """自动修可修的：返回 (新 html, 修复记录)。"""
    fixes = []

    # 先清引号：否则 style="..." 会在字体名的引号处被截断，后面解析不全
    if _QUOTED_VALUE_RE.search(html):
        html = _strip_css_quotes(html)
        fixes.append('清理 CSS 值里的引号（微信会把带引号的字体名拆成 courier="courier" 这类假属性）')

    if _PRE_RE.search(html):
        html = _PRE_RE.sub(_pre_to_blocks, html)
        fixes.append('把 <pre> 代码块拆成逐行 <section>（微信会剥 white-space，纯文本换行会挤成一行）')

    if re.search(r'white-space\s*:\s*pre', html, re.I):
        html = re.sub(r'white-space\s*:\s*pre[-\w]*\s*;?', '', html, flags=re.I)
        fixes.append('删掉 white-space:pre（换行只认块级元素）')

    if re.search(r'word-break\s*:\s*break-all', html, re.I):
        html = re.sub(r'word-break\s*:\s*break-all\s*;?', 'word-break:normal;', html, flags=re.I)
        fixes.append('word-break:break-all 改成 normal（否则 <Subject 2> 会被切成 Subje/ct 2）')

    return html, fixes


def check(html):
    """硬校验：返回违规列表，空列表 = 可以提交。"""
    v = []

    if re.search(r'<\s*pre\b', html, re.I):
        v.append({'problem': '正文仍含 <pre>',
                  'fix': '微信渲染时会剥掉 white-space，纯文本换行会挤成一行；改成一行一个 <section>'})
    if re.search(r'white-space\s*:\s*pre', html, re.I):
        v.append({'problem': '正文仍依赖 white-space:pre 换行',
                  'fix': '删掉该声明，改用逐行块级元素'})
    if re.search(r'word-break\s*:\s*break-all', html, re.I):
        v.append({'problem': '正文含 word-break:break-all',
                  'fix': '改成 word-wrap:break-word;word-break:normal'})
    if re.search(r'font-family\s*:[^;{}"\']*["\']', html, re.I):
        v.append({'problem': 'CSS 值里还有引号',
                  'fix': 'style 属性里的引号会提前闭合属性，微信会拆出 courier="courier" 这类假属性'})

    # 靠裸 \n 排版的块：块内没有子块却有多处换行，渲染时必定挤成一行
    for m in re.finditer(r'<(section|div|p|td)\b[^>]*>(.*?)</\1\s*>', html, re.I | re.S):
        inner = m.group(2)
        if inner.count('\n') >= 2 and not re.search(r'<(section|div|p|br|img)\b', inner, re.I):
            v.append({'problem': '有块级元素靠裸换行符排版（块内 %d 处 \\n、无子块）' % inner.count('\n'),
                      'fix': '每一行单独包一层 <section>，空行用 &nbsp; 占位'})
            break

    return v


def guard(html):
    """提交前唯一入口：先修再查，违规就抛。返回 (html, fixes)。"""
    fixed, fixes = normalize(html)
    violations = check(fixed)
    if violations:
        raise HtmlGuardError(violations)
    return fixed, fixes


def _selftest():
    """内置回归：python html_guard.py --selftest"""
    broken = ('<section style="white-space:pre-wrap;word-break:break-all;'
              'font-family:Consolas,Monaco,"Courier New",monospace;">'
              '<pre style="margin:0;white-space:pre-wrap;">主体定义\n&lt;Subject 1&gt; 是 &lt;Picture 1&gt; 中的女子。'
              '\n\n摘要\n她抬眼看他。</pre></section>')
    fixed, fixes = guard(broken)
    assert '<pre' not in fixed, '仍残留 <pre>'
    assert 'break-all' not in fixed, '仍残留 break-all'
    assert 'Courier New' not in fixed, '非法字体名未清理'
    # 5 行：主体定义 / Subject1 / 空行占位 / 摘要 / 她抬眼看他
    assert fixed.count('min-height:1em') == 5, '逐行块数不对: %d' % fixed.count('min-height:1em')
    assert '&lt;Subject 1&gt;' in fixed and '&amp;lt;' not in fixed, '转义被破坏'
    assert len(fixes) == 4, '修复项数不对: %d' % len(fixes)

    clean = ('<section style="font-family:monospace;">'
             '<section style="margin:0;">第一行</section>'
             '<section style="margin:0;">&nbsp;</section></section>')
    f2, fx2 = guard(clean)
    assert f2 == clean and not fx2, '合规正文被误改'

    try:
        guard('<section>第一行\n第二行\n第三行</section>')
    except HtmlGuardError:
        pass
    else:
        raise AssertionError('裸换行块应被拦截')

    print('自测通过：3 例（可修 / 合规 / 必拦）')


if __name__ == '__main__':
    import io
    import sys

    if '--selftest' in sys.argv:
        _selftest()
        raise SystemExit(0)

    if len(sys.argv) < 2:
        print('用法: python html_guard.py <正文.html>   或  --selftest')
        raise SystemExit(2)
    src = io.open(sys.argv[1], encoding='utf-8-sig').read()
    out_html, out_fixes = guard(src)
    print('修复项:')
    for f in out_fixes or ['（无需修复）']:
        print('  -', f)
    print('校验: 通过')
    print('长度: %d → %d' % (len(src), len(out_html)))
