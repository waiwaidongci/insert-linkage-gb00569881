"""混箱调拨的实物顺序重放，以及召回批号在各库位的精确剩余上下界。

流水只记录入库（签核记录、库位、批号、件数）和调拨（起止库位、件数）。
调拨不带批号，因此不能假定混箱先到先出；上下界在“所有与流水相容的批次
分配”上分别取到，而不是某一套先到先出的推算结果。

实现方式：以实物顺序展开时间层，把某批号可能停留的件数建成单商品流。
- 每一层每个库位的节点容量是该时刻该库位的实物总件数；
- 每条调拨在起止层之间提供一条容量为件数的弧；
- 未调拨的件数沿时间层停留。
该批号在该库位的最大可能剩余量，是源点到该库位末层的最大流；
最小可能剩余量，是入库总件数减去“流向其余全部库位”的最大流。
流值与逐笔枚举分配得到的结果一致，因此上下界是精确的。
"""

from collections import deque


class _Dinic:
    """整数最大流，BFS 分层 + 迭代增广。图规模随流水步数线性增长，可反复求值。"""

    __slots__ = ('size', 'graph')

    def __init__(self, size):
        self.size = size
        self.graph = [[] for _ in range(size)]

    def add(self, source, target, capacity):
        self.graph[source].append([target, capacity, len(self.graph[target])])
        self.graph[target].append([source, 0, len(self.graph[source]) - 1])

    def _levels(self, source, sink):
        level = [-1] * self.size
        level[source] = 0
        queue = deque([source])
        while queue:
            node = queue.popleft()
            for target, capacity, _ in self.graph[node]:
                if capacity > 0 and level[target] < 0:
                    level[target] = level[node] + 1
                    queue.append(target)
        return level if level[sink] >= 0 else None

    def _augment(self, source, sink, level, cursor):
        path = []
        node = source
        while True:
            if node == sink:
                push = min(self.graph[tail][index][1] for tail, index in path)
                for tail, index in path:
                    arc = self.graph[tail][index]
                    arc[1] -= push
                    self.graph[arc[0]][arc[2]][1] += push
                return push
            advanced = False
            arcs = self.graph[node]
            while cursor[node] < len(arcs):
                arc = arcs[cursor[node]]
                if arc[1] > 0 and level[arc[0]] == level[node] + 1:
                    path.append((node, cursor[node]))
                    node = arc[0]
                    advanced = True
                    break
                cursor[node] += 1
            if advanced:
                continue
            if not path:
                return 0
            level[node] = -1  # 死路剪枝：本层不再进入
            tail, index = path.pop()
            cursor[tail] = index + 1
            node = tail

    def maxflow(self, source, sink):
        total = 0
        while True:
            level = self._levels(source, sink)
            if level is None:
                return total
            cursor = [0] * self.size
            while True:
                pushed = self._augment(source, sink, level, cursor)
                if not pushed:
                    break
                total += pushed


def event_locations(event):
    if event['kind'] == 'inbound':
        return (event['location'],)
    return (event['from_location'], event['to_location'])


def physical_order(events):
    """按实物时间排序，同一时刻按登记先后，与录入先后无关。"""
    return sorted(events, key=lambda event: (event['occurred_at'], event['id']))


def timeline(events):
    """按实物顺序重放实物总件数，返回库位、逐步状态、库存不足步骤与末态。"""
    locations = []
    seen = set()
    for event in events:
        for location in event_locations(event):
            if location not in seen:
                seen.add(location)
                locations.append(location)
    running = {location: 0 for location in locations}
    steps = []
    shortages = []
    for position, event in enumerate(events, start=1):
        if event['kind'] == 'inbound':
            running[event['location']] += event['quantity']
        else:
            source = event['from_location']
            available = running[source]
            running[source] -= event['quantity']
            running[event['to_location']] += event['quantity']
            if running[source] < 0:
                shortages.append({
                    'position': position, 'event_id': event['id'],
                    'occurred_at': event['occurred_at'], 'location': source,
                    'required': event['quantity'], 'available': available,
                })
        steps.append({
            'position': position, 'event_id': event['id'], 'kind': event['kind'],
            'location': event.get('location'), 'batch_no': event.get('batch_no'),
            'from_location': event.get('from_location'), 'to_location': event.get('to_location'),
            'quantity': event['quantity'], 'occurred_at': event['occurred_at'],
            'totals': dict(running),
        })
    return locations, steps, shortages, dict(running)


def batch_totals(events):
    """按批号汇总入库件数，批号折叠大小写后匹配。"""
    batches = {}
    for event in events:
        if event['kind'] != 'inbound':
            continue
        key = event['batch_no'].casefold()
        entry = batches.setdefault(key, {'batch_no': event['batch_no'], 'inbound': 0})
        entry['inbound'] += event['quantity']
    return batches


def _levels_totals(events, locations):
    running = {location: 0 for location in locations}
    totals = [dict(running)]
    for event in events:
        if event['kind'] == 'inbound':
            running[event['location']] += event['quantity']
        else:
            running[event['from_location']] -= event['quantity']
            running[event['to_location']] += event['quantity']
        totals.append(dict(running))
    return totals


def _reachable(events, locations):
    """按实物顺序从任一次入库出发，正向传播可被调拨触及的库位。

    这是保守的超集（任何批号的入库都算起点），仅用于跳过完全不可能有货
    停留的库位；被保留的库位即使求流为 0，也由流本身给出正确上下界。
    """
    forward = {location: False for location in locations}
    state = {location: False for location in locations}
    for event in events:
        if event['kind'] == 'inbound':
            state[event['location']] = True
            forward[event['location']] = True
        elif state[event['from_location']]:
            state[event['to_location']] = True
            forward[event['to_location']] = True
    return {location for location, hit in forward.items() if hit}


def _max_to(events, locations, totals, supply, targets, batch_key):
    """该召回批号最终落在 targets 库位内的最大件数。

    时间层只在该库位“前后状态可能变化”的层级建点：每个触及该库位的事件
    同时登记事件前、事件后两层，相邻两层之间库位总件数恒定。节点数随流水
    步数而非“步数 × 库位数”增长。
    """
    attach = {location: {0, len(events)} for location in locations}
    for position, event in enumerate(events):
        if event['kind'] == 'inbound':
            attach[event['location']].update((position, position + 1))
        else:
            attach[event['from_location']].update((position, position + 1))
            attach[event['to_location']].update((position, position + 1))
    node_of = {}
    size = 0
    for location in locations:
        for level in sorted(attach[location]):
            node_of[(location, level)] = size
            size += 2
    source, sink = size, size + 1
    graph = _Dinic(size + 2)

    for location in locations:
        levels = sorted(attach[location])
        for order, level in enumerate(levels):
            node = node_of[(location, level)]
            graph.add(node, node + 1, totals[level][location])
            if order + 1 < len(levels):
                graph.add(node + 1, node_of[(location, levels[order + 1])], supply)
    for position, event in enumerate(events):
        if event['kind'] == 'inbound':
            if event['batch_no'].casefold() == batch_key:
                graph.add(source, node_of[(event['location'], position + 1)], event['quantity'])
        else:
            graph.add(node_of[(event['from_location'], position)] + 1,
                      node_of[(event['to_location'], position + 1)], event['quantity'])
    for location in targets:
        graph.add(node_of[(location, len(events))] + 1, sink, supply)
    return graph.maxflow(source, sink)


def bounds(events, batch_no):
    """返回库位到 (下界, 上界) 的映射。上下界均按整数件数取到。"""
    ordered = physical_order(events)
    locations, _, shortages, _ = timeline(ordered)
    if shortages:
        raise ValueError('流水存在库存不足的步骤，无法计算召回范围。')
    key = batch_no.casefold()
    batches = batch_totals(ordered)
    entry = batches.get(key)
    if entry is None:
        raise ValueError('没有该批号的入库记录。')
    if not locations:
        return {}
    supply = entry['inbound']
    totals = _levels_totals(ordered, locations)
    if len(batches) == 1:
        # 只有这一批入库时，实物总数就是该批号在各库位的剩余件数。
        final = totals[-1]
        return {location: (final[location], final[location]) for location in locations}
    result = {}
    reachable = _reachable(ordered, locations)
    for location in locations:
        if location not in reachable:
            result[location] = (0, 0)
            continue
        upper = _max_to(ordered, locations, totals, supply, {location}, key)
        result[location] = (0, upper) if upper == 0 else (
            supply - _max_to(ordered, locations, totals, supply, set(locations) - {location}, key),
            upper,
        )
    return result


def delivery(events, batch_no):
    """整理交付页面所需的各库位上下界与核对总量。

    下界之和与上界之和分别取到，不作为“上下界相加”的守恒量使用；各库位上下界
    本身是独立取到的精确值，不同库位不能同时取到各自上界。
    """
    ordered = physical_order(events)
    locations, _, shortages, final = timeline(ordered)
    if shortages:
        raise ValueError('流水存在库存不足的步骤，无法计算召回范围。')
    ranges = bounds(ordered, batch_no)
    entry = batch_totals(ordered).get(batch_no.casefold())
    rows = []
    for location in sorted(locations):
        lower, upper = ranges.get(location, (0, 0))
        rows.append({
            'location': location, 'lower': lower, 'upper': upper,
            'pinned': lower == upper, 'stock_total': final[location],
        })
    return {
        'batch_no': entry['batch_no'] if entry else batch_no,
        'inbound_total': entry['inbound'] if entry else 0,
        'locations': rows,
        'stock_total': sum(final.values()),
        'certain': all(row['pinned'] for row in rows),
        'mixed': len(batch_totals(ordered)) > 1,
    }
