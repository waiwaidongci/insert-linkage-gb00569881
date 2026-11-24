import {feedback, field} from '../components/common/fields.js';
import {empty, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {handler} from '../hooks/action.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

export async function recallDeliveryPage(route, refresh) {
  const {spec} = workspace;
  const feed = await workspace.recallEvents();
  const batches = [...new Set(feed.events.filter(item=>item.kind === 'inbound' && !item.voided && item.batch_no).map(item=>item.batch_no))];
  const batch = route.query?.get('batch') || batches[0] || '';
  const message = feedback();
  const picker = el('form', {class:'fields', onSubmit:handler(button('查询', {type:'submit', class:'primary'}), message, async event => {
    event.preventDefault();
    const value = new FormData(event.target).get('batch_no');
    location.hash = `#recallDelivery?batch=${encodeURIComponent(value)}`;
  })}, el('label', {class:'field', for:'batch-no'}, el('span', {}, '召回批号'),
    el('input', {id:'batch-no', name:'batch_no', required:true, maxlength:80, value:batch,
      list:'batch-options'}), el('datalist', {id:'batch-options'}, batches.map(item=>el('option', {value:item})))),
    button('查询', {type:'submit', class:'primary'}));
  if (!batch) {
    return el('div', {class:'two-col'},
      el('div', {class:'wide-panel'}, panel('召回交付范围', empty('还没有入库批号，请先登记流水。', '#recall', '前往登记'))),
      el('aside', {class:'sidebar-panel'}, panel('查询批号', picker, message)));
  }
  let result;
  try {
    result = await workspace.recallDelivery(batch);
  } catch (error) {
    return el('div', {class:'two-col'},
      el('div', {class:'wide-panel'}, panel('召回交付范围', p(error.message, 'feedback'))),
      el('aside', {class:'sidebar-panel'}, panel('查询批号', picker, message)));
  }
  const download = button('下载交付 CSV', {class:'quiet'});
  download.addEventListener('click', handler(download, message, () =>
    workspace.download(`/recall/delivery?batch_no=${encodeURIComponent(result.batch_no)}&download=true`, `recall-${result.batch_no}-v${result.version}.csv`)));
  const bounds = panel(`批号 ${result.batch_no} 的各库位可能剩余件数`,
    p(`按已发布版本 ${result.version} 给出精确上下界，不假定混箱先到先出。`,'subtle'),
    table([
      {key:'location', label:'库位'},
      {label:'可能剩余件数', render:row=>`${row.lower} – ${row.upper}`},
      {label:'是否唯一', render:row=>row.pinned ? pill('唯一','ok') : '区间'},
      {key:'stock_total', label:'库位实物总件数'},
    ], result.locations, {caption:'库位上下界'}),
    el('div', {class:'actions'}, download, message,
      result.pending && pill('有暂缓版本待处理','blocked')));
  const note = panel('口径说明',
    el('ul', {class:'method-list'},
      el('li', {}, `该批号入库共 ${result.inbound_total} 件，库位实物总件数合计 ${result.stock_total} 件。`),
      el('li', {}, '调拨只记录起止库位和件数，不带批号，因此各库位剩余量以区间给出；区间在所有与流水相容的分配上取到。'),
      el('li', {}, result.mixed ? '当前库位混有多个批号，区间可能较宽。' : '当前只有本批号入库，区间收敛为唯一值。')));
  return el('div', {class:'two-col'},
    el('div', {class:'wide-panel'}, bounds, note),
    el('aside', {class:'sidebar-panel'}, panel('查询批号', picker),
      panel('版本', p(`已发布版本 ${result.version}`, 'subtle'), date(result.published_at),
        link('查看召回版本', `#recallVersions/${result.version}`, {class:'quiet'}))));
}
