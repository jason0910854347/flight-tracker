"""
Local design preview — renders the exact card JSON that would be sent:
    left  : LINE Flex Message (mini renderer for the components this project uses)
    right : Adaptive Card (official adaptivecards.js with a Teams-like light host config)

Open preview/cards_preview.html in a browser (python app.py --preview).
"""

import json

_TEMPLATE = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>Fare Card Preview</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/adaptivecards@3.0.4/dist/adaptivecards.min.js"></script>
<style>
  body { margin: 0; background: #F0F2F5; font-family: "Segoe UI", "Noto Sans TC", "Microsoft JhengHei", sans-serif; color: #202124; }
  h1 { font-size: 20px; margin: 24px 24px 4px; } p.sub { margin: 0 24px 16px; color: #5F6368; font-size: 13px; }
  .cols { display: flex; gap: 32px; padding: 0 24px 32px; align-items: flex-start; flex-wrap: wrap; }
  .col h2 { font-size: 14px; color: #5F6368; margin: 8px 0 12px; letter-spacing: .5px; }
  .line-bg { background: #8CABD9; padding: 16px; border-radius: 12px; display: flex; flex-direction: column; gap: 16px; }
  .carousel { display: flex; gap: 10px; flex-wrap: wrap; }
  .bubble { width: 300px; background: #fff; border-radius: 16px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.15); display: flex; flex-direction: column; }
  .box { display: flex; min-width: 0; box-sizing: border-box; }
  .t { min-width: 0; line-height: 1.35; }
  .t.nowrap { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .t.wrap { white-space: pre-wrap; word-break: break-word; }
  .btn { display: flex; align-items: center; justify-content: center; color: #fff; border-radius: 8px; height: 40px; font-size: 15px; font-weight: 600; text-decoration: none; }
  .ac-wrap { width: 420px; display: flex; flex-direction: column; gap: 16px; }
  .ac-card { background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.15); overflow: hidden; }
</style></head>
<body>
<h1>✈ 機票價格監聽 — 卡片設計預覽</h1>
<p class="sub">以下為實際推送的 JSON 渲染結果（範例資料）。左：LINE Flex Message；右：Adaptive Card（Webhook / Teams）。</p>
<div class="cols">
  <div class="col"><h2>LINE FLEX MESSAGE</h2><div class="line-bg" id="line"></div></div>
  <div class="col"><h2>ADAPTIVE CARD</h2><div class="ac-wrap" id="ac"></div></div>
</div>
<script>
const LINE = __LINE__;
const CARDS = __CARDS__;

// ── Mini LINE Flex renderer (subset) ──
const SIZE = { xxs: 11, xs: 13, sm: 14, md: 16, lg: 19, xl: 22, xxl: 29 };
const GAP = { none: 0, xs: 2, sm: 4, md: 8, lg: 12, xl: 16, xxl: 20 };
const px = v => v == null ? null : (v in GAP ? GAP[v] + 'px' : v);
function applyFlex(el, node, parentLayout) {
  if (node.flex !== undefined) el.style.flex = node.flex === 0 ? '0 0 auto' : node.flex + ' 1 0';
  else if (parentLayout === 'horizontal') el.style.flex = '1 1 0';
  if (node.margin) el.style[parentLayout === 'horizontal' ? 'marginLeft' : 'marginTop'] = px(node.margin);
  if (node.gravity) el.style.alignSelf = { top: 'flex-start', center: 'center', bottom: 'flex-end' }[node.gravity];
}
function render(node, parentLayout) {
  if (node.type === 'box') {
    const el = document.createElement('div'); el.className = 'box';
    el.style.flexDirection = node.layout === 'vertical' ? 'column' : 'row';
    if (node.backgroundColor) el.style.background = node.backgroundColor;
    if (node.cornerRadius) el.style.borderRadius = px(node.cornerRadius);
    if (node.paddingAll) el.style.padding = px(node.paddingAll);
    ['Top', 'Bottom', 'Start', 'End'].forEach(k => { if (node['padding' + k]) el.style['padding' + ({Start: 'Left', End: 'Right'}[k] || k)] = px(node['padding' + k]); });
    if (node.spacing) el.style.gap = px(node.spacing);
    if (node.alignItems) el.style.alignItems = node.alignItems;
    if (node.width) { el.style.width = node.width; el.style.flex = '0 0 ' + node.width; }
    (node.contents || []).forEach(c => el.appendChild(render(c, node.layout)));
    applyFlex(el, node, parentLayout); if (node.width) el.style.flex = '0 0 ' + node.width;
    return el;
  }
  if (node.type === 'text') {
    const el = document.createElement('div'); el.className = 't ' + (node.wrap ? 'wrap' : 'nowrap');
    el.textContent = node.text; el.style.fontSize = (SIZE[node.size] || 16) + 'px';
    el.style.color = node.color || '#111'; if (node.weight === 'bold') el.style.fontWeight = '700';
    if (node.align) el.style.textAlign = { start: 'left', end: 'right', center: 'center' }[node.align];
    if (node.decoration === 'line-through') el.style.textDecoration = 'line-through';
    applyFlex(el, node, parentLayout); return el;
  }
  if (node.type === 'separator') {
    const el = document.createElement('div'); el.style.borderTop = '1px solid ' + (node.color || '#ddd');
    el.style.alignSelf = 'stretch'; if (node.margin) el.style.marginTop = px(node.margin); return el;
  }
  if (node.type === 'button') {
    const el = document.createElement('a'); el.className = 'btn'; el.href = node.action.uri; el.target = '_blank';
    el.textContent = node.action.label;
    if (node.style === 'secondary') { el.style.background = '#F1F3F4'; el.style.color = '#202124'; }
    else el.style.background = node.color || '#06C755';
    if (node.margin) el.style.marginTop = px(node.margin); return el;
  }
  return document.createElement('div');
}
function bubble(b) {
  const el = document.createElement('div'); el.className = 'bubble';
  ['header', 'body', 'footer'].forEach(k => { if (b[k]) { const s = render(b[k], 'vertical'); s.style.flex = 'none'; el.appendChild(s); } });
  return el;
}
const line = document.getElementById('line');
LINE.forEach(msg => {
  const wrap = document.createElement('div'); wrap.className = 'carousel';
  const c = msg.contents; (c.type === 'carousel' ? c.contents : [c]).forEach(b => wrap.appendChild(bubble(b)));
  line.appendChild(wrap);
});

// ── Adaptive Cards (Teams-like light host config) ──
const host = new AdaptiveCards.HostConfig({
  fontFamily: "Segoe UI, Noto Sans TC, Microsoft JhengHei, sans-serif",
  spacing: { small: 4, default: 8, medium: 16, large: 20, extraLarge: 24, padding: 16 },
  fontSizes: { small: 12, default: 14, medium: 16, large: 20, extraLarge: 26 },
  containerStyles: {
    default:   { backgroundColor: "#FFFFFF", foregroundColors: { default: { default: "#242424", subtle: "#616161" }, accent: { default: "#1A73E8" }, good: { default: "#188038" }, attention: { default: "#C4314B" }, warning: { default: "#B35C00" } } },
    emphasis:  { backgroundColor: "#F5F5F5", foregroundColors: { default: { default: "#242424", subtle: "#616161" }, accent: { default: "#1A73E8" } } },
    accent:    { backgroundColor: "#E8F0FE", foregroundColors: { default: { default: "#242424", subtle: "#616161" }, accent: { default: "#1A73E8" } } },
    good:      { backgroundColor: "#E7F2DA", foregroundColors: { default: { default: "#242424", subtle: "#4F5B48" }, good: { default: "#188038" } } },
    attention: { backgroundColor: "#FCF4F6", foregroundColors: { default: { default: "#242424", subtle: "#6B4F55" }, attention: { default: "#C4314B" } } },
    warning:   { backgroundColor: "#FBF6D9", foregroundColors: { default: { default: "#242424", subtle: "#5F5A3C" }, warning: { default: "#B35C00" } } }
  },
  actions: { actionsOrientation: "horizontal", actionAlignment: "stretch" }
});
const acWrap = document.getElementById('ac');
CARDS.forEach(json => {
  const card = new AdaptiveCards.AdaptiveCard(); card.hostConfig = host; card.parse(json);
  const el = document.createElement('div'); el.className = 'ac-card'; el.appendChild(card.render()); acWrap.appendChild(el);
});
document.body.dataset.ready = '1';
</script></body></html>
"""


def build_preview_html(line_messages: list[dict], adaptive_cards: list[dict]) -> str:
    return (_TEMPLATE
            .replace("__LINE__", json.dumps(line_messages, ensure_ascii=False))
            .replace("__CARDS__", json.dumps(adaptive_cards, ensure_ascii=False)))
