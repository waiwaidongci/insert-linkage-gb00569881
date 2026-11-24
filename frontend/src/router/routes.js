export const pages = ['overview', 'inputs', 'run', 'results', 'review', 'delivery', 'recall', 'recallVersions', 'recallDelivery'];

export function route() {
  const raw = location.hash.slice(1);
  const [path, search = ''] = raw.split('?');
  const [page, id, offset] = path.split('/');
  return {
    page: pages.includes(page) ? page : 'overview',
    id: Number(id) || null,
    offset: Math.max(0, Number(offset) || 0),
    query: new URLSearchParams(search),
  };
}
