import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Inbox,
  MailCheck,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Skull,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { getLanguage, t } from '../i18n';
import { TimeRangeControl } from '../TimeRangeControl';
import { appendDateRange, presetRange, type TimeRange } from '../time-range';

type DeliveryStatus = 'pending' | 'sending' | 'delivered' | 'dead' | 'cancelled';

type DeliveryItem = {
  id: number;
  delivery_key: string;
  destination: string;
  subject: string;
  body: string;
  incident_ids: number[];
  status: DeliveryStatus;
  attempts: number;
  next_attempt_at: number;
  lease_until: number | null;
  last_error: string;
  created_at: number;
  updated_at: number;
  delivered_at: number | null;
  priority: 'info' | 'warning' | 'critical';
};

type DeliveryPayload = {
  generated_at: number;
  total: number;
  limit: number;
  offset: number;
  counts: Record<DeliveryStatus, number>;
  destinations: string[];
  items: DeliveryItem[];
};

const STATUS_LABELS: Record<DeliveryStatus | 'all', string> = {
  all: '全部',
  pending: '待投递',
  sending: '发送中',
  delivered: '已送达',
  dead: '死信',
  cancelled: '已取消',
};

function formatTime(timestamp: number | null): string {
  if (!timestamp) return t('暂无');
  return new Intl.DateTimeFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(timestamp * 1000));
}

function statusIcon(status: DeliveryStatus) {
  if (status === 'delivered') return <CheckCircle2 size={16} />;
  if (status === 'dead') return <Skull size={16} />;
  if (status === 'cancelled') return <Ban size={16} />;
  if (status === 'sending') return <Send size={16} />;
  return <Clock3 size={16} />;
}

export function DeliveriesView() {
  const [payload, setPayload] = useState<DeliveryPayload | null>(null);
  const [status, setStatus] = useState<DeliveryStatus | 'all'>('all');
  const [destination, setDestination] = useState('all');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [action, setAction] = useState<'retry' | 'cancel' | null>(null);
  const [error, setError] = useState('');
  const [range, setRange] = useState<TimeRange>(() => presetRange(30));

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const load = useCallback(async () => {
    setLoading(true);
    const parameters = new URLSearchParams({ status, destination, limit: '100', offset: '0' });
    appendDateRange(parameters, range);
    if (query) parameters.set('q', query);
    try {
      const result = await api<DeliveryPayload>(`notifications/outbox?${parameters.toString()}`);
      setPayload(result);
      setSelectedIds((current) => current.filter((id) => result.items.some((item) => item.id === id)));
      setSelectedId((current) => result.items.some((item) => item.id === current) ? current : result.items[0]?.id ?? null);
      setError('');
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : t('投递记录加载失败'));
    } finally {
      setLoading(false);
    }
  }, [destination, query, range, status]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 10_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const selected = payload?.items.find((item) => item.id === selectedId) || null;
  const selectedItems = useMemo(
    () => payload?.items.filter((item) => selectedIds.includes(item.id)) || [],
    [payload, selectedIds],
  );
  const canRetry = selectedItems.length > 0 && selectedItems.every((item) => ['pending', 'dead', 'cancelled'].includes(item.status));
  const canCancel = selectedItems.length > 0 && selectedItems.every((item) => ['pending', 'dead'].includes(item.status));

  const mutate = async (nextAction: 'retry' | 'cancel', ids: number[]) => {
    if (!ids.length) return;
    setAction(nextAction);
    try {
      await api(`notifications/outbox/${nextAction}`, {
        method: 'POST',
        body: JSON.stringify({ ids }),
      });
      setSelectedIds([]);
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : t('投递操作失败'));
    } finally {
      setAction(null);
    }
  };

  const toggleSelection = (id: number) => {
    setSelectedIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  };

  const counts = payload?.counts || { pending: 0, sending: 0, delivered: 0, dead: 0, cancelled: 0 };
  const statuses: Array<DeliveryStatus | 'all'> = ['all', 'pending', 'sending', 'delivered', 'dead', 'cancelled'];

  return <section className="deliveries-view">
    <div className="section-heading delivery-heading">
      <div><span className="eyebrow">ALERT DELIVERY OPERATIONS</span><h2>{t('告警投递中心')}</h2><p>{t('查看每一条告警的投递状态，恢复死信，并对积压任务执行安全操作。')}</p></div>
      <div className="delivery-heading-actions"><span><i className={loading ? 'source-pulse source-pulse-loading' : 'source-pulse'} />{t('10 秒自动刷新')}</span><button className="secondary-button" type="button" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={14} />{t('立即刷新')}</button></div>
    </div>
    {error && <div className="inline-error"><AlertTriangle size={16} />{error}<button type="button" onClick={() => setError('')}>{t('关闭')}</button></div>}

    <div className="delivery-kpis">
      <button type="button" onClick={() => setStatus('pending')}><Clock3 /><span>{t('待投递')}</span><strong>{counts.pending}</strong></button>
      <button type="button" onClick={() => setStatus('sending')}><Send /><span>{t('发送中')}</span><strong>{counts.sending}</strong></button>
      <button type="button" onClick={() => setStatus('delivered')}><MailCheck /><span>{t('已送达')}</span><strong>{counts.delivered}</strong></button>
      <button type="button" className={counts.dead ? 'danger' : ''} onClick={() => setStatus('dead')}><Skull /><span>{t('死信')}</span><strong>{counts.dead}</strong></button>
      <button type="button" onClick={() => setStatus('cancelled')}><Ban /><span>{t('已取消')}</span><strong>{counts.cancelled}</strong></button>
    </div>

    <div className="delivery-command-bar">
      <TimeRangeControl compact value={range} onChange={setRange} />
      <label className="delivery-search"><Search size={15} /><input aria-label={t('搜索投递记录')} value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t('搜索标题、正文、失败原因或投递标识')} />{search && <button type="button" onClick={() => setSearch('')}><X size={14} /></button>}</label>
      <div className="segmented delivery-status-tabs">{statuses.map((item) => <button type="button" key={item} className={status === item ? 'active' : ''} onClick={() => setStatus(item)}>{t(STATUS_LABELS[item])}{item !== 'all' && <small>{counts[item]}</small>}</button>)}</div>
      <label><span>{t('通知渠道')}</span><select value={destination} onChange={(event) => setDestination(event.target.value)}><option value="all">{t('全部渠道')}</option>{payload?.destinations.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
    </div>

    {selectedIds.length > 0 && <div className="delivery-bulk-bar"><span>{t('已选择 {{count}} 条', { count: selectedIds.length })}</span><button type="button" disabled={!canRetry || action !== null} onClick={() => void mutate('retry', selectedIds)}><RotateCcw size={14} />{t('批量重试')}</button><button type="button" className="danger" disabled={!canCancel || action !== null} onClick={() => void mutate('cancel', selectedIds)}><Ban size={14} />{t('批量取消')}</button><button type="button" onClick={() => setSelectedIds([])}>{t('清除选择')}</button></div>}

    <div className="delivery-workspace">
      <div className="delivery-list-panel">
        <div className="delivery-list-head"><span>{t('投递记录')}</span><strong>{payload?.total || 0}</strong></div>
        <div className="delivery-list">
          {payload?.items.map((item) => <article className={`delivery-row delivery-${item.status} ${selectedId === item.id ? 'active' : ''}`} key={item.id}>
            <label className="delivery-check"><input type="checkbox" checked={selectedIds.includes(item.id)} onChange={() => toggleSelection(item.id)} aria-label={t('选择投递记录')} /><span /></label>
            <button type="button" className="delivery-row-main" onClick={() => setSelectedId(item.id)}>
              <span className="delivery-status-icon">{statusIcon(item.status)}</span>
              <span><span className="delivery-row-top"><b>{item.subject}</b><em>{t(STATUS_LABELS[item.status])}</em></span><small>{item.destination} · #{item.id} · {formatTime(item.updated_at)}</small><span>{item.last_error || item.body}</span></span>
              <ChevronRight size={15} />
            </button>
          </article>)}
          {!payload?.items.length && <div className="delivery-empty"><Inbox size={30} /><strong>{t('没有匹配的投递记录')}</strong><span>{t('调整状态、渠道或搜索条件后重试。')}</span></div>}
        </div>
      </div>

      <aside className="delivery-detail-panel">
        {selected ? <>
          <div className="delivery-detail-head"><span className={`delivery-status-icon delivery-${selected.status}`}>{statusIcon(selected.status)}</span><div><span>{selected.destination} · #{selected.id}</span><h3>{selected.subject}</h3><small>{t(STATUS_LABELS[selected.status])} · {selected.priority.toUpperCase()}</small></div></div>
          <div className="delivery-detail-metrics"><div><span>{t('尝试次数')}</span><strong>{selected.attempts}</strong></div><div><span>{t('下次重试')}</span><strong>{selected.status === 'pending' ? formatTime(selected.next_attempt_at) : '—'}</strong></div><div><span>{t('送达时间')}</span><strong>{formatTime(selected.delivered_at)}</strong></div></div>
          {selected.last_error && <section className="delivery-error-box"><div><AlertTriangle size={15} /><strong>{t('最近失败原因')}</strong></div><pre>{selected.last_error}</pre></section>}
          <section className="delivery-message-box"><div><MailCheck size={15} /><strong>{t('告警正文')}</strong></div><pre>{selected.body}</pre></section>
          <dl className="delivery-technical"><div><dt>{t('创建时间')}</dt><dd>{formatTime(selected.created_at)}</dd></div><div><dt>{t('最后更新')}</dt><dd>{formatTime(selected.updated_at)}</dd></div><div><dt>{t('投递标识')}</dt><dd>{selected.delivery_key}</dd></div><div><dt>{t('关联事件')}</dt><dd>{selected.incident_ids.length ? selected.incident_ids.map((id) => `#${id}`).join(', ') : '—'}</dd></div></dl>
          <div className="delivery-detail-actions"><button type="button" className="primary-button" disabled={!['pending', 'dead', 'cancelled'].includes(selected.status) || action !== null} onClick={() => void mutate('retry', [selected.id])}><RotateCcw size={15} />{selected.status === 'dead' ? t('恢复死信并重投') : t('立即重试')}</button><button type="button" className="secondary-button danger" disabled={!['pending', 'dead'].includes(selected.status) || action !== null} onClick={() => void mutate('cancel', [selected.id])}><Ban size={15} />{t('取消投递')}</button></div>
        </> : <div className="delivery-detail-empty"><Inbox size={32} /><strong>{t('选择一条投递记录')}</strong><span>{t('这里会展示失败原因、重试计划和完整告警正文。')}</span></div>}
      </aside>
    </div>
  </section>;
}
