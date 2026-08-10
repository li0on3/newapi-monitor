import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock3,
  Gauge,
  RefreshCw,
  Settings,
  ShieldCheck,
  TimerReset,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { quotaTone, quotaWindowLabel } from '../cpa-quota';
import { getLanguage, t } from '../i18n';
import type { CpaQuotaAccount, CpaQuotaPayload, CpaQuotaWindow } from '../types';

function formatTime(timestamp: number, includeSeconds = false): string {
  if (!timestamp) return t('暂无');
  return new Intl.DateTimeFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: includeSeconds ? '2-digit' : undefined,
    hour12: false,
  }).format(new Date(timestamp * 1000));
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  if (seconds <= 0) return t('已到达');
  if (seconds < 3600) return t('{{value}} 分钟', { value: Math.max(1, Math.round(seconds / 60)) });
  if (seconds < 86_400) return t('{{value}} 小时', { value: Math.round(seconds / 3600) });
  return t('{{value}} 天', { value: Math.round(seconds / 86_400) });
}

function compactNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(Math.max(0, Number(value || 0)));
}

function providerLabel(provider: string): string {
  if (provider === 'antigravity') return 'ANTIGRAVITY';
  if (provider === 'codex') return 'CODEX';
  return provider.toUpperCase();
}

function QuotaHistory({ window, threshold }: { window: CpaQuotaWindow; threshold: number }) {
  const points = window.history || [];
  const width = 640;
  const height = 150;
  const x = (index: number) => points.length <= 1 ? width : index / (points.length - 1) * width;
  const y = (value: number) => 10 + (100 - Math.min(100, Math.max(0, value))) / 100 * (height - 20);
  const line = points.map((point, index) => `${index ? 'L' : 'M'} ${x(index)} ${y(point.remaining_percent)}`).join(' ');
  const area = line ? `${line} L ${width} ${height} L 0 ${height} Z` : '';
  const thresholdY = y(threshold);
  return <div className="cpa-quota-chart" aria-label={t('{{name}} 配额历史', { name: window.window_name })}>
    <div className="cpa-quota-chart-head"><span><BarChart3 size={14} />{t('周内剩余趋势')}</span><small>{points.length} {t('个样本')}</small></div>
    {points.length ? <>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img">
        <defs><linearGradient id={`quota-${window.credential_id}-${window.window_id}`.replace(/[^a-z0-9-]/gi, '')} x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="currentColor" stopOpacity=".22" /><stop offset="1" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs>
        {[25, 50, 75].map((level) => <line key={level} x1="0" x2={width} y1={y(level)} y2={y(level)} className="cpa-quota-grid-line" />)}
        <line x1="0" x2={width} y1={thresholdY} y2={thresholdY} className="cpa-quota-threshold-line" />
        {area && <path d={area} fill={`url(#${`quota-${window.credential_id}-${window.window_id}`.replace(/[^a-z0-9-]/gi, '')})`} />}
        {line && <path d={line} className="cpa-quota-history-line" vectorEffect="non-scaling-stroke" />}
        <circle cx={x(points.length - 1)} cy={y(points[points.length - 1].remaining_percent)} r="5" className="cpa-quota-history-point" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="cpa-quota-chart-axis"><span>{formatTime(points[0].observed_at)}</span><span>{t('告警线')} {threshold}%</span><span>{formatTime(points[points.length - 1].observed_at)}</span></div>
    </> : <div className="cpa-quota-chart-empty"><Activity size={18} />{t('等待第一条配额样本')}</div>}
  </div>;
}

function ForecastPanel({ window, threshold, activityEnabled }: { window: CpaQuotaWindow; threshold: number; activityEnabled: boolean }) {
  const forecast = window.forecast;
  const projected = forecast.status === 'projected';
  const resetFirst = projected && forecast.reset_before_exhaustion;
  const confidence = forecast.confidence === 'high' ? t('高') : forecast.confidence === 'medium' ? t('中') : t('低');
  const activity = window.activity || {};
  return <div className="cpa-quota-forecast">
    <div className="cpa-quota-forecast-head"><span><TrendingUp size={15} />{t('预测与请求压力')}</span><b className={`confidence confidence-${forecast.confidence}`}>{t('{{level}}置信度', { level: confidence })}</b></div>
    <div className="cpa-quota-forecast-main">
      <div><small>{t('预计到达 {{threshold}}%', { threshold })}</small><strong>{projected ? (forecast.reset_before_threshold ? t('重置前不会到达') : formatDuration(forecast.seconds_to_threshold)) : forecast.status === 'stable' ? t('暂未观察到消耗') : t('建立基线中')}</strong></div>
      <div><small>{t('预计耗尽')}</small><strong>{projected ? (resetFirst ? t('先重置') : formatDuration(forecast.seconds_to_exhaustion)) : '—'}</strong></div>
      <div><small>{t('预测消耗速度')}</small><strong>{projected ? `${forecast.burn_percent_per_hour.toFixed(2)}% / h` : '—'}</strong></div>
    </div>
    {activityEnabled ? <div className="cpa-quota-pressure-row">
      <span><Zap size={13} />{t('近 1h')} <b>{compactNumber(activity.requests_1h)} {t('次')}</b></span>
      <span>{compactNumber(activity.tokens_1h)} Tokens</span>
      <span>{t('近 6h')} <b>{compactNumber(activity.requests_6h)} {t('次')}</b></span>
      <span className={(activity.pressure_ratio || 0) > 1.25 ? 'pressure-high' : ''}>{t('压力系数')} <b>{Number(activity.pressure_ratio || 0).toFixed(2)}×</b></span>
    </div> : <div className="cpa-quota-pressure-disabled"><Activity size={13} />{t('请求压力采集已关闭')}</div>}
    <p>{projected
      ? resetFirst
        ? t('预测显示配额大概率会在耗尽前重置；仍会在真实剩余达到 {{threshold}}% 时告警。', { threshold })
        : t('预测由真实配额下降速度校准，并用近 1 小时与 6 小时请求压力做有限修正。')
      : t('至少需要跨越 2 小时的真实配额样本；请求量只用于解释压力，不会被假定为供应商配额公式。')}</p>
  </div>;
}

function QuotaWindowCard({ window, threshold, recovery, activityEnabled }: { window: CpaQuotaWindow; threshold: number; recovery: number; activityEnabled: boolean }) {
  const remaining = Math.min(100, Math.max(0, window.remaining_percent));
  const tone = quotaTone(remaining, threshold, recovery);
  const windowName = quotaWindowLabel(window.window_name);
  return <article className={`cpa-quota-window cpa-quota-window-${tone}`}>
    <div className="cpa-quota-window-top">
      <div className="cpa-quota-ring" style={{ '--quota-angle': `${remaining * 3.6}deg` } as React.CSSProperties}><span><strong>{remaining.toFixed(0)}%</strong><small>{t('剩余')}</small></span></div>
      <div className="cpa-quota-window-title"><span>{window.window_id.startsWith('additional:') ? t('专项限额') : t('主周限额')}</span><h3>{windowName}</h3><p>{window.plan_label || providerLabel(window.provider)} · {t('已使用')} {window.used_percent.toFixed(0)}%</p></div>
      <div className="cpa-quota-reset"><TimerReset size={16} /><span><small>{t('下次重置')}</small><strong>{formatTime(window.reset_at)}</strong></span></div>
    </div>
    <div className="cpa-quota-rail"><i style={{ width: `${remaining}%` }} /><b style={{ left: `${threshold}%` }} title={`${t('告警阈值')} ${threshold}%`} /></div>
    <div className="cpa-quota-rail-label"><span>0%</span><span>{t('告警')} {threshold}%</span><span>100%</span></div>
    <div className="cpa-quota-window-grid"><QuotaHistory window={window} threshold={threshold} /><ForecastPanel window={window} threshold={threshold} activityEnabled={activityEnabled} /></div>
    <div className="cpa-quota-window-foot"><span><Clock3 size={13} />{t('采样于')} {formatTime(window.observed_at, true)}</span><span>{t('配额周期')} {Math.round(window.window_seconds / 86_400)} {t('天')}</span></div>
  </article>;
}

function AccountCard({ account, threshold, recovery, activityEnabled }: { account: CpaQuotaAccount; threshold: number; recovery: number; activityEnabled: boolean }) {
  const lowest = Math.min(...account.windows.map((window) => window.remaining_percent), 101);
  const quotaState = quotaTone(lowest, threshold, recovery);
  const tone = account.status === 'error' ? 'error' : quotaState;
  return <section className={`cpa-quota-account cpa-quota-account-${tone}`}>
    <header className="cpa-quota-account-head">
      <div className="cpa-provider-mark"><Gauge size={21} /></div>
      <div><span>{providerLabel(account.provider)}</span><h2>{account.account_label}</h2><p>{account.windows.length} {t('个周配额池')} · {account.last_success_at ? t('最后成功 {{time}}', { time: formatTime(account.last_success_at) }) : t('等待首次成功采集')}</p></div>
      <div className="cpa-account-state">{account.status === 'error' ? <><AlertTriangle size={15} />{t('采集异常')}</> : quotaState === 'critical' ? <><AlertTriangle size={15} />{t('配额偏低')}</> : quotaState === 'warning' ? <><AlertTriangle size={15} />{t('接近告警阈值')}</> : <><CheckCircle2 size={15} />{t('配额充足')}</>}</div>
    </header>
    {account.status === 'error' && <div className="cpa-account-error"><AlertTriangle size={16} /><div><strong>{t('该账号最近一次采集失败')}</strong><p>{account.last_error || t('CPA 未返回可用的周配额数据')}</p><small>{t('连续失败')} {account.consecutive_failures} {t('次')} · {formatTime(account.last_attempt_at, true)}</small></div></div>}
    <div className="cpa-quota-window-list">{account.windows.map((window) => <QuotaWindowCard key={window.window_id} window={window} threshold={threshold} recovery={recovery} activityEnabled={activityEnabled} />)}{!account.windows.length && account.status !== 'error' && <div className="cpa-account-empty"><Activity size={20} />{t('等待该账号的第一批周配额数据')}</div>}</div>
  </section>;
}

export function CpaQuotaView({ onConfigure }: { onConfigure: () => void }) {
  const [payload, setPayload] = useState<CpaQuotaPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const controller = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    controller.current?.abort();
    const requestController = new AbortController();
    controller.current = requestController;
    try {
      const result = await api<CpaQuotaPayload>('cpa-quota/summary', { signal: requestController.signal });
      setPayload(result);
      setError('');
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === 'AbortError') return;
      setError(requestError instanceof Error ? requestError.message : t('CPA 周配额加载失败'));
    } finally {
      if (controller.current === requestController) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 300_000);
    return () => { window.clearInterval(timer); controller.current?.abort(); };
  }, [load]);

  const refresh = async () => {
    setRefreshing(true); setMessage(''); setError('');
    try {
      await api('cpa-quota/refresh', { method: 'POST' });
      setMessage(t('已提交立即采集请求；通常会在数秒内完成。'));
      window.setTimeout(() => void load(), 8_000);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : t('CPA 周配额刷新失败'));
    } finally { setRefreshing(false); }
  };

  const status = useMemo(() => {
    if (!payload?.enabled || !payload.configured) return { tone: 'muted', label: t('等待配置'), detail: t('独立功能尚未启用') };
    if (payload.collector.status === 'stale') return { tone: 'warning', label: t('采集需要关注'), detail: t('配额数据已超过新鲜度阈值') };
    if (payload.collector.status !== 'ok') return { tone: 'muted', label: t('正在建立基线'), detail: t('等待第一批新鲜周配额样本') };
    if (payload.summary.at_risk_windows > 0) return { tone: 'critical', label: t('周配额不足'), detail: t('{{count}} 个配额池低于阈值', { count: payload.summary.at_risk_windows }) };
    if (payload.summary.error_accounts > 0) return { tone: 'warning', label: t('采集需要关注'), detail: t('{{count}} 个账号采集异常', { count: payload.summary.error_accounts }) };
    if (!payload.window_count) return { tone: 'muted', label: t('正在建立基线'), detail: t('等待第一批周配额样本') };
    return { tone: 'healthy', label: t('容量充足'), detail: t('全部周配额池高于告警阈值') };
  }, [payload]);
  const threshold = payload?.thresholds.alert_remaining_percent ?? 20;
  const recovery = payload?.thresholds.recovery_remaining_percent ?? 25;

  return <section className="cpa-quota-page">
    <div className="cpa-quota-hero">
      <div className="cpa-quota-hero-copy"><span className="eyebrow">CPA CAPACITY / WEEKLY WINDOWS</span><h1>{t('CPA 周配额')}</h1><p>{t('独立监控 CLIProxyAPI / API Management 的周限额，并用真实配额变化与附近请求压力预测容量风险。')}</p><div><ShieldCheck size={14} />{t('仅管理员可见 · 只读采集 · 不修改 CPA 与 New API')}</div></div>
      <div className={`cpa-quota-state cpa-quota-state-${status.tone}`}><i /><span><small>{t('容量状态')}</small><strong>{status.label}</strong><em>{status.detail}</em></span></div>
      <div className="cpa-quota-actions"><button className="secondary-button" type="button" onClick={onConfigure}><Settings size={16} />{t('配额设置')}</button><button className="primary-button" type="button" disabled={refreshing || !payload?.enabled || !payload.configured} onClick={() => void refresh()}>{refreshing ? <RefreshCw className="spin" size={16} /> : <RefreshCw size={16} />}{refreshing ? t('正在提交') : t('立即采集')}</button></div>
    </div>

    {message && <div className="cpa-quota-message"><CheckCircle2 size={16} />{message}</div>}
    {error && <div className="inline-error"><AlertTriangle size={16} />{error}<button type="button" onClick={() => void load()}>{t('重试')}</button></div>}

    {!loading && payload && (!payload.enabled || !payload.configured) && <div className="cpa-quota-setup">
      <div><Gauge size={30} /></div><span className="eyebrow">ONE-TIME SETUP</span><h2>{t('连接 CPA 后开始建立周配额基线')}</h2><p>{t('只需配置 CPA 地址与管理密钥。密钥仅在后端加密保存，浏览器不会收到 auth_index、项目 ID 或任何上游 Token。')}</p><button className="primary-button" type="button" onClick={onConfigure}><Settings size={16} />{t('打开 CPA 配额设置')}</button>
    </div>}

    {payload?.enabled && payload.configured && <>
      <div className="cpa-quota-kpis">
        <article><span><Gauge size={16} />{t('最低剩余')}</span><strong>{payload.summary.lowest_remaining_percent == null ? '—' : `${payload.summary.lowest_remaining_percent.toFixed(0)}%`}</strong><small>{t('告警阈值')} {threshold}%</small></article>
        <article><span><AlertTriangle size={16} />{t('风险配额池')}</span><strong>{payload.summary.at_risk_windows}<i> / {payload.window_count}</i></strong><small>{payload.summary.at_risk_windows ? t('已触发低配额状态') : t('当前全部安全')}</small></article>
        <article><span><TrendingUp size={16} />{t('最早预计到达阈值')}</span><strong>{payload.summary.earliest_threshold_at ? formatTime(payload.summary.earliest_threshold_at) : '—'}</strong><small>{payload.summary.earliest_threshold_at ? t('仅展示不会先重置的预测') : t('暂无明确耗尽风险')}</small></article>
        <article><span><Clock3 size={16} />{t('采集新鲜度')}</span><strong>{payload.collector.status === 'ok' ? t('正常') : payload.collector.status === 'stale' ? t('已过期') : t('启动中')}</strong><small>{payload.collector.last_success_at ? t('{{duration}}前成功', { duration: formatDuration(payload.collector.age_seconds) }) : t('等待首次采集')} · {t('每 {{duration}}', { duration: formatDuration(payload.interval_seconds) })}</small></article>
      </div>

      <div className="cpa-quota-policy-strip"><div><ShieldCheck size={18} /><span><strong>{t('低噪声告警策略')}</strong><small>{t('真实剩余 ≤ {{threshold}}% 才告警，恢复到 {{recovery}}% 后关闭事件；持续偏低最多每 {{duration}} 重复一次。', { threshold, recovery: payload.thresholds.recovery_remaining_percent, duration: formatDuration(payload.repeat_seconds) })}</small></span></div><div><Activity size={18} /><span><strong>{t('预测不单独触发告警')}</strong><small>{t('预测用于提前判断和解释请求压力，避免供应商口径不透明造成误报。')}</small></span></div></div>

      <div className="cpa-quota-account-list">{payload.accounts.map((account) => <AccountCard key={account.credential_id} account={account} threshold={threshold} recovery={recovery} activityEnabled={payload.activity_enabled} />)}{!payload.accounts.length && !loading && <div className="cpa-quota-setup compact"><Activity size={28} /><h2>{t('CPA 暂未返回可监控账号')}</h2><p>{t('当前支持 Codex 与 Antigravity 周配额。请检查凭据是否已启用，以及管理密钥是否有读取权限。')}</p></div>}</div>

      <div className="cpa-quota-security"><ShieldCheck size={18} /><div><strong>{t('安全与数据边界')}</strong><p>{t('页面仅展示脱敏账号、周配额百分比、重置时间和聚合请求压力。auth_index、project_id、管理密钥和上游 Token 永不写入配额历史，也不会返回浏览器。')}</p></div><span>{t('数据源：CPA 配额接口 / Token Usage Tracker')}</span></div>
    </>}
    {loading && !payload && <div className="cpa-quota-loading"><RefreshCw className="spin" size={22} />{t('正在读取 CPA 周配额')}</div>}
  </section>;
}
