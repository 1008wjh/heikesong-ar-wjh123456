# -*- coding: utf-8 -*-
"""按新流程改测试：进在听不自动切片；按确认→识别人→认出王姐→出三按钮。"""
import io

p = r"E:\ar-memory\test\glasses_sim.js"
s = io.open(p, encoding="utf-8").read()

# 识别人时间调短，别让测试等 20 秒
s = s.replace('process.env.MEMORYLENS_FIRST_SLICE_MS = process.env.MEMORYLENS_FIRST_SLICE_MS || "15000";',
              'process.env.MEMORYLENS_FIRST_SLICE_MS = process.env.MEMORYLENS_FIRST_SLICE_MS || "15000";\n'
              'process.env.MEMORYLENS_IDENTIFY_MS = process.env.MEMORYLENS_IDENTIFY_MS || "3000";', 1)

old = '''  await waitUntil(() => hud().includes("王姐"), 30000);
  let lines = hud();
  check("切片后认出王姐", lines.includes("王姐"), JSON.stringify(lines));
  check("卡片带相似度", lines.some((v) => v.includes("SIM")), JSON.stringify(lines));
  check("HUD 出现身份", lines.some((v) => v.startsWith("产品经理")), JSON.stringify(lines));
  check("第一片回的是数据库内容（身份 + 信息，不是 AI 总结）",
    lines.join("").includes("产品经理") && (lines.join("").includes("报价单") || lines.join("").includes("豆豆")),
    JSON.stringify(lines));

  // 1b) 录音中按「确认」→ 进入「识别人」（新流程：先认出对方，再出三个按钮）
  await sleep(900);
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("识别人")), 6000);
  lines = hud();
  check("按确认进入识别人（状态行显示 识别人 N/20）",
    lines.some((v) => v.includes("识别人")), JSON.stringify(lines));

  // 识别人期间：卡片仍然保留在上面（没有被刷成空白）
  lines = hud();
  check("识别人期间上一张卡片还在（没被刷掉）",
    lines.some((v) => v.includes("王姐")), JSON.stringify(lines));
  navQueue.push("back");   // 退出识别人（返回 = 结束这段）
'''

new = '''  // 进在听之后不再自动切片：界面上只有「识别人」
  let lines = hud();
  check("进在听只给「识别人」按钮", lines.some((v) => v.includes("识别人")), JSON.stringify(lines));
  check("没有自动切片（还没卡片）", !lines.some((v) => v.includes("王姐")), JSON.stringify(lines));

  // 1b) 按「确认」→ 录一段做分轨认人
  navQueue.push("activate");
  await waitUntil(() => hud().some((v) => v.includes("识别人 ")), 4000);
  check("识别人开始计时", hud().some((v) => v.includes("识别人 ")), JSON.stringify(hud()));

  await waitUntil(() => hud().includes("王姐"), 20000);
  lines = hud();
  check("识别人认出王姐", lines.includes("王姐"), JSON.stringify(lines));
  check("卡片带相似度", lines.some((v) => v.includes("SIM")), JSON.stringify(lines));
  check("HUD 出现身份", lines.some((v) => v.startsWith("产品经理")), JSON.stringify(lines));
  check("认出之后出现三个预设按钮",
    lines.some((v) => v.includes("提词")) || lines.some((v) => v.includes("待办")), JSON.stringify(lines));
'''
assert old in s, "old block"
s = s.replace(old, new, 1)

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
