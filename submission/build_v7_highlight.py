# -*- coding: utf-8 -*-
"""Text-only 2-page brief with prominent sections 01-02 and condensed 03-09."""
import os
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

ROOT = r"E:\ar-memory"
OUT = os.path.join(ROOT, "submission")

INK = RGBColor(0x17, 0x22, 0x1D)
BODY = RGBColor(0x2B, 0x35, 0x30)
MUTED = RGBColor(0x6A, 0x78, 0x71)
GREEN = RGBColor(0x0B, 0x6B, 0x4A)
ACCENT = RGBColor(0x12, 0x9B, 0x62)

FONT = "Segoe UI"
EA = "微软雅黑"
PAGE_W = 17.8
BORDER = "D8E2DC"
HEAD_FILL = "F3F6F4"


def _ea(run):
    rPr = run._element.get_or_add_rPr()
    rf = rPr.get_or_add_rFonts()
    rf.set(qn("w:eastAsia"), EA)


def R(p, text, size=9.4, bold=False, color=BODY, font=FONT, italic=False):
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = color
    r.font.name = font
    _ea(r)
    return r


def P(container, text="", size=9.4, bold=False, color=BODY, align=None,
      before=0, after=0, line=12.6, left=0, first=0, keep=False):
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


def bullet(container, text, size=9.2, line=12.2, color=BODY, before=0.4, after=0):
    p = container.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = Pt(line)
    pf.left_indent = Cm(0.38)
    pf.first_line_indent = Cm(-0.38)
    R(p, "•  ", size=size, color=GREEN, bold=True)
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


def cell_margins(cell, top=0.06, bottom=0.06, left=0.1, right=0.1):
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


def table_h(rows, cols, widths, header=False, borders=True):
    t = doc.add_table(rows=rows, cols=cols)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    tblPr = t._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    bs = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{side}")
        if borders and side in ("top", "bottom", "insideH"):
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), "4")
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), BORDER)
        else:
            el.set(qn("w:val"), "nil")
        bs.append(el)
    tblPr.append(bs)
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:type"), "dxa")
    tblW.set(qn("w:w"), str(int(sum(widths) / 2.54 * 1440)))
    tblPr.append(tblW)
    for i, w in enumerate(widths):
        t.columns[i].width = Cm(w)
        for c in t.columns[i].cells:
            c.width = Cm(w)
    for row in t.rows:
        trPr = row._tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:cantSplit"))
    if header:
        for c in t.rows[0].cells:
            shade(c, HEAD_FILL)
    return t


def cp(cell, text="", size=8.2, bold=False, color=BODY, align=None,
       before=0, after=0, line=10.4):
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


def spacer(h=3.0):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = Pt(h)
    p.add_run("").font.size = Pt(1)


def section_head(num, title, tag="", big=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(12 if big else 7)
    pf.space_after = Pt(3 if big else 2)
    pf.line_spacing = Pt(24 if big else 16)
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
    R(p, num, 11 if big else 9.4, True, GREEN)
    R(p, "  " + title, 15 if big else 11.2, True, INK)
    if tag:
        R(p, "\t" + tag, 8.0, False, MUTED)
    return p


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
sec.top_margin = Cm(1.05)
sec.bottom_margin = Cm(0.85)
sec.left_margin = Cm(1.6)
sec.right_margin = Cm(1.6)
sec.header_distance = Cm(0.4)
sec.footer_distance = Cm(0.3)

normal = doc.styles["Normal"]
normal.font.name = FONT
normal.font.size = Pt(9.4)
normal.font.color.rgb = BODY
rPr = normal.element.get_or_add_rPr()
rf = rPr.get_or_add_rFonts()
rf.set(qn("w:ascii"), FONT)
rf.set(qn("w:hAnsi"), FONT)
rf.set(qn("w:eastAsia"), EA)
normal.paragraph_format.space_before = Pt(0)
normal.paragraph_format.space_after = Pt(0)
normal.paragraph_format.line_spacing = Pt(12.6)

# footer
f = sec.footer.paragraphs[0]
f.paragraph_format.space_before = Pt(0)
f.paragraph_format.space_after = Pt(0)
f.paragraph_format.line_spacing = Pt(8)
f.paragraph_format.tab_stops.add_tab_stop(Cm(8.0), WD_TAB_ALIGNMENT.CLEAR)
f.paragraph_format.tab_stops.add_tab_stop(Cm(16.0), WD_TAB_ALIGNMENT.CLEAR)
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
R(f, "MemoryLens 记忆眼镜  ·  JUNCTION CHINA 2026 智能穿戴赛道", 7.0, False, MUTED)
R(f, "\t第 ", 7.0, False, MUTED)
field(f, " PAGE ", "1")
R(f, " 页 / 共 ", 7.0, False, MUTED)
field(f, " NUMPAGES ", "2")
R(f, " 页", 7.0, False, MUTED)

# title
p = P(doc, "", before=0, after=1, line=32)
R(p, "MemoryLens 记忆眼镜", 25, True, INK)
p = P(doc, "", before=0, after=1, line=16)
R(p, "面向一对一社交的 AR 记忆眼镜", 12.5, True, GREEN)
p = P(doc, "", before=0, after=2, line=12)
R(p, "声纹认人 · 镜片提示 · 对话归档", 9.8, False, MUTED)
p = P(doc, "", before=0, after=0, line=10.5)
R(p, "JUNCTION CHINA 2026 · 智能穿戴赛道 · 亮亮视野 LEION    作品说明 / 2026-11-08", 8.2, False, MUTED)

spacer(3)
rl = table_h(1, 1, [PAGE_W], borders=False)
rc = rl.cell(0, 0)
rc.paragraphs[0].paragraph_format.line_spacing = Pt(1.0)
rc.paragraphs[0].add_run("").font.size = Pt(0.8)
tcPr = rc._tc.get_or_add_tcPr()
shd = OxmlElement("w:shd")
shd.set(qn("w:val"), "clear")
shd.set(qn("w:color"), "auto")
shd.set(qn("w:fill"), "129B62")
tcPr.append(shd)

# ---------------------------------------------------------------- 01
section_head("01", "项目概述", "场景价值与创新 · 10 分", big=True)
p = P(doc, "", before=1, after=3.5, line=20)
R(p, "见一个人，聊两句，镜片浮出：他是谁、上次聊到哪、这次该说什么。", 14.0, True, INK)

P(doc, "MemoryLens 用声纹识别对话对象，在镜片上显示对方经授权的档案、上次对话要点与三条提词；按返回，对话归档到本人人脉库。", 11.8, False, BODY, after=2.5, line=16.4)
P(doc, "它解决三类问题：见人多、记不住；掏手机查询打断对话；名片与 CRM 只能事后录入。", 11.8, False, BODY, after=2.5, line=16.4)
P(doc, "眼镜是少数能抬头看、双手不停的屏幕；声纹不用正脸、不挑光线。平台没有开放摄像头，本作品不做人脸识别。", 11.8, False, BODY, after=0, line=16.4)

# ---------------------------------------------------------------- 02
section_head("02", "场景与价值", "场景价值与创新 · 10 分", big=True)
spacer(1)
qt = table_h(1, 1, [PAGE_W], borders=True)
qc = qt.cell(0, 0)
cell_margins(qc, top=0.12, bottom=0.12, left=0.12, right=0.12)
p = cp(qc, "", line=19)
R(p, "商业场合的第一次见面，往往决定还有没有第二次。", 14.0, True, INK)
p = cp(qc, "", line=19, before=0.4)
R(p, "名片留下联系方式，MemoryLens 留下下一次开口的理由。", 14.0, True, GREEN)

P(doc, "多数人的关系数据分散在通讯录、聊天记录和记忆里，见面时无法调用。MemoryLens 把它整理成可检索的关系记忆库。", 11.8, False, BODY, before=2.0, after=2.5, line=16.4)

ut = table_h(1, 2, [PAGE_W / 2] * 2, borders=False)
users = [
    ("慢热、在社交场合容易紧张的人", "降低开口成本：不用在几秒内搜索名字、上次话题和承诺。"),
    ("销售、创始人、展会与校友会常客", "掌握对话节奏：主动提问、主动兑现、主动推进。"),
]
for i, (h, txt) in enumerate(users):
    c = ut.cell(0, i)
    cell_margins(c, top=0.1, bottom=0.1, left=0.12, right=0.12)
    p = cp(c, "", line=12.2)
    R(p, h, 10.2, True, GREEN)
    p = cp(c, txt, 9.8, False, BODY, line=12.6)

p = P(doc, "", before=2.5, after=1.5, line=16.4)
R(p, "与现有方案的区别：", 11.8, True, INK)
R(p, "会议转写记录内容，不解决下一次开口；名片 / CRM 事后录入，对话中无法调用；通用助手不掌握这个人的档案。MemoryLens 在对话中，按人、按档案给出下一句。", 11.8, False, BODY)

p = P(doc, "", before=0, after=0, line=16.4)
R(p, "数据留在本机：", 11.8, True, INK)
R(p, "声纹、对话要点、承诺与待办；音频只在本机转写，模型只接收文本，对方同意是前提。", 11.8, False, BODY)

p = P(doc, "", before=2.5, after=0, line=16.4)
R(p, "为什么现在：", 11.0, True, INK)
R(p, "AR 眼镜有了可用的显示与麦克风；声纹可在本地 20–50ms 完成；大模型能把档案与当下对话拼成可说的话。", 10.8, False, BODY)

# before / after
p = P(doc, "", before=3.0, after=1.2, line=13, keep=True)
R(p, "使用前后", 11.0, True, INK)
bt = table_h(4, 3, [3.2, 7.3, 7.3], header=True)
for i, h in enumerate(["场景", "没有 MemoryLens", "有 MemoryLens"]):
    c = bt.cell(0, i)
    cell_margins(c, top=0.08, bottom=0.08)
    cp(c, h, 9.2, True, INK)
brows = [
    ("开场", "先想「他是谁」", "先问「报价单还要吗」"),
    ("中途", "怕漏掉承诺", "镜片提示上次答应的事"),
    ("结束", "回去补记录", "按返回，自动归档"),
]
for r, row in enumerate(brows, start=1):
    for ci, val in enumerate(row):
        c = bt.cell(r, ci)
        cell_margins(c, top=0.08, bottom=0.08)
        cp(c, val, 9.2, ci == 0, INK if ci == 0 else BODY)

# ---------------------------------------------------------------- numbers
spacer(4)
nt = table_h(1, 4, [PAGE_W / 4] * 4, borders=True)
nums = [("15s", "首片认人"), ("3 条", "可说的话"), ("0.998s", "提词 P50"), ("24/24", "验收检查")]
for i, (n, lab) in enumerate(nums):
    c = nt.cell(0, i)
    cell_margins(c, top=0.12, bottom=0.1)
    p = cp(c, "", line=20, align=WD_ALIGN_PARAGRAPH.CENTER)
    R(p, n, 18, True, GREEN)
    p = cp(c, lab, 7.6, False, MUTED, line=9.2, align=WD_ALIGN_PARAGRAPH.CENTER)

# ---------------------------------------------------------------- page 2
p = doc.add_paragraph()
p.add_run().add_break(WD_BREAK.PAGE)
p.paragraph_format.space_before = Pt(0)
p.paragraph_format.space_after = Pt(0)
p.paragraph_format.line_spacing = Pt(1)

# 03
section_head("03", "方案与交互", "ARUI 设计与交互 · 25 分")
P(doc, "640×480 单色，每行 ≤6 个汉字、最多 6 行；层级为状态 → 姓名 → 身份 → 2–3 行记忆 → 按键提示。提词 20 秒收起，待命不显示人名，多人不混入单人档案。", 9.3, False, BODY, after=2.0, line=12.4)

st = table_h(6, 3, [3.2, 8.4, 6.2], header=True)
for i, h in enumerate(["状态", "用户看到", "触发"]):
    c = st.cell(0, i)
    cell_margins(c)
    cp(c, h, 8.0, True, INK)
rows = [
    ("待命", "「按一下开始听」", "空闲"),
    ("处理中", "「在想… + 计时」", "按确认 / 请求中"),
    ("待确认", "「是他吗？ + 候选」", "歧义 / 低置信"),
    ("完成", "「卡片 + 提词」", "识别 / 建议成功"),
    ("异常", "「没声音 · 检查麦克风」", "静音 / 模型 / 网络失败"),
]
for r, row in enumerate(rows, start=1):
    for ci, val in enumerate(row):
        c = st.cell(r, ci)
        cell_margins(c)
        cp(c, val, 7.9, ci == 0, INK if ci == 0 else BODY)

p = P(doc, "", before=2.0, after=0, line=12.6)
R(p, "两次连续调整：", 9.3, True, INK)
R(p, "只看待办 → 日期 / 跟进（内容）；优先日期 → 置顶 + focus（布局）；只保留日期 → 3→1 行（精简）；显示全部 → 还原。", 9.3, False, BODY)

# 04
section_head("04", "系统与自动化", "自动化与智能体执行 · 25 分")
P(doc, "眼镜只负责采集、显示、按键；服务端用 LangGraph 编排：转写 → 认人 → 抽要点 → 提词 → 渲染 → 入库。", 9.3, False, BODY, after=1.2, line=12.4)
bullet(doc, "首片 15 秒只认人；提词带档案；歧义先确认，文本指令只改画面。", size=9.1, line=12.0)
bullet(doc, "模型失败用规则兜底；静音不编字；旧结果作废。", size=9.1, line=12.0)
bullet(doc, "48 小时优先主链路；会议分人用现成模型，待办用关键词规则；不做人脸、不训练模型。", size=9.1, line=12.0, after=1.0)

# 05
section_head("05", "多模态协同", "多模态协同 · 20 分")
P(doc, "语音负责当下，文本负责精确修正；同一个任务，两条路径，同一份 view。", 9.3, False, BODY, after=1.0, line=12.4)
bullet(doc, "语音认王姐 → 文本「换成 Lena」→ 再语音仍 Lena；「换成王」先待确认。", size=9.1, line=12.0)
bullet(doc, "纯文本续片沿用 person_id；换人必须显式「换成 名字」。", size=9.1, line=12.0, after=1.0)

# 06
section_head("06", "实时性与可靠性", "实时性与可靠性 · 10 分")
P(doc, "P50 0.998s / P95 1.427s / 文本 22ms；10 次连续提词，700ms 轮询，outbox 8s 补传。", 9.3, False, BODY, after=1.0, line=12.4)

# 07
section_head("07", "会议模式", "第二场景")
P(doc, "15s/55s 切片、说话人分离、逐段转写、行动项、归档；限制：抢话可能并人，待办为关键词规则。", 9.3, False, BODY, after=1.0, line=12.4)

# 08
section_head("08", "验收与边界", "交付与真实性 · 10 分")
P(doc, "8 项验收、24 项检查全部通过；10 次连续延迟；提交包不含密钥。", 9.3, True, GREEN, after=1.5, line=12.4)

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
ag = table_h(2, 4, [PAGE_W / 4] * 4, borders=False)
for i, (a, b, c3) in enumerate(acc):
    c = ag.cell(i // 4, i % 4)
    cell_margins(c, top=0.08, bottom=0.08)
    p = cp(c, "", line=9.6)
    R(p, "✓ ", 7.8, True, ACCENT)
    R(p, a, 8.1, True, INK)
    p = cp(c, b, 7.4, False, BODY, line=9.2)
    p = cp(c, c3, 6.7, True, GREEN, line=8.0)

p = P(doc, "", before=2.0, after=0.8, line=12.4)
R(p, "边界：", 9.3, True, INK)
R(p, "不做人脸；声纹为合成语音实测；会议待办为关键词规则；50 条人物为构造数据；AI 建议需人工确认。", 9.3, False, BODY)
p = P(doc, "", before=0, after=1.0, line=12.4)
R(p, "风险：", 9.3, True, INK)
R(p, "嘈杂环境声纹下降；会议抢话会并人；提词可能跑偏；尚无真实用户数据。", 9.3, False, BODY)
p = P(doc, "", before=0, after=0, line=12.4)
R(p, "下一步：", 9.3, True, INK)
R(p, "10 个真实用户试用；接入日历 / CRM / 名片；优化会议分人。", 9.3, False, BODY)

# 09
section_head("09", "团队分工", "交付与真实性 · 10 分")
p = P(doc, "", before=0, after=0.6, line=12.4)
R(p, "平台与开源提供：", 9.3, True, INK)
R(p, "IrisView FusionUI、LEION 模拟器、DeepSeek API、CAM++、SenseVoice。", 9.3, False, BODY)
p = P(doc, "", before=0, after=0, line=12.4)
R(p, "团队新增开发：", 9.3, True, INK)
R(p, "LangGraph 7 节点、22 个接口、人脉库 / 两档记忆、五状态 HUD、outbox、控制台、验收脚本。", 9.3, False, BODY)

section_head("10", "项目亮点", "")
bullet(doc, "同一条链路完成声纹认人、档案调取、三条提词与归档，镜片只保留当前对话需要的 2–3 行。", size=9.1, line=12.0)
bullet(doc, "两档记忆控制信息量；确认、权限、旧结果作废，处理原型阶段的不确定性。", size=9.1, line=12.0)
bullet(doc, "结果有脚本和原始数据：8 项 / 24 检查、10 次连续延迟；提交包不含密钥。", size=9.1, line=12.0)

# ---------------------------------------------------------------- save
doc.core_properties.title = "MemoryLens 记忆眼镜 · JUNCTION CHINA 2026 智能穿戴赛道作品说明"
doc.core_properties.author = "MemoryLens Team"
doc.core_properties.subject = "ARUI · 自动化 · 多模态：AR 眼镜实时交互多模态智能体"
out_docx = os.path.join(OUT, "MemoryLens-作品说明-重点版.docx")
doc.save(out_docx)
print("saved", out_docx)
