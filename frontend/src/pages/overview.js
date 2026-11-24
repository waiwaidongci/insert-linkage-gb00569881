import {panel, metrics, pill, empty} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {STATUS} from '../constants/enums.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {el, h, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

export async function overview() {
  const {spec, datasets, runs} = workspace;
  const latest = runs[0];
  const feed = session.canReview ? await workspace.audit() : [];
  const [recallFeed, versions] = await Promise.all([workspace.recallEvents(), workspace.recallVersions()]);
  const published = versions.find(item => item.status === 'published');
  return el('div', {class:'overview'},
    el('div', {class:'overview-main'},
      el('section', {class:'hero'}, p(spec.organization,'eyeline'), h(2,spec.subtitle), p(spec.workflow,'intro-copy'),
        el('div',{class:'actions'}, link(spec.pages[1], '#inputs', {class:'primary'}), link(spec.pages[2], '#run',{class:'quiet'}),
          link(spec.pages[6], '#recall', {class:'quiet'}), link(spec.pages[8], '#recallDelivery', {class:'quiet'}))),
      panel('业务处理步骤', el('ol', {class:'work-steps'}, spec.steps.map(text => el('li', {}, text)))),
      recallFeed.shortages.length && panel('召回流水待修正', p(`有 ${recallFeed.shortages.length} 步库存不足，最新候选版本暂缓发布，已发布版本照常可查。`,'feedback'),
        link('查看待修正步骤','#recall',{class:'quiet'})),
      feed.length && panel('最近审计事件',table([
        {key:'actor',label:'操作者'}, {key:'event',label:'动作'}, {key:'target',label:'关联记录'},
        {label:'时间',render:row=>date(row.created_at)},
      ],feed.slice(0,8),{caption:'最近审计事件'}))),
    el('aside', {class:'sidebar-panel'},
      panel('当前批次', metrics([{label:'数据批次',value:datasets.length},{label:'运行记录',value:runs.length}]),
        datasets[0] && p(datasets[0].filename,'filename')),
      panel('召回流水', metrics([{label:'流水步数',value:recallFeed.events.length},{label:'召回版本',value:versions.length}]),
        p(published ? `已发布版本 ${published.number}` : '尚无已发布版本','subtle'),
        el('div',{class:'actions'}, link('登记流水','#recall',{class:'quiet'}), link('交付范围','#recallDelivery',{class:'quiet'}))),
      panel('最近运行', latest ? el('div',{class:'activity'},link(latest.name,`#results/${latest.id}`),
        pill(STATUS[latest.status],latest.status),p(date(latest.created_at),'subtle'))
        : empty('进入运行页，使用演示数据执行首次分析。','#run','前往运行')),
      panel('现场问题', el('ul', {class:'method-list'}, spec.pains.map(text => el('li',{},text))))));
}
