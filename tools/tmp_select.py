# -*- coding: utf-8 -*-
"""模式选择改成平台原生 Select 组件；聊天行仍用 Text（等下一步再换 ScrollView）。"""
import io

P = r"E:\ar-memory\glasses\src\index.js"
s = io.open(P, encoding="utf-8").read()

old = '''function paintMode() {
  if (!modeSelect) return;
  putText(retained.status, "选模式 · 左右选 · 确认进入");
  putText(retained.name, "开始记录");
  putText(retained.role, "先选这一段是什么");
  putText(retained.sim, " ");
  const rows = MODE_ITEMS.map((m, i) => (i === modeSelect.index ? "【" + m.label + "】" : "  " + m.label));
  retained.info.forEach((t, i) => putText(t, rows[i] || ""));
  retained.info[2] && putText(retained.info[2], MODE_ITEMS[modeSelect.index].hint);
  putText(retained.foot, "确认进入 · 返回关掉");
}'''

new = '''// 平台自带 Select（下拉/选项）——模式选择不再用一排 Text 手搓
let modeSel = null;
let modeSelTried = false;

function ensureModeSel() {
  if (modeSelTried) return modeSel;
  modeSelTried = true;
  try {
    if (!ui || !ui.Select || typeof ui.Select.create !== "function") return null;
    const s = ui.Select.create();
    s.setParent(retained.board);
    s.setIgnoreLayout(true);
    s.setPosition([8, 126, 0]);
    s.setSize([300, 44, 0]);
    s.setOptions(MODE_ITEMS.map((m) => m.label));
    s.getLocalStyle().setTextColor(GREEN);
    modeSel = s;
  } catch (e) {
    modeSel = null;
  }
  return modeSel;
}

function hideModeSel() {
  if (modeSel) {
    try { modeSel.setHidden(true); } catch (e) { /* 旧版本没有 hidden */ }
  }
}

function paintMode() {
  if (!modeSelect) return;
  putText(retained.status, "选模式 · 左右选 · 确认进入");
  putText(retained.name, "开始记录");
  putText(retained.role, "先选这一段是什么");
  putText(retained.sim, " ");
  const sel = ensureModeSel();
  if (sel) {
    // 有原生组件就用它：选项、选中态、高亮都由平台画
    try {
      sel.setSelectedIndex(modeSelect.index);
      sel.setHidden(false);
    } catch (e) { /* 忽略 */ }
    retained.info.forEach((t) => putText(t, " "));
    putText(retained.info[0], MODE_ITEMS[modeSelect.index].hint);
  } else {
    const rows = MODE_ITEMS.map((m, i) => (i === modeSelect.index ? "【" + m.label + "】" : "  " + m.label));
    retained.info.forEach((t, i) => putText(t, rows[i] || ""));
    putText(retained.info[2], MODE_ITEMS[modeSelect.index].hint);
  }
  putText(retained.foot, "确认进入 · 返回关掉");
}'''

assert old in s
s = s.replace(old, new, 1)

# 其它画面统一把 Select 藏起来
for fn in ("paintIdle(msg) {", "paintCard(card, rev, ms) {", "paintListening() {", "paintText() {"):
    if fn in s:
        s = s.replace(fn, fn + "\n  hideModeSel();", 1)

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("patched")
