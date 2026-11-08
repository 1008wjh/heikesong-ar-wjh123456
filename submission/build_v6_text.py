# -*- coding: utf-8 -*-
"""Text-only, resume-toned 2-page MemoryLens brief (DOCX)."""
import os
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

ROOT = r"E:\ar-memory"
OUT = os.path.join(ROOT, "submission")

INK = RGBColor(0x17, 0x22, 0x1D)
BODY = RGBColor(0x2B, 0x35, 0x30)
MUTED = RGBColor(0x6A, 0x78, 0x71)
GREEN = RGBColor(0x0B, 0x6B, 0x4A)
ACCENT = RGBColor(0x12, 0x9B, 0x62)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

FONT = "Segoe UI"
EA = "微软雅黑"
PAGE_W = 17.8
BORDER = "D8E2DC"
HEAD_FILL = "F3F6F4"


def _ea(run):
    rPr = run._element.get_or_add_rPr()
    rf = rPr.get_or_add_rFonts()
    rf.set(qn("w:eastAsia"), EA)


def R(p, text, size=9.5, bold=False, color=BODY, font=FONT, italic=False):
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = color
    r.font.name = font
    _ea(r)
    return r


def P(container, text="", size=9.5, bold=False, color=BODY, align=None,
      before=0, after=0, line=13.0, left=0, first=0, keep=False):
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


def bullet(container, text, size=9.3, line=12.6, color=BODY, before=0.5, after=0):
    p = container.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = Pt(line)
    pf.left_indent = Cm(0.4)
    pf.first_line_indent = Cm(-0.4)
    R(p, "•  ", size=size, color=GREEN, bold=True)
    R(p, text, size=size, color=color)
    return p


def section_head(num, title, tag=""):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(8)
    pf.space_after = Pt(2)
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
    R(p, num, 10.0, True, GREEN)
    R(p, "  " + title, 11.8, True, INK)
    if tag:
        R(p, "\t" + tag, 8.0, False, MUTED)
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


def table_h(rows, cols, widths, header=True):
    t = doc.add_table(rows=rows, cols=cols)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    tblPr = t._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{side}")
        if side in ("top", "bottom", "insideH"):
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), "4")
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), BORDER)
        else:
            el.set(qn("w:val"), "nil")
        borders.append(el)
    tblPr.append(borders)
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


def cp(cell, text="", size=8.3, bold=False, color=BODY, align=None,
       before=0, after=0, line=10.6):
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
sec.top_margin = Cm(1.15)
sec.bottom_margin = Cm(0.95)
sec.left_margin = Cm(1.6)
sec.right_margin = Cm(1.6)
sec.header_distance = Cm(0.4)
sec.footer_distance = Cm(0.3)

normal = doc.styles["Normal"]
normal.font.name = FONT
normal.font.size = Pt(9.5)
normal.font.color.rgb = BODY
rPr = normal.element.get_or_add_rPr()
rf = rPr.get_or_add_rFonts()
rf.set(qn("w:ascii"), FONT)
rf.set(qn("w:hAnsi"), FONT)
rf.set(qn("w:eastAsia"), EA)
normal.paragraph_format.space_before = Pt(0)
normal.paragraph_format.space_after = Pt(0)
normal.paragraph_format.line_spacing = Pt(13)

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
p = P(doc, "", before=0, after=1, line=30)
R(p, "MemoryLens 记忆眼镜", 23, True, INK)
p = P(doc, "", before=0, after=1, line=15)
R(p, "面向一对一社交的 AR 记忆眼镜", 11.8, True, GREEN)
p = P(doc, "", before=0, after=2, line=11.5)
R(p, "声纹认人 · 镜片提示 · 对话归档", 9.4, False, MUTED)
p = P(doc, "", before=0, after=0, line=10.5)
R(p, "JUNCTION CHINA 2026 · 智能穿戴赛道 · 亮亮视野 LEION    作品说明 / 2026-11-08", 8.2, False, MUTED)

# green rule
spacer(2.5)
rl = table_h(1, 1, [PAGE_W], header=False)
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
section_head("01", "项目概述", "场景价值与创新 · 10 分")
p = P(doc, "", before=0, after=1.5, line=13.2)
R(p, "项目描述：", 9.5, True, INK)
R(p, "MemoryLens 是一副面向一对一社交的 AR 记忆眼镜。声纹识别对话对象，镜片显示对方经授权的档案、上次对话要点与三条提词；按返回，对话归档到本人人脉库。", 9.5, False, BODY)

p = P(doc, "", before=0, after=1.5, line=13.2)
R(p, "技术栈：", 9.5, True, INK)
R(p, "Python 3.12、LangGraph、CAM++、SenseVoice、DeepSeek、IrisView FusionUI、HTML/JS、Docker。", 9.5, False, BODY)

p = P(doc, "", before=0, after=1.0, line=13.2)
R(p, "运行环境：", 9.5, True, INK)
R(p, "IrisView 模拟器；后端 Python 3.12；控制台为浏览器页面。", 9.5, False, BODY)

# ---------------------------------------------------------------- 02
section_head("02", "场景与价值", "场景价值与创新 · 10 分")
P(doc, "商业场合的第一次见面，往往决定还有没有第二次。名片留下联系方式，MemoryLens 留下下一次开口的理由。", 9.5, False, BODY, after=1.2, line=13.2)
P(doc, "多数人的关系数据分散在通讯录、聊天记录和记忆里，见面时无法调用。MemoryLens 把声纹身份、对话要点、承诺与偏好整理成一份可检索的关系记忆库。", 9.5, False, BODY, after=1.2, line=13.2)

p = P(doc, "", before=0, after=0.8, line=13.2)
R(p, "两类用户：", 9.5, True, INK)

bullet(doc, "慢热、在社交场合容易紧张的人：降低开口成本，不用在几秒内搜索名字、上次话题和承诺。", size=9.3, line=12.6)
bullet(doc, "销售、创始人、展会与校友会常客：掌握对话节奏，主动提问、主动兑现、主动推进。", size=9.3, line=12.6, after=1.5)

p = P(doc, "", before=0, after=1.2, line=13.2)
R(p, "与现有方案的区别：", 9.5, True, INK)
R(p, "会议转写记录内容，不解决下一次开口；名片 / CRM 事后录入，对话中无法调用；通用助手不掌握这个人的档案。MemoryLens 在对话中，按人、按档案给出下一句。", 9.5, False, BODY)

p = P(doc, "", before=0, after=1.2, line=13.2)
R(p, "为什么是眼镜：", 9.5, True, INK)
R(p, "手机也能查，但低头、解锁、滑屏会打断对话。眼镜是抬头就能看、双手不停的屏幕；声纹是不用正脸、不挑光线的身份入口。平台没有开放摄像头，本作品以声纹为主入口，不做人脸识别。", 9.5, False, BODY)

p = P(doc, "", before=0, after=1.2, line=13.2)
R(p, "关系数据三层：", 9.5, True, INK)
R(p, "身份层（声纹，确认是谁）、记忆层（要点 / 承诺 / 偏好，聊过什么）、关系层（上次见面 / 待办 / 跟进，走到哪）。三层数据存在本机，可查看、修改、删除；音频只在本机转写，模型只接收文本；对方同意是前提。", 9.5, False, BODY)

# ---------------------------------------------------------------- 03
section_head("03", "方案与交互", "ARUI 设计与交互 · 25 分")
p = P(doc, "", before=0, after=1.2, line=13.2)
R(p, "ARUI 约束：", 9.5, True, INK)
R(p, "640×480 单色，单行 Text 约 110px。HUD 采用窄列：每行 ≤6 个汉字、最多 6 行、超长轮播；长内容不溢出，也不遮视线。", 9.5, False, BODY)

p = P(doc, "", before=0, after=1.2, line=13.2)
R(p, "信息层级：", 9.5, True, INK)
R(p, "状态 / 计时 → 姓名 → 身份关系 → 2–3 行记忆 → 按键提示。第一眼看到的是人，不是菜单。", 9.5, False, BODY)

p = P(doc, "", before=0, after=1.2, line=13.2)
R(p, "低干扰：", 9.5, True, INK)
R(p, "提词只占下半屏，20 秒自动收起，录音不中断；待命不显示任何人名；多人场景不混入单人档案。", 9.5, False, BODY)

p = P(doc, "", before=1.0, after=1.2, line=12, keep=True)
R(p, "五种状态", 9.4, True, INK)
st = table_h(6, 3, [3.4, 8.2, 6.2])
heads = ["状态", "用户看到", "触发"]
for i, h in enumerate(heads):
    c = st.cell(0, i)
    cell_margins(c)
    cp(c, h, 8.2, True, INK)
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
        cp(c, val, 8.1, ci == 0, INK if ci == 0 else BODY)

p = P(doc, "", before=2.0, after=1.2, line=12, keep=True)
R(p, "两次连续调整", 9.4, True, INK)
p = P(doc, "同一 session 内完成，ARUI 自动更新，不重进页面。", 8.6, False, MUTED, before=0, after=1.0, line=10.8)
at = table_h(5, 3, [5.0, 9.8, 3.0])
for i, h in enumerate(["输入", "镜片变化", "类型"]):
    c = at.cell(0, i)
    cell_margins(c)
    cp(c, h, 8.2, True, INK)
adj = [
    ("「只看待办」", "只剩日期 / 跟进", "内容"),
    ("「优先日期」", "日期置顶，layout=focus", "重点 / 布局"),
    ("「只保留日期」", "3 行 → 1 行", "精简"),
    ("「显示全部」", "还原 3 行", "内容"),
]
for r, row in enumerate(adj, start=1):
    for ci, val in enumerate(row):
        c = at.cell(r, ci)
        cell_margins(c)
        cp(c, val, 8.1, ci == 0, INK if ci == 0 else BODY)

p = P(doc, "", before=2.0, after=1.0, line=13.2)
R(p, "典型使用：", 9.5, True, INK)
R(p, "戴上眼镜 → 确认 → 选「单人」→ 与对方交谈 → 15 秒后镜片显示档案 → 按确认获得三条提词 → 按返回归档。", 9.5, False, BODY)

p = P(doc, "", before=1.0, after=0.8, line=13.2)
R(p, "设计原则：", 9.5, True, INK)
bullet(doc, "镜片只显示当前对话需要的信息，不做实时字幕。", size=9.3, line=12.6)
bullet(doc, "写入档案的要点带原话，可回溯、可删改。", size=9.3, line=12.6)
bullet(doc, "模型失败时宁可显示「没声音」，也不让模型编字。", size=9.3, line=12.6)

# ---------------------------------------------------------------- page 2
p = doc.add_paragraph()
p.add_run().add_break(WD_BREAK.PAGE)
p.paragraph_format.space_before = Pt(0)
p.paragraph_format.space_after = Pt(0)
p.paragraph_format.line_spacing = Pt(1)

# 04
section_head("04", "系统与自动化", "自动化与智能体执行 · 25 分")
p = P(doc, "", before=0, after=1.2, line=13.2)
R(p, "编排：", 9.5, True, INK)
R(p, "一张 LangGraph 图，7 个节点：ingest → route → identify → extract → recommend → render → commit。声纹用 CAM++，转写用本地 SenseVoice，提词用 DeepSeek。", 9.5, False, BODY)

p = P(doc, "", before=0, after=0.8, line=13.2)
R(p, "实现要点：", 9.5, True, INK)

bullet(doc, "首片只认人：15 秒首片不跑提词，避免刚开口就给错建议。", size=9.3, line=12.6)
bullet(doc, "ARUI 自动更新：render 把状态、布局、rows 组装成 view；commit 把要点写回档案。", size=9.3, line=12.6)
bullet(doc, "提词带档案：simple / complex 与本段转写一起进提示词，建议针对当前这个人。", size=9.3, line=12.6)
bullet(doc, "确认与权限：歧义先待确认；文本指令只改画面不写档案；多人模式不入库；旧请求过期不写。", size=9.3, line=12.6)
bullet(doc, "失败可降级：503 / 坏 JSON → 规则兜底标「兜底」；静音 → 「没声音」；慢请求 stale 作废。", size=9.3, line=12.6, after=1.2)

p = P(doc, "", before=0, after=0, line=13.2)
R(p, "限时取舍：", 9.5, True, INK)
R(p, "48 小时优先保证主链路可跑——真人声纹、提词、入库、五状态。会议分人复用现成模型，待办用关键词规则；没有做人脸、没有训练模型、没有做云端多租户。", 9.5, False, BODY)

# 05
section_head("05", "多模态协同", "多模态协同 · 20 分")
P(doc, "语音负责当下，文本负责精确修正。同一个任务，两条路径，同一份 view。", 9.5, False, BODY, after=0.8, line=13.2)
bullet(doc, "跨方式：语音认王姐 → 文本「换成 Lena」→ 人变 Lena → 再语音（旧 person_id）→ 仍 Lena。", size=9.3, line=12.6)
bullet(doc, "纠正：「换成王」→ 待确认，列出王姐 / 王兴 / 王石；「就是他」→ 才执行。", size=9.3, line=12.6)
bullet(doc, "上下文：纯文本续片沿用上一片 person_id；换人必须显式「换成 名字」，避免身份静默漂移。", size=9.3, line=12.6, after=1.2)

# 06
section_head("06", "实时性与可靠性", "实时性与可靠性 · 10 分")
p = P(doc, "", before=0, after=1.2, line=13.2)
R(p, "P50 0.998s / P95 1.427s / 文本 22ms。", 9.5, True, INK)
R(p, " 10 次连续提词，本地 SenseVoice + DeepSeek，含 HTTP 往返。", 9.5, False, BODY)
P(doc, "700ms 轮询，rev 变化才重绘；离线 outbox 8s 补传；单段最长 30 分钟。", 9.5, False, BODY, after=1.0, line=13.2)

# 07
section_head("07", "会议模式", "第二场景")
P(doc, "同一套能力迁移到多人会议：15s/55s 自动切片、说话人分离、逐段转写、行动项、会后归档；控制台可复盘。", 9.5, False, BODY, after=1.0, line=13.2)
P(doc, "限制：抢话 / 重叠可能并人；待办为关键词规则版。会议模式是第二场景，不改变一对一社交主线。", 9.5, False, BODY, after=0.5, line=13.2)

# 08
section_head("08", "验收与边界", "交付与真实性 · 10 分")
p = P(doc, "", before=0, after=1.2, line=13.2)
R(p, "8 项验收、24 项检查全部通过", 9.5, True, GREEN)
R(p, "；10 次连续延迟；提交包不含密钥。", 9.5, False, BODY)

p = P(doc, "", before=0, after=1.5, line=13.2)
R(p, "验收方式：", 9.5, True, INK)
R(p, "test/acceptance_8.py 跑 8 项用例（24 个检查）；test/latency_10.py 连续 10 次采样。静音为真实失败路径，503 / 坏 JSON 为注入测试。", 9.5, False, BODY)

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
ag = table_h(3, 4, [PAGE_W / 4] * 4, header=False)
for i, (a, b, c3) in enumerate(acc):
    c = ag.cell(i // 4 + 1, i % 4)
    cell_margins(c, top=0.09, bottom=0.09)
    p = cp(c, "", line=9.8)
    R(p, "✓ ", 8.0, True, ACCENT)
    R(p, a, 8.4, True, INK)
    p = cp(c, b, 7.6, False, BODY, line=9.4)
    p = cp(c, c3, 6.9, True, GREEN, line=8.2)

# remove the empty first row (placeholder for header=False)
# Move cells up by deleting row 0
tr = ag.rows[0]._tr
tr.getparent().remove(tr)

p = P(doc, "", before=2.0, after=1.0, line=13.2)
R(p, "边界：", 9.5, True, INK)
R(p, "不做人脸；声纹指标为合成语音实测；会议待办为关键词规则，抢话可能并人；50 条人物为构造演示数据；AI 建议可能跑偏，控制台入库前需人工勾选。", 9.5, False, BODY)

p = P(doc, "", before=0, after=1.2, line=13.2)
R(p, "风险与不足：", 9.5, True, INK)
R(p, "嘈杂环境声纹下降；会议抢话会并人；提词可能跑偏；尚无真实用户数据。", 9.5, False, BODY)

p = P(doc, "", before=0, after=0, line=13.2)
R(p, "下一步：", 9.5, True, INK)
R(p, "找 10 个真实用户做一周试用，把「敢不敢开口」变成可测量指标；在授权前提下接入日历、CRM、名片；补充真实场景声纹数据，优化会议分人。", 9.5, False, BODY)

# 09
section_head("09", "团队分工", "交付与真实性 · 10 分")
p = P(doc, "", before=0, after=1.0, line=13.2)
R(p, "平台与开源提供：", 9.5, True, INK)
R(p, "IrisView FusionUI、LEION 模拟器、DeepSeek API、CAM++、SenseVoice。", 9.5, False, BODY)
p = P(doc, "", before=0, after=0, line=13.2)
R(p, "团队新增开发：", 9.5, True, INK)
R(p, "LangGraph 7 节点智能体、22 个 HTTP 接口、人脉库与两档记忆、五状态 HUD、outbox、控制台、切片 / 阈值策略、8 项验收与 10 次延迟脚本。", 9.5, False, BODY)

section_head("10", "项目亮点", "")
bullet(doc, "同一条链路完成声纹认人、档案调取、三条提词与归档，镜片只保留当前对话需要的 2–3 行。", size=9.3, line=12.6)
bullet(doc, "两档记忆控制信息量：simple 上镜，complex 留在服务端；确认、权限、stale 作废处理原型阶段的不确定性。", size=9.3, line=12.6)
bullet(doc, "所有结果有脚本与原始数据：8 项 / 24 项检查、10 次连续延迟；提交包不含密钥。", size=9.3, line=12.6)

# ---------------------------------------------------------------- save
doc.core_properties.title = "MemoryLens 记忆眼镜 · JUNCTION CHINA 2026 智能穿戴赛道作品说明"
doc.core_properties.author = "MemoryLens Team"
doc.core_properties.subject = "ARUI · 自动化 · 多模态：AR 眼镜实时交互多模态智能体"
out_docx = os.path.join(OUT, "MemoryLens-作品说明-文字版.docx")
doc.save(out_docx)
print("saved", out_docx)
