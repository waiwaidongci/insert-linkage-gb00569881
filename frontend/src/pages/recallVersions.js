import {panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {el, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

const STATE = {published:'已发布', blocked:'暂缓发布'};

export async function recallVersionsPage(route, refresh) {
  const {spec} = workspace;
  const versions = await workspace.recallVersions();
  const selected = route.id ? await workspace.recallVersion(route.id) : (versions.length ? await workspace.recallVersion(versions[0].number) : null);
  const list = panel('召回版本', table([
    {label:'版本', render:row=>link(`版本 ${row.number}`,`#recallVersions/${row.number}`)},
    {label:'状态', render:row=>pill(STATE[row.status] || row.status, row.status)},
    {key:'shortages', label:'库存不足步骤'},
    {label:'批号', render:row=>row.batches.join('、') || '—'},
    {label:'创建时间', render:row=>date(row.created_at)},
    {label:'发布时间', render:row=>date(row.published_at)},
  ], versions, {caption:'召回版本', empty:'登记流水后生成版本。'}));
  if (!selected) {
    return el('div', {class:'two-col'},
      el('div', {class:'wide-panel'}, list),
      el('aside', {class:'sidebar-panel'}, panel('版本说明', p('每次登记都会在实物顺序上重算并生成候选版本。库存不足时暂缓发布，上一版继续可查。','subtle'))));
  }
  const snapshot = selected.snapshot;
  const detail = panel(`版本 ${selected.number}`, p(`${STATE[selected.status] || selected.status} · 创建于 ${date(selected.created_at)}`, 'subtle'),
    selected.reason && p(selected.reason, 'feedback'),
    table([
      {label:'实物序号', render:row=>row.position}, {label:'类型', render:row=>row.kind === 'inbound' ? '入库' : '调拨'},
      {label:'库位', render:row=>row.kind === 'inbound' ? row.location : `${row.from_location} → ${row.to_location}`},
      {label:'批号', render:row=>row.batch_no || '—'}, {key:'quantity', label:'件数'},
      {label:'发生时间', render:row=>date(row.occurred_at)},
    ], snapshot.steps, {caption:'版本内部流水'}));
  const batches = Object.values(snapshot.batches || {});
  const batchTable = panel('该版本的批号与交付范围', table([
    {label:'批号', render:row=>row.batch_no},
    {key:'inbound', label:'入库件数'},
    {label:'库位上下界', render:row=>row.delivery
      ? row.delivery.locations.map(item=>`${item.location} ${item.lower}–${item.upper}`).join('；')
      : link('按批号查询','#recallDelivery',{class:'quiet'})},
  ], batches, {caption:'批号交付范围'}));
  return el('div', {class:'two-col'},
    el('div', {class:'wide-panel'}, list, detail, batchTable),
    el('aside', {class:'sidebar-panel'}, panel('版本只读', p('版本按实物顺序存档，补录或作废后会生成新的候选版本；历史版本内容不变。','subtle'),
      link('返回交付范围', '#recallDelivery', {class:'quiet'}))));
}
