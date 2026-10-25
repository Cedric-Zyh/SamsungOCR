"""Self-contained Chinese HTML for one offline OCR call profile."""
from __future__ import annotations

from html import escape
import math
from pathlib import Path, PurePosixPath
from urllib.parse import quote


KINDS = {'boxes': '框检测', 'line': '文字行识别', 'text': '文字检测与识别', 'seal': '印章专用识别'}


def _text(value):
    return escape(str(value if value is not None else '—'), quote=True)


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _seconds(value):
    return f'{_number(value):.3f} 秒'


def _milliseconds(value):
    milliseconds = _number(value) * 1000
    return f'{milliseconds:,.3f} ms' if abs(milliseconds) < 1 else f'{milliseconds:,.1f} ms'


def _input_url(value):
    """Only sibling inputs/ images; never allow schemes, absolute paths or traversal."""
    if not isinstance(value, str) or '\\' in value or any(ord(character) < 32 for character in value):
        return None
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts or path.parts[0] != 'inputs'
            or '..' in path.parts or path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}):
        return None
    return quote(path.as_posix(), safe='/-._~')


def _call_markup(call, index):
    kind = str(call.get('kind', ''))
    hit = call.get('cache_hit') is True
    native, elapsed = max(0.0, _number(call.get('native_seconds'))), max(0.0, _number(call.get('elapsed_seconds')))
    angle = '—' if call.get('angle') is None else f"{call['angle']}°"
    texts = call.get('texts') or []
    if not isinstance(texts, (list, tuple)):
        texts = [texts]
    readback = ''.join(f'<span class="text-chip">{_text(text)}</span>' for text in texts)
    if not readback:
        readback = '<span class="muted">未读出文字</span>'
    source = _input_url(call.get('input_image'))
    image = (f'<details class="input-image"><summary>输入图 · 点击展开 / 收起</summary>'
             f'<figure><img src="{_text(source)}" loading="lazy" decoding="async" '
             f'alt="第 {index + 1} 次调用的实际输入图">'
             f'<figcaption>{_text(call.get("input_image"))}</figcaption></figure></details>') if source else (
                 '<p class="muted image-unavailable">没有可用的 inputs/ 相对输入图</p>')
    error = f'<p class="call-error"><strong>调用异常：</strong>{_text(call["error"])}</p>' if call.get('error') else ''
    state = '<span class="badge cache">复用结果</span>' if hit else '<span class="badge native">真实推理</span>'
    if not hit and native == 0:
        state = '<span class="badge neutral">未记录真实推理</span>'
    if call.get('error'):
        state += '<span class="badge failed">异常</span>'
    return f'''<article class="call" data-call data-kind="{_text(kind)}" data-sequence="{index}"
      data-native="{native:.12g}" data-elapsed="{elapsed:.12g}" {'hidden' if kind != 'boxes' else ''}>
      <div class="call-heading">
        <div class="call-number">#{_text(call.get('id', index + 1))}</div>
        <div class="call-purpose"><span class="kind">{_text(KINDS.get(kind, '其他调用'))}</span>
          <h3>{_text(call.get('phase') or '未标注用途')}</h3></div>
        <div class="call-state">{state}</div>
      </div>
      <div class="call-facts"><span>角度 <b>{_text(angle)}</b></span>
        <span>输入 <b>{_text(call.get('width'))} × {_text(call.get('height'))}</b> 像素</span>
        <span>尺度 <b>{_text(call.get('scale') or '未标注')}</b></span>
        <span>返回 <b>{_text(call.get('result_count', len(texts)))}</b> 项</span></div>
      <div class="duration-heading"><span>真实推理 <strong>{_milliseconds(native)}</strong></span>
        <span>本次总耗时 <b>{_milliseconds(elapsed)}</b></span></div>
      <div class="duration-track" aria-hidden="true"><span class="duration-total"></span><span class="duration-native"></span></div>
      <div class="duration-details"><span>模型获取 {_milliseconds(call.get('setup_seconds'))}</span>
        <span>等待锁 {_milliseconds(call.get('queue_seconds'))}</span></div>
      <div class="readback"><span class="readback-label">读出文字</span><div>{readback}</div></div>
      {error}{image}
    </article>'''


def _check_markup(checks):
    labels = (('structure_equal', '识别与判定'), ('images_equal', '输入 / 证据图像素'),
              ('native_calls_match', '实际推理计数'))
    entries = []
    for key, label in labels:
        value = checks.get(key)
        status, class_name = ('一致', 'good') if value is True else ('不一致', 'bad') if value is False else ('未核验', 'unknown')
        entries.append(f'<span class="check {class_name}">{label}<b>{status}</b></span>')
    pairs = checks.get('image_pairs', 0)
    if isinstance(pairs, (list, tuple, dict)):
        pairs = len(pairs)
    entries.append(f'<span class="check">图像对照 <b>{_text(pairs)} 对</b></span>')
    return ''.join(entries)


def _timing_markup(timings):
    labels = {'page_ocr': '整页 OCR', 'printed_page_preprocess': '整页输入预处理',
              'paddle_text_inference': '通用文字真实推理', 'paddle_line_inference': '文字行真实推理',
              'paddle_text_box_inference': '框检测真实推理', 'paddle_seal_inference': '印章专用真实推理',
              'paddle_queue_wait': '本地模型等待锁', 'paddle_model_setup': '本地模型获取',
              'paddle_text_box_setup': '框检测模型获取', 'date_recognition': '日期识别流程',
              'local_seal_recognition': '本地印章识别流程', 'preview_generation': '预览图生成',
              'qr_decode': '二维码解码', 'seal_api': '历史清瞳响应重放'}
    steps = timings.get('steps', {}) if isinstance(timings, dict) else {}
    if not steps:
        return ''
    rows = ''.join(f'<tr><td>{_text(labels.get(name, name))}</td><td>{_text(value.get("calls", 0))}</td>'
                   f'<td>{_seconds(value.get("seconds"))}</td></tr>' for name, value in steps.items() if isinstance(value, dict))
    return f'''<details class="timing-overview"><summary>查看流程阶段计时汇总</summary>
      <p class="muted">流程阶段会包含下层调用，且部分任务并行执行；这些数字不能直接相加得到整单耗时。</p>
      <table><thead><tr><th>阶段</th><th>次数</th><th>累计耗时</th></tr></thead><tbody>{rows}</tbody></table></details>'''


STYLES = r'''
:root{color-scheme:light;--ink:#24332e;--muted:#65766e;--line:#dbe4df;--paper:#fff;--green:#28745a;--pale:#e9f4ee;--bad:#a83e37}
*{box-sizing:border-box}body{margin:0;background:#f3f6f3;color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
[hidden]{display:none!important}.page{max-width:1160px;margin:auto;padding:38px 28px 70px}h1,h2,h3,p{margin:0}h1{font-size:30px;line-height:1.3;letter-spacing:-.6px;font-weight:650;overflow-wrap:anywhere}h2{font-size:20px}h3{font-size:15px;font-weight:600;overflow-wrap:anywhere}.eyebrow{font-size:12px;letter-spacing:1px;color:var(--green);margin-bottom:10px}.subtitle{margin-top:10px;color:var(--muted)}
.scope{background:#e9f0eb;border-left:3px solid #6c927c;border-radius:3px;padding:12px 16px;margin:22px 0;color:#43594a}.scope strong{font-weight:600}.scope small{display:block;margin-top:5px;color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.stat{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:18px 20px}.stat-label{color:var(--muted);font-size:12px}.stat-value{font-size:32px;line-height:1.3;font-weight:650;margin:5px 0}.stat-note{font-size:11px;color:var(--muted)}
.elapsed{display:flex;gap:12px 24px;flex-wrap:wrap;margin:18px 0 0;font-size:12px;color:var(--muted)}.elapsed b{font-weight:600;color:var(--ink);margin-left:5px}.checks{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.check{display:inline-flex;gap:7px;align-items:center;padding:5px 9px;border:1px solid var(--line);border-radius:5px;font-size:11px;background:#fff}.check b{font-weight:600}.check.good{color:#306c50;background:#eef7f1}.check.bad{color:var(--bad);border-color:#e8b8b2;background:#fff1ee}.check.unknown{color:#8d6c2f;background:#fbf6e9}.network-warning{color:var(--bad);font-weight:600}
.section-heading{display:flex;align-items:baseline;justify-content:space-between;gap:15px;margin-top:34px}.section-heading p{font-size:12px;color:var(--muted)}.controls{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 0;margin-bottom:12px;border-bottom:1px solid var(--line);flex-wrap:wrap}.view-toggle{display:flex;gap:7px}.controls button,.controls select{font:inherit;font-size:12px;border:1px solid #c8d5cd;border-radius:6px;color:var(--ink);background:#fff;padding:7px 12px;cursor:pointer}.controls button[aria-pressed=true]{background:var(--green);border-color:var(--green);color:#fff}.controls label{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:9px}button:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #80b49d;outline-offset:3px}.legend{display:flex;gap:18px;color:var(--muted);font-size:11px;margin:0 0 12px}.legend span:before{content:"";display:inline-block;width:14px;height:5px;background:#9bbda9;border-radius:3px;vertical-align:2px;margin-right:6px}.legend span+span:before{background:#dbe5df}
.call{background:#fff;border:1px solid var(--line);border-radius:10px;margin-bottom:13px;padding:17px 20px}.call-heading{display:flex;align-items:center;gap:13px}.call-number{font-size:14px;font-variant-numeric:tabular-nums;min-width:40px;color:var(--muted)}.call-purpose{flex:1;min-width:0}.kind{font-size:10px;letter-spacing:.5px;color:var(--green)}.call-state{display:flex;gap:5px;flex-shrink:0}.badge{font-size:10px;font-weight:600;padding:3px 8px;border-radius:4px;white-space:nowrap}.badge.native{background:#eef3ee;color:#576b58}.badge.cache{background:#e3f0f7;color:#3e6c88}.badge.failed{background:#fae9e5;color:var(--bad)}.badge.neutral{background:#f1f0e9;color:#817955}.call-facts{display:flex;gap:8px 23px;flex-wrap:wrap;color:var(--muted);font-size:11px;margin-top:11px}.call-facts b{font-weight:500;color:var(--ink);margin-left:4px}
.duration-heading{display:flex;justify-content:space-between;gap:14px;margin-top:13px;color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}.duration-heading strong{color:var(--green);font-size:16px;font-weight:650;margin-left:6px}.duration-heading b{color:var(--ink);font-size:12px;font-weight:500;margin-left:4px}.duration-track{position:relative;background:#f0f4f1;height:8px;border-radius:5px;overflow:hidden;margin:6px 0}.duration-total,.duration-native{display:block;position:absolute;height:100%;left:0;top:0;border-radius:5px;width:0}.duration-total{background:#dbe5df}.duration-native{background:#659c7e}.duration-details{display:flex;gap:20px;font-size:10px;color:#87948b;font-variant-numeric:tabular-nums}
.readback{display:flex;gap:11px;margin-top:13px;padding-top:12px;border-top:1px solid #edf1ed;font-size:12px}.readback-label{flex-shrink:0;font-size:10px;color:var(--muted);padding-top:4px}.readback>div{min-width:0;max-height:190px;overflow:auto}.text-chip{display:inline-block;white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f2;border-radius:4px;padding:2px 7px;margin:0 5px 5px 0}.muted{color:var(--muted)}.call-error{margin-top:10px;padding:9px 12px;background:#fff0ed;color:var(--bad);font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}.input-image{margin-top:8px;font-size:11px}.input-image summary,.timing-overview summary{cursor:pointer;color:var(--green);width:fit-content;padding:4px 0}.input-image figure{margin:10px 0 0;border:1px solid #dde4dc;background:#f8faf6;padding:12px;border-radius:6px}.input-image img{display:block;max-width:100%;height:auto;max-height:640px;object-fit:contain;margin:0 auto}.input-image figcaption{color:var(--muted);margin-top:8px;overflow-wrap:anywhere;font-size:10px}.image-unavailable{font-size:10px;margin-top:7px}.empty{padding:40px;text-align:center;background:#fff;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}
.timing-overview{margin-top:28px;padding:16px 20px;background:#fff;border:1px solid var(--line);border-radius:9px}.timing-overview p{margin:10px 0;font-size:11px}table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:8px;border-bottom:1px solid #edf1ec}th{color:var(--muted);font-weight:500}th+th,td+td{text-align:right;font-variant-numeric:tabular-nums}footer{font-size:11px;color:var(--muted);margin-top:24px}noscript{display:block;padding:10px;color:#946a24}
@media(max-width:700px){.page{padding:24px 14px 45px}h1{font-size:23px}.stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.stat{padding:13px}.stat-value{font-size:27px}.call{padding:14px}.call-number{min-width:25px;font-size:12px}.call-heading{gap:9px}.call-state{flex-direction:column}.call-facts{gap:5px 13px}.duration-heading{flex-wrap:wrap;gap:0}.section-heading{align-items:flex-start;flex-direction:column;gap:4px}.readback{gap:7px}.controls{align-items:flex-start}.controls label{flex-wrap:wrap}}
'''


SCRIPT = r'''
(() => {
  const calls = [...document.querySelectorAll('[data-call]')];
  const list = document.getElementById('call-list');
  const sort = document.getElementById('sort-order');
  const buttons = [...document.querySelectorAll('[data-view]')];
  let view = 'boxes';
  function render() {
    const visible = calls.filter(row => view === 'all' || row.dataset.kind === 'boxes');
    const maximum = Math.max(0.001, ...visible.map(row => Math.max(Number(row.dataset.native), Number(row.dataset.elapsed))));
    const ordered = [...calls].sort((left, right) => {
      const difference = sort.value === 'native' ? Number(right.dataset.native) - Number(left.dataset.native)
        : sort.value === 'elapsed' ? Number(right.dataset.elapsed) - Number(left.dataset.elapsed) : 0;
      return difference || Number(left.dataset.sequence) - Number(right.dataset.sequence);
    });
    ordered.forEach(row => {
      row.hidden = view !== 'all' && row.dataset.kind !== 'boxes';
      row.querySelector('.duration-native').style.width = Math.min(100, Number(row.dataset.native) / maximum * 100) + '%';
      row.querySelector('.duration-total').style.width = Math.min(100, Number(row.dataset.elapsed) / maximum * 100) + '%';
      list.appendChild(row);
    });
    document.getElementById('shown-count').textContent = '显示 ' + visible.length + ' / ' + calls.length + ' 次调用';
    document.getElementById('empty-state').hidden = visible.length !== 0;
    buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === view)));
  }
  buttons.forEach(button => button.addEventListener('click', () => { view = button.dataset.view; render(); }));
  sort.addEventListener('change', render);
  render();
})();
'''


def write_report(report, output: Path) -> Path:
    """Write report.html in output, or use an explicitly supplied HTML filename."""
    output = Path(output)
    target = output if output.suffix.lower() in {'.html', '.htm'} else output / 'report.html'
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    calls = list(report.get('calls') or [])
    boxes = [call for call in calls if call.get('kind') == 'boxes']
    real_boxes = sum(call.get('cache_hit') is not True and _number(call.get('native_seconds')) > 0 for call in boxes)
    hits = sum(call.get('cache_hit') is True for call in calls)
    others = sum(call.get('kind') != 'boxes' and call.get('cache_hit') is not True
                 and _number(call.get('native_seconds')) > 0 for call in calls)
    native_seconds = sum(max(0.0, _number(call.get('native_seconds'))) for call in calls)
    summary = ''.join(f'<div class="stat"><p class="stat-label">{label}</p><p class="stat-value">{value}</p>'
                      f'<p class="stat-note">{note}</p></div>' for label, value, note in (
                          ('真实框检测', str(real_boxes), f'共记录 {len(boxes)} 次框检测请求'),
                          ('缓存命中', str(hits), '直接复用同次任务的相同输入结果'),
                          ('其他真实识别', str(others), '文字行、整页文字或印章专用调用'),
                          ('累计真实推理', f'{native_seconds:.3f}<small> 秒</small>', '不等于整单经过时间'),
                      ))
    filename = _text(report.get('filename') or '未命名图片')
    attempts = max(0, int(_number(report.get('network_attempts'))))
    network = f'<span class="network-warning">已阻止 {attempts} 次外部联网尝试</span>' if attempts else '<span>外部联网 <b>0 次</b></span>'
    rows = ''.join(_call_markup(call, index) for index, call in enumerate(calls))
    content = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>{filename} · OCR 逐次耗时</title><style>{STYLES}</style></head>
<body><main class="page"><header><p class="eyebrow">本地 OCR · 单张调用记录</p>
<h1>{filename}</h1><p class="subtitle">结果 #{_text(report.get('result_id'))} · 查看复杂印章每一次检测、识别与复用花在哪里。</p></header>
<aside class="scope"><strong>模型已预热，所有本地模型调用均为真实识别。</strong>
清瞳使用历史完整响应即时重放，不包含实际网络等待，也不包含历史视觉参考匹配。
<small>这是一次运行的记录，耗时会受当时机器负载影响。框检测调用包含文字检测与识别；复用调用不会再次执行模型。</small></aside>
<section class="stats" aria-label="调用统计">{summary}</section>
<div class="elapsed"><span>本次记录总耗时 <b>{_seconds(report.get('wall_seconds'))}</b></span>
<span>识别流程耗时 <b>{_seconds(report.get('recognition_seconds'))}</b></span>
<span>含冷启动的预热运行 <b>{_seconds(report.get('warmup_seconds'))}</b></span>
<span>估计记录开销 <b>{_seconds(report.get('trace_overhead_seconds'))}</b></span>{network}</div>
<div class="checks" aria-label="等价性检查">{_check_markup(report.get('checks') or {})}</div>
<div class="section-heading"><h2>逐次调用</h2><p id="shown-count" role="status">显示 {len(boxes)} / {len(calls)} 次调用</p></div>
<div class="controls"><div class="view-toggle" role="group" aria-label="调用范围">
<button type="button" data-view="boxes" aria-pressed="true">只看框检测 · 含复用</button>
<button type="button" data-view="all" aria-pressed="false">全部调用</button></div>
<label>排序 <select id="sort-order"><option value="sequence">实际执行顺序</option>
<option value="native">真实推理耗时 · 从长到短</option><option value="elapsed">本次总耗时 · 从长到短</option></select></label></div>
<div class="legend"><span>真实推理</span><span>本次总耗时</span></div>
<noscript>启用 JavaScript 后可筛选和排序；输入图仍可点击展开。</noscript>
<section id="call-list" aria-label="每次调用明细">{rows}</section>
<p id="empty-state" class="empty" {'hidden' if boxes else ''}>当前范围没有记录，可切换“全部调用”查看。</p>
{_timing_markup(report.get('timings') or {})}
<footer>单次总耗时包含输入检查、模型获取与等待；图片留存等记录开销单列估计。耗时条按当前显示范围缩放，排序只改变展示顺序。所有文字、候选、判定和图像是否保持一致，以等价性检查为准。</footer>
</main><script>{SCRIPT}</script></body></html>'''
    target.write_text(content, encoding='utf-8')
    return target
