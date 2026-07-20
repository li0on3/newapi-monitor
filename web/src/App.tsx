import {
  Activity,
  AlertTriangle,
  BarChart3,
  BellRing,
  CheckCircle2,
  ChevronRight,
  CircleGauge,
  CircleDollarSign,
  CircleDot,
  Clock3,
  Copy,
  Cpu,
  Database,
  Eye,
  EyeOff,
  HardDrive,
  Inbox,
  Fingerprint,
  KeyRound,
  LogOut,
  Mail,
  MemoryStick,
  MessageSquare,
  Network,
  RefreshCw,
  Save,
  Search,
  Send,
  Server,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  TimerReset,
  TerminalSquare,
  UserCog,
  X,
  XCircle,
} from 'lucide-react';
import { FormEvent, PointerEvent as ReactPointerEvent, ReactNode, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, ApiError } from './api';
import { readRoute, routePath } from './routes';
import type { AppRoute, AppTab, SettingsPage } from './routes';
import type { AuthUser, Channel, ChannelMonitorConfig, ContainerMetric, Incident, IncidentPayload, IncidentSummary, KeyUsageCall, KeyUsageResult, LogItem, ResourceSample, Summary, SystemHealth } from './types';

type Tab = AppTab;

const REFRESH_SECONDS = 5;
const SLOW_SECONDS = 60;

function formatTime(timestamp: number, includeDate = false): string {
  if (!timestamp) return '暂无';
  return new Intl.DateTimeFormat('zh-CN', {
    month: includeDate ? '2-digit' : undefined,
    day: includeDate ? '2-digit' : undefined,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(timestamp * 1000));
}

function formatFullTime(timestamp: number): string {
  if (!timestamp) return '暂无';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(timestamp * 1000));
}

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return '—';
  if (ms >= 60_000) return `${(ms / 1000).toFixed(1)} s`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.round(ms)} ms`;
}

function formatElapsed(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
  return `${Math.floor(seconds / 86_400)}d ${Math.floor(seconds % 86_400 / 3600)}h`;
}

function formatPercent(value: number | null | undefined): string {
  return value == null ? '—' : `${value.toFixed(1)}%`;
}

function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat('zh-CN', { notation: value >= 10_000 ? 'compact' : 'standard', maximumFractionDigits: 2 }).format(value);
}

function formatQuota(value: number): string {
  return `$${new Intl.NumberFormat('zh-CN', { minimumFractionDigits: value > 0 && value < 0.01 ? 4 : 2, maximumFractionDigits: 6 }).format(value)}`;
}

function classNames(...names: Array<string | false | null | undefined>): string {
  return names.filter(Boolean).join(' ');
}

function StatusPill({ tone, children }: { tone: 'ok' | 'warn' | 'bad' | 'muted'; children: ReactNode }) {
  return <span className={`status-pill status-${tone}`}>{children}</span>;
}

function Login({ onSuccess }: { onSuccess: (username: string) => void }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const result = await api<{ authenticated: boolean; username: string }>('auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      onSuccess(result.username);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '登录失败');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="login-mark"><Activity size={26} /></div>
        <div className="eyebrow">PRIVATE OPERATIONS CONSOLE</div>
        <h1>New API Monitor</h1>
        <p>渠道真实探测、请求耗时与机器资源统一监控。</p>
        <a className="sso-button" href="/login"><ShieldCheck size={17} />使用 New API 账号登录</a>
        <div className="login-divider"><span>紧急管理员登录</span></div>
        <form onSubmit={submit}>
          <label>
            <span>账号</span>
            <div className="input-wrap"><ShieldCheck size={17} /><input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></div>
          </label>
          <label>
            <span>密码</span>
            <div className="input-wrap"><KeyRound size={17} /><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus /></div>
          </label>
          {error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}
          <button className="primary-button" type="submit" disabled={submitting || !username || !password}>
            {submitting ? <RefreshCw className="spin" size={17} /> : <TerminalSquare size={17} />}
            {submitting ? '正在验证' : '进入监控台'}
          </button>
        </form>
        <div className="login-foot"><span className="pulse-dot" />当前站点仅限授权账号访问</div>
      </section>
    </main>
  );
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (checked: boolean) => void; label: string }) {
  return <label className="toggle-row"><span>{label}</span><button type="button" className={classNames('switch', checked && 'switch-on')} role="switch" aria-label={label} aria-checked={checked} onClick={() => onChange(!checked)}><i /></button></label>;
}

function ChannelSettingsView() {
  const [items, setItems] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<number | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await api<{ items: Channel[] }>('channel-settings');
      setItems(payload.items.map((item) => {
        const anthropic = item.channel_type === 14;
        return {
          ...item,
          monitor_config: {
            probe_format: anthropic ? 'anthropic' : 'responses',
            probe_path: anthropic ? '/v1/messages' : '/v1/responses',
            probe_prompt: '1',
            max_output_tokens: 1,
            ...item.monitor_config,
          },
        };
      }));
      setError('');
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '渠道配置加载失败');
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const edit = (channelId: number, key: keyof ChannelMonitorConfig, value: string | number | boolean) => {
    setItems((current) => current.map((item) => item.channel_id === channelId
      ? { ...item, monitor_config: { ...item.monitor_config, [key]: value, ...(key === 'probe_format' ? { probe_path: value === 'anthropic' ? '/v1/messages' : value === 'chat' ? '/v1/chat/completions' : '/v1/responses' } : {}) }, name: key === 'display_name' ? String(value || item.source_name || item.name) : item.name }
      : item));
  };
  const save = async (channel: Channel) => {
    setSaving(channel.channel_id);
    try {
      const { display_enabled: _legacyVisibility, overview_admin_visible: _adminVisibility, overview_viewer_visible: _viewerVisibility, ...config } = channel.monitor_config || {};
      await api(`channel-settings/${channel.channel_id}`, { method: 'PUT', body: JSON.stringify(config) });
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '渠道配置保存失败');
    } finally { setSaving(null); }
  };
  return <section>
    <div className="section-heading"><div><span className="eyebrow">CHANNEL PRESENTATION & PROBES</span><h2>渠道配置</h2><p>New API 管理渠道状态，监控平台仅保存展示、探测与告警覆盖项。</p></div><button className="secondary-button" onClick={() => void load()}><RefreshCw className={loading ? 'spin' : ''} size={15} />立即同步</button></div>
    {error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}
    <div className="config-channel-grid">{items.map((channel) => {
      const config = channel.monitor_config || {};
      return <article className={classNames('config-channel-card', !channel.enabled && 'config-channel-disabled')} key={channel.channel_id}>
        <div className="config-channel-head"><div className="provider-mark">{channel.name.slice(0, 2).toUpperCase()}</div><div><h3>{channel.name}</h3><p>#{channel.channel_id} · {channel.enabled ? 'New API 已启用' : 'New API 已禁用'}</p></div><StatusPill tone={channel.enabled ? 'ok' : 'muted'}>{channel.enabled ? '同步中' : '不展示'}</StatusPill></div>
        <div className="config-form-grid">
          <label><span>显示名称</span><input value={config.display_name ?? ''} placeholder={channel.source_name || channel.name} onChange={(event) => edit(channel.channel_id, 'display_name', event.target.value)} /></label>
          <label><span>排序权重</span><input type="number" value={config.sort_order ?? 0} onChange={(event) => edit(channel.channel_id, 'sort_order', Number(event.target.value))} /></label>
          <label><span>探测模型</span><input value={config.probe_model ?? ''} placeholder={channel.models[0] || 'gpt-5.4'} onChange={(event) => edit(channel.channel_id, 'probe_model', event.target.value)} /></label>
          <label><span>请求协议</span><select value={config.probe_format ?? 'responses'} onChange={(event) => edit(channel.channel_id, 'probe_format', event.target.value)}><option value="responses">OpenAI Responses</option><option value="chat">OpenAI Chat Completions</option><option value="anthropic">Anthropic Messages</option></select></label>
          <label><span>最大输出 Token</span><input type="number" min="1" max="4096" value={config.max_output_tokens ?? 1} onChange={(event) => edit(channel.channel_id, 'max_output_tokens', Number(event.target.value))} /></label>
          <label className="config-wide"><span>探测路径</span><input value={config.probe_path ?? ''} placeholder="自动选择协议默认路径" onChange={(event) => edit(channel.channel_id, 'probe_path', event.target.value)} /></label>
          <label className="config-wide"><span>探测提示词</span><input value={config.probe_prompt ?? ''} placeholder="1（建议使用最小探测内容）" onChange={(event) => edit(channel.channel_id, 'probe_prompt', event.target.value)} /></label>
        </div>
        <div className="config-visibility-note"><Eye size={15} /><span>总览展示范围由管理员在“系统配置 → 总览展示”中统一管理。</span></div>
        <div className="config-toggle-grid"><Toggle checked={config.probe_enabled ?? false} onChange={(value) => edit(channel.channel_id, 'probe_enabled', value)} label="使用真实请求探测" /><Toggle checked={config.alert_enabled ?? true} onChange={(value) => edit(channel.channel_id, 'alert_enabled', value)} label="渠道告警" /><Toggle checked={config.maintenance_mode ?? false} onChange={(value) => edit(channel.channel_id, 'maintenance_mode', value)} label="维护模式" /></div>
        <button className="primary-button compact-button" disabled={saving === channel.channel_id} onClick={() => void save(channel)}>{saving === channel.channel_id ? <RefreshCw className="spin" size={15} /> : <Save size={15} />}保存渠道配置</button>
      </article>;
    })}</div>
  </section>;
}

type SettingField = { key: string; label: string; type?: 'number' | 'text' | 'password' | 'boolean' | 'select'; options?: Array<[string, string]>; hint?: string };
type SettingSectionId = 'connection' | 'keyUsage' | 'collection' | 'thresholds' | 'advanced';
type SettingsPageId = SettingsPage;
type NotificationChannelId = 'email' | 'wecom_app' | 'wecom_webhook' | 'feishu_app' | 'feishu_webhook';
const SECRET_SETTING_KEYS = ['new_api_access_token', 'relay_api_token', 'smtp_password', 'wecom_app_secret', 'wecom_webhook_url', 'feishu_app_secret', 'feishu_webhook_url', 'feishu_webhook_secret'];
const SETTING_SECTIONS: Array<{ id: SettingSectionId; title: string; short: string; description: string; icon: ReactNode; fields: SettingField[] }> = [
  { id: 'connection', title: 'New API 连接', short: '连接与凭据', icon: <Network size={18} />, description: '管理接口只读同步与真实探测凭据。敏感字段不会回显。', fields: [
    { key: 'new_api_base_url', label: 'New API 地址' }, { key: 'new_api_user_id', label: '管理用户 ID', type: 'number' }, { key: 'new_api_access_token', label: '管理访问令牌', type: 'password', hint: '留空保持原值' }, { key: 'relay_api_token', label: '真实探测令牌', type: 'password', hint: '留空保持原值' },
  ] },
  { id: 'keyUsage', title: 'Key 用量查询', short: '权限与查询策略', icon: <KeyRound size={18} />, description: '按 Key 即时读取其额度与最近调用。Key 仅在当前请求中转发给 New API，不写入监控数据库、审计日志或浏览器地址。', fields: [
    { key: 'key_usage_enabled', label: '启用 Key 用量查询', type: 'boolean', hint: '关闭后入口和接口同时停用' },
    { key: 'key_usage_min_role', label: '最低可用角色', type: 'select', options: [['admin', '仅管理员'], ['operator', '运维员及管理员'], ['viewer', '所有已登录用户']], hint: '建议保持仅管理员，降低 Key 信息泄露风险' },
    { key: 'key_usage_log_limit', label: '单次返回调用数', type: 'number', hint: '10–500，New API 最多提供最近 1000 条' },
    { key: 'key_usage_attempts_per_minute', label: '每用户每分钟查询次数', type: 'number', hint: '防止撞库、滥用与上游压力' },
    { key: 'key_usage_quota_per_unit', label: '额度换算单位', type: 'number', hint: '默认 500000，即 500000 额度显示为 $1' },
  ] },
  { id: 'collection', title: '采集频率', short: '同步与采样', icon: <RefreshCw size={18} />, description: '保存后监控工作线程将在数秒内热加载。', fields: [
    { key: 'dashboard_refresh_seconds', label: '页面刷新（秒）', type: 'number' }, { key: 'channel_sync_interval_seconds', label: '渠道同步（秒）', type: 'number' }, { key: 'channel_interval_seconds', label: '渠道探测（秒）', type: 'number' }, { key: 'log_interval_seconds', label: '日志同步（秒）', type: 'number' }, { key: 'resource_interval_seconds', label: '资源采样（秒）', type: 'number' }, { key: 'report_interval_seconds', label: '周期报告（秒）', type: 'number' }, { key: 'retention_days', label: '数据保留（天）', type: 'number' },
  ] },
  { id: 'thresholds', title: '耗时与资源阈值', short: '告警策略', icon: <CircleGauge size={18} />, description: '总耗时或首字超过慢请求阈值，并满足 3/5 或 5/10 时告警。', fields: [
    { key: 'slow_request_seconds', label: '慢请求阈值（秒）', type: 'number' }, { key: 'latency_hard_limit_seconds', label: '单次严重阈值（秒）', type: 'number' }, { key: 'latency_reminder_seconds', label: '重复提醒间隔（秒）', type: 'number' }, { key: 'channel_slow_seconds', label: '渠道慢探测（秒）', type: 'number' }, { key: 'resource_sustain_seconds', label: '资源持续时间（秒）', type: 'number' }, { key: 'system_cpu_threshold', label: 'CPU 阈值（%）', type: 'number' }, { key: 'system_memory_threshold', label: '内存阈值（%）', type: 'number' }, { key: 'system_disk_threshold', label: '磁盘阈值（%）', type: 'number' },
  ] },
  { id: 'advanced', title: '高级采集', short: '范围与排除', icon: <SlidersHorizontal size={18} />, description: '日志重叠窗口、容器范围及排除项。', fields: [
    { key: 'log_overlap_seconds', label: '日志重叠窗口（秒）', type: 'number' }, { key: 'log_initial_lookback_seconds', label: '首次回溯（秒）', type: 'number' }, { key: 'docker_container_names', label: '容器名称（逗号分隔）' }, { key: 'disk_path', label: '磁盘采集路径' }, { key: 'excluded_token_names', label: '排除令牌名（逗号分隔）' }, { key: 'container_cpu_threshold', label: '容器 CPU 阈值（%）', type: 'number' }, { key: 'container_memory_threshold', label: '容器内存阈值（%）', type: 'number' },
  ] },
];

function SettingsView({ activePage, onActivePageChange }: { activePage: SettingsPageId; onActivePageChange: (page: SettingsPageId) => void }) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [baseline, setBaseline] = useState<Record<string, unknown>>({});
  const [audit, setAudit] = useState<Array<Record<string, unknown>>>([]);
  const [users, setUsers] = useState<Array<{ username: string; role: string }>>([]);
  const [systemStatus, setSystemStatus] = useState<SystemHealth | null>(null);
  const [overviewChannels, setOverviewChannels] = useState<Channel[]>([]);
  const [overviewBaseline, setOverviewBaseline] = useState('[]');
  const [newUser, setNewUser] = useState('');
  const [newRole, setNewRole] = useState('viewer');
  const [saving, setSaving] = useState(false);
  const [testingChannel, setTestingChannel] = useState<NotificationChannelId | null>(null);
  const [notificationTestResults, setNotificationTestResults] = useState<Partial<Record<NotificationChannelId, { success: boolean; text: string }>>>({});
  const [expandedNotifications, setExpandedNotifications] = useState<Set<NotificationChannelId>>(new Set(['wecom_app']));
  const [message, setMessage] = useState('');
  const load = useCallback(async () => {
    const [settingsPayload, auditPayload, usersPayload, statusPayload, channelPayload] = await Promise.all([api<{ values: Record<string, unknown> }>('settings'), api<{ items: Array<Record<string, unknown>> }>('config-audit?limit=30'), api<{ items: Array<{ username: string; role: string }> }>('access/users'), api<SystemHealth>('system/status'), api<{ items: Channel[] }>('channel-settings')]);
    setValues(settingsPayload.values); setBaseline(settingsPayload.values); setAudit(auditPayload.items); setUsers(usersPayload.items); setSystemStatus(statusPayload);
    const normalizedChannels = channelPayload.items.map((channel) => {
      const legacyVisible = channel.monitor_config?.display_enabled ?? true;
      return {
        ...channel,
        overview_admin_visible: channel.overview_admin_visible ?? channel.monitor_config?.overview_admin_visible ?? legacyVisible,
        overview_viewer_visible: channel.overview_viewer_visible ?? channel.monitor_config?.overview_viewer_visible ?? legacyVisible,
      };
    });
    setOverviewChannels(normalizedChannels);
    setOverviewBaseline(JSON.stringify(normalizedChannels.map((channel) => [channel.channel_id, channel.overview_admin_visible, channel.overview_viewer_visible])));
  }, []);
  useEffect(() => { void load().catch((error) => setMessage(error instanceof Error ? error.message : '配置加载失败')); }, [load]);
  const setValue = (key: string, value: unknown) => setValues((current) => ({ ...current, [key]: value }));
  const save = async () => {
    setSaving(true); setMessage('');
    const payload = { ...values };
    for (const key of SECRET_SETTING_KEYS) if (payload[key] === '********') payload[key] = '';
    try { await api('settings', { method: 'PUT', body: JSON.stringify(payload) }); setMessage('配置已保存，采集器正在热加载'); await load(); }
    catch (error) { setMessage(error instanceof Error ? error.message : '保存失败'); }
    finally { setSaving(false); }
  };
  const testNotification = async (channel: NotificationChannelId) => {
    if (dirty) { setMessage('请先保存当前通知配置，再发送测试消息'); return; }
    setTestingChannel(channel); setMessage('');
    setNotificationTestResults((current) => {
      const next = { ...current };
      delete next[channel];
      return next;
    });
    try {
      await api('notifications/test', { method: 'POST', body: JSON.stringify({ channel }) });
      setNotificationTestResults((current) => ({ ...current, [channel]: { success: true, text: `测试告警发送成功 · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}` } }));
    } catch (error) {
      setNotificationTestResults((current) => ({ ...current, [channel]: { success: false, text: error instanceof Error ? error.message : '测试告警发送失败' } }));
    }
    finally { setTestingChannel(null); }
  };
  const toggleNotificationPanel = (channel: NotificationChannelId) => setExpandedNotifications((current) => {
    const next = new Set(current);
    if (next.has(channel)) next.delete(channel); else next.add(channel);
    return next;
  });
  const addUser = async () => { if (!newUser.trim()) return; await api(`access/users/${encodeURIComponent(newUser.trim())}`, { method: 'PUT', body: JSON.stringify({ role: newRole }) }); setNewUser(''); await load(); };
  const setOverviewVisibility = (channelId: number, audience: 'admin' | 'viewer', visible: boolean) => setOverviewChannels((current) => current.map((channel) => channel.channel_id === channelId ? { ...channel, [audience === 'admin' ? 'overview_admin_visible' : 'overview_viewer_visible']: visible } : channel));
  const setAllOverviewVisibility = (audience: 'admin' | 'viewer', visible: boolean) => setOverviewChannels((current) => current.map((channel) => ({ ...channel, [audience === 'admin' ? 'overview_admin_visible' : 'overview_viewer_visible']: visible })));
  const saveOverviewVisibility = async () => {
    setSaving(true); setMessage('');
    try {
      await api('channel-settings/visibility', { method: 'PUT', body: JSON.stringify({ items: overviewChannels.map((channel) => ({ channel_id: channel.channel_id, overview_admin_visible: channel.overview_admin_visible ?? true, overview_viewer_visible: channel.overview_viewer_visible ?? true })) }) });
      setMessage('总览展示范围已保存，对应用户下次刷新时立即生效');
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : '总览展示配置保存失败'); }
    finally { setSaving(false); }
  };
  const activeSection = SETTING_SECTIONS.find((section) => section.id === activePage);
  const dirty = JSON.stringify(values) !== JSON.stringify(baseline);
  const overviewDirty = JSON.stringify(overviewChannels.map((channel) => [channel.channel_id, channel.overview_admin_visible, channel.overview_viewer_visible])) !== overviewBaseline;
  const pages: Array<{ id: SettingsPageId; title: string; description: string; icon: ReactNode; count?: number }> = [
    { id: 'status', title: '运行状态', description: '采集链路自检', icon: <ShieldCheck size={18} />, count: systemStatus ? Object.keys(systemStatus.collectors).length : 0 },
    { id: 'overview', title: '总览展示', description: '按角色控制渠道', icon: <Eye size={18} />, count: overviewChannels.filter((channel) => channel.enabled).length },
    { id: 'notifications', title: '通知中心', description: '企微、飞书与邮件', icon: <BellRing size={18} />, count: ['email_enabled', 'wecom_app_enabled', 'wecom_webhook_enabled', 'feishu_app_enabled', 'feishu_webhook_enabled'].filter((key) => Boolean(values[key])).length },
    ...SETTING_SECTIONS.map((section) => ({ id: section.id, title: section.title, description: section.short, icon: section.icon, count: section.fields.length })),
    { id: 'access', title: '角色映射', description: '访问与权限', icon: <UserCog size={18} />, count: users.length },
    { id: 'audit', title: '配置审计', description: '最近变更记录', icon: <Clock3 size={18} />, count: audit.length },
  ];
  return <section>
    <div className="section-heading settings-heading"><div><span className="eyebrow">RUNTIME CONTROL CENTER</span><h2>系统配置</h2><p>按任务分区管理，只展示当前配置组；配置保存在监控数据库中，不写入 New API。</p></div><div className={classNames('settings-dirty-state', (dirty || overviewDirty) && 'settings-dirty')}><i />{dirty || overviewDirty ? '有未保存更改' : '配置已同步'}</div></div>
    {message && <div className="config-message">{message}</div>}
    <div className="settings-workspace">
      <aside className="settings-nav" aria-label="系统配置分类">{pages.map((page) => <button type="button" className={classNames(activePage === page.id && 'active')} key={page.id} onClick={() => onActivePageChange(page.id)}><span className="settings-nav-icon">{page.icon}</span><span><strong>{page.title}</strong><small>{page.description}</small></span>{page.count != null && <b>{page.count}</b>}<ChevronRight size={15} /></button>)}</aside>
      <div className="settings-stage">
        {activePage === 'status' && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><ShieldCheck size={18} /></div><div><span className="eyebrow">SELF MONITORING</span><h3>采集链路状态</h3><p>监控程序同时检查自身是否仍在持续产生新数据，避免“页面正常但采集已经停止”。</p></div><StatusPill tone={systemStatus?.status === 'ok' ? 'ok' : 'bad'}>{systemStatus?.status === 'ok' ? '全部正常' : '存在降级'}</StatusPill></div><div className="collector-health-grid"><div className="collector-health-card"><span>数据库</span><strong>{systemStatus?.database === 'ok' ? '正常' : '异常'}</strong><small>{systemStatus?.database_error || 'SQLite 可读写'}</small></div><div className="collector-health-card"><span>监控进程</span><strong>{systemStatus?.monitor_worker === 'running' ? '运行中' : systemStatus?.monitor_worker || '未知'}</strong><small>{systemStatus?.monitor_error || '工作线程持续运行'}</small></div>{Object.entries(systemStatus?.collectors || {}).map(([name, collector]) => { const labels: Record<string, string> = { channel_sync: '渠道同步', channel_probe: '渠道探测', logs: '使用日志', resources: '机器资源' }; return <div className={classNames('collector-health-card', collector.status === 'stale' && 'collector-health-stale')} key={name}><span>{labels[name] || name}</span><strong>{collector.status === 'ok' ? '正常' : collector.status === 'starting' ? '启动中' : '数据过期'}</strong><small>最后成功 {collector.age_seconds}s 前 · 阈值 {collector.stale_after_seconds}s</small>{collector.consecutive_failures > 0 && <em>连续失败 {collector.consecutive_failures} 次</em>}{collector.last_error && <code title={collector.last_error}>{collector.last_error}</code>}</div>; })}</div><div className="settings-action-bar"><div><strong>最后检查 {systemStatus ? formatFullTime(systemStatus.timestamp) : '—'}</strong><small>数据超过动态失效阈值后，健康检查变为 503，并生成异常与恢复事件。</small></div><button className="secondary-button" onClick={() => void load()}><RefreshCw size={16} />立即刷新</button></div></article>}
        {activePage === 'overview' && <article className="settings-card settings-focus-card overview-settings-card">
          <div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><Eye size={18} /></div><div><span className="eyebrow">ROLE-BASED OVERVIEW</span><h3>总览渠道展示</h3><p>管理端与普通用户使用独立渠道清单；总览状态、渠道卡片和异常判断都按当前登录角色计算。</p></div><span className="settings-field-count">{overviewChannels.length} 个渠道</span></div>
          <div className="overview-audience-summary">
            <div><span className="overview-audience-icon admin"><ShieldCheck size={18} /></span><div><strong>管理端</strong><small>管理员与运维员可见</small></div><b>{overviewChannels.filter((channel) => channel.enabled && channel.overview_admin_visible).length}</b></div>
            <div><span className="overview-audience-icon viewer"><Eye size={18} /></span><div><strong>普通用户</strong><small>只读总览用户可见</small></div><b>{overviewChannels.filter((channel) => channel.enabled && channel.overview_viewer_visible).length}</b></div>
            <p><AlertTriangle size={15} />隐藏仅影响对应角色的总览展示和状态汇总，不会停止渠道探测、日志采集或告警。</p>
          </div>
          <div className="overview-visibility-toolbar"><div><strong>批量设置</strong><small>先批量调整，再对个别渠道微调。</small></div><div><button onClick={() => setAllOverviewVisibility('admin', true)}>管理端全开</button><button onClick={() => setAllOverviewVisibility('admin', false)}>管理端全关</button><button onClick={() => setAllOverviewVisibility('viewer', true)}>普通用户全开</button><button onClick={() => setAllOverviewVisibility('viewer', false)}>普通用户全关</button><button onClick={() => setOverviewChannels((current) => current.map((channel) => ({ ...channel, overview_viewer_visible: channel.overview_admin_visible })))}>普通用户跟随管理端</button></div></div>
          <div className="overview-visibility-table">
            <div className="overview-visibility-head"><span>渠道</span><span>New API</span><span>管理端总览</span><span>普通用户总览</span></div>
            {overviewChannels.map((channel) => <div className={classNames('overview-visibility-row', !channel.enabled && 'disabled')} key={channel.channel_id}><div><span className="provider-mark compact">{channel.name.slice(0, 2).toUpperCase()}</span><span><strong>{channel.name}</strong><small>#{channel.channel_id} · {channel.group || 'default'}</small></span></div><StatusPill tone={channel.enabled ? 'ok' : 'muted'}>{channel.enabled ? '已启用' : '已禁用'}</StatusPill><Toggle checked={channel.overview_admin_visible ?? true} onChange={(visible) => setOverviewVisibility(channel.channel_id, 'admin', visible)} label="管理端" /><Toggle checked={channel.overview_viewer_visible ?? true} onChange={(visible) => setOverviewVisibility(channel.channel_id, 'viewer', visible)} label="普通用户" /></div>)}
          </div>
          <div className="settings-action-bar"><div><strong>{overviewDirty ? '展示范围尚未应用' : '角色展示范围已生效'}</strong><small>保存后无需重启，管理员和普通用户刷新总览即可看到各自渠道。</small></div><button className="secondary-button" disabled={!overviewDirty || saving} onClick={() => void load()}>撤销更改</button><button className="primary-button settings-save" disabled={!overviewDirty || saving || !overviewChannels.length} onClick={() => void saveOverviewVisibility()}>{saving ? <RefreshCw className="spin" size={16} /> : <Save size={16} />}保存展示范围</button></div>
        </article>}
        {activePage === 'notifications' && <article className="settings-card settings-focus-card notification-center-card">
          <div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><BellRing size={18} /></div><div><span className="eyebrow">MULTI-CHANNEL DELIVERY</span><h3>告警通知中心</h3><p>同一告警可同时发送到多个渠道；单个渠道失败不会阻断其他渠道。敏感凭据加密保存且不会回显。</p></div><span className="settings-field-count">{['email_enabled', 'wecom_app_enabled', 'wecom_webhook_enabled', 'feishu_app_enabled', 'feishu_webhook_enabled'].filter((key) => Boolean(values[key])).length} 个已启用</span></div>
          <div className="notification-global-bar"><label><span>通知标题前缀</span><input value={String(values.subject_prefix ?? '')} onChange={(event) => setValue('subject_prefix', event.target.value)} /></label><Toggle checked={Boolean(values.send_startup_email)} onChange={(value) => setValue('send_startup_email', value)} label="监控启动时发送通知" /><div><ShieldCheck size={15} /><span>应用 Secret、Webhook 地址与签名密钥均按秘密字段加密存储。</span></div></div>
          <div className="notification-channel-grid">
            {([
              { id: 'wecom_app' as const, title: '企业微信自建应用', description: '适合直接通知应用可见范围内的成员', icon: <MessageSquare size={18} />, enabled: Boolean(values.wecom_app_enabled), configured: Boolean(values.wecom_corp_id && values.wecom_agent_id && values.wecom_app_secret && (values.wecom_to_user || values.wecom_to_party || values.wecom_to_tag)), fields: <><label><span>企业 ID</span><input value={String(values.wecom_corp_id ?? '')} onChange={(event) => setValue('wecom_corp_id', event.target.value)} /></label><label><span>AgentId</span><input type="number" value={String(values.wecom_agent_id ?? '')} onChange={(event) => setValue('wecom_agent_id', Number(event.target.value))} /></label><label className="notification-wide"><span>应用 Secret</span><input type="password" value={String(values.wecom_app_secret ?? '')} placeholder="留空保持原值" onChange={(event) => setValue('wecom_app_secret', event.target.value)} /></label><label><span>成员</span><input value={String(values.wecom_to_user ?? '')} placeholder="@all 或 user1|user2" onChange={(event) => setValue('wecom_to_user', event.target.value)} /></label><label><span>部门</span><input value={String(values.wecom_to_party ?? '')} placeholder="可选，1|2" onChange={(event) => setValue('wecom_to_party', event.target.value)} /></label><label><span>标签</span><input value={String(values.wecom_to_tag ?? '')} placeholder="可选，1|2" onChange={(event) => setValue('wecom_to_tag', event.target.value)} /></label></> },
              { id: 'wecom_webhook' as const, title: '企业微信群机器人', description: '通过群机器人 Webhook 推送到指定群聊', icon: <Send size={18} />, enabled: Boolean(values.wecom_webhook_enabled), configured: Boolean(values.wecom_webhook_url), fields: <label className="notification-wide"><span>Webhook 地址</span><input type="password" value={String(values.wecom_webhook_url ?? '')} placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." onChange={(event) => setValue('wecom_webhook_url', event.target.value)} /></label> },
              { id: 'feishu_app' as const, title: '飞书自建应用', description: '使用应用身份向用户或群聊发送消息', icon: <MessageSquare size={18} />, enabled: Boolean(values.feishu_app_enabled), configured: Boolean(values.feishu_app_id && values.feishu_app_secret && values.feishu_receive_id), fields: <><label><span>App ID</span><input value={String(values.feishu_app_id ?? '')} onChange={(event) => setValue('feishu_app_id', event.target.value)} /></label><label><span>App Secret</span><input type="password" value={String(values.feishu_app_secret ?? '')} placeholder="留空保持原值" onChange={(event) => setValue('feishu_app_secret', event.target.value)} /></label><label><span>接收者类型</span><select value={String(values.feishu_receive_id_type ?? 'chat_id')} onChange={(event) => setValue('feishu_receive_id_type', event.target.value)}><option value="chat_id">群聊 chat_id</option><option value="open_id">用户 open_id</option><option value="user_id">用户 user_id</option><option value="union_id">用户 union_id</option><option value="email">用户邮箱</option></select></label><label><span>接收者 ID</span><input value={String(values.feishu_receive_id ?? '')} placeholder="还需要提供此项" onChange={(event) => setValue('feishu_receive_id', event.target.value)} /></label></> },
              { id: 'feishu_webhook' as const, title: '飞书群机器人', description: '支持普通 Webhook 与签名校验机器人', icon: <Send size={18} />, enabled: Boolean(values.feishu_webhook_enabled), configured: Boolean(values.feishu_webhook_url), fields: <><label className="notification-wide"><span>Webhook 地址</span><input type="password" value={String(values.feishu_webhook_url ?? '')} placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." onChange={(event) => setValue('feishu_webhook_url', event.target.value)} /></label><label className="notification-wide"><span>签名密钥</span><input type="password" value={String(values.feishu_webhook_secret ?? '')} placeholder="机器人未启用签名时可留空" onChange={(event) => setValue('feishu_webhook_secret', event.target.value)} /></label></> },
              { id: 'email' as const, title: '电子邮件', description: '保留 SMTP 作为独立通道或故障兜底', icon: <Mail size={18} />, enabled: Boolean(values.email_enabled), configured: Boolean(values.smtp_host && values.smtp_to), fields: <><label><span>SMTP 地址</span><input value={String(values.smtp_host ?? '')} onChange={(event) => setValue('smtp_host', event.target.value)} /></label><label><span>端口</span><input type="number" value={String(values.smtp_port ?? '')} onChange={(event) => setValue('smtp_port', Number(event.target.value))} /></label><label><span>SMTP 用户</span><input value={String(values.smtp_user ?? '')} onChange={(event) => setValue('smtp_user', event.target.value)} /></label><label><span>SMTP 密码</span><input type="password" value={String(values.smtp_password ?? '')} placeholder="留空保持原值" onChange={(event) => setValue('smtp_password', event.target.value)} /></label><label><span>发件人</span><input value={String(values.smtp_from ?? '')} onChange={(event) => setValue('smtp_from', event.target.value)} /></label><label><span>收件人</span><input value={String(values.smtp_to ?? '')} placeholder="多个地址用逗号分隔" onChange={(event) => setValue('smtp_to', event.target.value)} /></label><Toggle checked={Boolean(values.smtp_ssl)} onChange={(value) => { setValue('smtp_ssl', value); if (value) setValue('smtp_starttls', false); }} label="SSL" /><Toggle checked={Boolean(values.smtp_starttls)} onChange={(value) => { setValue('smtp_starttls', value); if (value) setValue('smtp_ssl', false); }} label="STARTTLS" /></> },
            ]).map((channel) => <section className={classNames('notification-channel', channel.enabled && 'enabled', expandedNotifications.has(channel.id) && 'expanded')} key={channel.id}><button type="button" className="notification-channel-head" onClick={() => toggleNotificationPanel(channel.id)}><span className="notification-channel-icon">{channel.icon}</span><span><strong>{channel.title}</strong><small>{channel.description}</small></span><i className={classNames('notification-state-dot', channel.enabled && channel.configured && 'ready', channel.enabled && !channel.configured && 'incomplete')} /><b>{channel.enabled ? channel.configured ? '已启用' : '待补全' : channel.configured ? '可测试' : '未配置'}</b><ChevronRight size={16} /></button>{expandedNotifications.has(channel.id) && <div className="notification-channel-body"><div className="notification-enable-row"><Toggle checked={channel.enabled} onChange={(enabled) => setValue(`${channel.id}_enabled`, enabled)} label="启用此通知渠道" /><button type="button" className="secondary-button notification-test" disabled={!channel.configured || dirty || testingChannel !== null} onClick={() => void testNotification(channel.id)}>{testingChannel === channel.id ? <RefreshCw className="spin" size={14} /> : <Send size={14} />}{testingChannel === channel.id ? '正在发送' : '触发测试告警'}</button></div>{notificationTestResults[channel.id] && <div className={classNames('notification-test-result', notificationTestResults[channel.id]?.success ? 'success' : 'failed')}>{notificationTestResults[channel.id]?.success ? <CheckCircle2 size={15} /> : <XCircle size={15} />}<span>{notificationTestResults[channel.id]?.text}</span></div>}<div className="notification-fields">{channel.fields}</div>{channel.id === 'feishu_app' && <p className="notification-requirement"><AlertTriangle size={14} />App ID 与 Secret 已足够换取令牌，但发送消息仍必须填写用户或群聊的接收者 ID。</p>}</div>}</section>)}
          </div>
          <div className="settings-action-bar"><div><strong>{dirty ? '通知配置尚未应用' : '通知路由已生效'}</strong><small>{dirty ? '保存后工作线程会热加载；测试按钮将在保存后可用。' : '可以展开任一渠道发送真实测试通知。'}</small></div><button className="secondary-button" disabled={!dirty || saving} onClick={() => { setValues(baseline); setMessage('已撤销本次未保存更改'); }}>撤销更改</button><button className="primary-button settings-save" disabled={!dirty || saving} onClick={() => void save()}>{saving ? <RefreshCw className="spin" size={16} /> : <Save size={16} />}保存通知配置</button></div>
        </article>}
        {activeSection && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark">{activeSection.icon}</div><div><span className="eyebrow">CONFIGURATION GROUP</span><h3>{activeSection.title}</h3><p>{activeSection.description}</p></div><span className="settings-field-count">{activeSection.fields.length} 项</span></div><div className="settings-fields">{activeSection.fields.map((field) => field.type === 'boolean' ? <Toggle key={field.key} label={field.label} checked={Boolean(values[field.key])} onChange={(value) => setValue(field.key, value)} /> : <label key={field.key}><span>{field.label}</span>{field.type === 'select' ? <select value={String(values[field.key] ?? '')} onChange={(event) => setValue(field.key, event.target.value)}>{field.options?.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select> : <input type={field.type === 'password' ? 'password' : field.type === 'number' ? 'number' : 'text'} value={String(values[field.key] ?? '')} placeholder={field.hint} onChange={(event) => setValue(field.key, field.type === 'number' ? Number(event.target.value) : event.target.value)} />}<small>{field.hint}</small></label>)}</div><div className="settings-action-bar"><div><strong>{dirty ? '更改尚未应用' : '当前配置已生效'}</strong><small>{dirty ? '保存后采集器将在数秒内热加载，无需重启。' : '你可以切换左侧分类继续检查其他配置。'}</small></div><button className="secondary-button" disabled={!dirty || saving} onClick={() => { setValues(baseline); setMessage('已撤销本次未保存更改'); }}>撤销更改</button><button className="primary-button settings-save" disabled={!dirty || saving} onClick={() => void save()}>{saving ? <RefreshCw className="spin" size={16} /> : <Save size={16} />}保存并应用</button></div></article>}
        {activePage === 'access' && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><UserCog size={18} /></div><div><span className="eyebrow">ACCESS CONTROL</span><h3>角色映射</h3><p>普通 New API 用户默认只能查看总览，Admin 自动为运维员，Root 自动为管理员；这里可以对指定用户覆盖。</p></div></div><div className="user-add user-add-wide"><input placeholder="New API 用户名" value={newUser} onChange={(event) => setNewUser(event.target.value)} /><select value={newRole} onChange={(event) => setNewRole(event.target.value)}><option value="viewer">只读总览</option><option value="operator">运维</option><option value="admin">管理员</option></select><button onClick={() => void addUser()}><UserCog size={15} />添加映射</button></div><div className="role-list">{users.map((user) => <div key={user.username}><strong>{user.username}</strong><span>{user.role}</span><button onClick={async () => { await api(`access/users/${encodeURIComponent(user.username)}`, { method: 'PUT', body: JSON.stringify({ role: null }) }); await load(); }}><X size={14} /></button></div>)}{!users.length && <p>暂无用户覆盖规则</p>}</div></article>}
        {activePage === 'audit' && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><Clock3 size={18} /></div><div><span className="eyebrow">CHANGE HISTORY</span><h3>配置审计</h3><p>最近30次系统、渠道和权限变更，便于快速定位误操作。</p></div><span className="settings-field-count">{audit.length} 条</span></div><div className="audit-list audit-list-wide">{audit.map((entry) => <div key={String(entry.id)}><span>{formatFullTime(Number(entry.created_at))}</span><strong>{String(entry.actor)}</strong><small>{String(entry.action)} · {String(entry.target)}</small></div>)}</div></article>}
      </div>
    </div>
  </section>;
}

function MetricCard({ icon, label, value, detail, tone = 'neutral' }: { icon: ReactNode; label: string; value: string; detail: string; tone?: 'neutral' | 'ok' | 'warn' | 'bad' }) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <div className="metric-icon">{icon}</div>
      <div><div className="metric-label">{label}</div><div className="metric-value">{value}</div><div className="metric-detail">{detail}</div></div>
    </article>
  );
}

function HistoryBars({ channel }: { channel: Channel }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [pinnedIndex, setPinnedIndex] = useState<number | null>(null);
  const closeTimer = useRef<number | null>(null);
  const historyBlock = useRef<HTMLDivElement | null>(null);
  const barRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [tooltipLayout, setTooltipLayout] = useState({ left: 0, top: 0, width: 360, arrow: 180, below: false });
  useEffect(() => () => {
    if (closeTimer.current != null) window.clearTimeout(closeTimer.current);
  }, []);
  const activeIndex = hoverIndex ?? pinnedIndex;
  const activePoint = activeIndex == null ? null : channel.history[activeIndex];
  const activeState = activePoint
    ? !activePoint.success
      ? 'bad'
      : activePoint.elapsed_ms > 30_000 || (activePoint.frt_ms || 0) > 30_000
        ? 'warn'
        : 'ok'
    : 'ok';
  const activeStatus = activeState === 'bad' ? '异常' : activeState === 'warn' ? '延迟' : '正常';
  useLayoutEffect(() => {
    if (!activePoint || activeIndex == null) return;
    const update = () => {
      const bar = barRefs.current[activeIndex];
      if (!bar) return;
      const rect = bar.getBoundingClientRect();
      const viewportPadding = 12;
      const width = Math.min(420, Math.max(300, window.innerWidth - viewportPadding * 2));
      const anchor = rect.left + rect.width / 2;
      const tooltipHeight = tooltipRef.current?.offsetHeight || 260;
      const below = rect.top - tooltipHeight - 12 < viewportPadding;
      const left = Math.min(
        Math.max(viewportPadding, anchor - width / 2),
        window.innerWidth - width - viewportPadding,
      );
      const top = below ? rect.bottom + 10 : rect.top - tooltipHeight - 10;
      setTooltipLayout({ left, top, width, arrow: anchor - left, below });
    };
    update();
    const frame = window.requestAnimationFrame(update);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [activePoint, activeIndex]);
  const cancelClose = () => {
    if (closeTimer.current != null) window.clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };
  const scheduleClose = () => {
    cancelClose();
    if (pinnedIndex != null) return;
    closeTimer.current = window.setTimeout(() => setHoverIndex(null), 320);
  };

  return (
    <div
      ref={historyBlock}
      className={classNames('history-block', activePoint && 'history-block-active')}
      onMouseEnter={cancelClose}
      onMouseLeave={scheduleClose}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget) && pinnedIndex == null) setHoverIndex(null);
      }}
    >
      <div className="history-heading"><span>HISTORY ({channel.history.length}PTS)</span><span>PAST → NOW</span></div>
      <div className="history-bars" role="group" aria-label={`${channel.name} 最近探测历史`} style={channel.history.length ? { gridTemplateColumns: `repeat(${channel.history.length}, minmax(0, 1fr))` } : undefined}>
        {channel.history.length === 0 && <div className="history-empty">等待首次探测</div>}
        {channel.history.map((point, index) => {
          const state = !point.success ? 'bad' : point.elapsed_ms > 30_000 || (point.frt_ms || 0) > 30_000 ? 'warn' : 'ok';
          const label = `${formatFullTime(point.observed_at)}，${point.success ? '正常' : '异常'}，总耗时 ${formatDuration(point.elapsed_ms)}，首字 ${formatDuration(point.frt_ms)}`;
          return (
            <button
              key={`${point.observed_at}-${index}`}
              ref={(element) => { barRefs.current[index] = element; }}
              type="button"
              className={classNames('history-bar', `history-${state}`, activeIndex === index && 'history-bar-active', pinnedIndex === index && 'history-bar-pinned')}
              aria-label={label}
              aria-describedby={activeIndex === index ? `history-tooltip-${channel.channel_id}` : undefined}
              aria-pressed={pinnedIndex === index}
              onMouseEnter={() => { cancelClose(); setHoverIndex(index); }}
              onFocus={() => setHoverIndex(index)}
              onClick={() => { setPinnedIndex((current) => current === index ? null : index); setHoverIndex(index); }}
            />
          );
        })}
      </div>
      {activePoint && createPortal(
        <div
          ref={tooltipRef}
          id={`history-tooltip-${channel.channel_id}`}
          className={classNames('history-tooltip', `history-tooltip-${activeState}`, tooltipLayout.below && 'history-tooltip-below', pinnedIndex === activeIndex && 'history-tooltip-pinned')}
          role="tooltip"
          style={{ left: tooltipLayout.left, top: tooltipLayout.top, width: tooltipLayout.width, '--tooltip-arrow-left': `${tooltipLayout.arrow}px` } as React.CSSProperties}
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
        >
          <div className="history-tooltip-head">
            <StatusPill tone={activeState}>{activeStatus}</StatusPill>
            <time>{formatFullTime(activePoint.observed_at)}</time>
            <strong>{activeStatus}</strong>
          </div>
          <div className="history-tooltip-metrics">
            <div><span>Latency</span><strong>{formatDuration(activePoint.elapsed_ms)}</strong></div>
            <div><span>First byte</span><strong>{formatDuration(activePoint.frt_ms)}</strong></div>
          </div>
          <div className="history-tooltip-message">
            <div><small>{activePoint.success ? '探测结果' : '错误详情'}</small><span>{activePoint.success ? `验证通过（${formatDuration(activePoint.elapsed_ms)}）` : activePoint.message || '验证失败'}</span></div>
            <em>{activePoint.source === 'real' ? '真实请求' : '内置测试'} · {pinnedIndex === activeIndex ? '已固定' : '点击色块固定'}</em>
          </div>
        </div>
      , document.body)}
    </div>
  );
}

function ChannelCard({ channel, onOpen }: { channel: Channel; onOpen: () => void }) {
  const latest = channel.latest;
  const stale = latest ? Date.now() / 1000 - latest.observed_at > 12 * 60 : true;
  const delayed = Boolean(latest && latest.success && (latest.elapsed_ms > 30_000 || (latest.frt_ms || 0) > 30_000));
  const tone = stale ? 'muted' : !latest?.success ? 'bad' : delayed ? 'warn' : 'ok';
  const statusText = stale ? '数据陈旧' : !latest?.success ? '异常' : delayed ? '延迟' : '正常';
  const modelLabel = channel.models.length ? channel.models.slice(0, 2).join(' · ') : '未配置模型';
  return (
    <article className={classNames('channel-card', tone === 'bad' && 'channel-card-bad')}>
      <button className="channel-open" onClick={onOpen} aria-label={`查看 ${channel.name} 详情`}><ChevronRight size={19} /></button>
      <div className="channel-header">
        <div className="provider-mark">{channel.name.slice(0, 2).toUpperCase()}</div>
        <div className="channel-title"><h3>{channel.name}</h3><p>{channel.group || 'default'} <span>·</span> {modelLabel}</p></div>
        <StatusPill tone={tone}>{statusText}</StatusPill>
      </div>
      <div className="probe-source"><span>{latest?.source === 'real' ? 'REAL REQUEST' : 'BUILT-IN CHECK'}</span><span>{latest ? formatTime(latest.observed_at) : '未探测'}</span></div>
      <div className="channel-stats">
        <div><span><Activity size={14} />探测总耗时</span><strong>{formatDuration(latest?.elapsed_ms)}</strong></div>
        <div><span><Network size={14} />首字响应</span><strong>{formatDuration(latest?.frt_ms)}</strong></div>
      </div>
      <div className="availability-row">
        <div><span>可用率（7天）</span><small>{channel.availability.successes}/{channel.availability.total} 成功</small></div>
        <strong className={tone === 'bad' ? 'text-bad' : tone === 'warn' ? 'text-warn' : 'text-ok'}>{channel.availability.percentage == null ? '—' : `${channel.availability.percentage.toFixed(2)}%`}</strong>
      </div>
      <div className="usage-strip"><span>24H 请求 <b>{channel.usage_24h.requests}</b></span><span>P95 <b>{channel.usage_24h.p95_seconds.toFixed(2)}s</b></span><span>慢请求 <b className={channel.usage_24h.slow ? 'text-warn' : ''}>{channel.usage_24h.slow}</b></span></div>
      <HistoryBars channel={channel} />
    </article>
  );
}

function DetailDrawer({ channel, onClose }: { channel: Channel; onClose: () => void }) {
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`${channel.name} 渠道详情`} onMouseDown={(event) => event.stopPropagation()}>
        <button className="icon-button drawer-close" onClick={onClose} aria-label="关闭"><X size={20} /></button>
        <div className="eyebrow">CHANNEL DETAIL / #{channel.channel_id}</div>
        <h2>{channel.name}</h2>
        <p className="drawer-subtitle">数据与 New API 渠道配置实时同步，探测结果独立存档。</p>
        <div className="drawer-grid">
          <div><span>状态</span><strong>{channel.latest?.success ? '正常' : '异常'}</strong></div>
          <div><span>探测方式</span><strong>{channel.latest?.source === 'real' ? '真实模型请求' : 'New API 内置测试'}</strong></div>
          <div><span>总耗时</span><strong>{formatDuration(channel.latest?.elapsed_ms)}</strong></div>
          <div><span>首字耗时</span><strong>{formatDuration(channel.latest?.frt_ms)}</strong></div>
        </div>
        <section className="drawer-section"><h3>最近 60 次探测</h3><HistoryBars channel={channel} /></section>
        <section className="drawer-section"><h3>模型范围</h3><div className="tag-list">{channel.models.map((model) => <span key={model}>{model}</span>)}</div></section>
        <section className="drawer-section"><h3>同步信息</h3><dl className="detail-list"><div><dt>渠道组</dt><dd>{channel.group || 'default'}</dd></div><div><dt>配置同步</dt><dd>{formatTime(channel.synced_at, true)}</dd></div><div><dt>最后请求</dt><dd>{formatTime(channel.usage_24h.last_request_at, true)}</dd></div></dl></section>
      </aside>
    </div>
  );
}

function Overview({ summary, channels, onChannel }: { summary: Summary; channels: Channel[]; onChannel: (channel: Channel) => void }) {
  const resourceAge = summary.resources.created_at ? Math.floor(Date.now() / 1000 - summary.resources.created_at) : null;
  return (
    <>
      <section className="metrics-grid">
        <MetricCard icon={<CheckCircle2 />} label="渠道健康" value={`${summary.channels.healthy}/${summary.channels.total}`} detail={`${summary.channels.failed} 异常 · ${summary.channels.unknown} 未知`} tone={summary.channels.failed ? 'bad' : 'ok'} />
        <MetricCard icon={<Clock3 />} label="24H 请求耗时" value={`P95 ${summary.requests.p95_seconds.toFixed(2)}s`} detail={`平均 ${summary.requests.average_seconds.toFixed(2)}s · ${summary.requests.total} 次`} tone={summary.requests.slow ? 'warn' : 'neutral'} />
        <MetricCard icon={<AlertTriangle />} label="慢请求" value={`${summary.requests.slow}`} detail={`总耗时或首字 > ${SLOW_SECONDS}s · ${summary.requests.slow_ratio.toFixed(1)}%`} tone={summary.requests.slow ? 'warn' : 'ok'} />
        <MetricCard icon={<Server />} label="机器资源" value={`MEM ${formatPercent(summary.resources.system_memory)}`} detail={resourceAge == null ? '等待资源样本' : `${resourceAge}s 前更新 · DISK ${formatPercent(summary.resources.system_disk)}`} tone={(summary.resources.system_memory || 0) > 85 ? 'bad' : 'neutral'} />
      </section>
      <div className="section-heading"><div><span className="eyebrow">LIVE CHANNEL MATRIX</span><h2>渠道运行状态</h2></div><div className="legend"><span><i className="legend-ok" />正常</span><span><i className="legend-warn" />延迟</span><span><i className="legend-bad" />异常</span></div></div>
      <section className="channel-grid">
        {channels.map((channel) => <ChannelCard key={channel.channel_id} channel={channel} onOpen={() => onChannel(channel)} />)}
        {!channels.length && <div className="empty-state"><Database size={28} /><strong>等待渠道同步</strong><span>监控程序将在首次轮询后展示 New API 渠道。</span></div>}
      </section>
    </>
  );
}

function LogsView({ channels }: { channels: Channel[] }) {
  const [items, setItems] = useState<LogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [slowOnly, setSlowOnly] = useState(false);
  const [channelId, setChannelId] = useState('');
  const [model, setModel] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    const params = new URLSearchParams({ limit: '200', slow_only: String(slowOnly) });
    if (channelId) params.set('channel_id', channelId);
    if (model.trim()) params.set('model_name', model.trim());
    try {
      const payload = await api<{ items: LogItem[]; total: number }>(`logs?${params}`);
      setItems(payload.items);
      setTotal(payload.total);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '日志加载失败');
    } finally {
      setLoading(false);
    }
  }, [channelId, model, slowOnly]);

  useEffect(() => { void load(); }, [load]);
  return (
    <section>
      <div className="section-heading"><div><span className="eyebrow">REAL CONSUMPTION LOGS</span><h2>真实使用日志耗时</h2></div><span className="source-note">仅保存耗时元数据，不保存提示词或响应正文</span></div>
      <div className="filter-bar">
        <label><span>渠道</span><select value={channelId} onChange={(event) => setChannelId(event.target.value)}><option value="">全部渠道</option>{channels.map((channel) => <option key={channel.channel_id} value={channel.channel_id}>{channel.name}</option>)}</select></label>
        <label><span>模型精确匹配</span><div className="filter-input"><Search size={15} /><input value={model} onChange={(event) => setModel(event.target.value)} placeholder="例如 gpt-5.6-sol" /></div></label>
        <label className="check-label"><input type="checkbox" checked={slowOnly} onChange={(event) => setSlowOnly(event.target.checked)} /><span>只看超过 60 秒</span></label>
        <button className="secondary-button" onClick={() => void load()}><RefreshCw className={loading ? 'spin' : ''} size={16} />刷新</button>
      </div>
      {error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}
      <div className="table-shell">
        <div className="table-meta">匹配 {total} 条，显示最近 {items.length} 条</div>
        <div className="table-scroll"><table><thead><tr><th>时间</th><th>渠道 / 模型</th><th>用户 / 令牌</th><th>总耗时</th><th>首字</th><th>模式</th><th>请求 ID</th></tr></thead><tbody>
          {items.map((item) => {
            const slow = item.use_time > SLOW_SECONDS || (item.frt_ms || 0) > SLOW_SECONDS * 1000;
            return <tr key={`${item.request_id}-${item.created_at}`} className={slow ? 'slow-row' : ''}><td className="mono">{formatTime(item.created_at, true)}</td><td><strong>{item.channel_name || `#${item.channel_id}`}</strong><span>{item.model_name}</span></td><td><strong>{item.username || '—'}</strong><span>{item.token_name || '—'}</span></td><td><b className={item.use_time > SLOW_SECONDS ? 'text-bad' : ''}>{item.use_time.toFixed(2)}s</b></td><td><b className={(item.frt_ms || 0) > SLOW_SECONDS * 1000 ? 'text-bad' : ''}>{formatDuration(item.frt_ms)}</b></td><td>{item.is_stream ? '流式' : '非流式'}</td><td className="mono request-id" title={item.request_id}>{item.request_id || '—'}</td></tr>;
          })}
          {!loading && !items.length && <tr><td colSpan={7}><div className="empty-row">当前筛选条件下暂无日志</div></td></tr>}
        </tbody></table></div>
      </div>
    </section>
  );
}

function KeyUsageView() {
  const [apiKey, setApiKey] = useState('');
  const [revealed, setRevealed] = useState(false);
  const [result, setResult] = useState<KeyUsageResult | null>(null);
  const [selected, setSelected] = useState<KeyUsageCall | null>(null);
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const query = async (event: FormEvent) => {
    event.preventDefault();
    if (!apiKey) return;
    setLoading(true); setError(''); setSelected(null);
    try {
      const payload = await api<KeyUsageResult>('key-usage/query', { method: 'POST', body: JSON.stringify({ api_key: apiKey }) });
      setResult(payload);
    } catch (requestError) {
      setResult(null);
      setError(requestError instanceof Error ? requestError.message : 'Key 查询失败');
    } finally { setLoading(false); }
  };
  const clear = () => { setApiKey(''); setResult(null); setSelected(null); setFilter(''); setError(''); setRevealed(false); };
  const copyText = (value: string) => { if (value) void navigator.clipboard.writeText(value); };
  const calls = useMemo(() => {
    const keyword = filter.trim().toLowerCase();
    if (!result || !keyword) return result?.calls || [];
    return result.calls.filter((item) => [item.model_name, item.channel_name, item.request_id, item.upstream_request_id, item.group].some((value) => value.toLowerCase().includes(keyword)));
  }, [filter, result]);
  const usagePercentage = result?.usage.used_percentage ?? 0;
  const expiryTone = !result?.usage.expires_at || result.usage.expires_at > Date.now() / 1000 ? 'ok' : 'bad';

  return <section className="key-usage-page">
    <div className="section-heading key-usage-heading"><div><span className="eyebrow">TOKEN INTELLIGENCE / ON DEMAND</span><h2>Key 用量与调用详情</h2><p>直接读取该 Key 在 New API 中的真实额度与最近调用，不依赖监控采集延迟。</p></div><span className="source-note"><ShieldCheck size={14} />只读查询 · 不保存 Key</span></div>
    <form className="key-query-console" onSubmit={(event) => void query(event)}>
      <div className="key-query-mark"><Fingerprint size={24} /></div>
      <div className="key-query-copy"><strong>输入需要核验的 API Key</strong><span>Key 只通过服务端内存转发到已配置的 New API；不会进入 URL、数据库、审计记录或前端缓存。</span></div>
      <label className="key-query-input"><KeyRound size={18} /><input type={revealed ? 'text' : 'password'} value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="sk-••••••••••••••••" autoComplete="off" spellCheck={false} aria-label="API Key" /><button type="button" onClick={() => setRevealed((value) => !value)} title={revealed ? '隐藏 Key' : '显示 Key'}>{revealed ? <EyeOff size={17} /> : <Eye size={17} />}</button>{apiKey && <button type="button" onClick={clear} title="清空"><X size={17} /></button>}</label>
      <button className="primary-button key-query-submit" type="submit" disabled={loading || apiKey.length < 4}>{loading ? <RefreshCw className="spin" size={17} /> : <Search size={17} />}{loading ? '正在安全查询' : '查询用量'}</button>
    </form>
    {error && <div className="inline-error key-query-error"><AlertTriangle size={16} />{error}</div>}
    {!result && !loading && <div className="key-usage-empty"><div><KeyRound size={28} /></div><strong>一次查询，确认额度与调用轨迹</strong><p>适合快速核验用户反馈、定位 Key 是否仍有额度、确认最近模型与请求耗时。</p><ul><li><CheckCircle2 size={14} />实时额度</li><li><CheckCircle2 size={14} />最近调用</li><li><CheckCircle2 size={14} />Token 与耗时</li></ul></div>}
    {result && <>
      <div className="key-result-identity">
        <div className="key-usage-ring" style={{ '--usage-progress': `${Math.min(100, usagePercentage) * 3.6}deg` } as React.CSSProperties}><span><b>{result.usage.unlimited_quota ? '∞' : formatPercent(result.usage.used_percentage)}</b><small>已使用</small></span></div>
        <div className="key-result-title"><span className="eyebrow">VERIFIED TOKEN</span><h3>{result.usage.name}</h3><div><StatusPill tone={expiryTone}>{result.usage.expires_at ? (expiryTone === 'ok' ? `有效至 ${formatTime(result.usage.expires_at, true)}` : '已过期') : '长期有效'}</StatusPill><span>查询于 {formatTime(result.queried_at, true)}</span></div></div>
        <div className="key-model-scope"><span>模型权限</span><strong>{result.usage.model_limits_enabled ? `${Object.keys(result.usage.model_limits).length} 个模型` : '未限制'}</strong><small>{result.usage.model_limits_enabled ? Object.keys(result.usage.model_limits).slice(0, 3).join(' · ') : '跟随账号与分组策略'}</small></div>
      </div>
      <div className="key-usage-metrics">
        <article><span><CircleDollarSign size={16} />已使用额度</span><strong>{formatQuota(result.usage.total_used_display)}</strong><small>原始额度 {formatCompactNumber(result.usage.total_used)}</small></article>
        <article><span><CircleGauge size={16} />可用额度</span><strong>{result.usage.unlimited_quota ? '不限额' : formatQuota(result.usage.total_available_display)}</strong><small>{result.usage.unlimited_quota ? '此 Key 未设置额度上限' : `总授予 ${formatQuota(result.usage.total_granted_display)}`}</small></article>
        <article><span><Activity size={16} />最近调用</span><strong>{formatCompactNumber(result.summary.calls)}</strong><small>{formatCompactNumber(result.summary.total_tokens)} Tokens · {result.summary.models.length} 个模型</small></article>
        <article><span><TimerReset size={16} />P95 总耗时</span><strong>{result.summary.p95_seconds.toFixed(2)}s</strong><small>平均 {result.summary.average_seconds.toFixed(2)}s</small></article>
      </div>
      <div className="key-call-workspace">
        <div className="key-call-list">
          <div className="key-call-toolbar"><div><strong>最近调用详情</strong><span>New API 返回 {result.calls.length} 条 · 当前显示 {calls.length} 条</span></div><label><Search size={15} /><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="模型、渠道、请求 ID" />{filter && <button onClick={() => setFilter('')}><X size={14} /></button>}</label></div>
          <div className="key-call-table-scroll"><table className="key-call-table"><thead><tr><th>时间</th><th>模型 / 渠道</th><th>Tokens</th><th>额度</th><th>耗时</th><th>模式</th><th /></tr></thead><tbody>{calls.map((item) => <tr key={`${item.id}-${item.request_id}`} className={selected?.id === item.id ? 'active' : ''} onClick={() => setSelected(item)}><td className="mono">{formatTime(item.created_at, true)}</td><td><strong>{item.model_name || '未知模型'}</strong><span>{item.channel_name || `渠道 #${item.channel_id}`} · {item.group || 'default'}</span></td><td><b>{formatCompactNumber(item.prompt_tokens + item.completion_tokens)}</b><span>{item.prompt_tokens} + {item.completion_tokens}</span></td><td><b>{formatQuota(item.quota_display)}</b></td><td><b className={item.use_time > SLOW_SECONDS ? 'text-bad' : ''}>{item.use_time.toFixed(2)}s</b><span>首字 {formatDuration(item.frt_ms)}</span></td><td>{item.is_stream ? '流式' : '非流式'}</td><td><ChevronRight size={15} /></td></tr>)}{!calls.length && <tr><td colSpan={7}><div className="empty-row">没有匹配的调用记录</div></td></tr>}</tbody></table></div>
        </div>
        <aside className="key-call-detail">
          {selected ? <><div className="key-detail-head"><span className="key-detail-icon"><TerminalSquare size={19} /></span><div><span>REQUEST INSPECTOR</span><h3>{selected.model_name || '调用详情'}</h3></div><button onClick={() => setSelected(null)}><X size={16} /></button></div><dl><div><dt>请求时间</dt><dd>{formatFullTime(selected.created_at)}</dd></div><div><dt>渠道</dt><dd>{selected.channel_name || `#${selected.channel_id}`}</dd></div><div><dt>总耗时 / 首字</dt><dd>{selected.use_time.toFixed(3)}s / {formatDuration(selected.frt_ms)}</dd></div><div><dt>Token</dt><dd>{selected.prompt_tokens} 输入 + {selected.completion_tokens} 输出</dd></div><div><dt>计费额度</dt><dd>{formatQuota(selected.quota_display)} <small>({formatCompactNumber(selected.quota)})</small></dd></div><div><dt>请求模式</dt><dd>{selected.is_stream ? '流式' : '非流式'} · {selected.group || 'default'}</dd></div></dl><div className="key-request-id"><span>REQUEST ID</span><code>{selected.request_id || '—'}</code><button onClick={() => copyText(selected.request_id)} disabled={!selected.request_id}><Copy size={14} />复制</button></div>{selected.upstream_request_id && <div className="key-request-id"><span>UPSTREAM REQUEST ID</span><code>{selected.upstream_request_id}</code><button onClick={() => copyText(selected.upstream_request_id)}><Copy size={14} />复制</button></div>}{selected.content && <div className="key-call-content"><span>New API 记录</span><p>{selected.content}</p></div>}</> : <div className="key-detail-empty"><TerminalSquare size={26} /><strong>选择一条调用记录</strong><span>查看请求 ID、Token 拆分、计费额度与精确耗时。</span></div>}
        </aside>
      </div>
    </>}
  </section>;
}

type ResourceField = 'system_cpu' | 'system_memory' | 'system_disk';

function MetricChart({ samples, field, color, label, description, threshold, icon }: { samples: ResourceSample[]; field: ResourceField; color: string; label: string; description: string; threshold: number; icon: ReactNode }) {
  const width = 1000;
  const height = 250;
  const paddingY = 14;
  const plotHeight = height - paddingY * 2;
  const gradientId = useId().replace(/:/g, '');
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const values = samples.map((sample) => Math.min(100, Math.max(0, Number(sample[field] ?? 0))));
  const current = values[values.length - 1];
  const previous = values[values.length - 2];
  const average = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  const peak = values.length ? Math.max(...values) : null;
  const low = values.length ? Math.min(...values) : null;
  const selectedIndex = activeIndex ?? Math.max(0, samples.length - 1);
  const selectedSample = samples[selectedIndex];
  const selectedValue = values[selectedIndex];
  const xAt = (index: number) => values.length <= 1 ? 0 : index / (values.length - 1) * width;
  const yAt = (value: number) => paddingY + (100 - value) / 100 * plotHeight;
  const linePath = values.map((value, index) => `${index === 0 ? 'M' : 'L'} ${xAt(index)} ${yAt(value)}`).join(' ');
  const areaPath = linePath ? `${linePath} L ${width} ${height} L 0 ${height} Z` : '';
  const thresholdY = yAt(threshold);
  const delta = current != null && previous != null ? current - previous : null;
  const tone = current == null ? 'muted' : current >= threshold ? 'bad' : current >= threshold * .8 ? 'warn' : 'ok';
  const status = tone === 'bad' ? '超过阈值' : tone === 'warn' ? '接近阈值' : tone === 'ok' ? '运行平稳' : '等待数据';
  const updateSelection = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!samples.length) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
    setActiveIndex(Math.round(ratio * (samples.length - 1)));
  };
  return (
    <article className={`chart-card chart-card-${tone}`} style={{ '--chart-color': color } as React.CSSProperties}>
      <header className="chart-heading">
        <div className="chart-title"><span className="chart-icon">{icon}</span><div><strong>{label}</strong><small>{description}</small></div></div>
        <div className="chart-current"><span className={`chart-state chart-state-${tone}`}>{status}</span><strong>{current == null ? '—' : `${current.toFixed(1)}%`}</strong><small className={delta != null && delta > 0 ? 'trend-up' : 'trend-down'}>{delta == null ? '暂无趋势' : `${delta > 0 ? '↑' : delta < 0 ? '↓' : '→'} ${Math.abs(delta).toFixed(1)}% 较上次`}</small></div>
      </header>
      <div className="chart-kpis"><span>平均 <b>{average == null ? '—' : `${average.toFixed(1)}%`}</b></span><span>峰值 <b>{peak == null ? '—' : `${peak.toFixed(1)}%`}</b></span><span>最低 <b>{low == null ? '—' : `${low.toFixed(1)}%`}</b></span><span>阈值 <b>{threshold}%</b></span></div>
      <div
        className="chart-stage"
        role="group"
        tabIndex={0}
        aria-label={`${label}历史曲线，当前${current == null ? '无数据' : `${current.toFixed(1)}%`}`}
        onPointerMove={updateSelection}
        onPointerLeave={() => setActiveIndex(null)}
        onFocus={() => samples.length && setActiveIndex(samples.length - 1)}
        onBlur={() => setActiveIndex(null)}
        onKeyDown={(event) => {
          if (!samples.length || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
          event.preventDefault();
          const step = event.key === 'ArrowLeft' ? -1 : 1;
          setActiveIndex(Math.min(samples.length - 1, Math.max(0, (activeIndex ?? samples.length - 1) + step)));
        }}
      >
        <div className="chart-y-axis"><span>100</span><span>75</span><span>50</span><span>25</span><span>0</span></div>
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label} 历史曲线`} preserveAspectRatio="none">
          <defs><linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity=".3" /><stop offset="100%" stopColor={color} stopOpacity="0" /></linearGradient></defs>
          {[25, 50, 75, 100].map((level) => <line key={level} x1="0" x2={width} y1={yAt(level)} y2={yAt(level)} className="chart-grid-line" />)}
          <line x1="0" x2={width} y1={thresholdY} y2={thresholdY} className="chart-threshold-line" />
          {areaPath && <path d={areaPath} fill={`url(#${gradientId})`} />}
          {linePath && <path d={linePath} fill="none" stroke={color} strokeWidth="3" vectorEffect="non-scaling-stroke" />}
          {selectedSample && <><line x1={xAt(selectedIndex)} x2={xAt(selectedIndex)} y1="0" y2={height} className="chart-crosshair" /><circle cx={xAt(selectedIndex)} cy={yAt(selectedValue)} r="7" fill={color} className="chart-point" vectorEffect="non-scaling-stroke" /></>}
        </svg>
        {selectedSample && activeIndex != null && <div className="chart-tooltip" style={{ left: `${Math.min(92, Math.max(8, xAt(selectedIndex) / width * 100))}%` }}><time>{formatFullTime(selectedSample.created_at)}</time><strong>{selectedValue.toFixed(1)}%</strong><span>{selectedValue >= threshold ? '已超过告警阈值' : `距阈值 ${(threshold - selectedValue).toFixed(1)}%`}</span></div>}
      </div>
      <div className="chart-axis"><span>{samples.length ? formatTime(samples[0].created_at) : 'PAST'}</span><span>{samples.length > 2 ? formatTime(samples[Math.floor(samples.length / 2)].created_at) : ''}</span><span>{samples.length ? formatTime(samples[samples.length - 1].created_at) : 'NOW'}</span></div>
    </article>
  );
}

function ResourcesView() {
  const [samples, setSamples] = useState<ResourceSample[]>([]);
  const [error, setError] = useState('');
  const [hours, setHours] = useState(24);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await api<{ samples: ResourceSample[] }>(`resources?hours=${hours}`);
      setSamples(payload.samples);
      setError('');
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : '资源加载失败'); }
    finally { setLoading(false); }
  }, [hours]);
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), REFRESH_SECONDS * 1000); return () => window.clearInterval(timer); }, [load]);
  const latest = samples[samples.length - 1];
  const containers = latest?.containers || {};
  const highest = Math.max(latest?.system_cpu || 0, latest?.system_memory || 0, latest?.system_disk || 0);
  const resourceTone = highest >= 85 ? 'bad' : highest >= 68 ? 'warn' : 'ok';
  const resourceLabel = resourceTone === 'bad' ? '资源压力较高' : resourceTone === 'warn' ? '资源需要关注' : '资源运行平稳';
  return (
    <section>
      <div className="section-heading resource-heading"><div><span className="eyebrow">HOST & CONTAINER TELEMETRY</span><h2>机器资源</h2></div><div className="resource-controls"><span className="source-note"><i className={loading ? 'source-pulse source-pulse-loading' : 'source-pulse'} />15 秒采样 · {samples.length} 个数据点</span><div className="segmented range-switch" aria-label="资源趋势时间范围">{[1, 6, 24].map((value) => <button key={value} className={hours === value ? 'active' : ''} onClick={() => setHours(value)}>{value}H</button>)}</div></div></div>
      {error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}
      <div className={`resource-insight resource-insight-${resourceTone}`}><div className="resource-insight-mark"><Activity size={22} /></div><div><span>RESOURCE SIGNAL</span><strong>{resourceLabel}</strong><small>{latest ? `最后采样 ${formatFullTime(latest.created_at)}` : '正在等待第一批资源样本'}</small></div><div className="resource-insight-score"><span>最高负载</span><strong>{latest ? `${highest.toFixed(1)}%` : '—'}</strong></div></div>
      <div className="metrics-grid resource-metrics"><MetricCard icon={<Cpu />} label="CPU" value={formatPercent(latest?.system_cpu)} detail="告警阈值 85%" tone={(latest?.system_cpu || 0) > 85 ? 'bad' : 'neutral'} /><MetricCard icon={<MemoryStick />} label="内存" value={formatPercent(latest?.system_memory)} detail={`可用 ${latest?.system_available_mb ? (latest.system_available_mb / 1024).toFixed(2) : '—'} GB`} tone={(latest?.system_memory || 0) > 85 ? 'bad' : 'neutral'} /><MetricCard icon={<HardDrive />} label="系统盘" value={formatPercent(latest?.system_disk)} detail="告警阈值 80%" tone={(latest?.system_disk || 0) > 80 ? 'bad' : 'neutral'} /><MetricCard icon={<CircleGauge />} label="Swap" value={formatPercent(latest?.system_swap)} detail={`最后采样 ${formatTime(latest?.created_at || 0)}`} /></div>
      <div className="chart-grid"><MetricChart samples={samples} field="system_cpu" color="#39df94" label="CPU 使用率" description="计算负载与调度压力" threshold={85} icon={<Cpu size={18} />} /><MetricChart samples={samples} field="system_memory" color="#78a8ff" label="内存使用率" description="物理内存实时占用" threshold={85} icon={<MemoryStick size={18} />} /><MetricChart samples={samples} field="system_disk" color="#ffad32" label="系统盘使用率" description="根分区存储容量" threshold={80} icon={<HardDrive size={18} />} /></div>
      <div className="section-heading compact"><div><span className="eyebrow">DOCKER RUNTIME</span><h2>容器状态</h2></div></div>
      <div className="container-grid">{Object.entries(containers).map(([name, metric]) => <ContainerCard key={name} name={name} metric={metric} />)}{!Object.keys(containers).length && <div className="empty-state"><Server size={26} /><strong>暂无容器数据</strong><span>检查 Docker 只读代理连接。</span></div>}</div>
    </section>
  );
}

function ContainerCard({ name, metric }: { name: string; metric: ContainerMetric }) {
  const healthy = metric.status === 'running' && !metric.oom_killed;
  return <article className="container-card"><div className="container-head"><div><Server size={18} /><strong>{name}</strong></div><StatusPill tone={healthy ? 'ok' : 'bad'}>{healthy ? '运行中' : metric.status}</StatusPill></div><div className="container-stats"><span>CPU <b>{metric.cpu.toFixed(1)}%</b></span><span>MEM <b>{metric.memory_mb.toFixed(0)} MB</b></span><span>重启 <b>{metric.restarts}</b></span></div>{metric.error && <p>{metric.error}</p>}</article>;
}

const INCIDENT_CATEGORIES: Record<string, string> = {
  all: '全部类型',
  channel: '渠道健康',
  latency: '请求耗时',
  resource: '机器资源',
  container: '容器状态',
  service: '服务可用性',
  collector: '采集器',
  other: '其他',
};

const EMPTY_INCIDENT_SUMMARY: IncidentSummary = {
  open: 0,
  critical_open: 0,
  warning_open: 0,
  resolved: 0,
  resolved_24h: 0,
  average_resolution_seconds: 0,
};

function IncidentsView() {
  const [items, setItems] = useState<Incident[]>([]);
  const [summary, setSummary] = useState<IncidentSummary>(EMPTY_INCIDENT_SUMMARY);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<'all' | 'open' | 'resolved'>('all');
  const [severity, setSeverity] = useState('all');
  const [category, setCategory] = useState('all');
  const [windowHours, setWindowHours] = useState(168);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [generatedAt, setGeneratedAt] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pageSize = 50;

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => { setPage(0); }, [status, severity, category, windowHours, query]);

  const load = useCallback(async () => {
    setLoading(true);
    const parameters = new URLSearchParams({
      status,
      severity,
      category,
      window_hours: String(windowHours),
      limit: String(pageSize),
      offset: String(page * pageSize),
    });
    if (query) parameters.set('q', query);
    try {
      const payload = await api<IncidentPayload>(`incidents?${parameters.toString()}`);
      setItems(payload.items);
      setSummary(payload.summary);
      setTotal(payload.total);
      setGeneratedAt(payload.generated_at);
      setSelectedId((current) => payload.items.some((item) => item.id === current) ? current : payload.items[0]?.id ?? null);
      setError('');
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '事件加载失败');
    } finally { setLoading(false); }
  }, [category, page, query, severity, status, windowHours]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const selected = items.find((item) => item.id === selectedId) || null;
  const clearFilters = () => {
    setStatus('all');
    setSeverity('all');
    setCategory('all');
    setWindowHours(168);
    setSearch('');
    setQuery('');
  };

  return (
    <section className="incidents-view">
      <div className="section-heading incident-heading">
        <div><span className="eyebrow">INCIDENT OPERATIONS</span><h2>事件调查中心</h2><p>从告警信号定位触发原因，并完整追踪恢复过程。</p></div>
        <div className="incident-sync"><span><i className={loading ? 'source-pulse source-pulse-loading' : 'source-pulse'} />30 秒自动刷新</span><small>数据时间 {formatFullTime(generatedAt)}</small><button className="secondary-button" onClick={() => void load()} disabled={loading}><RefreshCw size={14} className={loading ? 'spin' : ''} />立即刷新</button></div>
      </div>
      {error && <div className="inline-error"><AlertTriangle size={16} />{error}<button onClick={() => void load()}>重试</button></div>}

      <div className="incident-kpis">
        <button className="incident-kpi incident-kpi-danger" onClick={() => { setStatus('open'); setSeverity('critical'); }}><span><BellRing size={17} />未恢复严重事件</span><strong>{summary.critical_open}</strong><small>需要优先处置</small></button>
        <button className="incident-kpi incident-kpi-warning" onClick={() => { setStatus('open'); setSeverity('warning'); }}><span><AlertTriangle size={17} />未恢复警告</span><strong>{summary.warning_open}</strong><small>共 {summary.open} 个活跃事件</small></button>
        <button className="incident-kpi incident-kpi-ok" onClick={() => { setStatus('resolved'); setSeverity('all'); }}><span><CheckCircle2 size={17} />24H 已恢复</span><strong>{summary.resolved_24h}</strong><small>筛选范围内共 {summary.resolved}</small></button>
        <div className="incident-kpi"><span><TimerReset size={17} />平均恢复时间</span><strong>{formatElapsed(summary.average_resolution_seconds)}</strong><small>基于已恢复事件</small></div>
      </div>

      <div className="incident-command-bar">
        <label className="incident-search"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索标题、原因、恢复信息或事件标识" aria-label="搜索事件" />{search && <button onClick={() => setSearch('')} title="清空搜索"><X size={14} /></button>}</label>
        <div className="segmented incident-status-filter" aria-label="事件状态">{([['all', '全部'], ['open', '未恢复'], ['resolved', '已恢复']] as const).map(([value, label]) => <button key={value} className={status === value ? 'active' : ''} onClick={() => setStatus(value)}>{label}</button>)}</div>
        <label><span>级别</span><select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="all">全部级别</option><option value="critical">严重</option><option value="warning">警告</option><option value="info">信息</option></select></label>
        <label><span>类型</span><select value={category} onChange={(event) => setCategory(event.target.value)}>{Object.entries(INCIDENT_CATEGORIES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><span>时间范围</span><select value={windowHours} onChange={(event) => setWindowHours(Number(event.target.value))}><option value={24}>最近 24 小时</option><option value={168}>最近 7 天</option><option value={720}>最近 30 天</option><option value={0}>全部历史</option></select></label>
        <button className="incident-clear" onClick={clearFilters}><X size={14} />重置</button>
      </div>

      <div className="incident-workspace">
        <div className="incident-list-panel">
          <div className="incident-panel-head"><div><span>事件队列</span><strong>{total} 个匹配结果</strong></div>{loading && <RefreshCw size={14} className="spin" />}</div>
          <div className="incident-queue">
            {items.map((item) => (
              <button className={classNames('incident-row', `incident-row-${item.severity}`, selectedId === item.id && 'active')} key={item.id} onClick={() => setSelectedId(item.id)}>
                <span className="incident-row-icon">{item.status === 'resolved' ? <CheckCircle2 size={17} /> : item.severity === 'critical' ? <XCircle size={17} /> : <AlertTriangle size={17} />}</span>
                <span className="incident-row-main"><span className="incident-row-top"><b>{item.title}</b><em>{item.status === 'resolved' ? '已恢复' : '未恢复'}</em></span><small>{INCIDENT_CATEGORIES[item.category] || '其他'} · {formatTime(item.started_at, true)}</small><span className="incident-row-snippet">{item.body || (item.legacy_cause_missing ? '历史事件未保留原始触发原因' : item.resolution_body || '暂无诊断详情')}</span></span>
                <ChevronRight size={15} className="incident-row-arrow" />
              </button>
            ))}
            {!items.length && <div className="incident-empty"><Inbox size={28} /><strong>没有匹配的事件</strong><span>尝试放宽时间范围或重置筛选条件。</span><button onClick={clearFilters}>重置筛选</button></div>}
          </div>
          {total > pageSize && <div className="incident-pagination"><button disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>上一页</button><span>{page + 1} / {Math.ceil(total / pageSize)}</span><button disabled={(page + 1) * pageSize >= total} onClick={() => setPage((value) => value + 1)}>下一页</button></div>}
        </div>

        <div className="incident-detail-panel">
          {selected ? <>
            <div className="incident-detail-head">
              <div className={`incident-detail-mark incident-detail-${selected.severity}`}>{selected.status === 'resolved' ? <CheckCircle2 /> : selected.severity === 'critical' ? <XCircle /> : <AlertTriangle />}</div>
              <div><div className="incident-detail-tags"><span>{INCIDENT_CATEGORIES[selected.category] || '其他'}</span><span className={`severity-${selected.severity}`}>{selected.severity === 'critical' ? '严重' : selected.severity === 'warning' ? '警告' : '信息'}</span><span className={selected.status === 'resolved' ? 'resolved' : 'open'}>{selected.status === 'resolved' ? '已恢复' : '处理中'}</span></div><h3>{selected.title}</h3><p>{selected.status === 'resolved' ? `事件持续 ${formatElapsed(selected.duration_seconds)}，当前已恢复。` : `事件已持续 ${formatElapsed(selected.duration_seconds)}，等待指标恢复到安全范围。`}</p></div>
            </div>

            <div className="incident-timeline" aria-label="事件时间线">
              <div className="complete"><span><CircleDot size={15} /></span><div><strong>事件触发</strong><small>{formatFullTime(selected.started_at)}</small></div></div>
              <div className="complete"><span><BellRing size={15} /></span><div><strong>最后一次告警通知</strong><small>{formatFullTime(selected.last_notified_at)}</small></div></div>
              <div className={selected.status === 'resolved' ? 'complete' : ''}><span><CheckCircle2 size={15} /></span><div><strong>{selected.status === 'resolved' ? '指标恢复' : '等待恢复'}</strong><small>{selected.resolved_at ? formatFullTime(selected.resolved_at) : `最后更新 ${formatFullTime(selected.updated_at)}`}</small></div></div>
            </div>

            <div className="incident-explanation-grid">
              <article className="incident-explanation cause"><div><AlertTriangle size={17} /><span>为什么发生</span></div>{selected.legacy_cause_missing ? <p className="incident-legacy-note">该事件由旧版本记录，恢复时曾覆盖原始告警内容，因此无法可靠还原触发原因。新事件已完整保留告警与恢复上下文。</p> : <pre>{selected.body || '事件源未提供额外诊断内容，请结合事件标识和对应监控指标排查。'}</pre>}</article>
              <article className="incident-explanation recovery"><div><CheckCircle2 size={17} /><span>为什么恢复</span></div>{selected.status === 'resolved' ? <pre>{selected.resolution_body || '监控指标已重新满足健康条件，但事件源未提供详细恢复说明。'}</pre> : <p>尚未恢复。系统会持续采样；当指标回到恢复阈值并通过状态判定后，会在这里记录恢复依据和时间。</p>}</article>
            </div>

            <div className="incident-technical"><div className="incident-technical-head"><span>技术上下文</span><small>用于日志检索与二次排查</small></div><dl><div><dt>事件标识</dt><dd>{selected.incident_key}</dd></div><div><dt>事件类型</dt><dd>{selected.kind}</dd></div><div><dt>事件编号</dt><dd>#{selected.id}</dd></div><div><dt>最后更新</dt><dd>{formatFullTime(selected.updated_at)}</dd></div></dl></div>
          </> : <div className="incident-detail-empty"><CircleDot size={32} /><strong>选择一个事件开始调查</strong><span>右侧将展示触发原因、恢复依据与完整时间线。</span></div>}
        </div>
      </div>
    </section>
  );
}

export default function App() {
  const [authState, setAuthState] = useState<'loading' | 'guest' | 'ready'>('loading');
  const [user, setUser] = useState<AuthUser | null>(null);
  const [route, setRoute] = useState<AppRoute>(() => readRoute());
  const [summary, setSummary] = useState<Summary | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [countdown, setCountdown] = useState(REFRESH_SECONDS);
  const refreshSeconds = Math.max(2, user?.dashboard_refresh_seconds || REFRESH_SECONDS);
  const tab: Tab = route.tab;

  const navigate = useCallback((nextRoute: AppRoute, replace = false) => {
    const path = routePath(nextRoute);
    if (replace) window.history.replaceState(null, '', path);
    else window.history.pushState(null, '', path);
    setRoute(nextRoute);
    window.scrollTo({ top: 0, behavior: 'auto' });
  }, []);

  useEffect(() => { api<AuthUser>('auth/me').then((result) => { setUser(result); setCountdown(result.dashboard_refresh_seconds || REFRESH_SECONDS); setAuthState('ready'); }).catch(() => setAuthState('guest')); }, []);
  useEffect(() => {
    const onPopState = () => setRoute(readRoute());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const loadCore = useCallback(async () => {
    setRefreshing(true);
    try {
      const [summaryPayload, channelPayload] = await Promise.all([api<Summary>('dashboard/summary'), api<{ items: Channel[] }>('channels')]);
      setSummary(summaryPayload);
      const enabledChannels = channelPayload.items.filter((channel) => channel.enabled);
      setChannels(enabledChannels);
      setSelectedChannel((current) => current
        ? enabledChannels.find((channel) => channel.channel_id === current.channel_id) || null
        : null);
      setError('');
      setCountdown(refreshSeconds);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 401) setAuthState('guest');
      else setError(requestError instanceof Error ? requestError.message : '监控数据加载失败');
    } finally { setRefreshing(false); }
  }, [refreshSeconds]);

  useEffect(() => { if (authState !== 'ready') return; void loadCore(); const timer = window.setInterval(() => void loadCore(), refreshSeconds * 1000); return () => window.clearInterval(timer); }, [authState, loadCore, refreshSeconds]);
  useEffect(() => { if (authState !== 'ready') return; const timer = window.setInterval(() => setCountdown((value) => value <= 1 ? refreshSeconds : value - 1), 1000); return () => window.clearInterval(timer); }, [authState, refreshSeconds]);
  useEffect(() => {
    if (!user) return;
    const elevated = user.role === 'operator' || user.role === 'admin';
    const allowed = tab === 'overview'
      || (tab === 'keyUsage' && user.key_usage_available)
      || (elevated && ['logs', 'resources', 'incidents', 'channels'].includes(tab))
      || (tab === 'settings' && user.role === 'admin');
    if (!allowed) navigate({ tab: 'overview', settingsPage: 'status' }, true);
  }, [navigate, tab, user]);

  const overall = useMemo(() => {
    if (!summary) return { tone: 'muted' as const, label: '正在同步' };
    if (summary.channels.failed || summary.incidents.critical) return { tone: 'bad' as const, label: '存在异常' };
    if (summary.channels.unknown || summary.requests.slow) return { tone: 'warn' as const, label: '需要关注' };
    return { tone: 'ok' as const, label: '运行正常' };
  }, [summary]);

  async function logout() { await api('auth/logout', { method: 'POST' }).catch(() => undefined); setAuthState('guest'); setUser(null); setSummary(null); }
  if (authState === 'loading') return <div className="boot-screen"><Activity className="spin" /><span>正在建立安全会话</span></div>;
  if (authState === 'guest') return <Login onSuccess={(name) => { api<AuthUser>('auth/me').then((authenticatedUser) => setUser(authenticatedUser)).catch(() => setUser({ authenticated: true, username: name, role: 'admin', source: 'emergency', key_usage_available: true })); setAuthState('ready'); }} />;

  const elevated = user?.role === 'operator' || user?.role === 'admin';
  const navItems = [
    ['overview', '总览', BarChart3],
    ...(user?.key_usage_available ? [['keyUsage', 'Key 查询', KeyRound] as const] : []),
    ...(elevated ? [
      ['logs', '使用日志', Clock3] as const,
      ['resources', '机器资源', Cpu] as const,
      ['incidents', '事件', AlertTriangle] as const,
      ['channels', '渠道配置', SlidersHorizontal] as const,
    ] : []),
    ...(user?.role === 'admin' ? [['settings', '系统配置', Settings] as const] : []),
  ] as const;

  return (
    <div className="app-shell">
      <header className="topbar"><div className="brand"><div className="brand-mark"><Activity size={21} /></div><div><span>NEW API</span><strong>MONITOR</strong></div></div><nav>{navItems.map(([key, label, Icon]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => navigate({ tab: key, settingsPage: key === 'settings' ? route.settingsPage : 'status' })}><Icon size={16} />{label}</button>)}</nav><div className="top-actions"><div className="refresh-state"><RefreshCw className={refreshing ? 'spin' : ''} size={14} /><span>{countdown}s</span></div><span className="user-chip">{user?.display_name || user?.username}<small>{user?.role}</small></span><button className="icon-button" onClick={() => void logout()} title="退出登录"><LogOut size={17} /></button></div></header>
      <main className="content"><section className="hero"><div><div className="eyebrow">OPERATIONS / REAL-TIME</div><h1>服务运行态势</h1><p>真实渠道探测、真实消费日志、主机与容器资源。</p></div><div className={`overall-status overall-${overall.tone}`}><span className="status-beacon" /><div><small>OVERALL STATUS</small><strong>{overall.label}</strong></div><span>{summary ? formatTime(summary.generated_at) : '同步中'}</span></div></section>
        {error && <div className="inline-error"><AlertTriangle size={16} />{error}<button onClick={() => void loadCore()}>重试</button></div>}
        {summary ? <>{tab === 'overview' && <Overview summary={summary} channels={channels} onChannel={setSelectedChannel} />}{tab === 'keyUsage' && user?.key_usage_available && <KeyUsageView />}{tab === 'logs' && elevated && <LogsView channels={channels} />}{tab === 'resources' && elevated && <ResourcesView />}{tab === 'incidents' && elevated && <IncidentsView />}{tab === 'channels' && elevated && <ChannelSettingsView />}{tab === 'settings' && user?.role === 'admin' && <SettingsView activePage={route.settingsPage} onActivePageChange={(settingsPage) => navigate({ tab: 'settings', settingsPage })} />}</> : <div className="loading-panel"><RefreshCw className="spin" /><span>正在读取第一批监控数据</span></div>}
      </main>
      <footer><span>数据源：New API 管理接口 / 真实 Relay 请求 / Linux & Docker</span><span>告警阈值：总耗时或首字 &gt; 60s，3/5 或 5/10 触发</span></footer>
      {selectedChannel && <DetailDrawer channel={selectedChannel} onClose={() => setSelectedChannel(null)} />}
    </div>
  );
}
