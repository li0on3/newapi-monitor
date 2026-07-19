import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  CircleGauge,
  Clock3,
  Cpu,
  Database,
  Eye,
  EyeOff,
  HardDrive,
  KeyRound,
  LogOut,
  MemoryStick,
  Network,
  RefreshCw,
  Save,
  Search,
  Server,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  TerminalSquare,
  UserCog,
  X,
  XCircle,
} from 'lucide-react';
import { FormEvent, PointerEvent as ReactPointerEvent, ReactNode, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, ApiError } from './api';
import type { AuthUser, Channel, ChannelMonitorConfig, ContainerMetric, Incident, LogItem, ResourceSample, Summary, SystemHealth } from './types';

type Tab = 'overview' | 'logs' | 'resources' | 'incidents' | 'channels' | 'settings';

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

function formatPercent(value: number | null | undefined): string {
  return value == null ? '—' : `${value.toFixed(1)}%`;
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
  return <label className="toggle-row"><span>{label}</span><button type="button" className={classNames('switch', checked && 'switch-on')} role="switch" aria-checked={checked} onClick={() => onChange(!checked)}><i /></button></label>;
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
      ? { ...item, monitor_config: { ...item.monitor_config, [key]: value, ...(key === 'probe_format' ? { probe_path: value === 'anthropic' ? '/v1/messages' : value === 'chat' ? '/v1/chat/completions' : '/v1/responses' } : {}) }, display_enabled: key === 'display_enabled' ? Boolean(value) : item.display_enabled, name: key === 'display_name' ? String(value || item.source_name || item.name) : item.name }
      : item));
  };
  const save = async (channel: Channel) => {
    setSaving(channel.channel_id);
    try {
      await api(`channel-settings/${channel.channel_id}`, { method: 'PUT', body: JSON.stringify(channel.monitor_config || {}) });
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
        <div className="config-toggle-grid"><Toggle checked={config.display_enabled ?? true} onChange={(value) => edit(channel.channel_id, 'display_enabled', value)} label="监控页展示" /><Toggle checked={config.probe_enabled ?? false} onChange={(value) => edit(channel.channel_id, 'probe_enabled', value)} label="使用真实请求探测" /><Toggle checked={config.alert_enabled ?? true} onChange={(value) => edit(channel.channel_id, 'alert_enabled', value)} label="渠道告警" /><Toggle checked={config.maintenance_mode ?? false} onChange={(value) => edit(channel.channel_id, 'maintenance_mode', value)} label="维护模式" /></div>
        <button className="primary-button compact-button" disabled={saving === channel.channel_id} onClick={() => void save(channel)}>{saving === channel.channel_id ? <RefreshCw className="spin" size={15} /> : <Save size={15} />}保存渠道配置</button>
      </article>;
    })}</div>
  </section>;
}

type SettingField = { key: string; label: string; type?: 'number' | 'text' | 'password' | 'boolean' | 'select'; options?: Array<[string, string]>; hint?: string };
type SettingSectionId = 'connection' | 'collection' | 'thresholds' | 'mail' | 'advanced';
type SettingsPageId = SettingSectionId | 'status' | 'access' | 'audit';
const SETTING_SECTIONS: Array<{ id: SettingSectionId; title: string; short: string; description: string; icon: ReactNode; fields: SettingField[] }> = [
  { id: 'connection', title: 'New API 连接', short: '连接与凭据', icon: <Network size={18} />, description: '管理接口只读同步与真实探测凭据。敏感字段不会回显。', fields: [
    { key: 'new_api_base_url', label: 'New API 地址' }, { key: 'new_api_user_id', label: '管理用户 ID', type: 'number' }, { key: 'new_api_access_token', label: '管理访问令牌', type: 'password', hint: '留空保持原值' }, { key: 'relay_api_token', label: '真实探测令牌', type: 'password', hint: '留空保持原值' },
  ] },
  { id: 'collection', title: '采集频率', short: '同步与采样', icon: <RefreshCw size={18} />, description: '保存后监控工作线程将在数秒内热加载。', fields: [
    { key: 'dashboard_refresh_seconds', label: '页面刷新（秒）', type: 'number' }, { key: 'channel_sync_interval_seconds', label: '渠道同步（秒）', type: 'number' }, { key: 'channel_interval_seconds', label: '渠道探测（秒）', type: 'number' }, { key: 'log_interval_seconds', label: '日志同步（秒）', type: 'number' }, { key: 'resource_interval_seconds', label: '资源采样（秒）', type: 'number' }, { key: 'report_interval_seconds', label: '周期报告（秒）', type: 'number' }, { key: 'retention_days', label: '数据保留（天）', type: 'number' },
  ] },
  { id: 'thresholds', title: '耗时与资源阈值', short: '告警策略', icon: <CircleGauge size={18} />, description: '总耗时或首字超过慢请求阈值，并满足 3/5 或 5/10 时告警。', fields: [
    { key: 'slow_request_seconds', label: '慢请求阈值（秒）', type: 'number' }, { key: 'latency_hard_limit_seconds', label: '单次严重阈值（秒）', type: 'number' }, { key: 'latency_reminder_seconds', label: '重复提醒间隔（秒）', type: 'number' }, { key: 'channel_slow_seconds', label: '渠道慢探测（秒）', type: 'number' }, { key: 'resource_sustain_seconds', label: '资源持续时间（秒）', type: 'number' }, { key: 'system_cpu_threshold', label: 'CPU 阈值（%）', type: 'number' }, { key: 'system_memory_threshold', label: '内存阈值（%）', type: 'number' }, { key: 'system_disk_threshold', label: '磁盘阈值（%）', type: 'number' },
  ] },
  { id: 'mail', title: '邮件通知', short: 'SMTP 与通知', icon: <ShieldCheck size={18} />, description: 'SMTP 密码只写存储，配置后可由监控工作线程直接使用。', fields: [
    { key: 'smtp_host', label: 'SMTP 地址' }, { key: 'smtp_port', label: 'SMTP 端口', type: 'number' }, { key: 'smtp_user', label: 'SMTP 用户' }, { key: 'smtp_password', label: 'SMTP 密码', type: 'password', hint: '留空保持原值' }, { key: 'smtp_from', label: '发件人' }, { key: 'smtp_to', label: '收件人（逗号分隔）' }, { key: 'smtp_starttls', label: 'STARTTLS', type: 'boolean' }, { key: 'smtp_ssl', label: 'SSL', type: 'boolean' }, { key: 'send_startup_email', label: '启动通知', type: 'boolean' }, { key: 'subject_prefix', label: '邮件标题前缀' },
  ] },
  { id: 'advanced', title: '高级采集', short: '范围与排除', icon: <SlidersHorizontal size={18} />, description: '日志重叠窗口、容器范围及排除项。', fields: [
    { key: 'log_overlap_seconds', label: '日志重叠窗口（秒）', type: 'number' }, { key: 'log_initial_lookback_seconds', label: '首次回溯（秒）', type: 'number' }, { key: 'docker_container_names', label: '容器名称（逗号分隔）' }, { key: 'disk_path', label: '磁盘采集路径' }, { key: 'excluded_token_names', label: '排除令牌名（逗号分隔）' }, { key: 'container_cpu_threshold', label: '容器 CPU 阈值（%）', type: 'number' }, { key: 'container_memory_threshold', label: '容器内存阈值（%）', type: 'number' },
  ] },
];

function SettingsView() {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [baseline, setBaseline] = useState<Record<string, unknown>>({});
  const [audit, setAudit] = useState<Array<Record<string, unknown>>>([]);
  const [users, setUsers] = useState<Array<{ username: string; role: string }>>([]);
  const [systemStatus, setSystemStatus] = useState<SystemHealth | null>(null);
  const [activePage, setActivePage] = useState<SettingsPageId>('status');
  const [newUser, setNewUser] = useState('');
  const [newRole, setNewRole] = useState('viewer');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const load = useCallback(async () => {
    const [settingsPayload, auditPayload, usersPayload, statusPayload] = await Promise.all([api<{ values: Record<string, unknown> }>('settings'), api<{ items: Array<Record<string, unknown>> }>('config-audit?limit=30'), api<{ items: Array<{ username: string; role: string }> }>('access/users'), api<SystemHealth>('system/status')]);
    setValues(settingsPayload.values); setBaseline(settingsPayload.values); setAudit(auditPayload.items); setUsers(usersPayload.items); setSystemStatus(statusPayload);
  }, []);
  useEffect(() => { void load().catch((error) => setMessage(error instanceof Error ? error.message : '配置加载失败')); }, [load]);
  const setValue = (key: string, value: unknown) => setValues((current) => ({ ...current, [key]: value }));
  const save = async () => {
    setSaving(true); setMessage('');
    const payload = { ...values };
    for (const key of ['new_api_access_token', 'relay_api_token', 'smtp_password']) if (payload[key] === '********') payload[key] = '';
    try { await api('settings', { method: 'PUT', body: JSON.stringify(payload) }); setMessage('配置已保存，采集器正在热加载'); await load(); }
    catch (error) { setMessage(error instanceof Error ? error.message : '保存失败'); }
    finally { setSaving(false); }
  };
  const addUser = async () => { if (!newUser.trim()) return; await api(`access/users/${encodeURIComponent(newUser.trim())}`, { method: 'PUT', body: JSON.stringify({ role: newRole }) }); setNewUser(''); await load(); };
  const activeSection = SETTING_SECTIONS.find((section) => section.id === activePage);
  const dirty = JSON.stringify(values) !== JSON.stringify(baseline);
  const pages: Array<{ id: SettingsPageId; title: string; description: string; icon: ReactNode; count?: number }> = [
    { id: 'status', title: '运行状态', description: '采集链路自检', icon: <ShieldCheck size={18} />, count: systemStatus ? Object.keys(systemStatus.collectors).length : 0 },
    ...SETTING_SECTIONS.map((section) => ({ id: section.id, title: section.title, description: section.short, icon: section.icon, count: section.fields.length })),
    { id: 'access', title: '角色映射', description: '访问与权限', icon: <UserCog size={18} />, count: users.length },
    { id: 'audit', title: '配置审计', description: '最近变更记录', icon: <Clock3 size={18} />, count: audit.length },
  ];
  return <section>
    <div className="section-heading settings-heading"><div><span className="eyebrow">RUNTIME CONTROL CENTER</span><h2>系统配置</h2><p>按任务分区管理，只展示当前配置组；配置保存在监控数据库中，不写入 New API。</p></div><div className={classNames('settings-dirty-state', dirty && 'settings-dirty')}><i />{dirty ? '有未保存更改' : '配置已同步'}</div></div>
    {message && <div className="config-message">{message}</div>}
    <div className="settings-workspace">
      <aside className="settings-nav" aria-label="系统配置分类">{pages.map((page) => <button type="button" className={classNames(activePage === page.id && 'active')} key={page.id} onClick={() => setActivePage(page.id)}><span className="settings-nav-icon">{page.icon}</span><span><strong>{page.title}</strong><small>{page.description}</small></span>{page.count != null && <b>{page.count}</b>}<ChevronRight size={15} /></button>)}</aside>
      <div className="settings-stage">
        {activePage === 'status' && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><ShieldCheck size={18} /></div><div><span className="eyebrow">SELF MONITORING</span><h3>采集链路状态</h3><p>监控程序同时检查自身是否仍在持续产生新数据，避免“页面正常但采集已经停止”。</p></div><StatusPill tone={systemStatus?.status === 'ok' ? 'ok' : 'bad'}>{systemStatus?.status === 'ok' ? '全部正常' : '存在降级'}</StatusPill></div><div className="collector-health-grid"><div className="collector-health-card"><span>数据库</span><strong>{systemStatus?.database === 'ok' ? '正常' : '异常'}</strong><small>{systemStatus?.database_error || 'SQLite 可读写'}</small></div><div className="collector-health-card"><span>监控进程</span><strong>{systemStatus?.monitor_worker === 'running' ? '运行中' : systemStatus?.monitor_worker || '未知'}</strong><small>{systemStatus?.monitor_error || '工作线程持续运行'}</small></div>{Object.entries(systemStatus?.collectors || {}).map(([name, collector]) => { const labels: Record<string, string> = { channel_sync: '渠道同步', channel_probe: '渠道探测', logs: '使用日志', resources: '机器资源' }; return <div className={classNames('collector-health-card', collector.status === 'stale' && 'collector-health-stale')} key={name}><span>{labels[name] || name}</span><strong>{collector.status === 'ok' ? '正常' : collector.status === 'starting' ? '启动中' : '数据过期'}</strong><small>最后成功 {collector.age_seconds}s 前 · 阈值 {collector.stale_after_seconds}s</small>{collector.consecutive_failures > 0 && <em>连续失败 {collector.consecutive_failures} 次</em>}{collector.last_error && <code title={collector.last_error}>{collector.last_error}</code>}</div>; })}</div><div className="settings-action-bar"><div><strong>最后检查 {systemStatus ? formatFullTime(systemStatus.timestamp) : '—'}</strong><small>数据超过动态失效阈值后，健康检查变为 503，并生成异常与恢复事件。</small></div><button className="secondary-button" onClick={() => void load()}><RefreshCw size={16} />立即刷新</button></div></article>}
        {activeSection && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark">{activeSection.icon}</div><div><span className="eyebrow">CONFIGURATION GROUP</span><h3>{activeSection.title}</h3><p>{activeSection.description}</p></div><span className="settings-field-count">{activeSection.fields.length} 项</span></div><div className="settings-fields">{activeSection.fields.map((field) => field.type === 'boolean' ? <Toggle key={field.key} label={field.label} checked={Boolean(values[field.key])} onChange={(value) => setValue(field.key, value)} /> : <label key={field.key}><span>{field.label}</span><input type={field.type === 'password' ? 'password' : field.type === 'number' ? 'number' : 'text'} value={String(values[field.key] ?? '')} placeholder={field.hint} onChange={(event) => setValue(field.key, field.type === 'number' ? Number(event.target.value) : event.target.value)} /><small>{field.hint}</small></label>)}</div><div className="settings-action-bar"><div><strong>{dirty ? '更改尚未应用' : '当前配置已生效'}</strong><small>{dirty ? '保存后采集器将在数秒内热加载，无需重启。' : '你可以切换左侧分类继续检查其他配置。'}</small></div><button className="secondary-button" disabled={!dirty || saving} onClick={() => { setValues(baseline); setMessage('已撤销本次未保存更改'); }}>撤销更改</button><button className="primary-button settings-save" disabled={!dirty || saving} onClick={() => void save()}>{saving ? <RefreshCw className="spin" size={16} /> : <Save size={16} />}保存并应用</button></div></article>}
        {activePage === 'access' && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><UserCog size={18} /></div><div><span className="eyebrow">ACCESS CONTROL</span><h3>角色映射</h3><p>Root 自动为管理员，Admin 自动为运维员；这里可以对指定用户覆盖。</p></div></div><div className="user-add user-add-wide"><input placeholder="New API 用户名" value={newUser} onChange={(event) => setNewUser(event.target.value)} /><select value={newRole} onChange={(event) => setNewRole(event.target.value)}><option value="viewer">只读</option><option value="operator">运维</option><option value="admin">管理员</option></select><button onClick={() => void addUser()}><UserCog size={15} />添加映射</button></div><div className="role-list">{users.map((user) => <div key={user.username}><strong>{user.username}</strong><span>{user.role}</span><button onClick={async () => { await api(`access/users/${encodeURIComponent(user.username)}`, { method: 'PUT', body: JSON.stringify({ role: null }) }); await load(); }}><X size={14} /></button></div>)}{!users.length && <p>暂无用户覆盖规则</p>}</div></article>}
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

function IncidentsView() {
  const [items, setItems] = useState<Incident[]>([]);
  const [status, setStatus] = useState('all');
  const [error, setError] = useState('');
  const load = useCallback(async () => { try { const payload = await api<{ items: Incident[] }>(`incidents?status=${status}`); setItems(payload.items); setError(''); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : '事件加载失败'); } }, [status]);
  useEffect(() => { void load(); }, [load]);
  return <section><div className="section-heading"><div><span className="eyebrow">ALERT TIMELINE</span><h2>告警与恢复事件</h2></div><div className="segmented"><button className={status === 'all' ? 'active' : ''} onClick={() => setStatus('all')}>全部</button><button className={status === 'open' ? 'active' : ''} onClick={() => setStatus('open')}>未恢复</button><button className={status === 'resolved' ? 'active' : ''} onClick={() => setStatus('resolved')}>已恢复</button></div></div>{error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}<div className="incident-list">{items.map((item) => <article className={`incident-card incident-${item.severity}`} key={item.id}><div className="incident-icon">{item.status === 'resolved' ? <CheckCircle2 /> : item.severity === 'critical' ? <XCircle /> : <AlertTriangle />}</div><div className="incident-main"><div className="incident-title"><h3>{item.title}</h3><StatusPill tone={item.status === 'resolved' ? 'ok' : item.severity === 'critical' ? 'bad' : 'warn'}>{item.status === 'resolved' ? '已恢复' : '处理中'}</StatusPill></div><pre>{item.body}</pre><div className="incident-time"><span>开始 {formatTime(item.started_at, true)}</span><span>更新 {formatTime(item.updated_at, true)}</span>{item.resolved_at && <span>恢复 {formatTime(item.resolved_at, true)}</span>}</div></div></article>)}{!items.length && <div className="empty-state"><CheckCircle2 size={28} /><strong>没有匹配的事件</strong><span>渠道、耗时和资源告警将在这里形成完整时间线。</span></div>}</div></section>;
}

export default function App() {
  const [authState, setAuthState] = useState<'loading' | 'guest' | 'ready'>('loading');
  const [user, setUser] = useState<AuthUser | null>(null);
  const [tab, setTab] = useState<Tab>('overview');
  const [summary, setSummary] = useState<Summary | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [countdown, setCountdown] = useState(REFRESH_SECONDS);
  const refreshSeconds = Math.max(2, user?.dashboard_refresh_seconds || REFRESH_SECONDS);

  useEffect(() => { api<AuthUser>('auth/me').then((result) => { setUser(result); setCountdown(result.dashboard_refresh_seconds || REFRESH_SECONDS); setAuthState('ready'); }).catch(() => setAuthState('guest')); }, []);

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

  const overall = useMemo(() => {
    if (!summary) return { tone: 'muted' as const, label: '正在同步' };
    if (summary.channels.failed || summary.incidents.critical) return { tone: 'bad' as const, label: '存在异常' };
    if (summary.channels.unknown || summary.requests.slow) return { tone: 'warn' as const, label: '需要关注' };
    return { tone: 'ok' as const, label: '运行正常' };
  }, [summary]);

  async function logout() { await api('auth/logout', { method: 'POST' }).catch(() => undefined); setAuthState('guest'); setUser(null); setSummary(null); }
  if (authState === 'loading') return <div className="boot-screen"><Activity className="spin" /><span>正在建立安全会话</span></div>;
  if (authState === 'guest') return <Login onSuccess={(name) => { setUser({ authenticated: true, username: name, role: 'admin', source: 'emergency' }); setAuthState('ready'); }} />;

  const navItems = [
    ['overview', '总览', BarChart3], ['logs', '使用日志', Clock3], ['resources', '机器资源', Cpu], ['incidents', '事件', AlertTriangle],
    ...(user?.role === 'operator' || user?.role === 'admin' ? [['channels', '渠道配置', SlidersHorizontal] as const] : []),
    ...(user?.role === 'admin' ? [['settings', '系统配置', Settings] as const] : []),
  ] as const;

  return (
    <div className="app-shell">
      <header className="topbar"><div className="brand"><div className="brand-mark"><Activity size={21} /></div><div><span>NEW API</span><strong>MONITOR</strong></div></div><nav>{navItems.map(([key, label, Icon]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}><Icon size={16} />{label}</button>)}</nav><div className="top-actions"><div className="refresh-state"><RefreshCw className={refreshing ? 'spin' : ''} size={14} /><span>{countdown}s</span></div><span className="user-chip">{user?.display_name || user?.username}<small>{user?.role}</small></span><button className="icon-button" onClick={() => void logout()} title="退出登录"><LogOut size={17} /></button></div></header>
      <main className="content"><section className="hero"><div><div className="eyebrow">OPERATIONS / REAL-TIME</div><h1>服务运行态势</h1><p>真实渠道探测、真实消费日志、主机与容器资源。</p></div><div className={`overall-status overall-${overall.tone}`}><span className="status-beacon" /><div><small>OVERALL STATUS</small><strong>{overall.label}</strong></div><span>{summary ? formatTime(summary.generated_at) : '同步中'}</span></div></section>
        {error && <div className="inline-error"><AlertTriangle size={16} />{error}<button onClick={() => void loadCore()}>重试</button></div>}
        {summary ? <>{tab === 'overview' && <Overview summary={summary} channels={channels} onChannel={setSelectedChannel} />}{tab === 'logs' && <LogsView channels={channels} />}{tab === 'resources' && <ResourcesView />}{tab === 'incidents' && <IncidentsView />}{tab === 'channels' && (user?.role === 'operator' || user?.role === 'admin') && <ChannelSettingsView />}{tab === 'settings' && user?.role === 'admin' && <SettingsView />}</> : <div className="loading-panel"><RefreshCw className="spin" /><span>正在读取第一批监控数据</span></div>}
      </main>
      <footer><span>数据源：New API 管理接口 / 真实 Relay 请求 / Linux & Docker</span><span>告警阈值：总耗时或首字 &gt; 60s，3/5 或 5/10 触发</span></footer>
      {selectedChannel && <DetailDrawer channel={selectedChannel} onClose={() => setSelectedChannel(null)} />}
    </div>
  );
}
