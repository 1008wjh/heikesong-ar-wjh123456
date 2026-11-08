# -*- coding: utf-8 -*-
"""Build the 2-page MemoryLens submission brief (DOCX)."""
import os
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_BREAK, WD_LINE_SPACING
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from PIL import Image, ImageDraw, ImageFont

ROOT = r"E:\ar-memory"
OUT = os.path.join(ROOT, "submission")
os.makedirs(OUT, exist_ok=True)

INK = RGBColor(0x1A, 0x26, 0x21)
BODY = RGBColor(0x2E, 0x3A, 0x34)
MUTED = RGBColor(0x6B, 0x7A, 0x72)
GREEN = RGBColor(0x0C, 0x8F, 0x5F)
ACCENT = RGBColor(0x12, 0xB7, 0x6A)
DARK = "0C2B22"
LIGHT = "EEF8F2"
BORDER = "D7E6DD"
AMBER = RGBColor(0xB5, 0x47, 0x08)
RED = RGBColor(0xB4, 0x23, 0x18)
BLUE = RGBColor(0x17, 0x5C, 0xD3)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
MINT = RGBColor(0x8F, 0xE3, 0xB8)

FONT = "Segoe UI"
EA = "微软雅黑"
PAGE_W = 18.2  # cm usable width


# ---------------------------------------------------------------- primitives
def _eastasia(run, ea=EA):
    rPr = run._element.get_or_add_rPr()
    rf = rPr.get_or_add_rFonts()
    rf.set(qn("w:eastAsia"), ea)


def R(p, text, size=8.5, bold=False, color=BODY, italic=False, font=FONT, ea=EA,
      tracking=None):
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = color
    r.font.name = font
    _eastasia(r, ea)
    if tracking:
        rPr = r._element.get_or_add_rPr()
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:val"), str(int(tracking * 20)))
        rPr.append(sp)
    return r


def P(container, text="", size=9.0, bold=False, color=BODY, align=None,
      before=0, after=0, line=12.8, left=0, first=0, keep=False, italic=False,
      font=FONT):
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
        R(p, text, size=size, bold=bold, color=color, italic=italic, font=font)
    return p


def bullet(container, text, size=8.7, line=12.0, color=BODY, before=0.6, after=0):
    p = container.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = Pt(line)
    pf.left_indent = Cm(0.36)
    pf.first_line_indent = Cm(-0.36)
    R(p, "▪ ", size=size, color=ACCENT, bold=True)
    R(p, text, size=size, color=color)
    return p


def shade(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")):
        tcPr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
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


def cellp(cell, text="", size=7.6, bold=False, color=BODY, align=None,
          before=0, after=0, line=10.6, first=True):
    p = cell.paragraphs[0] if first and not cell.paragraphs[0].runs and len(cell.paragraphs) == 1 else cell.add_paragraph()
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
    r = p.add_run("")
    r.font.size = Pt(1)


def section_head(num, title, score):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(6)
    pf.space_after = Pt(2.5)
    pf.line_spacing = Pt(17)
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
    R(p, num, 13.8, True, GREEN)
    R(p, "  " + title, 12.3, True, INK)
    R(p, "\t" + score, 7.0, False, MUTED)
    return p


def lede(text):
    p = P(doc, "", before=1, after=2, line=13.6)
    R(p, text, 10.2, True, INK)
    return p


def body(text, before=0, after=1.5, size=9.0, line=12.8, color=BODY):
    return P(doc, text, size=size, color=color, before=before, after=after, line=line)


def add_picture(cell, path, width_cm, caption=None):
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.add_run().add_picture(path, width=Cm(width_cm))
    if caption:
        cp = cell.add_paragraph()
        cp.paragraph_format.space_before = Pt(0)
        cp.paragraph_format.space_after = Pt(0)
        cp.paragraph_format.line_spacing = Pt(8.5)
        R(cp, caption, 6.6, False, MUTED)


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


# ---------------------------------------------------------------- chart
def make_chart(path):
    vals = [933, 1086, 1174, 1031, 923, 998, 818, 1427, 967, 1046]
    W, H = 1500, 450
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    try:
        f_reg = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 38)
        f_sm = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 32)
        f_bold = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 37)
    except Exception:
        f_reg = f_sm = f_bold = ImageFont.load_default()

    left, right, top, bottom = 42, 28, 58, 42
    vmin, vmax = 700, 1500
    plot_h = H - top - bottom

    def y(v):
        return top + (vmax - v) / (vmax - vmin) * plot_h

    for gv in (800, 1200):
        yy = y(gv)
        d.line([(left, yy), (W - right, yy)], fill="#E7F0EA", width=2)

    n = len(vals)
    avail = W - left - right
    step = avail / n
    bw = int(step * 0.52)
    for i, v in enumerate(vals):
        cx = left + step * (i + 0.5)
        x0, x1 = cx - bw / 2, cx + bw / 2
        y0 = y(v)
        d.rounded_rectangle([x0, y0, x1, y(vmin)], radius=5, fill="#12B76A")
        lab = str(v)
        tw = d.textlength(lab, font=f_bold)
        d.rectangle([cx - tw / 2 - 3, y0 - 44, cx + tw / 2 + 3, y0 - 4], fill="white")
        d.text((cx - tw / 2, y0 - 44), lab, font=f_bold, fill="#0C5C40")
        num = str(i + 1)
        tw = d.textlength(num, font=f_sm)
        d.text((cx - tw / 2, H - bottom + 8), num, font=f_sm, fill="#88978E")

    p50 = 998
    yy = y(p50)
    # dashed line
    x = left
    while x < W - right:
        d.line([(x, yy), (min(x + 12, W - right), yy)], fill="#0B2A21", width=2)
        x += 22

    img.save(path)


# ---------------------------------------------------------------- document
doc = Document()
sec = doc.sections[0]
sec.page_width = Cm(21)
sec.page_height = Cm(29.7)
sec.top_margin = Cm(1.0)
sec.bottom_margin = Cm(0.95)
sec.left_margin = Cm(1.4)
sec.right_margin = Cm(1.4)
sec.header_distance = Cm(0.5)
sec.footer_distance = Cm(0.35)

normal = doc.styles["Normal"]
normal.font.name = FONT
normal.font.size = Pt(8.5)
normal.font.color.rgb = BODY
rPr = normal.element.get_or_add_rPr()
rf = rPr.get_or_add_rFonts()
rf.set(qn("w:ascii"), FONT)
rf.set(qn("w:hAnsi"), FONT)
rf.set(qn("w:eastAsia"), EA)
normal.paragraph_format.space_before = Pt(0)
normal.paragraph_format.space_after = Pt(0)
normal.paragraph_format.line_spacing = Pt(12)

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
R(f, "MemoryLens 记忆眼镜  ·  v3.1.0 (66)  ·  JUNCTION CHINA 2026 智能穿戴赛道", 6.6, False, MUTED)
R(f, "\t第 ", 6.6, False, MUTED)
field(f, " PAGE ", "1")
R(f, " 页 / 共 ", 6.6, False, MUTED)
field(f, " NUMPAGES ", "2")
R(f, " 页", 6.6, False, MUTED)

# ---------------------------------------------------------------- masthead
hm = table(1, 1, [PAGE_W])
hc = hm.cell(0, 0)
shade(hc, DARK)
cell_margins(hc, top=0.32, bottom=0.3, left=0.55, right=0.55)
hc.vertical_alignment = WD_ALIGN_VERTICAL.TOP
p = cellp(hc, "", line=10)
R(p, "JUNCTION CHINA 2026  ·  智能穿戴赛道  ·  亮亮视野 LEION", 7.2, True, MINT, tracking=0.5)
p = cellp(hc, "", line=30, before=4)
R(p, "MemoryLens", 25, True, WHITE)
R(p, "  记忆眼镜", 17, True, RGBColor(0xD8, 0xF5, 0xE6))
p = cellp(hc, "", line=15, before=1)
R(p, "抬头就知道：他是谁、上次聊到哪、这次该说什么。", 10.3, True, RGBColor(0xC9, 0xF2, 0xDD))
p = cellp(hc, "", line=10.5, before=2)
R(p, "作品说明 · 声纹认人 / 记忆提词 / 会议行动项 · 模拟器实测 2026-11-08 · 按 100 分评审维度组织", 7.0, False, RGBColor(0x9D, 0xC4, 0xB3))

# accent rule
spacer(1.2)
rule = table(1, 1, [PAGE_W])
rc = rule.cell(0, 0)
shade(rc, "12B76A")
cell_margins(rc, top=0, bottom=0, left=0, right=0)
rc.paragraphs[0].paragraph_format.line_spacing = Pt(1.2)
rc.paragraphs[0].add_run("").font.size = Pt(0.8)

# chips
spacer(3)
chips = ["声纹免手认人", "提词，不是总结", "两档隐私记忆", "异常可演示"]
ct = table(1, 4, [PAGE_W / 4] * 4, border="FFFFFF", border_sz=12)
for i, txt in enumerate(chips):
    c = ct.cell(0, i)
    shade(c, LIGHT)
    cell_margins(c, top=0.1, bottom=0.1, left=0.08, right=0.08)
    c.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p = cellp(c, "", align=WD_ALIGN_PARAGRAPH.CENTER, line=10)
    R(p, txt, 7.6, True, GREEN)

# ---------------------------------------------------------------- 01
section_head("01", "场景与创意", "场景价值与创新 · 10 分")
lede("不是会议纪要，是对话进行中的第二层记忆。")
body("目标用户是销售 / BD、创始人 / 投资人、展会与校友会常客：一天见几十人，回程只记得三五张脸。名片和 CRM 都要求事后录入，而最需要信息的时刻，恰恰是正在对话的那 30 秒。MemoryLens 把记忆提前：戴着眼镜正常聊天，第 15 秒镜片浮出对方经授权的档案；按一次确认，给三条能直接说出口的话；按返回，这段对话自动归档。相较事后补录，它在需要的那一刻把信息递到眼前；相较通用话术，它带着这个人的档案说话。")
spacer(1.5)
cr = table(1, 1, [PAGE_W], border=None)
cc = cr.cell(0, 0)
shade(cc, LIGHT)
cell_margins(cc, top=0.1, bottom=0.1, left=0.18, right=0.18)
tcPr = cc._tc.get_or_add_tcPr()
tb = OxmlElement("w:tcBorders")
el = OxmlElement("w:left")
el.set(qn("w:val"), "single")
el.set(qn("w:sz"), "24")
el.set(qn("w:color"), "12B76A")
tb.append(el)
tcPr.append(tb)
p = cellp(cc, "", line=11.6)
R(p, "创意一句话：", 8.8, True, GREEN)
R(p, "把「事后 CRM」变成「对话中的提词器」，把「会议纪要」变成「会中可说的下一句」。", 9.2, False, INK)
spacer(2)

cards = [
    ("①", "免手身份入口",
     "平台无摄像头 API，我们用 192 维声纹认人：开口即识别，不抬手、不掏手机。同人 0.82–0.94、异人 0.20–0.67，单句 20–50ms。"),
    ("②", "两档隐私记忆",
     "simple 上镜（身份、上次见面、一句提醒），complex 留在服务端、按确认才展开。抬头 1 秒可读，公共场合不泄露隐私。"),
    ("③", "提词，不是总结",
     "输出是「报价单下周能给我吗」这类能直接说的话，不是摘要。AI 只提议、不替你开口；每条要点带原话，可回溯、可删改。"),
]
t = table(1, 3, [PAGE_W / 3] * 3, border="FFFFFF", border_sz=14)
for i, (num, title, txt) in enumerate(cards):
    c = t.cell(0, i)
    shade(c, "F7FBF9")
    cell_margins(c, top=0.12, bottom=0.12, left=0.16, right=0.16)
    # top accent
    tcPr = c._tc.get_or_add_tcPr()
    tb = OxmlElement("w:tcBorders")
    el = OxmlElement("w:top")
    el.set(qn("w:val"), "single")
    el.set(qn("w:sz"), "18")
    el.set(qn("w:color"), "12B76A")
    tb.append(el)
    tcPr.append(tb)
    p = cellp(c, "", line=13.6)
    R(p, num + " ", 10.5, True, ACCENT)
    R(p, title, 8.8, True, INK)
    p = cellp(c, "", line=11.0, first=False)
    R(p, txt, 7.9, False, BODY)

spacer(2)
eth = table(1, 1, [PAGE_W], border=BORDER, border_sz=4)
ec = eth.cell(0, 0)
shade(ec, "F2F8F5")
cell_margins(ec, top=0.1, bottom=0.1, left=0.18, right=0.18)
p = cellp(ec, "", line=11.2)
R(p, "数据伦理不是补丁，是设计前提：", 7.9, True, GREEN)
R(p, "本人录入、对方同意；音频只在本机转写，发给大模型的只有文本；镜片不显示未经授权的他人信息。", 7.9, False, BODY)

# ---------------------------------------------------------------- 02
section_head("02", "ARUI 设计与交互", "25 分")
t = table(1, 2, [12.1, 6.1])
lc, rc = t.cell(0, 0), t.cell(0, 1)
cell_margins(lc, 0, 0, 0, 0.18)
cell_margins(rc, 0, 0, 0, 0)
lc.vertical_alignment = WD_ALIGN_VERTICAL.TOP
rc.vertical_alignment = WD_ALIGN_VERTICAL.TOP

p = cellp(lc, "", line=13.4)
R(p, "抬头场景的第一原则是「少」。", 9.7, True, INK)
p = cellp(lc, "640×480 单色、单行 Text 约 110px，我们把 HUD 做成窄列：每行 ≤6 个汉字、最多 6 行，超长自动轮播——长内容不溢出，也不遮挡视线。", 8.7, False, BODY, line=12.4, first=False, before=1)
p = cellp(lc, "信息层级：状态/计时 → 姓名 → 身份关系 → 2–3 行记忆 → 按键提示。第一眼看到的是「人」，不是菜单。", 8.7, False, BODY, line=12.4, first=False, before=1)
p = cellp(lc, "低干扰：提词只占下半屏，20 秒自动收起，录音不中断；待命不显示任何人名；多人场景只提示「另开会议模块」，不擅自入库。", 8.7, False, BODY, line=12.4, first=False, before=1)
p = cellp(lc, "交互减法：确认=开始 / 出提词 / 入库；上=短语键盘，下=建议，返回=收尾。没有双击、长按和嵌套菜单。", 8.7, False, BODY, line=12.4, first=False, before=1)

add_picture(rc, os.path.join(ROOT, "test", "hud_preview.png"), 6.05,
            "HUD 设计稿：640×480 单色，一眼一人")

p = P(doc, "五种可观察状态：文字、颜色、图标与可执行动作同步变化。", 7.8, False, MUTED, before=1.2, after=1.0, line=9.6)
states = [
    ("待命", "按一下开始听", "F3F6F4", RGBColor(0x3F, 0x4F, 0x47)),
    ("处理中", "在想… + 计时", "EAF2FF", BLUE),
    ("待确认", "是他吗？+ 候选", "FFF6E5", AMBER),
    ("完成", "卡片 + 提词", "EAF9F1", GREEN),
    ("异常", "没声音 · 检查麦克风", "FDECEA", RED),
]
st = table(1, 5, [PAGE_W / 5] * 5, border="FFFFFF", border_sz=10)
for i, (name, desc, bg, col) in enumerate(states):
    c = st.cell(0, i)
    shade(c, bg)
    cell_margins(c, top=0.08, bottom=0.08, left=0.1, right=0.1)
    p = cellp(c, "", line=10.6)
    R(p, "● ", 8, True, col)
    R(p, name, 8.0, True, col)
    p = cellp(c, desc, 6.7, False, MUTED, line=8.8, first=False)

# ---------------------------------------------------------------- 03
section_head("03", "自动化与智能体", "25 分")
nodes = [
    ("ingest", "转写 / 切片"),
    ("route", "单人 / 多人"),
    ("identify", "声纹 → 语义"),
    ("extract", "抽要点"),
    ("recommend", "3 条提词"),
    ("render", "五状态"),
    ("commit", "入库"),
]
nt = table(1, 7, [PAGE_W / 7] * 7, border="FFFFFF", border_sz=8)
for i, (n, fn) in enumerate(nodes):
    c = nt.cell(0, i)
    shade(c, "F4FAF6")
    cell_margins(c, top=0.1, bottom=0.09, left=0.08, right=0.08)
    tcPr = c._tc.get_or_add_tcPr()
    tb = OxmlElement("w:tcBorders")
    el = OxmlElement("w:top")
    el.set(qn("w:val"), "single")
    el.set(qn("w:sz"), "14")
    el.set(qn("w:color"), "12B76A")
    tb.append(el)
    tcPr.append(tb)
    p = cellp(c, n, 7.4, True, INK, line=9.8)
    p = cellp(c, fn, 6.5, False, MUTED, line=8.2, first=False)
p = P(doc, "LangGraph StateGraph · 同一 session 用 MemorySaver 保持上下文 · 一次 feed 推进一小片", 6.8, False, MUTED, before=1, after=1.8, line=8.8)

bullet(doc, "第一片只认人：15 秒首片不跑提词，先把「是谁」放到镜片上——避免刚开口就给错建议，也把首屏延迟压到最低。")
bullet(doc, "提词带档案：recommend 把 simple / complex 与本段转写一起拼进提示词，建议是「对这个人说的」，不是通用话术。")
bullet(doc, "确认与权限有闸门：歧义先待确认，未确认不切换；文本指令只改画面、不写档案；多人模式不入库；旧请求过期不写档案。")
bullet(doc, "失败可降级：503 / 坏 JSON → 规则抽取标「兜底」；静音 → 报「没声音」，不让模型编字；慢请求 stale 作废，不覆盖最新结果。")
p = P(doc, "", before=1.4, after=0, line=9.5)
R(p, "工具与模型：", 7.3, True, GREEN)
R(p, "本地 SenseVoice（转写） · CAM++（声纹） · DeepSeek deepseek-chat（抽取 / 提词） · 规则兜底（无 Key 也能跑）", 7.3, False, MUTED)

# architecture band
spacer(4)
p = P(doc, "", before=0, after=1.4, line=10.5, keep=True)
R(p, "系统分工", 8.8, True, INK)
R(p, "   眼镜不推理，服务器是唯一事实源", 7.0, False, MUTED)
arc = table(1, 3, [PAGE_W / 3] * 3, border="FFFFFF", border_sz=10)
arc_cells = [
    ("眼镜端", "麦克风 + 640×480 屏 + 6 键；切片、轮询、画 HUD"),
    ("服务器", "LangGraph 7 节点；认人、抽取、提词、入库"),
    ("控制台", "人物 CRUD、声纹录入、两档 JSON、会议复盘"),
]
for i, (name, desc) in enumerate(arc_cells):
    c = arc.cell(0, i)
    shade(c, "F7FBF9")
    cell_margins(c, top=0.09, bottom=0.09, left=0.14, right=0.14)
    p = cellp(c, "", line=9.8)
    R(p, "● ", 7.4, True, ACCENT)
    R(p, name, 7.8, True, INK)
    p = cellp(c, desc, 6.8, False, BODY, line=8.8, first=False)

p = P(doc, "", before=2.4, after=0, line=10.2)
R(p, "刻意不做：", 8.2, True, AMBER)
R(p, "人脸（平台无摄像头 API） · 长摘要（镜片是余光，不是主屏） · 自动入库（记忆写错比不写更糟）。", 8.2, False, BODY)

# demo storyboard
spacer(4.5)
p = P(doc, "", before=0, after=1.6, line=10.5, keep=True)
R(p, "120 秒演示动线", 9.0, True, INK)
R(p, "   对应提交视频：新需求 → 自动更新 → 一次异常处理或纠正", 7.2, False, MUTED)
steps = [
    ("00–15s", "开场 / 待命"),
    ("15–40s", "认人 + 3 条提词"),
    ("40–60s", "两次连续调整"),
    ("60–80s", "语音 ↔ 文本 + 纠正"),
    ("80–100s", "断网 / 模型失败"),
    ("100–120s", "入库 + 过期作废"),
]
dmt = table(1, 6, [PAGE_W / 6] * 6, border="FFFFFF", border_sz=10)
for i, (tm, act) in enumerate(steps):
    c = dmt.cell(0, i)
    shade(c, LIGHT if i % 2 == 0 else "F7FBF9")
    cell_margins(c, top=0.1, bottom=0.1, left=0.1, right=0.1)
    p = cellp(c, "", line=9.4)
    R(p, tm, 7.0, True, GREEN)
    p = cellp(c, act, 6.6, False, BODY, line=8.6, first=False)

p = P(doc, "", before=3, after=0, line=11, align=WD_ALIGN_PARAGRAPH.RIGHT)
R(p, "—— 让眼镜记住，让人专心聊天。", 9.2, False, MUTED, italic=True)

# ---------------------------------------------------------------- page 2
p = doc.add_paragraph()
p.add_run().add_break(WD_BREAK.PAGE)
p.paragraph_format.space_before = Pt(0)
p.paragraph_format.space_after = Pt(0)
p.paragraph_format.line_spacing = Pt(1)

# 04
section_head("04", "多模态协同", "20 分")
body("同一个任务，两条输入路径，同一份 view。", after=2)
vt = table(2, 2, [PAGE_W / 2] * 2, border=BORDER, border_sz=4)
heads = ["语音 VOICE", "文本 TEXT"]
for i, h in enumerate(heads):
    c = vt.cell(0, i)
    shade(c, DARK)
    cell_margins(c, top=0.08, bottom=0.08, left=0.15, right=0.15)
    p = cellp(c, "", line=9.8)
    R(p, h, 7.7, True, WHITE, tracking=0.4)
texts = [
    "选「单人」直接说；15s 首片、之后 55s 自动切片；会议模式全程免手。",
    "↑ 短语键盘或控制台输入；同一个 /api/agent/feed，返回同一份 view。",
]
for i, txt in enumerate(texts):
    c = vt.cell(1, i)
    shade(c, "FAFCFB")
    cell_margins(c, top=0.09, bottom=0.09, left=0.15, right=0.15)
    p = cellp(c, txt, 7.9, False, BODY, line=10.8)

spacer(2)
bullet(doc, "切换且保上下文（case4）：语音认出王姐 → 文本「换成 Lena」→ 人变 Lena → 再发语音（仍带旧 person_id）→ 仍是 Lena；再文本仍保持。", size=8.3, line=11.4)
bullet(doc, "独立纠正 + 歧义先问（case5）：「换成王」→ 待确认，列出 王姐/王兴/王石，人不变；「不是他」→ 再确认；「就是他」→ 才执行。", size=8.3, line=11.4)
bullet(doc, "指代：「他 / 她 / 这个人」绑定当前 person_id，换人即换指代。", size=8.3, line=11.4, after=2.5)

adj = table(5, 3, [5.2, 10.2, 2.8], border=BORDER, border_sz=4)
hdr = ["输入（同一 session）", "镜片变化", "类型"]
for i, h in enumerate(hdr):
    c = adj.cell(0, i)
    shade(c, DARK)
    cell_margins(c, top=0.07, bottom=0.07, left=0.14, right=0.14)
    p = cellp(c, "", line=9.4)
    R(p, h, 7.2, True, WHITE)
rows = [
    ("「只看待办」", "只剩 日期 / 跟进", "内容"),
    ("「优先日期」", "日期置顶，layout=focus", "重点/布局"),
    ("「只保留日期」", "3 行 → 1 行", "精简"),
    ("「显示全部」", "还原 3 行档案", "内容"),
]
for r, (a, b, c3) in enumerate(rows, start=1):
    shade(adj.cell(r, 0), "FFFFFF" if r % 2 else "F7FBF9")
    shade(adj.cell(r, 1), "FFFFFF" if r % 2 else "F7FBF9")
    shade(adj.cell(r, 2), "FFFFFF" if r % 2 else "F7FBF9")
    for ci, val in enumerate((a, b, c3)):
        c = adj.cell(r, ci)
        cell_margins(c, top=0.06, bottom=0.06, left=0.14, right=0.12)
        p = cellp(c, "", line=9.4)
        R(p, val, 7.7, ci == 0, INK if ci == 0 else (GREEN if ci == 2 else BODY))
p = P(doc, "两次连续调整在同一 session 内完成，ARUI 自动更新，无需重进页面。", 7.2, False, MUTED, before=1, after=0, line=8.8)

# 05
section_head("05", "实时性与可靠性", "10 分")
stats = [
    ("0.998s", "提词 P50"),
    ("1.427s", "提词 P95"),
    ("22ms", "文本指令 P50"),
    ("24/24", "验收检查"),
]
stt = table(1, 4, [PAGE_W / 4] * 4, border="FFFFFF", border_sz=12)
for i, (num, lab) in enumerate(stats):
    c = stt.cell(0, i)
    shade(c, LIGHT)
    cell_margins(c, top=0.08, bottom=0.08, left=0.1, right=0.1)
    p = cellp(c, "", align=WD_ALIGN_PARAGRAPH.CENTER, line=16)
    R(p, num, 15.5, True, GREEN)
    p = cellp(c, lab, 6.9, False, MUTED, align=WD_ALIGN_PARAGRAPH.CENTER, line=8.8, first=False)

spacer(2)
chart_path = os.path.join(OUT, "latency_chart.png")
make_chart(chart_path)
ct = table(1, 2, [8.2, 10.0])
cc, tc = ct.cell(0, 0), ct.cell(0, 1)
cell_margins(cc, 0, 0, 0, 0.2)
cell_margins(tc, 0, 0, 0, 0)
add_picture(cc, chart_path, 7.8, "10 次连续提词延迟（ms），虚线为 P50 = 998ms")
p = cellp(tc, "10 次连续提词（本地 SenseVoice + DeepSeek，含 HTTP 往返）：预期 <2s，实测 818–1427ms；纯文本指令不调模型，P50 22ms。", 8.0, False, BODY, line=10.8)
p = cellp(tc, "眼镜 700ms 轮询 /api/latest，rev 变化才重绘；导航 120ms，按键即时响应。模型不可用自动降级并标注；离线录音进 outbox，每 8s 补传；单段最长 30 分钟。", 8.0, False, BODY, line=10.8, first=False, before=1.5)

# 06
section_head("06", "交付与真实性", "10 分")
acc_head = ["验收项", "预期结果", "实际结果（2026-11-08）", "证据"]
acc_rows = [
    ("① 新需求", "新输入 → 处理 → 生成 ARUI", "完成；王姐，3 行提词", "case1"),
    ("② 内容修改", "换对象 / 范围 / 条件", "只看待办→日期/跟进；只保留日期→1 行", "case2"),
    ("③ 重点与布局", "突出 / 精简 / 重排", "优先日期置顶，layout=focus；3→2 行", "case3"),
    ("④ 跨方式", "语音↔文本、纠正、上下文", "王姐→Lena→语音仍 Lena→文本仍在", "case4"),
    ("⑤ 歧义与确认", "先澄清、未确认不执行", "换成王→待确认（3 候选），人不变", "case5"),
    ("⑥ 服务失败", "准确反馈、注明注入", "静音→异常；503/坏 JSON 13 项", "case6"),
    ("⑦ 连续请求", "旧结果不覆盖最新", "慢请求 stale=true；cancel「已取消」", "case7"),
    ("⑧ 显示边界", "不溢出、可区分", "rows≤6、单行≤52 字；详情 5 行", "case8"),
]
at = table(9, 4, [2.9, 4.3, 8.0, 3.0], border=BORDER, border_sz=4)
for i, h in enumerate(acc_head):
    c = at.cell(0, i)
    shade(c, DARK)
    cell_margins(c, top=0.05, bottom=0.05, left=0.1, right=0.1)
    p = cellp(c, "", line=9.2)
    R(p, h, 6.9, True, WHITE)
for r, row in enumerate(acc_rows, start=1):
    for ci, val in enumerate(row):
        c = at.cell(r, ci)
        shade(c, "FFFFFF" if r % 2 else "F7FBF9")
        cell_margins(c, top=0.05, bottom=0.05, left=0.1, right=0.1)
        p = cellp(c, "", line=9.3)
        if ci == 0:
            R(p, val, 7.2, True, INK)
        elif ci == 3:
            R(p, val, 6.7, False, GREEN)
        else:
            R(p, val, 7.0, False, BODY)
p = P(doc, "", before=1.0, after=2)
R(p, "24 个检查 24 passed / 0 failed", 7.8, True, GREEN)
R(p, "  —  test/acceptance_report.json；延迟原始数据 test/latency_report.json", 6.8, False, MUTED)

spacer(1)
ctb = table(2, 2, [PAGE_W / 2] * 2, border=BORDER, border_sz=4)
for i, h in enumerate(["平台 / 开源提供", "我们新增开发"]):
    c = ctb.cell(0, i)
    shade(c, "F2F8F5")
    cell_margins(c, top=0.06, bottom=0.06, left=0.14, right=0.14)
    p = cellp(c, "", line=9.2)
    R(p, h, 7.3, True, GREEN)
vals = [
    "IrisView FusionUI（Board/Text/六键/麦克风）、LEION 模拟器、DeepSeek API、开源模型 CAM++ 与 SenseVoice。",
    "LangGraph 7 节点智能体、22 个 HTTP 接口、人脉库与两档记忆、眼镜端五状态 HUD + 离线补传、控制台、切片与阈值策略、8 项验收与 10 次延迟脚本。",
]
for i, v in enumerate(vals):
    c = ctb.cell(1, i)
    cell_margins(c, top=0.07, bottom=0.07, left=0.14, right=0.14)
    p = cellp(c, v, 7.5, False, BODY, line=10.2)

spacer(2)
p = P(doc, "", before=0, after=0.8, line=11.0)
R(p, "如实说明边界：", 7.8, True, AMBER)
R(p, "平台未开放摄像头 → 不做人脸识别；声纹指标为合成语音实测；会议待办是关键词规则版；50 条人物为构造演示数据；AI 建议可能跑偏，控制台入库前需人工勾选，眼镜端要点带原话、可删改；注入测试与真实失败已分别标注。", 7.8, False, BODY)
p = P(doc, "", before=0, after=0, line=11.0)
R(p, "交付：", 7.8, True, GREEN)
R(p, "README、固定版本与配置、账号申请、提示词/流程、依赖说明、一键 start.bat / sim.bat、8 项复现步骤、10 次延迟原始数据、数据/素材/代码与 AI 使用声明；提交包不含任何密钥。证据索引：docs/34、test/acceptance_report.json、test/latency_report.json。", 7.8, False, BODY)

spacer(3)
p = P(doc, "模拟器实拍 · 640×480 单色", 7.2, True, MUTED, before=0, after=1.0, line=9.0)
shots = [
    ("live55_mode.png", "选模式：单人 / 多人"),
    ("live55_ident.png", "在听：正在识别"),
    ("glasses_keys.png", "短语键盘：文本输入"),
]
sht = table(1, 3, [PAGE_W / 3] * 3, border="FFFFFF", border_sz=10)
for i, (fn, cap) in enumerate(shots):
    c = sht.cell(0, i)
    cell_margins(c, top=0, bottom=0, left=0.05, right=0.05)
    p = c.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(os.path.join(ROOT, "test", fn), width=Cm(5.4))
    cp = c.add_paragraph()
    cp.paragraph_format.space_before = Pt(0)
    cp.paragraph_format.space_after = Pt(0)
    cp.paragraph_format.line_spacing = Pt(8)
    cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    R(cp, cap, 6.4, False, MUTED)

# ---------------------------------------------------------------- save
doc.core_properties.title = "MemoryLens 记忆眼镜 · JUNCTION CHINA 2026 智能穿戴赛道作品说明"
doc.core_properties.author = "MemoryLens Team"
doc.core_properties.subject = "ARUI · 自动化 · 多模态：AR 眼镜实时交互多模态智能体"
out_docx = os.path.join(OUT, "MemoryLens-作品说明-JUNCTION-CHINA-2026.docx")
doc.save(out_docx)
print("saved", out_docx)
