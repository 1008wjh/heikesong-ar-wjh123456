const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const res = document.querySelector('.res.unknown');
if (!res) return 'no unknown card: ' + document.getElementById('results').innerText;
res.querySelector('.act button').click();
await sleep(50);
const input = res.querySelector('.act input');
input.value = '测试乙';
const btns = res.querySelectorAll('.act button');
btns[0].click();                       // 保存
await sleep(1200);
const after = document.querySelectorAll('.res');
return JSON.stringify({
  cards: after.length,
  firstClass: after[0] && after[0].className,
  firstText: after[0] && after[0].innerText.replace(/\n/g, ' | '),
  people: [...document.querySelectorAll('#people li')].map((li) => li.innerText.replace(/\n/g, ' ')),
});
