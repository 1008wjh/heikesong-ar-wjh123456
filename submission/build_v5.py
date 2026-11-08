# -*- coding: utf-8 -*-
"""Build the 2-page MemoryLens submission brief (DOCX) - v5."""
import os
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_BREAK, WD_LINE_SPACING
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

ROOT = r"E:\ar-memory"
OUT = os.path.join(ROOT, "submission")
os.makedirs(OUT, exist_ok=True)

INK = RGBColor(0x17, 0x22, 0x1D)
BODY = RGBColor(0x2F, 0x3A, 0x34)
MUTED = RGBColor(0x6C, 0x7A, 0x73)
GREEN = RGBColor(0x0C, 0x8F, 0x5F)
ACCENT = RGBColor(0x12, 0xB7, 0x6A)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
MINT = RGBColor(0xB9, 0xF0, 0xD5)
AMBER = RGBColor(0xB5, 0x47, 0x08)
RED = RGBColor(0xB4, 0x23, 0x18)
BLUE = RGBColor(0x17, 0x5C, 0xD3)
DARK = "0B2A21"
LIGHT = "EEF8F2"
BORDER = "D8E5DD"
PAGE_W = 18.4

FONT = "Segoe UI"
EA = "微软雅黑"


def _ea(run):
    rPr = run._element.get_or_add_rPr()
    rf = rPr.get_or_add_rFonts()
    rf.set(qn("w:eastAsia"), EA)


def R(p, text, size=8.5, bold=False, color=BODY, font=FONT, italic=False, tracking=None):
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = color
    r.font.name = font
    _ea(r)
    if tracking:
        rPr = r._element.get_or_add_rPr()
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:val"), str(int(tracking * 20)))
        rPr.append(sp)
    return r


def P(container, text="", size=9.0, bold=False, color=BODY, align=None,
      before=0, after=0, line=12.2, left=0, first=0, keep=False):
    p = container.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = Pt(line)
    pf.left_indent = Cm(left)
    pf.first_line_indent = Cm(first)
    pf.keep_with_next = keep
    if align is not None:
        p.alignment = align
    if text:
        R(p, text, size=size, bold=bold, color=color)
    return p


def bullet(container, text, size=8.9, line=12.2, color=BODY, before=0.5, after=0):
    p = container.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = Pt(line)
    pf.left_indent = Cm(0.34)
    pf.first_line_indent = Cm(-0.34)
    R(p, "▪ ", size=size, color=ACCENT, bold=True)
    R(p, text, size=size, color=color)
    return p


def shade(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")):
        tcPr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)


def cell_margins(cell, top=0.1, bottom=0.1, left=0.12, right=0.12):
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:tcMar")):
        tcPr.remove(old)
    mar = OxmlElement("w:tcMar")
    for side, val in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(int(val * 567)))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tcPr.append(mar)


def table(rows, cols, widths, border=None, border_sz=4, align=WD_TABLE_ALIGNMENT.CENTER):
    t = doc.add_table(rows=rows, cols=cols)
    t.alignment = align
    t.autofit = False
    tblPr = t._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "single" if border else "nil")
        el.set(qn("w:sz"), str(border_sz))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), border or "auto")
        borders.append(el)
    tblPr.append(borders)
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:type"), "dxa")
    tblW.set(qn("w:w"), str(int(sum(widths) / 2.54 * 1440)))
    tblPr.append(tblW)
    ind = OxmlElement("w:tblInd")
    ind.set(qn("w:w"), "0")
    ind.set(qn("w:type"), "dxa")
    tblPr.append(ind)
    for i, w in enumerate(widths):
        t.columns[i].width = Cm(w)
        for c in t.columns[i].cells:
            c.width = Cm(w)
    for row in t.rows:
        trPr = row._tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:cantSplit"))
    return t


def cp(cell, text="", size=7.5, bold=False, color=BODY, align=None,
       before=0, after=0, line=10.5):
    first = cell.paragraphs[0]
    if len(cell.paragraphs) == 1 and not first.runs:
        p = first
    else:
        p = cell.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = Pt(line)
    if align is not None:
        p.alignment = align
    if text:
        R(p, text, size=size, bold=bold, color=color)
    return p


def spacer(h=2.0):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = Pt(h)
    p.add_run("").font.size = Pt(1)


def section_head(num, title, tag):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(5.5)
    pf.space_after = Pt(2.2)
    pf.line_spacing = Pt(16)
    pf.keep_with_next = True
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), BORDER)
    bdr.append(bottom)
    pPr.append(bdr)
    p.paragraph_format.tab_stops.add_tab_stop(Cm(PAGE_W), WD_TAB_ALIGNMENT.RIGHT)
    R(p, num, 12.8, True, GREEN)
    R(p, "  " + title, 11.8, True, INK)
    R(p, "\t" + tag, 6.9, False, MUTED)
    return p


def add_picture(cell, path, width_cm, caption=None):
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.add_run().add_picture(path, width=Cm(width_cm))
    if caption:
        c = cell.add_paragraph()
        c.paragraph_format.space_before = Pt(0)
        c.paragraph_format.space_after = Pt(0)
        c.paragraph_format.line_spacing = Pt(8.4)
        R(c, caption, 6.5, False, MUTED)


def box(fill, border_color=None, left_color=None, left_sz=18):
    t = table(1, 1, [PAGE_W], border=border_color, border_sz=4)
    c = t.cell(0, 0)
    shade(c, fill)
    cell_margins(c, top=0.1, bottom=0.1, left=0.18, right=0.18)
    if left_color:
        tcPr = c._tc.get_or_add_tcPr()
        tb = OxmlElement("w:tcBorders")
        el = OxmlElement("w:left")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(left_sz))
        el.set(qn("w:color"), left_color)
        tb.append(el)
        tcPr.append(tb)
    return t, c


def field(par, instr, placeholder="1"):
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), instr)
    r = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "13")
    rPr.append(sz)
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "8A9A91")
    rPr.append(color)
    r.append(rPr)
    t = OxmlElement("w:t")
    t.text = placeholder
    r.append(t)
    fld.append(r)
    par._p.append(fld)


# ---------------------------------------------------------------- document
doc = Document()
sec = doc.sections[0]
sec.page_width = Cm(21)
sec.page_height = Cm(29.7)
sec.top_margin = Cm(0.9)
sec.bottom_margin = Cm(0.8)
sec.left_margin = Cm(1.3)
sec.right_margin = Cm(1.3)
sec.header_distance = Cm(0.4)
sec.footer_distance = Cm(0.3)

normal = doc.styles["Normal"]
normal.font.name = FONT
normal.font.size = Pt(9.0)
normal.font.color.rgb = BODY
rPr = normal.element.get_or_add_rPr()
rf = rPr.get_or_add_rFonts()
rf.set(qn("w:ascii"), FONT)
rf.set(qn("w:hAnsi"), FONT)
rf.set(qn("w:eastAsia"), EA)
normal.paragraph_format.space_before = Pt(0)
normal.paragraph_format.space_after = Pt(0)
normal.paragraph_format.line_spacing = Pt(12.2)

# footer
f = sec.footer.paragraphs[0]
f.paragraph_format.space_before = Pt(0)
f.paragraph_format.space_after = Pt(0)
f.paragraph_format.line_spacing = Pt(8)
f.paragraph_format.tab_stops.add_tab_stop(Cm(8.255), WD_TAB_ALIGNMENT.CLEAR)
f.paragraph_format.tab_stops.add_tab_stop(Cm(16.51), WD_TAB_ALIGNMENT.CLEAR)
f.paragraph_format.tab_stops.add_tab_stop(Cm(PAGE_W), WD_TAB_ALIGNMENT.RIGHT)
pPr = f._p.get_or_add_pPr()
bdr = OxmlElement("w:pBdr")
top = OxmlElement("w:top")
top.set(qn("w:val"), "single")
top.set(qn("w:sz"), "4")
top.set(qn("w:space"), "3")
top.set(qn("w:color"), BORDER)
bdr.append(top)
pPr.append(bdr)
R(f, "MemoryLens 记忆眼镜  ·  v3.1.0 (66)  ·  JUNCTION CHINA 2026 智能穿戴赛道", 6.4, False, MUTED)
R(f, "\t第 ", 6.4, False, MUTED)
field(f, " PAGE ", "1")
R(f, " 页 / 共 ", 6.4, False, MUTED)
field(f, " NUMPAGES ", "2")
R(f, " 页", 6.4, False, MUTED)

# ---------------------------------------------------------------- masthead
hm = table(1, 1, [PAGE_W])
hc = hm.cell(0, 0)
shade(hc, DARK)
cell_margins(hc, top=0.3, bottom=0.28, left=0.5, right=0.5)
p = cp(hc, "", line=9.5)
R(p, "JUNCTION CHINA 2026  ·  智能穿戴赛道  ·  亮亮视野 LEION", 7.0, True, MINT, tracking=0.4)
p = cp(hc, "", line=29, before=4)
R(p, "MemoryLens", 24, True, WHITE)
R(p, "  记忆眼镜", 15, True, RGBColor(0xD8, 0xF5, 0xE6))
p = cp(hc, "", line=14.5, before=1)
R(p, "把一次见面，变成可继续的关系。", 10.0, True, RGBColor(0xC9, 0xF2, 0xDD))
p = cp(hc, "", line=11.5, before=3)
R(p, "15s", 8.6, True, WHITE)
R(p, " 首片认人    ·    ", 7.4, False, MINT)
R(p, "3 条", 8.6, True, WHITE)
R(p, " 可说的话    ·    ", 7.4, False, MINT)
R(p, "0.998s", 8.6, True, WHITE)
R(p, " 提词 P50", 7.4, False, MINT)
p = cp(hc, "", line=9.5, before=1)
R(p, "作品说明  /  ARUI · 自动化 · 多模态  /  2026-11-08", 6.6, False, RGBColor(0x9D, 0xC4, 0xB3))

# accent rule
spacer(1.2)
rule = table(1, 1, [PAGE_W])
rc = rule.cell(0, 0)
shade(rc, "12B76A")
cell_margins(rc, top=0, bottom=0, left=0, right=0)
rc.paragraphs[0].paragraph_format.line_spacing = Pt(1.0)
rc.paragraphs[0].add_run("").font.size = Pt(0.8)

# ---------------------------------------------------------------- 01
section_head("01", "一次见面，为什么总是停在寒暄", "场景价值与创新 · 10 分")
p = P(doc, "", before=1, after=2, line=13)
R(p, "商业场合的第一次见面，往往决定还有没有第二次。名片留下联系方式，MemoryLens 留下下一次开口的理由。", 9.4, True, INK)

P(doc, "《孙子兵法》讲知己知彼，百战不殆。放回日常社交，这句话并不指向算计，而指向准备：知道对方是谁、上次聊到哪、自己答应过什么。准备充分的人，更容易掌握对话的主动权。", 8.5, False, BODY, after=1.2, line=11.7)
P(doc, "关系会随时间产生复利。多数人的关系数据散落在通讯录、聊天记录和记忆里，见面时无法调用。MemoryLens 把声纹身份、对话要点、承诺与偏好，整理成一份本人可控的关系记忆库。", 8.5, False, BODY, after=2.0, line=11.7)

# users
ut = table(1, 2, [PAGE_W / 2] * 2, border="FFFFFF", border_sz=10)
users = [
    ("慢热、在社交场合容易紧张的人", "降低开口成本：不用在几秒内搜索名字、上次话题和承诺。"),
    ("销售、创始人、展会与校友会常客", "掌握对话节奏：主动提问、主动兑现、主动推进。"),
]
for i, (h, txt) in enumerate(users):
    c = ut.cell(0, i)
    shade(c, "F7FBF9")
    cell_margins(c, top=0.08, bottom=0.08, left=0.14, right=0.14)
    p = cp(c, "", line=10.6)
    R(p, h, 7.6, True, GREEN)
    p = cp(c, txt, 7.2, False, BODY, line=9.7)

# differentiation
spacer(2)
dt, dc = box("F2F8F5", border_color=BORDER, left_color="12B76A", left_sz=20)
p = cp(dc, "", line=10.7)
R(p, "与现有方案的区别：", 7.7, True, GREEN)
R(p, "会议转写记录内容，不解决下一次开口；名片 / CRM 事后录入，对话中无法调用；通用助手不掌握这个人的档案。", 7.5, False, BODY)
p = cp(dc, "", line=10.7, before=0.5)
R(p, "MemoryLens 在对话中，按人、按档案给出下一句。", 7.7, True, INK)

p = P(doc, "", before=1.4, after=1.4, line=11.0)
R(p, "为什么是眼镜：", 7.7, True, GREEN)
R(p, "手机也能查，但低头、解锁、滑屏会打断对话。眼镜是抬头就能看、双手不停的屏幕；声纹是不用正脸、不挑光线的身份入口。平台没有开放摄像头，本作品以声纹为主入口，不做人脸识别。", 7.5, False, BODY)

p = P(doc, "", before=0.6, after=1.4, line=11.0)
R(p, "为什么现在：", 7.7, True, GREEN)
R(p, "AR 眼镜有了可用的显示与麦克风；声纹可在本地 20–50ms 完成；大模型能把档案与当下对话拼成可说的话。", 7.5, False, BODY)

# data layers
spacer(2)
p = P(doc, "", before=0, after=1.2, line=10.2, keep=True)
R(p, "关系数据的三层结构", 8.4, True, INK)
R(p, "   身份、记忆、关系，互相支撑", 6.7, False, MUTED)
lt = table(1, 3, [PAGE_W / 3] * 3, border="FFFFFF", border_sz=10)
layers = [
    ("身份层", "声纹", "确认是谁"),
    ("记忆层", "要点 · 承诺 · 偏好", "确认聊过什么"),
    ("关系层", "上次见面 · 待办 · 跟进", "确认关系走到哪"),
]
for i, (name, what, why) in enumerate(layers):
    c = lt.cell(0, i)
    shade(c, "F7FBF9")
    cell_margins(c, top=0.08, bottom=0.08, left=0.14, right=0.14)
    p = cp(c, "", line=10.2)
    R(p, name, 7.6, True, GREEN)
    R(p, "  " + what, 6.8, False, BODY)
    p = cp(c, why, 6.3, False, MUTED, line=8.2)

p = P(doc, "三层数据存在本机，可查看、修改、删除；音频只在本机转写，模型只接收文本；对方同意是前提。", 6.6, False, MUTED, before=1, after=0.5, line=8.4)

# ---------------------------------------------------------------- 02
section_head("02", "一眼能读完的 ARUI", "ARUI 设计与交互 · 25 分")
t = table(1, 2, [11.9, 6.3])
lc, rc = t.cell(0, 0), t.cell(0, 1)
cell_margins(lc, 0, 0, 0, 0.15)
cell_margins(rc, 0, 0, 0, 0)
lc.vertical_alignment = WD_ALIGN_VERTICAL.TOP
rc.vertical_alignment = WD_ALIGN_VERTICAL.TOP

p = cp(lc, "", line=12.2)
R(p, "640×480 单色，单行 Text 约 110px。", 8.4, True, INK)
R(p, " HUD 采用窄列：每行 ≤6 个汉字、最多 6 行、超长轮播；长内容不溢出，也不遮视线。", 8.1, False, BODY)
p = cp(lc, "", line=11.4, before=1.2)
R(p, "层级：", 7.7, True, GREEN)
R(p, "状态 / 计时 → 姓名 → 身份关系 → 2–3 行记忆 → 按键提示。第一眼看到的是人，不是菜单。", 8.0, False, BODY)
p = cp(lc, "", line=11.4, before=1.2)
R(p, "低干扰：", 7.7, True, GREEN)
R(p, "提词只占下半屏，20 秒自动收起，录音不中断；待命不显示任何人名；多人场景不混入单人档案。", 8.0, False, BODY)

add_picture(rc, os.path.join(ROOT, "test", "hud_preview.png"), 6.05,
            "HUD 设计稿：640×480 单色，一眼一人")

spacer(1.5)
states = [
    ("待命", "按一下开始听", "F3F6F4", RGBColor(0x3F, 0x4F, 0x47)),
    ("处理中", "在想… + 计时", "EAF2FF", BLUE),
    ("待确认", "是他吗？ + 候选", "FFF6E5", AMBER),
    ("完成", "卡片 + 提词", "EAF9F1", GREEN),
    ("异常", "没声音 · 检查麦克风", "FDECEA", RED),
]
st = table(1, 5, [PAGE_W / 5] * 5, border="FFFFFF", border_sz=8)
for i, (name, desc, bg, col) in enumerate(states):
    c = st.cell(0, i)
    shade(c, bg)
    cell_margins(c, top=0.07, bottom=0.07, left=0.09, right=0.09)
    p = cp(c, "", line=9.8)
    R(p, "● ", 7.5, True, col)
    R(p, name, 7.8, True, col)
    p = cp(c, desc, 6.3, False, MUTED, line=8.2)

spacer(1.5)
p = P(doc, "", before=0, after=1.0, line=9.5, keep=True)
R(p, "两次连续调整", 8.2, True, INK)
R(p, "   同一 session 内完成，ARUI 自动更新", 6.6, False, MUTED)
adj = [
    ("只看待办", "只剩日期 / 跟进", "内容"),
    ("优先日期", "日期置顶 + focus", "重点 / 布局"),
    ("只保留日期", "3 行 → 1 行", "精简"),
    ("显示全部", "还原 3 行", "内容"),
]
at = table(1, 4, [PAGE_W / 4] * 4, border="FFFFFF", border_sz=8)
for i, (a, b, c3) in enumerate(adj):
    c = at.cell(0, i)
    shade(c, "F7FBF9")
    cell_margins(c, top=0.07, bottom=0.07, left=0.1, right=0.1)
    p = cp(c, "", line=9.6)
    R(p, "「" + a + "」", 7.4, True, INK)
    p = cp(c, b, 6.8, False, BODY, line=8.6)
    p = cp(c, c3, 6.0, True, GREEN, line=7.8)

# typical flow
spacer(3)
p = P(doc, "", before=0, after=1.2, line=10.6, keep=True)
R(p, "一次典型使用", 8.5, True, INK)
R(p, "   从戴上到归档，全程只用确认 / 返回", 6.8, False, MUTED)
flow = [
    ("01", "戴上 / 选单人", "按确认，进入单人模式"),
    ("02", "说话 / 15s 首片", "镜片显示「在听」"),
    ("03", "认人 / 档案上镜", "姓名、关系、上次记忆"),
    ("04", "确认 / 3 条提词", "直接能说出口的话"),
    ("05", "返回 / 自动归档", "要点写回人脉库"),
]
ft = table(1, 5, [PAGE_W / 5] * 5, border="FFFFFF", border_sz=8)
for i, (n, h, d) in enumerate(flow):
    c = ft.cell(0, i)
    shade(c, "F7FBF9")
    cell_margins(c, top=0.08, bottom=0.08, left=0.1, right=0.1)
    p = cp(c, "", line=9.8)
    R(p, n + "  ", 7.2, True, ACCENT)
    R(p, h, 7.6, True, INK)
    p = cp(c, d, 6.4, False, MUTED, line=8.3)

spacer(3)
p = P(doc, "", before=0, after=1.0, line=10.4)
R(p, "已完成：", 7.6, True, GREEN)
R(p, "声纹认人 · 三条提词 · 五状态 HUD · 文本指令 · 会议模式 · 离线补传 · 控制台 · 8 项验收", 7.4, False, BODY)

p = P(doc, "", before=0, after=0, line=10.4, align=WD_ALIGN_PARAGRAPH.RIGHT)
R(p, "让眼镜记住，让人专心聊天。", 8.8, False, MUTED, italic=True)

# ---------------------------------------------------------------- page 2
p = doc.add_paragraph()
p.add_run().add_break(WD_BREAK.PAGE)
p.paragraph_format.space_before = Pt(0)
p.paragraph_format.space_after = Pt(0)
p.paragraph_format.line_spacing = Pt(1)

# 03
section_head("03", "从听到提词：智能体怎么工作", "自动化与智能体执行 · 25 分")
nodes = [
    ("ingest", "转写 / 切片"),
    ("route", "单人 / 多人"),
    ("identify", "声纹 → 语义"),
    ("extract", "抽要点"),
    ("recommend", "3 条提词"),
    ("render", "五状态"),
    ("commit", "入库"),
]
nt = table(1, 7, [PAGE_W / 7] * 7, border="FFFFFF", border_sz=7)
for i, (n, fn) in enumerate(nodes):
    c = nt.cell(0, i)
    shade(c, "F4FAF6")
    cell_margins(c, top=0.08, bottom=0.07, left=0.07, right=0.07)
    tcPr = c._tc.get_or_add_tcPr()
    tb = OxmlElement("w:tcBorders")
    el = OxmlElement("w:top")
    el.set(qn("w:val"), "single")
    el.set(qn("w:sz"), "14")
    el.set(qn("w:color"), "12B76A")
    tb.append(el)
    tcPr.append(tb)
    p = cp(c, n, 7.0, True, INK, line=9.2)
    p = cp(c, fn, 6.1, False, MUTED, line=7.8)
p = P(doc, "LangGraph StateGraph · 声纹 CAM++ · 转写 SenseVoice · 提词 DeepSeek · 同 session 用 MemorySaver 保持上下文", 6.4, False, MUTED, before=0.8, after=1.5, line=8.2)

bullet(doc, "首片只认人：15 秒首片不跑提词，避免刚开口就给错建议。", size=8.2, line=10.8)
bullet(doc, "ARUI 自动更新：render 把状态、布局、rows 组装成 view；commit 把要点写回档案。", size=8.2, line=10.8)
bullet(doc, "提词带档案：simple / complex 与本段转写一起进提示词，建议针对当前这个人。", size=8.2, line=10.8)
bullet(doc, "确认与权限：歧义先待确认；文本指令只改画面不写档案；多人模式不入库；旧请求过期不写。", size=8.2, line=10.8)
bullet(doc, "失败可降级：503 / 坏 JSON → 规则兜底标「兜底」；静音 → 「没声音」；慢请求 stale 作废。", size=8.2, line=10.8, after=2)

# 48h box
bt, bc = box("FFF7ED", border_color="F2D5B8", left_color="B54708", left_sz=20)
p = cp(bc, "", line=10.4)
R(p, "48 小时取舍：", 7.8, True, AMBER)
R(p, "优先保证主链路可跑——真人声纹、提词、入库、五状态。会议分人用现成模型，待办用关键词规则；没有做人脸、没有训练模型、没有做云端多租户。", 7.6, False, BODY)

# 04
section_head("04", "语音与文本，同一条链路", "多模态协同 · 20 分")
vt = table(2, 2, [PAGE_W / 2] * 2, border=BORDER, border_sz=4)
for i, h in enumerate(["语音 VOICE", "文本 TEXT"]):
    c = vt.cell(0, i)
    shade(c, DARK)
    cell_margins(c, top=0.06, bottom=0.06, left=0.13, right=0.13)
    p = cp(c, "", line=9.0)
    R(p, h, 7.2, True, WHITE, tracking=0.4)
texts = [
    "选「单人」直接说；15s 首片、之后 55s 自动切片；会议模式免手。",
    "↑ 短语键盘或控制台输入；同一个 /api/agent/feed，同一份 view。",
]
for i, txt in enumerate(texts):
    c = vt.cell(1, i)
    shade(c, "FAFCFB")
    cell_margins(c, top=0.07, bottom=0.07, left=0.13, right=0.13)
    p = cp(c, txt, 7.3, False, BODY, line=9.8)

spacer(2)
bullet(doc, "跨方式：语音认王姐 → 文本「换成 Lena」→ 人变 Lena → 再语音（旧 person_id）→ 仍 Lena。", size=8.0, line=10.6)
bullet(doc, "纠正：「换成王」→ 待确认，列出王姐 / 王兴 / 王石；「就是他」→ 才执行。", size=8.0, line=10.6)
bullet(doc, "上下文：纯文本续片沿用上一片 person_id；换人必须显式「换成 名字」，避免身份静默漂移。", size=8.0, line=10.6, after=2)

# 05
section_head("05", "结果与延迟", "实时性与可靠性 · 10 分")
stats = [("0.998s", "提词 P50"), ("1.427s", "提词 P95"), ("22ms", "文本指令"), ("24/24", "验收检查")]
stt = table(1, 4, [PAGE_W / 4] * 4, border="FFFFFF", border_sz=10)
for i, (num, lab) in enumerate(stats):
    c = stt.cell(0, i)
    shade(c, LIGHT)
    cell_margins(c, top=0.06, bottom=0.06, left=0.08, right=0.08)
    p = cp(c, "", line=14.2, align=WD_ALIGN_PARAGRAPH.CENTER)
    R(p, num, 14.0, True, GREEN)
    p = cp(c, lab, 6.5, False, MUTED, align=WD_ALIGN_PARAGRAPH.CENTER, line=8.0)
p = P(doc, "10 次连续提词（含 HTTP 往返），本地 SenseVoice + DeepSeek；700ms 轮询，rev 变化才重绘；outbox 8s 补传；单段最长 30 分钟。", 7.8, False, BODY, before=1.2, after=1.5, line=10.4)

spacer(1.5)
ct = table(1, 1, [PAGE_W])
cc = ct.cell(0, 0)
cell_margins(cc, 0, 0, 0, 0)
p = cc.paragraphs[0]
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(0)
p.paragraph_format.space_after = Pt(1)
p.paragraph_format.line_spacing = 1.0
p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
p.add_run().add_picture(os.path.join(OUT, "latency_chart.png"), width=Cm(7.6))
cap = cc.add_paragraph()
cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
cap.paragraph_format.space_before = Pt(0)
cap.paragraph_format.space_after = Pt(0)
cap.paragraph_format.line_spacing = Pt(8.2)
R(cap, "10 次连续提词延迟（ms），虚线为 P50 = 998ms", 6.5, False, MUTED)

# 06
section_head("06", "会议：同一套能力的第二场景", "场景扩展")
mt, mc = box("F4F8FB", border_color="D6E4F0", left_color="175CD3", left_sz=20)
p = cp(mc, "", line=10.4)
R(p, "15s/55s 自动切片、说话人分离、逐段转写、行动项、会后归档；控制台可复盘。", 7.4, False, BODY)
p = cp(mc, "", line=10.4, before=0.4)
R(p, "限制：抢话 / 重叠可能并人；待办为关键词规则版。第二场景，不改变一对一社交主线。", 7.2, False, MUTED)

# 07
section_head("07", "交付与边界", "交付与真实性 · 10 分")
p = P(doc, "", before=0, after=1.5, line=10.2)
R(p, "8 项验收、24 项检查全部通过", 8.0, True, GREEN)
R(p, "；10 次连续延迟；提交包不含密钥。", 7.4, False, BODY)

acc = [
    ("① 新需求", "完成，3 行提词", "case1"),
    ("② 内容修改", "只看待办→日期/跟进", "case2"),
    ("③ 重点布局", "日期置顶 + focus", "case3"),
    ("④ 跨方式", "王姐→Lena，上下文在", "case4"),
    ("⑤ 歧义确认", "换成王→待确认", "case5"),
    ("⑥ 服务失败", "静音→异常；503 注入", "case6"),
    ("⑦ 连续请求", "stale 作废", "case7"),
    ("⑧ 显示边界", "rows≤6；单行≤52", "case8"),
]
ag = table(2, 4, [PAGE_W / 4] * 4, border="FFFFFF", border_sz=7)
for i, (a, b, c3) in enumerate(acc):
    c = ag.cell(i // 4, i % 4)
    shade(c, "F7FBF9")
    cell_margins(c, top=0.06, bottom=0.06, left=0.1, right=0.08)
    p = cp(c, "", line=9.4)
    R(p, "✓ ", 7.5, True, ACCENT)
    R(p, a, 7.6, True, INK)
    p = cp(c, b, 6.6, False, BODY, line=8.2)
    p = cp(c, c3, 6.0, True, GREEN, line=7.6)

# team
spacer(2)
p = P(doc, "", before=0, after=0.5, line=10.4)
R(p, "团队分工：", 7.9, True, GREEN)
R(p, "平台与开源提供 IrisView FusionUI、LEION 模拟器、DeepSeek API、CAM++、SenseVoice；我们开发 LangGraph 7 节点、22 个 HTTP 接口、人脉库与两档记忆、五状态 HUD、outbox、控制台、8 项验收与延迟脚本。", 7.4, False, BODY)

# boundaries / next
spacer(1.5)
bt, bc = box("F2F8F5", border_color=BORDER, left_color="0C8F5F", left_sz=20)
p = cp(bc, "", line=10.2)
R(p, "边界：", 7.6, True, AMBER)
R(p, "不做人脸；声纹为合成语音实测；会议待办为关键词规则；50 条人物为构造数据；AI 建议需人工确认。", 7.4, False, BODY)
p = cp(bc, "", line=10.4, before=0.4)
R(p, "下一步：", 7.6, True, GREEN)
R(p, "10 个真实用户试用；授权接入日历 / CRM / 名片；会议分人优化。", 7.4, False, BODY)

spacer(3)
p = P(doc, "模拟器实拍 · 640×480 单色", 7.2, True, MUTED, before=0, after=1.0, line=9.0)
shots = [
    ("live55_ident.png", "在听：正在识别"),
    ("glasses_keys.png", "短语键盘：文本输入"),
]
sht = table(1, 2, [PAGE_W / 2] * 2, border="FFFFFF", border_sz=10)
for i, (fn, cap) in enumerate(shots):
    c = sht.cell(0, i)
    cell_margins(c, top=0, bottom=0, left=0.05, right=0.05)
    p = c.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(os.path.join(ROOT, "test", fn), width=Cm(5.8))
    cp_ = c.add_paragraph()
    cp_.paragraph_format.space_before = Pt(0)
    cp_.paragraph_format.space_after = Pt(0)
    cp_.paragraph_format.line_spacing = Pt(8)
    cp_.alignment = WD_ALIGN_PARAGRAPH.CENTER
    R(cp_, cap, 6.4, False, MUTED)

p = P(doc, "完整 8 项记录见 docs/34；120 秒演示视频见提交包。", 6.7, False, MUTED, before=2, after=0, line=8.6, align=WD_ALIGN_PARAGRAPH.CENTER)

# ---------------------------------------------------------------- save
doc.core_properties.title = "MemoryLens 记忆眼镜 · JUNCTION CHINA 2026 智能穿戴赛道作品说明"
doc.core_properties.author = "MemoryLens Team"
doc.core_properties.subject = "ARUI · 自动化 · 多模态：AR 眼镜实时交互多模态智能体"
out_docx = os.path.join(OUT, "MemoryLens-作品说明-JUNCTION2026-v5.docx")
doc.save(out_docx)
print("saved", out_docx)
