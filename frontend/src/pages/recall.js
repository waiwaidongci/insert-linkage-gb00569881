import {feedback, field} from '../components/common/fields.js';
import {empty, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

const KIND = {inbound:'入库', transfer:'调拨'};

function newKey(prefix) {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}

function inboundForm(signoffs, message, refresh) {
  const submit = button('登记入库', {type:'submit', class:'primary', 'data-testid':'inbound-submit'});
  const stamp = new Date().toISOString().slice(0, 16);
  return el('form', {class:'fields', 'data-testid':'inbound-form', onSubmit:handler(submit, message, async event => {
    const data = new FormData(event.target);
    const body = {
      kind:'inbound', quantity:Number(data.get('quantity')), occurred_at:`${data.get('occurred_at')}:00+08:00`,
      request_key:data.get('request_key'), location:data.get('location'),
      batch_no:data.get('batch_no'), signoff_id:Number(data.get('signoff_id')),
    };
    const result = await workspace.recallRecord(body);
    if (result.version?.status === 'blocked') {
      message.textContent = `已登记，但版本 ${result.version.number} 暂缓发布：${result.version.reason}`;
    } else {
      message.textContent = result.duplicate ? '该笔入库此前已登记，未重复扣库。' : '入库已登记并发布新版本。';
    }
    await refresh();
  })},
    el('label', {class:'field', for:'signoff-id'}, el('span', {}, '关联签核记录'),
      el('select', {id:'signoff-id', name:'signoff_id', required:true}, signoffs.map(item =>
        el('option', {value:item.id}, `${item.code} · ${item.title}`)))),
    field({key:'location', label:'库位', type:'string', max_length:40}),
    field({key:'batch_no', label:'批号', type:'string', max_length:80}),
    field({key:'quantity', label:'件数', type:'int', min:1, max:1000000}),
    field({key:'occurred_at', label:'发生时间（北京时间）', type:'datetime-local', default:stamp}),
    el('input', {type:'hidden', name:'request_key', value:newKey('in')}), submit);
}

function transferForm(message, refresh) {
  const submit = button('登记调拨', {type:'submit', class:'primary', 'data-testid':'transfer-submit'});
  const stamp = new Date().toISOString().slice(0, 16);
  return el('form', {class:'fields', 'data-testid':'transfer-form', onSubmit:handler(submit, message, async event => {
    const data = new FormData(event.target);
    const body = {
      kind:'transfer', quantity:Number(data.get('quantity')), occurred_at:`${data.get('occurred_at')}:00+08:00`,
      request_key:data.get('request_key'), from_location:data.get('from_location'), to_location:data.get('to_location'),
    };
    const result = await workspace.recallRecord(body);
    if (result.version?.status === 'blocked') {
      message.textContent = `已登记，但版本 ${result.version.number} 暂缓发布：${result.version.reason}`;
    } else {
      message.textContent = result.duplicate ? '该笔调拨此前已登记，未重复扣库。' : '调拨已登记，按实物顺序重算。';
    }
    await refresh();
  })},
    field({key:'from_location', label:'起始库位', type:'string', max_length:40}),
    field({key:'to_location', label:'目的库位', type:'string', max_length:40}),
    field({key:'quantity', label:'件数', type:'int', min:1, max:1000000}),
    field({key:'occurred_at', label:'发生时间（北京时间）', type:'datetime-local', default:stamp}),
    el('input', {type:'hidden', name:'request_key', value:newKey('tr')}), submit);
}

function signoffForm(message, refresh) {
  if (!session.canReview) return null;
  const submit = button('签核混箱调拨', {type:'submit', class:'primary', 'data-testid':'signoff-submit'});
  return el('form', {class:'fields schedule-fields', onSubmit:handler(submit, message, async event => {
    const data = new FormData(event.target);
    await workspace.recallSignoff({code:data.get('code'), title:data.get('title'), note:data.get('note') || ''});
    message.textContent = '签核记录已保存。';
    await refresh();
  })},
    field({key:'code', label:'调拨单号', type:'string', max_length:60}),
    field({key:'title', label:'单据名称', type:'string', max_length:120}),
    el('label', {class:'field', for:'signoff-note'}, el('span', {}, '备注（可选）'),
      el('textarea', {id:'signoff-note', name:'note', maxlength:500, rows:2})), submit);
}

export async function recallPage(route, refresh) {
  const {spec} = workspace;
  const [signoffs, feed] = await Promise.all([workspace.recallSignoffs(), workspace.recallEvents()]);
  const message = feedback();
  const warnings = feed.shortages.length
    ? panel('库存不足，最新版本暂缓发布', p(`第 ${feed.shortages[0].position} 步调拨需要 ${feed.shortages[0].required} 件，库位 ${feed.shortages[0].location} 当时只有 ${Math.max(feed.shortages[0].available, 0)} 件。已发布版本照常可查。`,'feedback'))
    : null;
  const shortageList = feed.shortages.length
    ? panel('待修正的步骤', table([
        {key:'position', label:'实物序号'}, {key:'location', label:'库位'},
        {key:'required', label:'需要件数'}, {label:'当时可用', render:row=>Math.max(row.available, 0)},
        {label:'发生时间', render:row=>date(row.occurred_at)},
      ], feed.shortages, {caption:'库存不足步骤'}))
    : null;
  const rows = feed.events;
  const ledger = panel('实物流水', table([
    {label:'实物序号', render:row=>row.physical_position ?? '—'},
    {label:'类型', render:row=>KIND[row.kind] || row.kind},
    {label:'库位', render:row=>row.kind === 'inbound' ? row.location : `${row.from_location} → ${row.to_location}`},
    {label:'批号', render:row=>row.batch_no || '—'},
    {key:'quantity', label:'件数'},
    {label:'发生时间', render:row=>date(row.occurred_at)},
    {label:'状态', render:row=>row.voided ? pill('已作废','blocked') : '有效'},
    {label:'操作', render:row=>{
      if (row.voided || !session.canReview) return '—';
      const action = button('作废', {class:'quiet'});
      action.addEventListener('click', handler(action, message, async () => {
        const reason = globalThis.prompt('请输入作废原因');
        if (!reason) return;
        const result = await workspace.recallVoid(row.id, {reason});
        message.textContent = result.version ? `已作废并生成版本 ${result.version.number}（${result.version.status === 'published' ? '已发布' : '暂缓'}）。` : '已作废。';
        await refresh();
      }));
      return action;
    }},
  ], rows, {caption:'实物流水', empty:'还没有登记入库或调拨。'}));
  const totals = panel('当前库位实物总件数', table([
    {key:'location', label:'库位'}, {key:'quantity', label:'总件数'},
  ], Object.entries(feed.totals).map(([location, quantity]) => ({location, quantity})), {caption:'库位总件数'}));
  const form = session.canWrite
    ? el('div', {class:'panel'}, el('h2', {}, '登记流水'),
        p('入库需要签核记录；调拨只登记起止库位、件数和发生时间，批号不进调拨记录。允许补录早先的单据，系统按实物顺序重排。','subtle'),
        el('details', {class:'schedule-fields', open:true}, el('summary', {}, '入库'), inboundForm(signoffs, message, refresh)),
        el('details', {class:'schedule-fields', open:true}, el('summary', {}, '调拨'), transferForm(message, refresh)),
        signoffForm(message, refresh), message)
    : panel('登记流水', p('只读角色可以查看流水与交付范围。','subtle'));
  return el('div', {class:'two-col'},
    el('div', {class:'wide-panel'}, warnings, shortageList, ledger, totals),
    el('aside', {class:'sidebar-panel'}, form));
}
