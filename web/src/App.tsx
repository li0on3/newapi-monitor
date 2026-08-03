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
  Cloud,
  Clock3,
  Copy,
  Cpu,
  Database,
  Eye,
  EyeOff,
  ExternalLink,
  HardDrive,
  Inbox,
  Fingerprint,
  KeyRound,
  LayoutDashboard,
  Languages,
  LogOut,
  Mail,
  MemoryStick,
  MessageSquare,
  Network,
  RefreshCw,
  Save,
  Search,
  ScrollText,
  Send,
  Server,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  TimerReset,
  TerminalSquare,
  UserCog,
  Users,
  X,
  XCircle,
} from 'lucide-react';
import { TimeRangeControl } from './TimeRangeControl';
import { appendDateRange, isLiveRange, presetRange, rangeLabel, type TimeRange } from './time-range';
import { FormEvent, PointerEvent as ReactPointerEvent, ReactNode, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, ApiError } from './api';
import { getLanguage, setLanguage, t } from './i18n';
import { buildProviderStatusContext, DEFAULT_OPENAI_COMPONENT_NAMES } from './provider-status';
import { channelHealth, observationHealth, overallHealth } from './monitor-status';
import { canAccessMonitorModules, defaultAuthorizedRoute, enabledConsolePages, readRoute, routePath } from './routes';
import { ThemeSwitch } from './ThemeSwitch';
import type { AppRoute, AppTab, ConsolePage, SettingsPage } from './routes';
import type { AuthUser, Channel, ChannelMonitorConfig, ContainerMetric, Incident, IncidentPayload, IncidentSummary, KeyUsageCall, KeyUsageResult, LogItem, ProviderStatus, ResourceMetricSummary, ResourcePayload, ResourceSample, Summary, SystemHealth } from './types';
import { ConsoleShell } from './console/ConsoleShell';
import { numberText } from './console/utils';
import { DeliveriesView } from './deliveries/DeliveriesView';
import { SECRET_SETTING_KEYS, SETTING_SECTIONS } from './settings/catalog';
import type { SettingSectionId } from './settings/catalog';

type Tab = AppTab;

const REFRESH_SECONDS = 5;

function formatTime(timestamp: number, includeDate = false): string {
  if (!timestamp) return t('暂无');
  return new Intl.DateTimeFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', {
    month: includeDate ? '2-digit' : undefined,
    day: includeDate ? '2-digit' : undefined,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(timestamp * 1000));
}

function formatFullTime(timestamp: number): string {
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

function formatDateTimeLocal(timestamp: number | null | undefined): string {
  if (!timestamp) return '';
  const date = new Date(timestamp * 1000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function parseDateTimeLocal(value: string): number {
  if (!value) return 0;
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) ? Math.floor(timestamp / 1000) : 0;
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

function formatQuota(value: number): string {
  return `$${new Intl.NumberFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', { minimumFractionDigits: value > 0 && value < 0.01 ? 4 : 2, maximumFractionDigits: 6 }).format(value)}`;
}

function classNames(...names: Array<string | false | null | undefined>): string {
  return names.filter(Boolean).join(' ');
}

function LanguageSwitch({ compact = false }: { compact?: boolean }) {
  const language = getLanguage();
  const nextLanguage = language === 'zh-CN' ? 'en' : 'zh-CN';
  const label = language === 'zh-CN' ? 'English' : '中文';
  return (
    <button
      type="button"
      className={classNames('language-switch', compact && 'language-switch-compact')}
      title={language === 'zh-CN' ? 'Switch to English' : '切换到中文'}
      aria-label={language === 'zh-CN' ? 'Switch to English' : '切换到中文'}
      onClick={() => {
        setLanguage(nextLanguage);
        window.location.reload();
      }}
    >
      <Languages size={15} />
      <span>{label}</span>
    </button>
  );
}

function StatusPill({ tone, children }: { tone: 'ok' | 'warn' | 'bad' | 'muted'; children: ReactNode }) {
  return <span className={`status-pill status-${tone}`}>{children}</span>;
}

export function Login({ onSuccess }: { onSuccess: (user: AuthUser) => void }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [ssoSubmitting, setSsoSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await api<{ authenticated: boolean; username: string }>('auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      onSuccess(await api<AuthUser>('auth/me'));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : t('登录失败'));
    } finally {
      setSubmitting(false);
    }
  }

  async function useNewApiSession() {
    setSsoSubmitting(true);
    setError('');
    try {
      await api<{ enabled: boolean }>('auth/sso', { method: 'POST' });
      onSuccess(await api<AuthUser>('auth/me'));
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 401) {
        setError(t('当前浏览器没有有效账号会话，请先登录账户服务后重试。'));
        return;
      }
      setError(requestError instanceof Error ? requestError.message : t('登录失败'));
    } finally {
      setSsoSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="login-utilities"><ThemeSwitch compact /><LanguageSwitch /></div>
        <div className="login-mark"><Activity size={26} /></div>
        <div className="eyebrow">PRIVATE OPERATIONS CONSOLE</div>
        <h1>{t('API 服务中心')}</h1>
        <p>{t('服务状态、用量与访问凭据统一管理。')}</p>
        <button className="sso-button" type="button" onClick={() => void useNewApiSession()} disabled={ssoSubmitting}>
          {ssoSubmitting ? <RefreshCw className="spin" size={17} /> : <ShieldCheck size={17} />}
          {ssoSubmitting ? t('正在验证') : t('账号登录')}
        </button>
        <p className="sso-note">{t('使用已有账号会话安全登录；退出仅结束本平台会话。')}</p>
        <div className="login-divider"><span>{t("紧急管理员登录")}</span></div>
        <form onSubmit={submit}>
          <label>
            <span>{t("账号")}</span>
            <div className="input-wrap"><ShieldCheck size={17} /><input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></div>
          </label>
          <label>
            <span>{t("密码")}</span>
            <div className="input-wrap"><KeyRound size={17} /><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus /></div>
          </label>
          {error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}
          <button className="primary-button" type="submit" disabled={submitting || !username || !password}>
            {submitting ? <RefreshCw className="spin" size={17} /> : <TerminalSquare size={17} />}
            {submitting ? t('正在验证') : t('进入监控台')}
          </button>
        </form>
        <div className="login-foot"><span className="pulse-dot" />{t("当前站点仅限授权账号访问")}</div>
      </section>
    </main>
  );
}

type SetupStatus = { required: boolean; available: boolean; expires_at: number };

function SetupView({ status, onComplete }: { status: SetupStatus; onComplete: () => void }) {
  const [mode, setMode] = useState<'credentials' | 'tokens'>('credentials');
  const [setupToken, setSetupToken] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [username, setUsername] = useState('root');
  const [password, setPassword] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [userId, setUserId] = useState('');
  const [relayToken, setRelayToken] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await api('setup/complete', {
        method: 'POST',
        body: JSON.stringify({
          setup_token: setupToken,
          new_api_base_url: baseUrl,
          ...(mode === 'credentials'
            ? { username, password }
            : { new_api_access_token: accessToken, new_api_user_id: Number(userId), relay_api_token: relayToken }),
        }),
      });
      onComplete();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : t('初始化失败'));
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = status.available && setupToken && baseUrl && (mode === 'credentials'
    ? username && password
    : accessToken && Number(userId) > 0 && relayToken);

  return (
    <main className="login-shell setup-shell">
      <section className="login-panel setup-panel">
        <div className="login-utilities"><ThemeSwitch compact /><LanguageSwitch /></div>
        <div className="setup-heading">
          <div className="login-mark"><Network size={26} /></div>
          <div><div className="eyebrow">SECURE FIRST-RUN SETUP</div><h1>{t('连接 New API')}</h1><p>{t('只需完成一次，配置将加密保存并立即启动监控。')}</p></div>
        </div>
        {!status.available && <div className="setup-expired"><AlertTriangle size={18} /><div><strong>{t('初始化令牌已过期')}</strong><span>{t('请在服务器运行 sudo monitorctl renew-setup 后刷新页面。')}</span></div></div>}
        <form onSubmit={submit}>
          <div className="setup-step"><span>01</span><div><strong>{t('验证安装')}</strong><small>{status.expires_at ? t('令牌有效期至 {{time}}', { time: formatFullTime(status.expires_at) }) : t('使用安装完成时显示的一次性令牌')}</small></div></div>
          <label><span>{t('一次性初始化令牌')}</span><div className="input-wrap"><KeyRound size={17} /><input type="password" autoComplete="off" value={setupToken} onChange={(event) => setSetupToken(event.target.value)} /></div></label>
          <div className="setup-step"><span>02</span><div><strong>{t('连接服务')}</strong><small>{t('凭据仅用于换取所需令牌，不会保存 New API 密码。')}</small></div></div>
          <label><span>{t('New API 地址')}</span><div className="input-wrap"><Server size={17} /><input type="url" placeholder="https://newapi.example.com" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></div></label>
          <div className="setup-mode" role="tablist" aria-label={t('认证方式')}>
            <button type="button" className={mode === 'credentials' ? 'active' : ''} onClick={() => setMode('credentials')}><ShieldCheck size={16} />{t('管理员账号')}</button>
            <button type="button" className={mode === 'tokens' ? 'active' : ''} onClick={() => setMode('tokens')}><KeyRound size={16} />{t('已有令牌')}</button>
          </div>
          {mode === 'credentials' ? <div className="setup-grid">
            <label><span>{t('New API 用户名')}</span><div className="input-wrap"><UserCog size={17} /><input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></div></label>
            <label><span>{t('New API 密码')}</span><div className="input-wrap"><KeyRound size={17} /><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></div></label>
          </div> : <div className="setup-grid">
            <label className="setup-wide"><span>{t('New API 管理令牌')}</span><div className="input-wrap"><KeyRound size={17} /><input type="password" autoComplete="off" value={accessToken} onChange={(event) => setAccessToken(event.target.value)} /></div></label>
            <label><span>{t('New API 用户 ID')}</span><div className="input-wrap"><Fingerprint size={17} /><input type="number" min="1" value={userId} onChange={(event) => setUserId(event.target.value)} /></div></label>
            <label><span>{t('探测 API Key')}</span><div className="input-wrap"><Activity size={17} /><input type="password" autoComplete="off" value={relayToken} onChange={(event) => setRelayToken(event.target.value)} /></div></label>
          </div>}
          {error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}
          <div className="setup-security"><ShieldCheck size={17} /><span>{t('初始化接口受一次性令牌、有效期和速率限制保护；完成后自动关闭。')}</span></div>
          <button className="primary-button" type="submit" disabled={submitting || !canSubmit}>{submitting ? <RefreshCw className="spin" size={17} /> : <CheckCircle2 size={17} />}{submitting ? t('正在初始化') : t('完成初始化并启动')}</button>
        </form>
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
      setError(requestError instanceof Error ? requestError.message : t('渠道配置加载失败'));
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
      setError(requestError instanceof Error ? requestError.message : t('渠道配置保存失败'));
    } finally { setSaving(null); }
  };
  return <section>
    <div className="section-heading"><div><span className="eyebrow">CHANNEL PRESENTATION & PROBES</span><h2>{t("渠道配置")}</h2><p>{t("New API 管理渠道状态，监控平台仅保存展示、探测与告警覆盖项。")}</p></div><button className="secondary-button" onClick={() => void load()}><RefreshCw className={loading ? 'spin' : ''} size={15} />{t("立即同步")}</button></div>
    {error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}
    <div className="config-channel-grid">{items.map((channel) => {
      const config = channel.monitor_config || {};
      return <article className={classNames('config-channel-card', !channel.enabled && 'config-channel-disabled')} key={channel.channel_id}>
        <div className="config-channel-head"><div className="provider-mark">{channel.name.slice(0, 2).toUpperCase()}</div><div><h3>{channel.name}</h3><p>#{channel.channel_id} · {channel.enabled ? t('New API 已启用') : t('New API 已禁用')}</p></div><StatusPill tone={channel.enabled ? 'ok' : 'muted'}>{channel.enabled ? t('同步中') : t('不展示')}</StatusPill></div>
        <div className="config-form-grid">
          <label><span>{t("显示名称")}</span><input value={config.display_name ?? ''} placeholder={channel.source_name || channel.name} onChange={(event) => edit(channel.channel_id, 'display_name', event.target.value)} /></label>
          <label><span>{t("排序权重")}</span><input type="number" value={config.sort_order ?? 0} onChange={(event) => edit(channel.channel_id, 'sort_order', Number(event.target.value))} /></label>
          <label><span>{t("探测模型")}</span><input value={config.probe_model ?? ''} placeholder={channel.models[0] || 'gpt-5.4'} onChange={(event) => edit(channel.channel_id, 'probe_model', event.target.value)} /></label>
          <label><span>{t("请求协议")}</span><select value={config.probe_format ?? 'responses'} onChange={(event) => edit(channel.channel_id, 'probe_format', event.target.value)}><option value="responses">OpenAI Responses</option><option value="chat">OpenAI Chat Completions</option><option value="anthropic">Anthropic Messages</option></select></label>
          <label><span>{t("最大输出 Token")}</span><input type="number" min="1" max="4096" value={config.max_output_tokens ?? 1} onChange={(event) => edit(channel.channel_id, 'max_output_tokens', Number(event.target.value))} /></label>
          <label className="config-wide"><span>{t("探测路径")}</span><input value={config.probe_path ?? ''} placeholder={t("自动选择协议默认路径")} onChange={(event) => edit(channel.channel_id, 'probe_path', event.target.value)} /></label>
          <label className="config-wide"><span>{t("探测提示词")}</span><input value={config.probe_prompt ?? ''} placeholder={t("1（建议使用最小探测内容）")} onChange={(event) => edit(channel.channel_id, 'probe_prompt', event.target.value)} /></label>
        </div>
        <div className="config-visibility-note"><Eye size={15} /><span>{t("总览展示范围由管理员在“系统配置 → 总览展示”中统一管理。")}</span></div>
        <div className="config-toggle-grid"><Toggle checked={config.probe_enabled ?? false} onChange={(value) => edit(channel.channel_id, 'probe_enabled', value)} label={t("使用真实请求探测")} /><Toggle checked={config.alert_enabled ?? true} onChange={(value) => edit(channel.channel_id, 'alert_enabled', value)} label={t("渠道告警")} /><Toggle checked={config.maintenance_mode ?? false} onChange={(value) => edit(channel.channel_id, 'maintenance_mode', value)} label={t("维护模式")} /></div>
        <div className="maintenance-window-card"><div className="maintenance-window-head"><div><Clock3 size={15} /><span><strong>{t('计划维护窗口')}</strong><small>{t('到达结束时间后自动恢复探测与告警。')}</small></span></div><Toggle checked={config.maintenance_window_enabled ?? false} onChange={(value) => edit(channel.channel_id, 'maintenance_window_enabled', value)} label={t('启用计划维护')} /></div><div className="maintenance-window-fields"><label><span>{t('开始时间')}</span><input type="datetime-local" disabled={!config.maintenance_window_enabled} value={formatDateTimeLocal(config.maintenance_window_start)} onChange={(event) => edit(channel.channel_id, 'maintenance_window_start', parseDateTimeLocal(event.target.value))} /></label><label><span>{t('结束时间')}</span><input type="datetime-local" disabled={!config.maintenance_window_enabled} value={formatDateTimeLocal(config.maintenance_window_end)} onChange={(event) => edit(channel.channel_id, 'maintenance_window_end', parseDateTimeLocal(event.target.value))} /></label><label className="config-wide"><span>{t('维护原因')}</span><input disabled={!config.maintenance_window_enabled} value={config.maintenance_window_reason ?? ''} placeholder={t('例如：上游升级或线路切换')} onChange={(event) => edit(channel.channel_id, 'maintenance_window_reason', event.target.value)} /></label></div></div>
        <button className="primary-button compact-button" disabled={saving === channel.channel_id} onClick={() => void save(channel)}>{saving === channel.channel_id ? <RefreshCw className="spin" size={15} /> : <Save size={15} />}{t("保存渠道配置")}</button>
      </article>;
    })}</div>
  </section>;
}

type SettingsPageId = SettingsPage;
type NotificationChannelId = 'email' | 'wecom_app' | 'wecom_webhook' | 'feishu_app' | 'feishu_webhook';

function SettingsView({ activePage, onActivePageChange }: { activePage: SettingsPageId; onActivePageChange: (page: SettingsPageId) => void }) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [baseline, setBaseline] = useState<Record<string, unknown>>({});
  const [audit, setAudit] = useState<Array<Record<string, unknown>>>([]);
  const [users, setUsers] = useState<Array<{ username: string; role: string }>>([]);
  const [systemStatus, setSystemStatus] = useState<SystemHealth | null>(null);
  const [openAIStatus, setOpenAIStatus] = useState<ProviderStatus | null>(null);
  const [testingOpenAIStatus, setTestingOpenAIStatus] = useState(false);
  const [openAIStatusTest, setOpenAIStatusTest] = useState<{ success: boolean; text: string } | null>(null);
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
    const [settingsPayload, auditPayload, usersPayload, statusPayload, channelPayload, providerPayload] = await Promise.all([api<{ values: Record<string, unknown> }>('settings'), api<{ items: Array<Record<string, unknown>> }>('config-audit?limit=30'), api<{ items: Array<{ username: string; role: string }> }>('access/users'), api<SystemHealth>('system/status'), api<{ items: Channel[] }>('channel-settings'), api<ProviderStatus>('provider-status/openai').catch(() => null)]);
    setValues(settingsPayload.values); setBaseline(settingsPayload.values); setAudit(auditPayload.items); setUsers(usersPayload.items); setSystemStatus(statusPayload);
    setOpenAIStatus(providerPayload);
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
  useEffect(() => { void load().catch((error) => setMessage(error instanceof Error ? error.message : t('配置加载失败'))); }, [load]);
  const setValue = (key: string, value: unknown) => setValues((current) => ({ ...current, [key]: value }));
  const save = async () => {
    setSaving(true); setMessage('');
    const payload = Object.fromEntries(
      Object.entries(values).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(baseline[key])),
    );
    for (const key of SECRET_SETTING_KEYS) if (payload[key] === '********') delete payload[key];
    try { await api('settings', { method: 'PUT', body: JSON.stringify(payload) }); setMessage(t('配置已保存，采集器正在热加载')); await load(); }
    catch (error) { setMessage(error instanceof Error ? error.message : t('保存失败')); }
    finally { setSaving(false); }
  };
  const testNotification = async (channel: NotificationChannelId) => {
    if (dirty) { setMessage(t('请先保存当前通知配置，再发送测试消息')); return; }
    setTestingChannel(channel); setMessage('');
    setNotificationTestResults((current) => {
      const next = { ...current };
      delete next[channel];
      return next;
    });
    try {
      await api('notifications/test', { method: 'POST', body: JSON.stringify({ channel }) });
      setNotificationTestResults((current) => ({ ...current, [channel]: { success: true, text: t('测试告警发送成功 · {{time}}', { time: new Date().toLocaleTimeString(getLanguage() === 'en' ? 'en-US' : 'zh-CN', { hour12: false }) }) } }));
    } catch (error) {
      setNotificationTestResults((current) => ({ ...current, [channel]: { success: false, text: error instanceof Error ? error.message : t('测试告警发送失败') } }));
    }
    finally { setTestingChannel(null); }
  };
  const toggleNotificationPanel = (channel: NotificationChannelId) => setExpandedNotifications((current) => {
    const next = new Set(current);
    if (next.has(channel)) next.delete(channel); else next.add(channel);
    return next;
  });
  const testOpenAIStatus = async () => {
    setTestingOpenAIStatus(true); setOpenAIStatusTest(null); setMessage('');
    try {
      const result = await api<ProviderStatus & { success: boolean; component_count: number }>('provider-status/openai/test', { method: 'POST' });
      setOpenAIStatusTest({ success: true, text: t('连接成功：{{description}} · {{components}} 个组件 · {{incidents}} 个活跃事件', { description: result.description, components: result.component_count, incidents: result.active_incident_count }) });
      setOpenAIStatus(result);
    } catch (error) {
      setOpenAIStatusTest({ success: false, text: error instanceof Error ? error.message : t('OpenAI Status 连接测试失败') });
    } finally { setTestingOpenAIStatus(false); }
  };
  const effectiveOpenAIComponentIds = useMemo(() => {
    const configured = Array.isArray(values.openai_status_component_ids)
      ? values.openai_status_component_ids.map(String)
      : [];
    if (configured.includes('__none__')) return [];
    if (configured.length) return configured;
    return (openAIStatus?.components || [])
      .filter((component) => DEFAULT_OPENAI_COMPONENT_NAMES.has(component.name))
      .map((component) => component.id);
  }, [openAIStatus, values.openai_status_component_ids]);
  const toggleOpenAIComponent = (componentId: string) => {
    const selected = new Set(effectiveOpenAIComponentIds);
    if (selected.has(componentId)) selected.delete(componentId); else selected.add(componentId);
    setValue('openai_status_component_ids', selected.size ? Array.from(selected) : ['__none__']);
  };
  const addUser = async () => { if (!newUser.trim()) return; await api(`access/users/${encodeURIComponent(newUser.trim())}`, { method: 'PUT', body: JSON.stringify({ role: newRole }) }); setNewUser(''); await load(); };
  const setOverviewVisibility = (channelId: number, audience: 'admin' | 'viewer', visible: boolean) => setOverviewChannels((current) => current.map((channel) => channel.channel_id === channelId ? { ...channel, [audience === 'viewer' ? 'overview_viewer_visible' : 'overview_admin_visible']: visible } : channel));
  const setAllOverviewVisibility = (audience: 'admin' | 'viewer', visible: boolean) => setOverviewChannels((current) => current.map((channel) => ({ ...channel, [audience === 'viewer' ? 'overview_viewer_visible' : 'overview_admin_visible']: visible })));
  const saveOverviewVisibility = async () => {
    setSaving(true); setMessage('');
    try {
      await api('channel-settings/visibility', { method: 'PUT', body: JSON.stringify({ items: overviewChannels.map((channel) => ({ channel_id: channel.channel_id, overview_admin_visible: channel.overview_admin_visible ?? true, overview_viewer_visible: channel.overview_viewer_visible ?? true })) }) });
      setMessage(t('总览展示范围已保存，对应用户下次刷新时立即生效'));
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : t('总览展示配置保存失败')); }
    finally { setSaving(false); }
  };
  const activeSection = SETTING_SECTIONS.find((section) => section.id === activePage);
  const dirty = JSON.stringify(values) !== JSON.stringify(baseline);
  const overviewDirty = JSON.stringify(overviewChannels.map((channel) => [channel.channel_id, channel.overview_admin_visible, channel.overview_viewer_visible])) !== overviewBaseline;
  const pages: Array<{ id: SettingsPageId; title: string; description: string; icon: ReactNode; count?: number }> = [
    { id: 'status', title: t('运行状态'), description: t('采集链路自检'), icon: <ShieldCheck size={18} />, count: systemStatus ? Object.keys(systemStatus.collectors).length : 0 },
    { id: 'overview', title: t('总览展示'), description: t('按角色控制渠道'), icon: <Eye size={18} />, count: overviewChannels.filter((channel) => channel.enabled).length },
    { id: 'notifications', title: t('通知中心'), description: t('企微、飞书与邮件'), icon: <BellRing size={18} />, count: ['email_enabled', 'wecom_app_enabled', 'wecom_webhook_enabled', 'feishu_app_enabled', 'feishu_webhook_enabled'].filter((key) => Boolean(values[key])).length },
    { id: 'providers', title: t('上游官方状态'), description: t('OpenAI 状态与关联'), icon: <Cloud size={18} />, count: openAIStatus?.active_incident_count || 0 },
    ...SETTING_SECTIONS.map((section) => ({ id: section.id, title: section.title, description: section.short, icon: section.icon, count: section.fields.length })),
    { id: 'access', title: t('角色映射'), description: t('访问与权限'), icon: <UserCog size={18} />, count: users.length },
    { id: 'audit', title: t('配置审计'), description: t('最近变更记录'), icon: <Clock3 size={18} />, count: audit.length },
  ];
  return <section>
    <div className="section-heading settings-heading"><div><span className="eyebrow">RUNTIME CONTROL CENTER</span><h2>{t("系统配置")}</h2><p>{t("按任务分区管理，只展示当前配置组；配置保存在监控数据库中，不写入 New API。")}</p></div><div className={classNames('settings-dirty-state', (dirty || overviewDirty) && 'settings-dirty')}><i />{dirty || overviewDirty ? t('有未保存更改') : t('配置已同步')}</div></div>
    {message && <div className="config-message">{message}</div>}
    <div className="settings-workspace">
      <aside className="settings-nav" aria-label={t("系统配置分类")}>{pages.map((page) => <button type="button" className={classNames(activePage === page.id && 'active')} key={page.id} onClick={() => onActivePageChange(page.id)}><span className="settings-nav-icon">{page.icon}</span><span><strong>{page.title}</strong><small>{page.description}</small></span>{page.count != null && <b>{page.count}</b>}<ChevronRight size={15} /></button>)}</aside>
      <div className="settings-stage">
        {activePage === 'status' && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><ShieldCheck size={18} /></div><div><span className="eyebrow">SELF MONITORING</span><h3>{t("采集链路状态")}</h3><p>{t("监控程序同时检查自身是否仍在持续产生新数据，避免“页面正常但采集已经停止”。")}</p></div><StatusPill tone={systemStatus?.status === 'ok' ? 'ok' : 'bad'}>{systemStatus?.status === 'ok' ? t('全部正常') : t('存在降级')}</StatusPill></div><div className="collector-health-grid"><div className="collector-health-card"><span>{t("数据库")}</span><strong>{systemStatus?.database === 'ok' ? t('正常') : t('异常')}</strong><small>{systemStatus?.database_error || t('SQLite 可读写')}</small></div><div className={classNames('collector-health-card', (systemStatus?.storage?.over_capacity || (systemStatus?.storage?.outbox_dead || 0) > 0) && 'collector-health-stale')}><span>{t('存储与告警队列')}</span><strong>{systemStatus?.storage ? `${((systemStatus.storage.total_bytes || 0) / 1024 / 1024).toFixed(1)} MB` : '—'}</strong><small>{t('待投递')} {systemStatus?.storage?.outbox_pending || 0} · {t('死信')} {systemStatus?.storage?.outbox_dead || 0}</small></div><div className="collector-health-card"><span>{t("监控进程")}</span><strong>{systemStatus?.monitor_worker === 'running' ? t('运行中') : systemStatus?.monitor_worker || t('未知')}</strong><small>{systemStatus?.monitor_error || t('工作线程持续运行')}</small></div>{Object.entries(systemStatus?.collectors || {}).map(([name, collector]) => { const labels: Record<string, string> = { channel_sync: t('渠道同步'), channel_probe: t('渠道探测'), logs: t('使用日志'), resources: t('机器资源'), openai_status: t('OpenAI 官方状态') }; return <div className={classNames('collector-health-card', collector.status === 'stale' && 'collector-health-stale')} key={name}><span>{labels[name] || name}</span><strong>{collector.status === 'ok' ? t('正常') : collector.status === 'starting' ? t('启动中') : t('数据过期')}</strong><small>{t("最后成功")} {collector.age_seconds}{t("s 前 · 阈值")} {collector.stale_after_seconds}s</small>{collector.consecutive_failures > 0 && <em>{t("连续失败")} {collector.consecutive_failures} {t("次")}</em>}{collector.last_error && <code title={collector.last_error}>{collector.last_error}</code>}</div>; })}</div><div className="settings-action-bar"><div><strong>{t("最后检查")} {systemStatus ? formatFullTime(systemStatus.timestamp) : '—'}</strong><small>{t("数据超过动态失效阈值后，健康检查变为 503，并生成异常与恢复事件。")}</small></div><button className="secondary-button" onClick={() => void load()}><RefreshCw size={16} />{t("立即刷新")}</button></div></article>}
        {activePage === 'overview' && <article className="settings-card settings-focus-card overview-settings-card">
          <div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><Eye size={18} /></div><div><span className="eyebrow">MONITOR OVERVIEW</span><h3>{t("总览渠道展示")}</h3><p>{t("分别控制管理端与普通用户看到的渠道卡片和状态汇总。")}</p></div><span className="settings-field-count">{overviewChannels.length} {t("个渠道")}</span></div>
          <div className="overview-audience-summary">
            <div><span className="overview-audience-icon admin"><ShieldCheck size={18} /></span><div><strong>{t("管理端")}</strong><small>{t("管理员与运维员可见")}</small></div><b>{overviewChannels.filter((channel) => channel.enabled && channel.overview_admin_visible).length}</b></div>
            <div><span className="overview-audience-icon viewer"><Users size={18} /></span><div><strong>{t("用户端")}</strong><small>{t("普通用户可见")}</small></div><b>{overviewChannels.filter((channel) => channel.enabled && channel.overview_viewer_visible).length}</b></div>
            <p><AlertTriangle size={15} />{t("隐藏仅影响监控总览展示和状态汇总，不会停止渠道探测、日志采集或告警。")}</p>
          </div>
          <div className="overview-visibility-toolbar"><div><strong>{t("批量设置")}</strong><small>{t("先批量调整，再对个别渠道微调。")}</small></div><div><button onClick={() => setAllOverviewVisibility('admin', true)}>{t("管理端全开")}</button><button onClick={() => setAllOverviewVisibility('admin', false)}>{t("管理端全关")}</button><button onClick={() => setAllOverviewVisibility('viewer', true)}>{t("用户端全开")}</button><button onClick={() => setAllOverviewVisibility('viewer', false)}>{t("用户端全关")}</button></div></div>
          <div className="overview-visibility-table">
            <div className="overview-visibility-head"><span>{t("渠道")}</span><span>{t('源状态')}</span><span>{t("管理端总览")}</span><span>{t('用户端总览')}</span></div>
            {overviewChannels.map((channel) => <div className={classNames('overview-visibility-row', !channel.enabled && 'disabled')} key={channel.channel_id}><div><span className="provider-mark compact">{channel.name.slice(0, 2).toUpperCase()}</span><span><strong>{channel.name}</strong><small>#{channel.channel_id} · {channel.group || 'default'}</small></span></div><StatusPill tone={channel.enabled ? 'ok' : 'muted'}>{channel.enabled ? t('已启用') : t('已禁用')}</StatusPill><Toggle checked={channel.overview_admin_visible ?? true} onChange={(visible) => setOverviewVisibility(channel.channel_id, 'admin', visible)} label={t("管理端")} /><Toggle checked={channel.overview_viewer_visible ?? true} onChange={(visible) => setOverviewVisibility(channel.channel_id, 'viewer', visible)} label={t('用户端')} /></div>)}
          </div>
          <div className="settings-action-bar"><div><strong>{overviewDirty ? t('展示范围尚未应用') : t('角色展示范围已生效')}</strong><small>{t("保存后无需重启，各角色刷新监控总览即可生效。")}</small></div><button className="secondary-button" disabled={!overviewDirty || saving} onClick={() => void load()}>{t("撤销更改")}</button><button className="primary-button settings-save" disabled={!overviewDirty || saving || !overviewChannels.length} onClick={() => void saveOverviewVisibility()}>{saving ? <RefreshCw className="spin" size={16} /> : <Save size={16} />}{t("保存展示范围")}</button></div>
        </article>}
        {activePage === 'providers' && <article className="settings-card settings-focus-card provider-settings-card">
          <div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><Cloud size={18} /></div><div><span className="eyebrow">UPSTREAM STATUS INTELLIGENCE</span><h3>{t('OpenAI 官方状态')}</h3><p>{t('将 OpenAI 官方事件作为独立辅助上下文，用于解释故障和减少重复告警。')}</p></div><StatusPill tone={!openAIStatus || openAIStatus.stale ? 'muted' : openAIStatus.indicator === 'none' ? 'ok' : 'bad'}>{!openAIStatus ? t('等待同步') : openAIStatus.stale ? t('数据过期') : openAIStatus.description}</StatusPill></div>
          <div className="provider-settings-summary">
            <div><span>{t('官方整体状态')}</span><strong>{openAIStatus?.description || t('等待首次同步')}</strong><small>{openAIStatus?.observed_at ? formatFullTime(openAIStatus.observed_at) : t('尚无采集数据')}</small></div>
            <div><span>{t('组件与事件')}</span><strong>{openAIStatus?.components.length || 0} / {openAIStatus?.active_incident_count || 0}</strong><small>{t('组件总数 / 活跃官方事件')}</small></div>
            <div><span>{t('安全边界')}</span><strong>{t('只读')}</strong><small>{t('上游官方状态不会自动修改或禁用 New API 渠道。')}</small></div>
          </div>
          <div className="provider-settings-grid">
            <section className="provider-settings-controls">
              <div className="provider-subsection-head"><div><strong>{t('采集与告警')}</strong><small>{t('官方状态变化较慢，默认每 60 秒采集一次。')}</small></div></div>
              <div className="provider-toggle-stack"><Toggle checked={Boolean(values.openai_status_enabled)} onChange={(value) => setValue('openai_status_enabled', value)} label={t('启用 OpenAI Status 监控')} /><Toggle checked={Boolean(values.openai_status_alert_enabled)} onChange={(value) => setValue('openai_status_alert_enabled', value)} label={t('发送官方事件告警')} /><Toggle checked={Boolean(values.openai_status_include_in_overall)} onChange={(value) => setValue('openai_status_include_in_overall', value)} label={t('关注组件异常时影响总览状态')} /></div>
              <div className="provider-field-grid">
                <label><span>{t('采集间隔（秒）')}</span><input type="number" min="30" max="3600" value={String(values.openai_status_interval_seconds ?? 60)} onChange={(event) => setValue('openai_status_interval_seconds', Number(event.target.value))} /><small>{t('建议 60–300 秒')}</small></label>
                <label><span>{t('请求超时（秒）')}</span><input type="number" min="3" max="30" value={String(values.openai_status_timeout_seconds ?? 10)} onChange={(event) => setValue('openai_status_timeout_seconds', Number(event.target.value))} /><small>{t('采集失败不等于 OpenAI 故障')}</small></label>
                <label><span>{t('最低告警等级')}</span><select value={String(values.openai_status_min_impact ?? 'major')} onChange={(event) => setValue('openai_status_min_impact', event.target.value)}><option value="none">None</option><option value="minor">Minor</option><option value="major">Major</option><option value="critical">Critical</option></select><small>{t('建议 Major，避免低影响事件打扰')}</small></label>
                <label><span>{t('异常确认次数')}</span><input type="number" min="1" max="10" value={String(values.openai_status_failure_threshold ?? 2)} onChange={(event) => setValue('openai_status_failure_threshold', Number(event.target.value))} /><small>{t('用于组件状态防抖')}</small></label>
                <label><span>{t('恢复确认次数')}</span><input type="number" min="1" max="10" value={String(values.openai_status_recovery_threshold ?? 2)} onChange={(event) => setValue('openai_status_recovery_threshold', Number(event.target.value))} /><small>{t('连续正常后再发送恢复')}</small></label>
              </div>
              <div className="provider-subsection-head provider-visibility-head"><div><strong>{t('官方状态页可见范围')}</strong><small>{t('控制独立页面和总览轻量提示的可见性。')}</small></div></div>
              <div className="provider-toggle-stack provider-visibility-toggles"><Toggle checked={Boolean(values.openai_status_admin_visible)} onChange={(value) => setValue('openai_status_admin_visible', value)} label={t('管理员与运维员可见')} /><Toggle checked={Boolean(values.openai_status_viewer_visible)} onChange={(value) => setValue('openai_status_viewer_visible', value)} label={t('普通用户可见')} /></div>
            </section>
            <section className="provider-component-selector">
              <div className="provider-subsection-head"><div><strong>{t('关注组件')}</strong><small>{t('只监控与你当前业务有关的 OpenAI 服务。')}</small></div><button type="button" onClick={() => setValue('openai_status_component_ids', [])}>{t('恢复推荐')}</button></div>
              <div className="provider-component-list">
                {(openAIStatus?.components || []).map((component) => <label className={classNames(effectiveOpenAIComponentIds.includes(component.id) && 'selected', component.status !== 'operational' && 'degraded')} key={component.id}><input type="checkbox" checked={effectiveOpenAIComponentIds.includes(component.id)} onChange={() => toggleOpenAIComponent(component.id)} /><span><strong>{component.name}</strong><small>{component.status.replaceAll('_', ' ')}</small></span><i /></label>)}
                {!openAIStatus?.components.length && <div className="provider-components-empty"><Cloud size={24} /><strong>{t('等待组件同步')}</strong><span>{t('点击测试官方连接可立即读取组件列表。')}</span></div>}
              </div>
              <div className="provider-test-zone"><button type="button" className="secondary-button" disabled={testingOpenAIStatus} onClick={() => void testOpenAIStatus()}>{testingOpenAIStatus ? <RefreshCw className="spin" size={15} /> : <Network size={15} />}{testingOpenAIStatus ? t('正在测试') : t('测试官方连接')}</button>{openAIStatusTest && <div className={classNames('provider-test-result', openAIStatusTest.success ? 'success' : 'failed')}>{openAIStatusTest.success ? <CheckCircle2 size={15} /> : <XCircle size={15} />}<span>{openAIStatusTest.text}</span></div>}</div>
            </section>
          </div>
          <div className="settings-action-bar"><div><strong>{dirty ? t('上游状态配置尚未应用') : t('上游状态监控已生效')}</strong><small>{t('保存后采集器热加载；不会重启或修改 New API。')}</small></div><button className="secondary-button" disabled={!dirty || saving} onClick={() => { setValues(baseline); setMessage(t('已撤销本次未保存更改')); }}>{t('撤销更改')}</button><button className="primary-button settings-save" disabled={!dirty || saving} onClick={() => void save()}>{saving ? <RefreshCw className="spin" size={16} /> : <Save size={16} />}{t('保存并应用')}</button></div>
        </article>}
        {activePage === 'notifications' && <article className="settings-card settings-focus-card notification-center-card">
          <div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><BellRing size={18} /></div><div><span className="eyebrow">MULTI-CHANNEL DELIVERY</span><h3>{t("告警通知中心")}</h3><p>{t("同一告警可同时发送到多个渠道；单个渠道失败不会阻断其他渠道。敏感凭据加密保存且不会回显。")}</p></div><span className="settings-field-count">{['email_enabled', 'wecom_app_enabled', 'wecom_webhook_enabled', 'feishu_app_enabled', 'feishu_webhook_enabled'].filter((key) => Boolean(values[key])).length} {t("个已启用")}</span></div>
          <div className="notification-global-bar"><label><span>{t("通知标题前缀")}</span><input value={String(values.subject_prefix ?? '')} onChange={(event) => setValue('subject_prefix', event.target.value)} /></label><Toggle checked={Boolean(values.send_startup_email)} onChange={(value) => setValue('send_startup_email', value)} label={t("监控启动时发送通知")} /><div><ShieldCheck size={15} /><span>{t("应用 Secret、Webhook 地址与签名密钥均按秘密字段加密存储。")}</span></div></div>
          <div className="notification-policy-card"><div className="notification-policy-head"><div><Clock3 size={16} /><span><strong>{t('静默时段')}</strong><small>{t('静默期间保留告警并延后投递，不丢弃任何消息。')}</small></span></div><Toggle checked={Boolean(values.notification_quiet_hours_enabled)} onChange={(value) => setValue('notification_quiet_hours_enabled', value)} label={t('启用静默时段')} /></div><div className="notification-policy-fields"><label><span>{t('开始')}</span><input type="time" disabled={!values.notification_quiet_hours_enabled} value={String(values.notification_quiet_hours_start ?? '22:00')} onChange={(event) => setValue('notification_quiet_hours_start', event.target.value)} /></label><label><span>{t('结束')}</span><input type="time" disabled={!values.notification_quiet_hours_enabled} value={String(values.notification_quiet_hours_end ?? '08:00')} onChange={(event) => setValue('notification_quiet_hours_end', event.target.value)} /></label><label><span>{t('时区')}</span><input disabled={!values.notification_quiet_hours_enabled} value={String(values.notification_quiet_hours_timezone ?? 'Asia/Shanghai')} onChange={(event) => setValue('notification_quiet_hours_timezone', event.target.value)} /></label><Toggle checked={Boolean(values.notification_quiet_hours_allow_critical ?? true)} onChange={(value) => setValue('notification_quiet_hours_allow_critical', value)} label={t('严重告警仍立即发送')} /></div></div>
          <div className="notification-channel-grid">
            {([
              { id: 'wecom_app' as const, title: t('企业微信自建应用'), description: t('适合直接通知应用可见范围内的成员'), icon: <MessageSquare size={18} />, enabled: Boolean(values.wecom_app_enabled), configured: Boolean(values.wecom_corp_id && values.wecom_agent_id && values.wecom_app_secret && (values.wecom_to_user || values.wecom_to_party || values.wecom_to_tag)), fields: <><label><span>{t("企业 ID")}</span><input value={String(values.wecom_corp_id ?? '')} onChange={(event) => setValue('wecom_corp_id', event.target.value)} /></label><label><span>AgentId</span><input type="number" value={String(values.wecom_agent_id ?? '')} onChange={(event) => setValue('wecom_agent_id', Number(event.target.value))} /></label><label className="notification-wide"><span>{t("应用 Secret")}</span><input type="password" value={String(values.wecom_app_secret ?? '')} placeholder={t("留空保持原值")} onChange={(event) => setValue('wecom_app_secret', event.target.value)} /></label><label><span>{t("成员")}</span><input value={String(values.wecom_to_user ?? '')} placeholder={t("@all 或 user1|user2")} onChange={(event) => setValue('wecom_to_user', event.target.value)} /></label><label><span>{t("部门")}</span><input value={String(values.wecom_to_party ?? '')} placeholder={t("可选，1|2")} onChange={(event) => setValue('wecom_to_party', event.target.value)} /></label><label><span>{t("标签")}</span><input value={String(values.wecom_to_tag ?? '')} placeholder={t("可选，1|2")} onChange={(event) => setValue('wecom_to_tag', event.target.value)} /></label></> },
              { id: 'wecom_webhook' as const, title: t('企业微信群机器人'), description: t('通过群机器人 Webhook 推送到指定群聊'), icon: <Send size={18} />, enabled: Boolean(values.wecom_webhook_enabled), configured: Boolean(values.wecom_webhook_url), fields: <label className="notification-wide"><span>{t("Webhook 地址")}</span><input type="password" value={String(values.wecom_webhook_url ?? '')} placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." onChange={(event) => setValue('wecom_webhook_url', event.target.value)} /></label> },
              { id: 'feishu_app' as const, title: t('飞书自建应用'), description: t('使用应用身份向用户或群聊发送消息'), icon: <MessageSquare size={18} />, enabled: Boolean(values.feishu_app_enabled), configured: Boolean(values.feishu_app_id && values.feishu_app_secret && values.feishu_receive_id), fields: <><label><span>App ID</span><input value={String(values.feishu_app_id ?? '')} onChange={(event) => setValue('feishu_app_id', event.target.value)} /></label><label><span>App Secret</span><input type="password" value={String(values.feishu_app_secret ?? '')} placeholder={t("留空保持原值")} onChange={(event) => setValue('feishu_app_secret', event.target.value)} /></label><label><span>{t("接收者类型")}</span><select value={String(values.feishu_receive_id_type ?? 'chat_id')} onChange={(event) => setValue('feishu_receive_id_type', event.target.value)}><option value="chat_id">{t("群聊 chat_id")}</option><option value="open_id">{t("用户 open_id")}</option><option value="user_id">{t("用户 user_id")}</option><option value="union_id">{t("用户 union_id")}</option><option value="email">{t("用户邮箱")}</option></select></label><label><span>{t("接收者 ID")}</span><input value={String(values.feishu_receive_id ?? '')} placeholder={t("还需要提供此项")} onChange={(event) => setValue('feishu_receive_id', event.target.value)} /></label></> },
              { id: 'feishu_webhook' as const, title: t('飞书群机器人'), description: t('支持普通 Webhook 与签名校验机器人'), icon: <Send size={18} />, enabled: Boolean(values.feishu_webhook_enabled), configured: Boolean(values.feishu_webhook_url), fields: <><label className="notification-wide"><span>{t("Webhook 地址")}</span><input type="password" value={String(values.feishu_webhook_url ?? '')} placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." onChange={(event) => setValue('feishu_webhook_url', event.target.value)} /></label><label className="notification-wide"><span>{t("签名密钥")}</span><input type="password" value={String(values.feishu_webhook_secret ?? '')} placeholder={t("机器人未启用签名时可留空")} onChange={(event) => setValue('feishu_webhook_secret', event.target.value)} /></label></> },
              { id: 'email' as const, title: t('电子邮件'), description: t('保留 SMTP 作为独立通道或故障兜底'), icon: <Mail size={18} />, enabled: Boolean(values.email_enabled), configured: Boolean(values.smtp_host && values.smtp_to), fields: <><label><span>{t("SMTP 地址")}</span><input value={String(values.smtp_host ?? '')} onChange={(event) => setValue('smtp_host', event.target.value)} /></label><label><span>{t("端口")}</span><input type="number" value={String(values.smtp_port ?? '')} onChange={(event) => setValue('smtp_port', Number(event.target.value))} /></label><label><span>{t("SMTP 用户")}</span><input value={String(values.smtp_user ?? '')} onChange={(event) => setValue('smtp_user', event.target.value)} /></label><label><span>{t("SMTP 密码")}</span><input type="password" value={String(values.smtp_password ?? '')} placeholder={t("留空保持原值")} onChange={(event) => setValue('smtp_password', event.target.value)} /></label><label><span>{t("发件人")}</span><input value={String(values.smtp_from ?? '')} onChange={(event) => setValue('smtp_from', event.target.value)} /></label><label><span>{t("收件人")}</span><input value={String(values.smtp_to ?? '')} placeholder={t("多个地址用逗号分隔")} onChange={(event) => setValue('smtp_to', event.target.value)} /></label><Toggle checked={Boolean(values.smtp_ssl)} onChange={(value) => { setValue('smtp_ssl', value); if (value) setValue('smtp_starttls', false); }} label="SSL" /><Toggle checked={Boolean(values.smtp_starttls)} onChange={(value) => { setValue('smtp_starttls', value); if (value) setValue('smtp_ssl', false); }} label="STARTTLS" /></> },
            ]).map((channel) => <section className={classNames('notification-channel', channel.enabled && 'enabled', expandedNotifications.has(channel.id) && 'expanded')} key={channel.id}><button type="button" className="notification-channel-head" onClick={() => toggleNotificationPanel(channel.id)}><span className="notification-channel-icon">{channel.icon}</span><span><strong>{channel.title}</strong><small>{channel.description}</small></span><i className={classNames('notification-state-dot', channel.enabled && channel.configured && 'ready', channel.enabled && !channel.configured && 'incomplete')} /><b>{channel.enabled ? channel.configured ? t('已启用') : t('待补全') : channel.configured ? t('可测试') : t('未配置')}</b><ChevronRight size={16} /></button>{expandedNotifications.has(channel.id) && <div className="notification-channel-body"><div className="notification-enable-row"><Toggle checked={channel.enabled} onChange={(enabled) => setValue(`${channel.id}_enabled`, enabled)} label={t("启用此通知渠道")} /><button type="button" className="secondary-button notification-test" disabled={!channel.configured || dirty || testingChannel !== null} onClick={() => void testNotification(channel.id)}>{testingChannel === channel.id ? <RefreshCw className="spin" size={14} /> : <Send size={14} />}{testingChannel === channel.id ? t('正在发送') : t('触发测试告警')}</button></div>{notificationTestResults[channel.id] && <div className={classNames('notification-test-result', notificationTestResults[channel.id]?.success ? 'success' : 'failed')}>{notificationTestResults[channel.id]?.success ? <CheckCircle2 size={15} /> : <XCircle size={15} />}<span>{notificationTestResults[channel.id]?.text}</span></div>}<div className="notification-fields">{channel.fields}</div>{channel.id === 'feishu_app' && <p className="notification-requirement"><AlertTriangle size={14} />{t("App ID 与 Secret 已足够换取令牌，但发送消息仍必须填写用户或群聊的接收者 ID。")}</p>}</div>}</section>)}
          </div>
          <div className="settings-action-bar"><div><strong>{dirty ? t('通知配置尚未应用') : t('通知路由已生效')}</strong><small>{dirty ? t('保存后工作线程会热加载；测试按钮将在保存后可用。') : t('可以展开任一渠道发送真实测试通知。')}</small></div><button className="secondary-button" disabled={!dirty || saving} onClick={() => { setValues(baseline); setMessage(t('已撤销本次未保存更改')); }}>{t("撤销更改")}</button><button className="primary-button settings-save" disabled={!dirty || saving} onClick={() => void save()}>{saving ? <RefreshCw className="spin" size={16} /> : <Save size={16} />}{t("保存通知配置")}</button></div>
        </article>}
        {activeSection && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark">{activeSection.icon}</div><div><span className="eyebrow">CONFIGURATION GROUP</span><h3>{activeSection.title}</h3><p>{activeSection.description}</p></div><span className="settings-field-count">{activeSection.fields.length} {t("项")}</span></div><div className="settings-fields">{activeSection.fields.map((field) => field.type === 'boolean' ? <Toggle key={field.key} label={field.label} checked={Boolean(values[field.key])} onChange={(value) => setValue(field.key, value)} /> : <label key={field.key}><span>{field.label}</span>{field.type === 'select' ? <select value={String(values[field.key] ?? '')} onChange={(event) => setValue(field.key, event.target.value)}>{field.options?.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select> : <input type={field.type === 'password' ? 'password' : field.type === 'number' ? 'number' : 'text'} value={String(values[field.key] ?? '')} placeholder={field.hint} onChange={(event) => setValue(field.key, field.type === 'number' ? Number(event.target.value) : event.target.value)} />}<small>{field.hint}</small></label>)}</div><div className="settings-action-bar"><div><strong>{dirty ? t('更改尚未应用') : t('当前配置已生效')}</strong><small>{dirty ? t('保存后采集器将在数秒内热加载，无需重启。') : t('你可以切换左侧分类继续检查其他配置。')}</small></div><button className="secondary-button" disabled={!dirty || saving} onClick={() => { setValues(baseline); setMessage(t('已撤销本次未保存更改')); }}>{t("撤销更改")}</button><button className="primary-button settings-save" disabled={!dirty || saving} onClick={() => void save()}>{saving ? <RefreshCw className="spin" size={16} /> : <Save size={16} />}{t("保存并应用")}</button></div></article>}
        {activePage === 'access' && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><UserCog size={18} /></div><div><span className="eyebrow">ACCESS CONTROL</span><h3>{t("角色映射")}</h3><p>{t("用户无需预先同步，登录时实时识别。普通 New API 用户只能使用个人 New API 功能页，Admin 与 Root 自动成为管理员；这里可以对指定用户覆盖。")}</p></div></div><div className="user-add user-add-wide"><input placeholder={t("New API 用户名")} value={newUser} onChange={(event) => setNewUser(event.target.value)} /><select value={newRole} onChange={(event) => setNewRole(event.target.value)}><option value="viewer">{t("个人 New API 功能页")}</option><option value="operator">{t("运维")}</option><option value="admin">{t("管理员")}</option></select><button onClick={() => void addUser()}><UserCog size={15} />{t("添加映射")}</button></div><div className="role-list">{users.map((user) => <div key={user.username}><strong>{user.username}</strong><span>{user.role}</span><button onClick={async () => { await api(`access/users/${encodeURIComponent(user.username)}`, { method: 'PUT', body: JSON.stringify({ role: null }) }); await load(); }}><X size={14} /></button></div>)}{!users.length && <p>{t("暂无用户覆盖规则")}</p>}</div></article>}
        {activePage === 'audit' && <article className="settings-card settings-focus-card"><div className="settings-card-head settings-focus-head"><div className="settings-section-mark"><Clock3 size={18} /></div><div><span className="eyebrow">CHANGE HISTORY</span><h3>{t("配置审计")}</h3><p>{t("最近30次系统、渠道和权限变更，便于快速定位误操作。")}</p></div><span className="settings-field-count">{audit.length} {t("条")}</span></div><div className="audit-list audit-list-wide">{audit.map((entry) => <div key={String(entry.id)}><span>{formatFullTime(Number(entry.created_at))}</span><strong>{String(entry.actor)}</strong><small>{String(entry.action)} · {String(entry.target)}</small></div>)}</div></article>}
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
    ? observationHealth(activePoint, channel.slow_after_seconds).tone
    : 'ok';
  const activeStatus = activeState === 'bad' ? t('异常') : activeState === 'warn' ? t('延迟') : t('正常');
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
      <div className="history-bars" role="group" aria-label={t('{{name}} 最近探测历史', { name: channel.name })} style={channel.history.length ? { gridTemplateColumns: `repeat(${channel.history.length}, minmax(0, 1fr))` } : undefined}>
        {channel.history.length === 0 && <div className="history-empty">{t("等待首次探测")}</div>}
        {channel.history.map((point, index) => {
          const state = observationHealth(point, channel.slow_after_seconds).tone;
          const label = `${formatFullTime(point.observed_at)} · ${point.success ? t('正常') : t('异常')} · ${t('总耗时')} ${formatDuration(point.elapsed_ms)} · ${t('首字')} ${formatDuration(point.frt_ms)}`;
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
            <div><small>{activePoint.success ? t('探测结果') : t('错误详情')}</small><span>{activePoint.success ? `${t('成功')} · ${formatDuration(activePoint.elapsed_ms)}` : activePoint.message || t('验证失败')}</span></div>
            <em>{activePoint.source === 'real' ? t('真实请求') : t('内置测试')} · {pinnedIndex === activeIndex ? t('已固定') : t('点击色块固定')}</em>
          </div>
        </div>
      , document.body)}
    </div>
  );
}

function channelHealthText(state: ReturnType<typeof channelHealth>['state']): string {
  if (state === 'unknown') return t('未探测');
  if (state === 'stale') return t('数据陈旧');
  if (state === 'failed') return t('异常');
  if (state === 'delayed') return t('延迟');
  return t('正常');
}

function observationSourceText(source: string): string {
  if (source === 'real') return t('真实模型探测');
  if (source === 'builtin') return t('内置测试');
  return t('尚无探测来源');
}

function ChannelCard({ channel, onOpen, availabilityLabel }: { channel: Channel; onOpen: () => void; availabilityLabel: string }) {
  const latest = channel.latest;
  const health = channelHealth(channel);
  const tone = health.tone;
  const statusText = channelHealthText(health.state);
  const modelLabel = channel.models.length ? channel.models.slice(0, 2).join(' · ') : t('未配置模型');
  return (
    <article className={classNames('channel-card', tone === 'bad' && 'channel-card-bad')}>
      <button className="channel-open" onClick={onOpen} aria-label={t('查看 {{name}} 详情', { name: channel.name })}><ChevronRight size={19} /></button>
      <div className="channel-header">
        <div className="provider-mark">{channel.name.slice(0, 2).toUpperCase()}</div>
        <div className="channel-title"><h3>{channel.name}</h3><p>{channel.group || 'default'} <span>·</span> {modelLabel}</p></div>
        <StatusPill tone={tone}>{statusText}</StatusPill>
      </div>
      <div className="probe-source"><span>{latest?.source === 'real' ? 'REAL MODEL PROBE' : latest?.source === 'builtin' ? 'BUILT-IN CHECK' : 'WAITING FOR PROBE'}</span><span>{latest ? formatTime(latest.observed_at) : t('未探测')}</span></div>
      <div className="channel-stats">
        <div><span><Activity size={14} />{t("探测总耗时")}</span><strong>{formatDuration(latest?.elapsed_ms)}</strong></div>
        <div><span><Network size={14} />{t("首字响应")}</span><strong>{formatDuration(latest?.frt_ms)}</strong></div>
      </div>
      <div className="availability-row">
        <div><span>{t('可用率')} · {availabilityLabel}</span><small title={channel.availability.coverage_start_at ? `${formatFullTime(channel.availability.coverage_start_at)} → ${formatFullTime(channel.availability.coverage_end_at)}` : t('暂无有效样本')}>{observationSourceText(channel.availability.source)} · {channel.availability.successes}/{channel.availability.total} {t("成功")}</small></div>
        <strong className={tone === 'bad' ? 'text-bad' : tone === 'warn' ? 'text-warn' : 'text-ok'}>{channel.availability.percentage == null ? '—' : `${channel.availability.percentage.toFixed(2)}%`}</strong>
      </div>
      <div className="usage-strip"><span>{t("24H 请求")} <b>{channel.usage_24h.requests}</b></span><span>P95 <b>{channel.usage_24h.p95_seconds.toFixed(2)}s</b></span><span>{t("慢请求")} <b className={channel.usage_24h.slow ? 'text-warn' : ''}>{channel.usage_24h.slow}</b></span></div>
      <HistoryBars channel={channel} />
    </article>
  );
}

function DetailDrawer({ channel, onClose, customerView }: { channel: Channel; onClose: () => void; customerView: boolean }) {
  const health = channelHealth(channel);
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={t('{{name}} 渠道详情', { name: channel.name })} onMouseDown={(event) => event.stopPropagation()}>
        <button className="icon-button drawer-close" onClick={onClose} aria-label={t("关闭")}><X size={20} /></button>
        <div className="eyebrow">CHANNEL DETAIL / #{channel.channel_id}</div>
        <h2>{channel.name}</h2>
        <p className="drawer-subtitle">{customerView ? t('渠道配置按周期同步，探测结果独立存档。') : t('数据按配置周期与 New API 渠道同步，探测结果独立存档。')}</p>
        <div className="drawer-grid">
          <div><span>{t("状态")}</span><strong>{channelHealthText(health.state)}</strong></div>
          <div><span>{t("探测方式")}</span><strong>{channel.latest?.source === 'real' ? t('真实模型请求') : channel.latest?.source === 'builtin' ? (customerView ? t('平台健康检查') : t('New API 内置测试')) : t('尚未产生探测样本')}</strong></div>
          <div><span>{t("总耗时")}</span><strong>{formatDuration(channel.latest?.elapsed_ms)}</strong></div>
          <div><span>{t("首字耗时")}</span><strong>{formatDuration(channel.latest?.frt_ms)}</strong></div>
        </div>
        <section className="drawer-section"><h3>{t("最近 60 次探测")}</h3><HistoryBars channel={channel} /></section>
        <section className="drawer-section"><h3>{t("模型范围")}</h3><div className="tag-list">{channel.models.map((model) => <span key={model}>{model}</span>)}</div></section>
        <section className="drawer-section"><h3>{t("同步信息")}</h3><dl className="detail-list"><div><dt>{t("渠道组")}</dt><dd>{channel.group || 'default'}</dd></div><div><dt>{t("配置同步")}</dt><dd>{formatTime(channel.synced_at, true)}</dd></div><div><dt>{t('数据失效阈值')}</dt><dd>{formatElapsed(channel.stale_after_seconds)}</dd></div><div><dt>{t('可用率来源')}</dt><dd>{observationSourceText(channel.availability.source)}</dd></div><div><dt>{t('统计覆盖')}</dt><dd>{channel.availability.coverage_start_at ? `${formatFullTime(channel.availability.coverage_start_at)} → ${formatFullTime(channel.availability.coverage_end_at)}` : t('暂无有效样本')}</dd></div><div><dt>{t('历史保留')}</dt><dd>{t('最多 {{days}} 天', { days: channel.availability.retention_days })}</dd></div><div><dt>{t("最后请求")}</dt><dd>{formatTime(channel.usage_24h.last_request_at, true)}</dd></div></dl></section>
      </aside>
    </div>
  );
}

function ProviderStatusHint({ status, onOpen }: { status: ProviderStatus; onOpen: () => void }) {
  const context = buildProviderStatusContext(status);
  const healthy = context.relevantComponents.length - context.relevantDegradedComponents.length;
  const tone = context.state === 'stale' ? 'muted' : context.state === 'relevant-issue' ? 'bad' : context.state === 'global-notice' ? 'warn' : 'ok';
  const label = context.state === 'stale'
    ? t('官方数据已过期')
    : context.state === 'relevant-issue'
      ? t('关注组件存在异常')
      : context.state === 'global-notice'
        ? t('官方有其他事件')
        : t('关注组件全部正常');
  return (
    <button className={classNames('overview-provider-hint', `overview-provider-${tone}`)} type="button" onClick={onOpen}>
      <span className="overview-provider-icon"><Cloud size={15} /></span>
      <span className="overview-provider-copy"><small>{t('官方状态仅作参考')}</small><strong>{label}</strong></span>
      <span className="overview-provider-scope">{t('关注组件 {{healthy}}/{{total}} 正常', { healthy, total: context.relevantComponents.length })}</span>
      <ChevronRight size={15} />
    </button>
  );
}

function ProviderStatusView({ status, summary, onOverview }: { status: ProviderStatus; summary: Summary; onOverview: () => void }) {
  const context = buildProviderStatusContext(status);
  const relevantHealthy = context.relevantComponents.length - context.relevantDegradedComponents.length;
  const globalDegraded = status.components.filter((component) => component.status !== 'operational');
  const relevantTone = context.state === 'stale' ? 'muted' : context.state === 'relevant-issue' ? 'bad' : 'ok';
  const globalTone = status.stale ? 'muted' : status.indicator === 'none' ? 'ok' : status.indicator === 'minor' ? 'warn' : 'bad';
  const relevanceLabel = context.state === 'stale'
    ? t('官方数据已过期')
    : context.state === 'relevant-issue'
      ? t('关注组件存在异常')
      : t('关注组件全部正常');
  return (
    <section className="provider-status-workspace">
      <div className="provider-page-heading">
        <div className="provider-page-title"><span><Cloud size={23} /></span><div><div className="eyebrow">UPSTREAM CONTEXT / OPENAI</div><h2>{t('OpenAI 官方状态')}</h2><p>{t('官方状态用于补充故障上下文，不代表你的渠道一定受影响；业务判断始终以真实渠道探测和实际请求日志为准。')}</p></div></div>
        <div className="provider-page-actions"><button className="secondary-button" type="button" onClick={onOverview}>{t('返回渠道总览')}</button><a href={status.source_url || 'https://status.openai.com/'} target="_blank" rel="noreferrer">{t('打开官方状态页')}<ExternalLink size={14} /></a></div>
      </div>

      <div className="provider-signal-flow">
        <article className={summary.channels.failed ? 'signal-local signal-problem' : 'signal-local'}><span>PRIMARY SIGNAL</span><div><ShieldCheck size={18} /><strong>{t('{{healthy}}/{{total}} 个渠道正常', { healthy: summary.channels.healthy, total: summary.channels.total })}</strong></div><small>{t('真实模型请求与使用日志')}</small></article>
        <ChevronRight className="signal-arrow" size={20} />
        <article className={classNames('signal-official', `signal-${relevantTone}`)}><span>CONTEXT SIGNAL</span><div><Cloud size={18} /><strong>{relevanceLabel}</strong></div><small>{t('官方状态仅作参考')} · {t('不会覆盖本地渠道结论')}</small></article>
      </div>

      <div className="provider-status-kpis">
        <div><span>{t('业务相关组件')}</span><strong>{relevantHealthy}/{context.relevantComponents.length}</strong><small>{context.relevantDegradedComponents.length ? t('{{count}} 个关注组件异常', { count: context.relevantDegradedComponents.length }) : t('当前没有业务相关组件异常')}</small></div>
        <div><span>{t('OpenAI 全局状态')}</span><StatusPill tone={globalTone}>{status.description || t('等待首次同步')}</StatusPill><small>{globalDegraded.length ? t('{{count}} 个官方组件异常', { count: globalDegraded.length }) : t('全部官方组件正常')}</small></div>
        <div><span>{t('活跃官方事件')}</span><strong>{status.active_incident_count}</strong><small>{status.active_incident_count && !context.relevantDegradedComponents.length ? t('官方存在事件，但未发现关注组件异常。') : t('当前没有活跃官方事件')}</small></div>
        <div><span>{t('最后同步')}</span><strong>{status.observed_at ? formatTime(status.observed_at, true) : '—'}</strong><small>{status.stale ? t('数据过期') : t('数据新鲜')}</small></div>
      </div>

      <div className="provider-status-columns">
        <section className="provider-relevance-panel">
          <div className="provider-section-head"><div><span>WORKLOAD SCOPE</span><h3>{t('业务相关组件')}</h3><p>{t('仅展示系统配置中选中的组件；这里的异常才是官方状态与当前业务的直接关联信号。')}</p></div><StatusPill tone={relevantTone}>{relevanceLabel}</StatusPill></div>
          <div className="provider-relevance-list">
            {context.relevantComponents.map((component) => <article className={component.status === 'operational' ? 'healthy' : 'degraded'} key={component.id}><i /><div><strong>{component.name}</strong><small>{component.status.replaceAll('_', ' ')}</small></div><span>{component.status === 'operational' ? t('正常') : t('异常')}</span></article>)}
            {!context.relevantComponents.length && <div className="provider-page-empty"><Cloud size={22} /><strong>{t('尚未选择关注组件')}</strong><span>{t('请在系统配置中选择与你业务相关的 OpenAI 服务。')}</span></div>}
          </div>
        </section>

        <section className="provider-incidents-panel">
          <div className="provider-section-head"><div><span>GLOBAL CONTEXT</span><h3>{t('OpenAI 全局事件')}</h3><p>{t('可能包含与你当前 API 渠道无关的产品或功能。')}</p></div><StatusPill tone={status.active_incident_count ? 'warn' : 'ok'}>{status.active_incident_count}</StatusPill></div>
          <div className="provider-page-incidents">
            {status.incidents.map((incident) => <article key={incident.id}><div><span>{incident.impact || 'none'}</span><time>{formatTime(incident.updated_at || incident.created_at, true)}</time></div><strong>{incident.name}</strong><p>{incident.latest_update?.body || t('OpenAI 尚未提供进一步说明')}</p><small>{incident.status.replaceAll('_', ' ')}</small></article>)}
            {!status.incidents.length && <div className="provider-page-empty compact"><CheckCircle2 size={22} /><strong>{t('当前没有活跃官方事件')}</strong><span>{t('OpenAI 官方状态页当前未报告进行中的事件。')}</span></div>}
          </div>
        </section>
      </div>

      <details className="provider-all-components">
        <summary><span><strong>{t('全部官方组件')}</strong><small>{t('用于排查外围产品，不参与本地渠道健康结论')}</small></span><span>{status.components.length} · {t('{{healthy}} 正常 · {{degraded}} 异常', { healthy: status.components.length - globalDegraded.length, degraded: globalDegraded.length })}</span></summary>
        <div>{status.components.map((component) => <span className={component.status === 'operational' ? 'healthy' : 'degraded'} key={component.id}><i />{component.name}<em>{component.status.replaceAll('_', ' ')}</em></span>)}</div>
      </details>
    </section>
  );
}

function Overview({ summary, channels, range, onRange, onChannel, onProviderStatus, showProviderStatus }: { summary: Summary; channels: Channel[]; range: TimeRange; onRange: (range: TimeRange) => void; onChannel: (channel: Channel) => void; onProviderStatus: () => void; showProviderStatus: boolean }) {
  const resourceAge = summary.resources.created_at ? Math.floor(Date.now() / 1000 - summary.resources.created_at) : null;
  const resourceThresholds = summary.resources.thresholds || { system_cpu: 85, system_memory: 85, system_disk: 80 };
  const requestDetail = summary.requests.collector_status === 'stale'
    ? t('日志采集已延迟 {{duration}}', { duration: formatElapsed(summary.requests.collector_age_seconds) })
    : `${t('平均')} ${summary.requests.average_seconds.toFixed(2)}s · ${summary.requests.total} ${t('次')}`;
  const resourceDetail = summary.resources.collector_status === 'stale'
    ? t('资源采集已延迟 {{duration}}', { duration: formatElapsed(summary.resources.collector_age_seconds || 0) })
    : resourceAge == null ? t('等待资源样本') : `${resourceAge}s ${t('前更新')} · DISK ${formatPercent(summary.resources.system_disk)}`;
  return (
    <>
      {summary.channel_sync?.status === 'stale' && <div className="channel-sync-warning" role="alert"><span><AlertTriangle size={19} /></span><div><strong>{t('渠道清单同步中断')}</strong><p>{t('当前展示的是最近一次成功同步的历史快照，渠道数量与启用状态可能已经变化。')}</p><small>{t('同步链路已异常 {{duration}}', { duration: formatElapsed(summary.channel_sync.age_seconds) })}</small></div>{summary.channel_sync.last_error && <code title={summary.channel_sync.last_error}>{summary.channel_sync.last_error}</code>}</div>}
      <section className="metrics-grid">
        <MetricCard icon={<CheckCircle2 />} label={t("渠道健康")} value={`${summary.channels.healthy}/${summary.channels.total}`} detail={`${summary.channels.failed} ${t('异常')} · ${summary.channels.delayed} ${t('延迟')} · ${summary.channels.unknown} ${t('未知')}`} tone={summary.channels.failed ? 'bad' : summary.channels.delayed || summary.channels.unknown ? 'warn' : 'ok'} />
        <MetricCard icon={<Clock3 />} label={t("24H 请求耗时")} value={`P95 ${summary.requests.p95_seconds.toFixed(2)}s`} detail={requestDetail} tone={summary.requests.collector_status === 'stale' || summary.requests.slow ? 'warn' : 'neutral'} />
        <MetricCard icon={<AlertTriangle />} label={t("慢请求")} value={`${summary.requests.slow}`} detail={`${t('总耗时')} / ${t('首字')} > ${summary.requests.slow_after_seconds}s · ${summary.requests.slow_ratio.toFixed(1)}%`} tone={summary.requests.slow ? 'warn' : 'ok'} />
        <MetricCard icon={<Server />} label={t("机器资源")} value={`MEM ${formatPercent(summary.resources.system_memory)}`} detail={resourceDetail} tone={summary.resources.collector_status === 'stale' ? 'warn' : (summary.resources.system_memory || 0) > resourceThresholds.system_memory || (summary.resources.system_disk || 0) > resourceThresholds.system_disk ? 'bad' : 'neutral'} />
      </section>
      <div className="section-heading channel-section-heading"><div><span className="eyebrow">LIVE CHANNEL MATRIX</span><h2>{t("渠道运行状态")}</h2><TimeRangeControl compact value={range} onChange={onRange} /></div><div className="channel-heading-aside">{showProviderStatus && summary.provider_status && <ProviderStatusHint status={summary.provider_status} onOpen={onProviderStatus} />}<div className="legend"><span><i className="legend-ok" />{t("正常")}</span><span><i className="legend-warn" />{t("延迟")}</span><span><i className="legend-bad" />{t("异常")}</span></div></div></div>
      <section className="channel-grid">
        {channels.map((channel) => <ChannelCard key={channel.channel_id} channel={channel} availabilityLabel={rangeLabel(range, t('当前保留历史'))} onOpen={() => onChannel(channel)} />)}
        {!channels.length && <div className="empty-state"><Database size={28} /><strong>{t("等待渠道同步")}</strong><span>{t("首次状态同步完成后将在这里展示可用渠道。")}</span></div>}
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
  const [modelQuery, setModelQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [range, setRange] = useState<TimeRange>(() => presetRange(7));
  const [retainedFromAt, setRetainedFromAt] = useState(0);
  const [retainedUntilAt, setRetainedUntilAt] = useState(0);
  const [excludedTokenNames, setExcludedTokenNames] = useState<string[]>([]);
  const [slowAfterSeconds, setSlowAfterSeconds] = useState(60);
  const [page, setPage] = useState(0);
  const requestController = useRef<AbortController | null>(null);
  const pageSize = 200;

  useEffect(() => {
    const timer = window.setTimeout(() => setModelQuery(model.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [model]);

  const load = useCallback(async () => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setLoading(true);
    setError('');
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(page * pageSize), slow_only: String(slowOnly) });
    appendDateRange(params, range);
    if (channelId) params.set('channel_id', channelId);
    if (modelQuery) params.set('model_name', modelQuery);
    try {
      const payload = await api<{ items: LogItem[]; total: number; retained_from_at: number; retained_until_at: number; excluded_token_names: string[]; slow_after_seconds: number }>(`logs?${params}`, { signal: controller.signal });
      setItems(payload.items);
      setTotal(payload.total);
      setRetainedFromAt(payload.retained_from_at || 0);
      setRetainedUntilAt(payload.retained_until_at || 0);
      setExcludedTokenNames(payload.excluded_token_names || []);
      setSlowAfterSeconds(payload.slow_after_seconds || 60);
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === 'AbortError') return;
      setError(requestError instanceof Error ? requestError.message : t('日志加载失败'));
    } finally {
      if (requestController.current === controller) setLoading(false);
    }
  }, [channelId, modelQuery, page, range, slowOnly]);

  useEffect(() => { setPage(0); }, [channelId, modelQuery, range, slowOnly]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => () => requestController.current?.abort(), []);
  return (
    <section>
      <div className="section-heading"><div><span className="eyebrow">REAL CONSUMPTION LOGS</span><h2>{t("真实使用日志耗时")}</h2></div><span className="source-note">{t('仅统计真实用户请求；已排除监控探测和模型测试')}</span></div>
      <div className="filter-bar">
        <TimeRangeControl value={range} onChange={setRange} />
        <label><span>{t("渠道")}</span><select value={channelId} onChange={(event) => setChannelId(event.target.value)}><option value="">{t("全部渠道")}</option>{channels.map((channel) => <option key={channel.channel_id} value={channel.channel_id}>{channel.name}</option>)}</select></label>
        <label><span>{t("模型精确匹配")}</span><div className="filter-input"><Search size={15} /><input value={model} onChange={(event) => setModel(event.target.value)} placeholder={t("例如 gpt-5.6-sol")} /></div></label>
        <label className="check-label"><input type="checkbox" checked={slowOnly} onChange={(event) => setSlowOnly(event.target.checked)} /><span>{t('只看超过 {{seconds}} 秒', { seconds: slowAfterSeconds })}</span></label>
        <button className="secondary-button" onClick={() => void load()}><RefreshCw className={loading ? 'spin' : ''} size={16} />{t("刷新")}</button>
      </div>
      {error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}
      <div className="table-shell">
        <div className="table-meta">{t("匹配")} {total} {t("条，当前页")} {items.length} {t("条")} · {retainedFromAt ? t('当前保留自 {{time}}', { time: formatTime(retainedFromAt, true) }) : t('等待首条日志')} · {retainedUntilAt ? t('最新 {{time}}', { time: formatTime(retainedUntilAt, true) }) : '—'}{excludedTokenNames.length ? ` · ${t('排除')} ${excludedTokenNames.join('、')}` : ''}</div>
        <div className="table-scroll"><table><thead><tr><th>{t("时间")}</th><th>{t("渠道 / 模型")}</th><th>{t("用户 / 令牌")}</th><th>{t("总耗时")}</th><th>{t("首字")}</th><th>{t("模式")}</th><th>{t("请求 ID")}</th></tr></thead><tbody>
          {items.map((item) => {
            const slow = item.use_time > slowAfterSeconds || (item.frt_ms || 0) > slowAfterSeconds * 1000;
            return <tr key={`${item.request_id}-${item.created_at}`} className={slow ? 'slow-row' : ''}><td className="mono">{formatTime(item.created_at, true)}</td><td><strong>{item.channel_name || `#${item.channel_id}`}</strong><span>{item.model_name}</span></td><td><strong>{item.username || '—'}</strong><span>{item.token_name || '—'}</span></td><td><b className={item.use_time > slowAfterSeconds ? 'text-bad' : ''}>{item.use_time.toFixed(2)}s</b></td><td><b className={(item.frt_ms || 0) > slowAfterSeconds * 1000 ? 'text-bad' : ''}>{formatDuration(item.frt_ms)}</b></td><td>{item.is_stream ? t('流式') : t('非流式')}</td><td className="mono request-id" title={item.request_id}>{item.request_id || '—'}</td></tr>;
          })}
          {!loading && !items.length && <tr><td colSpan={7}><div className="empty-row">{t("当前筛选条件下暂无日志")}</div></td></tr>}
        </tbody></table></div>
        <div className="console-pagination"><span>{page + 1} / {Math.max(1, Math.ceil(total / pageSize))}</span><div><button type="button" disabled={page === 0 || loading} onClick={() => setPage((value) => Math.max(0, value - 1))}>{t('上一页')}</button><button type="button" disabled={(page + 1) * pageSize >= total || loading} onClick={() => setPage((value) => value + 1)}>{t('下一页')}</button></div></div>
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
      setError(requestError instanceof Error ? requestError.message : t('Key 查询失败'));
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
  const keySlowAfterSeconds = result?.slow_after_seconds || 60;
  const expiryTone = !result?.usage.expires_at || result.usage.expires_at > Date.now() / 1000 ? 'ok' : 'bad';

  return <section className="key-usage-page">
    <div className="section-heading key-usage-heading"><div><span className="eyebrow">TOKEN INTELLIGENCE / ON DEMAND</span><h2>{t("Key 用量与调用详情")}</h2><p>{t("直接读取该 Key 在 New API 中的真实额度与最近调用，不依赖监控采集延迟。")}</p></div><span className="source-note"><ShieldCheck size={14} />{t("只读查询 · 不保存 Key")}</span></div>
    <form className="key-query-console" onSubmit={(event) => void query(event)}>
      <div className="key-query-mark"><Fingerprint size={24} /></div>
      <div className="key-query-copy"><strong>{t("输入需要核验的 API Key")}</strong><span>{t("Key 只通过服务端内存转发到已配置的 New API；不会进入 URL、数据库、审计记录或前端缓存。")}</span></div>
      <label className="key-query-input"><KeyRound size={18} /><input type={revealed ? 'text' : 'password'} value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="sk-••••••••••••••••" autoComplete="off" spellCheck={false} aria-label="API Key" /><button type="button" onClick={() => setRevealed((value) => !value)} title={revealed ? t('隐藏 Key') : t('显示 Key')}>{revealed ? <EyeOff size={17} /> : <Eye size={17} />}</button>{apiKey && <button type="button" onClick={clear} title={t("清空")}><X size={17} /></button>}</label>
      <button className="primary-button key-query-submit" type="submit" disabled={loading || apiKey.length < 4}>{loading ? <RefreshCw className="spin" size={17} /> : <Search size={17} />}{loading ? t('正在安全查询') : t('查询用量')}</button>
    </form>
    {error && <div className="inline-error key-query-error"><AlertTriangle size={16} />{error}</div>}
    {!result && !loading && <div className="key-usage-empty"><div><KeyRound size={28} /></div><strong>{t("一次查询，确认额度与调用轨迹")}</strong><p>{t("适合快速核验用户反馈、定位 Key 是否仍有额度、确认最近模型与请求耗时。")}</p><ul><li><CheckCircle2 size={14} />{t("实时额度")}</li><li><CheckCircle2 size={14} />{t("最近调用")}</li><li><CheckCircle2 size={14} />{t("Token 与耗时")}</li></ul></div>}
    {result && <>
      {!result.quota_per_unit_matches_config && <div className="console-inline-warning"><AlertTriangle size={16} />{t('额度换算使用 New API 实时单位 {{live}}；系统配置 {{configured}} 已忽略，请在系统配置中校准。', { live: numberText(result.quota_per_unit), configured: numberText(result.configured_quota_per_unit) })}</div>}
      <div className="key-result-identity">
        <div className="key-usage-ring" style={{ '--usage-progress': `${Math.min(100, usagePercentage) * 3.6}deg` } as React.CSSProperties}><span><b>{result.usage.unlimited_quota ? '∞' : formatPercent(result.usage.used_percentage)}</b><small>{t("已使用")}</small></span></div>
        <div className="key-result-title"><span className="eyebrow">VERIFIED TOKEN</span><h3>{result.usage.name}</h3><div><StatusPill tone={expiryTone}>{result.usage.expires_at ? (expiryTone === 'ok' ? t('有效至 {{time}}', { time: formatTime(result.usage.expires_at, true) }) : t('已过期')) : t('长期有效')}</StatusPill><span>{t("查询于")} {formatTime(result.queried_at, true)}</span></div></div>
        <div className="key-model-scope"><span>{t("模型权限")}</span><strong>{result.usage.model_limits_enabled ? `${Object.keys(result.usage.model_limits).length} ${t('个模型')}` : t('未限制')}</strong><small>{result.usage.model_limits_enabled ? Object.keys(result.usage.model_limits).slice(0, 3).join(' · ') : t('跟随账号与分组策略')}</small></div>
      </div>
      <div className="key-usage-metrics">
        <article><span><CircleDollarSign size={16} />{t("已使用额度")}</span><strong>{formatQuota(result.usage.total_used_display)}</strong><small>{t("原始额度")} {numberText(result.usage.total_used)}</small></article>
        <article><span><CircleGauge size={16} />{t("可用额度")}</span><strong>{result.usage.unlimited_quota ? t('不限额') : formatQuota(result.usage.total_available_display)}</strong><small>{result.usage.unlimited_quota ? t('此 Key 未设置额度上限') : t('总授予 {{quota}}', { quota: formatQuota(result.usage.total_granted_display) })}</small></article>
        <article><span><Activity size={16} />{t('最近 {{count}} 条汇总', { count: result.returned_calls })}</span><strong>{numberText(result.summary.calls)}</strong><small>{numberText(result.summary.total_tokens)} Tokens · {result.summary.models.length} {t("个模型")}</small></article>
        <article><span><TimerReset size={16} />{t("P95 总耗时")}</span><strong>{result.summary.p95_seconds.toFixed(2)}s</strong><small>{t("平均")} {result.summary.average_seconds.toFixed(2)}s</small></article>
      </div>
      <div className="key-call-workspace">
        <div className="key-call-list">
          <div className="key-call-toolbar"><div><strong>{t("最近调用详情")}</strong><span>{t("New API 返回")} {result.calls.length} {t("条 · 当前显示")} {calls.length} {t("条")}{result.logs_may_be_truncated ? ` · ${t('这是最近记录汇总，不代表累计调用总量')}` : ''}</span></div><label><Search size={15} /><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={t("模型、渠道、请求 ID")} />{filter && <button onClick={() => setFilter('')}><X size={14} /></button>}</label></div>
          <div className="key-call-table-scroll"><table className="key-call-table"><thead><tr><th>{t("时间")}</th><th>{t("模型 / 渠道")}</th><th>Tokens</th><th>{t("额度")}</th><th>{t("耗时")}</th><th>{t("模式")}</th><th /></tr></thead><tbody>{calls.map((item) => <tr key={`${item.id}-${item.request_id}`} className={selected?.id === item.id ? 'active' : ''} onClick={() => setSelected(item)}><td className="mono">{formatTime(item.created_at, true)}</td><td><strong>{item.model_name || t('未知模型')}</strong><span>{item.channel_name || t('渠道 #{{id}}', { id: item.channel_id })} · {item.group || 'default'}</span></td><td><b>{numberText(item.prompt_tokens + item.completion_tokens)}</b><span>{numberText(item.prompt_tokens)} + {numberText(item.completion_tokens)}</span></td><td><b>{formatQuota(item.quota_display)}</b></td><td><b className={item.use_time > keySlowAfterSeconds ? 'text-bad' : ''}>{item.use_time.toFixed(2)}s</b><span>{t("首字")} {formatDuration(item.frt_ms)}</span></td><td>{item.is_stream ? t('流式') : t('非流式')}</td><td><ChevronRight size={15} /></td></tr>)}{!calls.length && <tr><td colSpan={7}><div className="empty-row">{t("没有匹配的调用记录")}</div></td></tr>}</tbody></table></div>
        </div>
        <aside className="key-call-detail">
          {selected ? <><div className="key-detail-head"><span className="key-detail-icon"><TerminalSquare size={19} /></span><div><span>REQUEST INSPECTOR</span><h3>{selected.model_name || t('调用详情')}</h3></div><button onClick={() => setSelected(null)}><X size={16} /></button></div><dl><div><dt>{t("请求时间")}</dt><dd>{formatFullTime(selected.created_at)}</dd></div><div><dt>{t("渠道")}</dt><dd>{selected.channel_name || `#${selected.channel_id}`}</dd></div><div><dt>{t("总耗时 / 首字")}</dt><dd>{selected.use_time.toFixed(3)}s / {formatDuration(selected.frt_ms)}</dd></div><div><dt>Token</dt><dd>{numberText(selected.prompt_tokens)} {t("输入 +")} {numberText(selected.completion_tokens)} {t("输出")}</dd></div><div><dt>{t("计费额度")}</dt><dd>{formatQuota(selected.quota_display)} <small>({numberText(selected.quota)})</small></dd></div><div><dt>{t("请求模式")}</dt><dd>{selected.is_stream ? t('流式') : t('非流式')} · {selected.group || 'default'}</dd></div></dl><div className="key-request-id"><span>REQUEST ID</span><code>{selected.request_id || '—'}</code><button onClick={() => copyText(selected.request_id)} disabled={!selected.request_id}><Copy size={14} />{t("复制")}</button></div>{selected.upstream_request_id && <div className="key-request-id"><span>UPSTREAM REQUEST ID</span><code>{selected.upstream_request_id}</code><button onClick={() => copyText(selected.upstream_request_id)}><Copy size={14} />{t("复制")}</button></div>}{selected.content && <div className="key-call-content"><span>{t("New API 记录")}</span><p>{selected.content}</p></div>}</> : <div className="key-detail-empty"><TerminalSquare size={26} /><strong>{t("选择一条调用记录")}</strong><span>{t("查看请求 ID、Token 拆分、计费额度与精确耗时。")}</span></div>}
        </aside>
      </div>
    </>}
  </section>;
}

type ResourceField = 'system_cpu' | 'system_memory' | 'system_disk';

function MetricChart({ samples, field, color, label, description, threshold, icon, currentValue, currentLabel, rangeEndLabel, summary }: { samples: ResourceSample[]; field: ResourceField; color: string; label: string; description: string; threshold: number; icon: ReactNode; currentValue: number | null | undefined; currentLabel: string; rangeEndLabel: string; summary: ResourceMetricSummary | undefined }) {
  const width = 1000;
  const height = 250;
  const paddingY = 14;
  const plotHeight = height - paddingY * 2;
  const gradientId = useId().replace(/:/g, '');
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const values = samples.map((sample) => {
    const value = Number(sample[field]);
    return Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0;
  });
  const currentNumber = Number(currentValue);
  const current = currentValue == null || !Number.isFinite(currentNumber) ? null : Math.min(100, Math.max(0, currentNumber));
  const trendCurrent = values[values.length - 1];
  const previous = values[values.length - 2];
  const average = summary?.average ?? null;
  const peak = summary?.max ?? null;
  const low = summary?.min ?? null;
  const selectedIndex = activeIndex ?? Math.max(0, samples.length - 1);
  const selectedSample = samples[selectedIndex];
  const selectedValue = values[selectedIndex];
  const xAt = (index: number) => values.length <= 1 ? 0 : index / (values.length - 1) * width;
  const yAt = (value: number) => paddingY + (100 - value) / 100 * plotHeight;
  const linePath = values.map((value, index) => `${index === 0 ? 'M' : 'L'} ${xAt(index)} ${yAt(value)}`).join(' ');
  const areaPath = linePath ? `${linePath} L ${width} ${height} L 0 ${height} Z` : '';
  const thresholdY = yAt(threshold);
  const delta = trendCurrent != null && previous != null ? trendCurrent - previous : null;
  const tone = current == null ? 'muted' : current > threshold ? 'bad' : current >= threshold * .8 ? 'warn' : 'ok';
  const status = tone === 'bad' ? t('超过阈值') : tone === 'warn' ? t('接近阈值') : tone === 'ok' ? t('运行平稳') : t('等待数据');
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
        <div className="chart-current"><span className={`chart-state chart-state-${tone}`}>{currentLabel} · {status}</span><strong>{current == null ? '—' : `${current.toFixed(1)}%`}</strong><small className={delta != null && delta > 0 ? 'trend-up' : 'trend-down'}>{delta == null ? t('暂无趋势') : `${delta > 0 ? '↑' : delta < 0 ? '↓' : '→'} ${Math.abs(delta).toFixed(1)}% ${t('相邻时间桶变化')}`}</small></div>
      </header>
      <div className="chart-kpis"><span>{t("平均")} <b>{average == null ? '—' : `${average.toFixed(1)}%`}</b></span><span>{t("峰值")} <b>{peak == null ? '—' : `${peak.toFixed(1)}%`}</b></span><span>{t("最低")} <b>{low == null ? '—' : `${low.toFixed(1)}%`}</b></span><span>{t("阈值")} <b>{threshold}%</b></span></div>
      <div
        className="chart-stage"
        role="group"
        tabIndex={0}
        aria-label={`${label} ${t('历史曲线')} · ${currentLabel} ${current == null ? t('无数据') : `${current.toFixed(1)}%`}`}
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
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label} ${t('历史曲线')}`} preserveAspectRatio="none">
          <defs><linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity=".3" /><stop offset="100%" stopColor={color} stopOpacity="0" /></linearGradient></defs>
          {[25, 50, 75, 100].map((level) => <line key={level} x1="0" x2={width} y1={yAt(level)} y2={yAt(level)} className="chart-grid-line" />)}
          <line x1="0" x2={width} y1={thresholdY} y2={thresholdY} className="chart-threshold-line" />
          {areaPath && <path d={areaPath} fill={`url(#${gradientId})`} />}
          {linePath && <path d={linePath} fill="none" stroke={color} strokeWidth="3" vectorEffect="non-scaling-stroke" />}
          {selectedSample && <><line x1={xAt(selectedIndex)} x2={xAt(selectedIndex)} y1="0" y2={height} className="chart-crosshair" /><circle cx={xAt(selectedIndex)} cy={yAt(selectedValue)} r="7" fill={color} className="chart-point" vectorEffect="non-scaling-stroke" /></>}
        </svg>
        {selectedSample && activeIndex != null && <div className="chart-tooltip" style={{ left: `${Math.min(92, Math.max(8, xAt(selectedIndex) / width * 100))}%` }}><time>{formatFullTime(selectedSample.created_at)}</time><strong>{selectedValue.toFixed(1)}%</strong><span>{t('该时间桶平均值')} · {selectedValue > threshold ? t('已超过告警阈值') : t('距阈值 {{value}}%', { value: Math.max(0, threshold - selectedValue).toFixed(1) })}</span></div>}
      </div>
      <div className="chart-axis"><span>{samples.length ? formatTime(samples[0].created_at) : 'PAST'}</span><span>{samples.length > 2 ? formatTime(samples[Math.floor(samples.length / 2)].created_at) : ''}</span><span>{samples.length ? rangeEndLabel : '—'}</span></div>
    </article>
  );
}

function ResourcesView() {
  const [payload, setPayload] = useState<ResourcePayload | null>(null);
  const [error, setError] = useState('');
  const [range, setRange] = useState<TimeRange>(() => presetRange(1));
  const [loading, setLoading] = useState(false);
  const requestController = useRef<AbortController | null>(null);
  const liveRange = isLiveRange(range);
  const refreshMilliseconds = Math.max(5_000, (payload?.sampling_interval_seconds || 15) * 1000);
  const load = useCallback(async () => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setLoading(true);
    try {
      const parameters = appendDateRange(new URLSearchParams(), range);
      const response = await api<ResourcePayload>(`resources?${parameters.toString()}`, { signal: controller.signal });
      setPayload(response);
      setError('');
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === 'AbortError') return;
      setError(requestError instanceof Error ? requestError.message : t('资源加载失败'));
    } finally {
      if (requestController.current === controller) setLoading(false);
    }
  }, [range]);
  useEffect(() => {
    void load();
    if (!liveRange) return () => requestController.current?.abort();
    const timer = window.setInterval(() => void load(), refreshMilliseconds);
    return () => { window.clearInterval(timer); requestController.current?.abort(); };
  }, [liveRange, load, refreshMilliseconds]);
  const samples = payload?.samples || [];
  const bucketSeconds = payload?.bucket_seconds || 0;
  const latest = payload?.latest;
  const thresholds = payload?.thresholds || { system_cpu: 85, system_memory: 85, system_disk: 80 };
  const collectorStale = liveRange && payload?.collector_status === 'stale';
  const containers = latest?.containers || {};
  const resourceValues = [latest?.system_cpu, latest?.system_memory, latest?.system_disk]
    .map(Number)
    .filter(Number.isFinite);
  const highest = resourceValues.length ? Math.max(...resourceValues) : null;
  const pressure = latest ? Math.max(
    Number(latest.system_cpu || 0) / Math.max(1, thresholds.system_cpu),
    Number(latest.system_memory || 0) / Math.max(1, thresholds.system_memory),
    Number(latest.system_disk || 0) / Math.max(1, thresholds.system_disk),
  ) : null;
  const resourceTone = collectorStale ? 'warn' : pressure == null ? 'muted' : pressure > 1 ? 'bad' : pressure >= .8 ? 'warn' : 'ok';
  const resourceLabel = collectorStale
    ? t('机器资源采集已过期')
    : highest == null
      ? t('等待资源数据')
      : liveRange
        ? resourceTone === 'bad' ? t('资源压力较高') : resourceTone === 'warn' ? t('资源需要关注') : t('资源运行平稳')
        : resourceTone === 'bad' ? t('区间末样本超过阈值') : resourceTone === 'warn' ? t('区间末样本接近阈值') : t('区间末样本正常');
  const sampleLabel = liveRange ? t('当前原始样本') : t('区间末原始样本');
  const rangeEndLabel = liveRange ? t('现在') : t('区间结束');
  return (
    <section>
      <div className="section-heading resource-heading"><div><span className="eyebrow">HOST & CONTAINER TELEMETRY</span><h2>{t("机器资源")}</h2></div><div className="resource-controls"><span className="source-note"><i className={loading ? 'source-pulse source-pulse-loading' : 'source-pulse'} />{collectorStale ? t('资源采集已延迟 {{duration}}', { duration: formatElapsed(payload?.collector_age_seconds || 0) }) : range.mode === 'all' ? (payload?.actual_start ? t('当前保留自 {{time}} · {{count}} 个原始样本', { time: formatFullTime(payload.actual_start), count: payload.sample_count }) : t('等待资源数据')) : liveRange ? t('趋势按 {{value}} 分桶取平均 · 当前值与极值来自 {{count}} 个原始样本', { value: formatElapsed(bucketSeconds), count: payload?.sample_count || 0 }) : t('历史区间不自动刷新 · {{count}} 个原始样本', { count: payload?.sample_count || 0 })}{payload ? ` · ${t('样本覆盖 {{value}}%', { value: (payload.sample_coverage_ratio * 100).toFixed(1) })}` : ''}</span><TimeRangeControl compact value={range} onChange={setRange} /></div></div>
      {error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}
      <div className={`resource-insight resource-insight-${resourceTone}`}><div className="resource-insight-mark"><Activity size={22} /></div><div><span>RESOURCE SIGNAL</span><strong>{resourceLabel}</strong><small>{latest ? t('最后原始采样 {{time}} · 每 {{interval}} 采集', { time: formatFullTime(latest.created_at), interval: formatElapsed(payload?.sampling_interval_seconds || 0) }) : t('正在等待第一批资源样本')}</small></div><div className="resource-insight-score"><span>{sampleLabel}</span><strong>{highest == null ? '—' : `${highest.toFixed(1)}%`}</strong></div></div>
      <div className="metrics-grid resource-metrics"><MetricCard icon={<Cpu />} label="CPU" value={formatPercent(latest?.system_cpu)} detail={t('告警阈值 {{value}}%', { value: thresholds.system_cpu })} tone={(latest?.system_cpu || 0) > thresholds.system_cpu ? 'bad' : 'neutral'} /><MetricCard icon={<MemoryStick />} label={t("内存")} value={formatPercent(latest?.system_memory)} detail={`${t('可用 {{value}} GB', { value: latest?.system_available_mb != null ? (latest.system_available_mb / 1024).toFixed(2) : '—' })} · ${t('阈值 {{value}}%', { value: thresholds.system_memory })}`} tone={(latest?.system_memory || 0) > thresholds.system_memory ? 'bad' : 'neutral'} /><MetricCard icon={<HardDrive />} label={t("系统盘")} value={formatPercent(latest?.system_disk)} detail={t('告警阈值 {{value}}%', { value: thresholds.system_disk })} tone={(latest?.system_disk || 0) > thresholds.system_disk ? 'bad' : 'neutral'} /><MetricCard icon={<CircleGauge />} label="Swap" value={formatPercent(latest?.system_swap)} detail={t('最后采样 {{time}}', { time: formatTime(latest?.created_at || 0) })} /></div>
      <div className="chart-grid"><MetricChart samples={samples} field="system_cpu" color="#39df94" label={t("CPU 使用率")} description={t("计算负载与调度压力 · 曲线为桶平均")} threshold={thresholds.system_cpu} icon={<Cpu size={18} />} currentValue={latest?.system_cpu} currentLabel={sampleLabel} rangeEndLabel={rangeEndLabel} summary={payload?.summary?.system_cpu} /><MetricChart samples={samples} field="system_memory" color="#78a8ff" label={t("内存使用率")} description={t("物理内存实时占用 · 曲线为桶平均")} threshold={thresholds.system_memory} icon={<MemoryStick size={18} />} currentValue={latest?.system_memory} currentLabel={sampleLabel} rangeEndLabel={rangeEndLabel} summary={payload?.summary?.system_memory} /><MetricChart samples={samples} field="system_disk" color="#ffad32" label={t("系统盘使用率")} description={t("根分区存储容量 · 曲线为桶平均")} threshold={thresholds.system_disk} icon={<HardDrive size={18} />} currentValue={latest?.system_disk} currentLabel={sampleLabel} rangeEndLabel={rangeEndLabel} summary={payload?.summary?.system_disk} /></div>
      <div className="section-heading compact"><div><span className="eyebrow">DOCKER RUNTIME</span><h2>{t("容器状态")}</h2></div></div>
      <div className="container-grid">{Object.entries(containers).map(([name, metric]) => <ContainerCard key={name} name={name} metric={metric} />)}{!Object.keys(containers).length && <div className="empty-state"><Server size={26} /><strong>{t("暂无容器数据")}</strong><span>{t("检查 Docker 只读代理连接。")}</span></div>}</div>
    </section>
  );
}

function ContainerCard({ name, metric }: { name: string; metric: ContainerMetric }) {
  const healthy = metric.status === 'running' && !metric.oom_killed;
  return <article className="container-card"><div className="container-head"><div><Server size={18} /><strong>{name}</strong></div><StatusPill tone={healthy ? 'ok' : 'bad'}>{healthy ? t('运行中') : metric.status}</StatusPill></div><div className="container-stats"><span>CPU <b>{metric.cpu.toFixed(1)}%</b></span><span>MEM <b>{metric.memory_mb.toFixed(0)} MB</b></span><span>{t("重启")} <b>{metric.restarts}</b></span></div>{metric.error && <p>{metric.error}</p>}</article>;
}

const INCIDENT_CATEGORIES: Record<string, string> = {
  all: t('全部类型'),
  channel: t('渠道健康'),
  latency: t('请求耗时'),
  resource: t('机器资源'),
  container: t('容器状态'),
  service: t('服务可用性'),
  collector: t('采集器'),
  provider: t('上游官方状态'),
  other: t('其他'),
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
  const [range, setRange] = useState<TimeRange>(() => presetRange(7));
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [generatedAt, setGeneratedAt] = useState(0);
  const [retentionDays, setRetentionDays] = useState(365);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [acknowledgementNote, setAcknowledgementNote] = useState('');
  const [acknowledging, setAcknowledging] = useState(false);
  const requestController = useRef<AbortController | null>(null);
  const pageSize = 50;

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => { setPage(0); }, [status, severity, category, range, query]);

  const load = useCallback(async () => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setLoading(true);
    const parameters = new URLSearchParams({
      status,
      severity,
      category,
      limit: String(pageSize),
      offset: String(page * pageSize),
    });
    appendDateRange(parameters, range);
    if (query) parameters.set('q', query);
    try {
      const payload = await api<IncidentPayload>(`incidents?${parameters.toString()}`, { signal: controller.signal });
      setItems(payload.items);
      setSummary(payload.summary);
      setTotal(payload.total);
      setGeneratedAt(payload.generated_at);
      setRetentionDays(payload.retention_days || 365);
      setSelectedId((current) => payload.items.some((item) => item.id === current) ? current : payload.items[0]?.id ?? null);
      setError('');
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === 'AbortError') return;
      setError(requestError instanceof Error ? requestError.message : t('事件加载失败'));
    } finally {
      if (requestController.current === controller) setLoading(false);
    }
  }, [category, page, query, range, severity, status]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => { window.clearInterval(timer); requestController.current?.abort(); };
  }, [load]);

  const selected = items.find((item) => item.id === selectedId) || null;
  const clearFilters = () => {
    setStatus('all');
    setSeverity('all');
    setCategory('all');
    setRange(presetRange(7));
    setSearch('');
    setQuery('');
  };

  const acknowledge = async (incident: Incident) => {
    setAcknowledging(true);
    try {
      await api(`incidents/${incident.id}/acknowledge`, {
        method: 'POST',
        body: JSON.stringify({ note: acknowledgementNote }),
      });
      setAcknowledgementNote('');
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : t('事件确认失败'));
    } finally {
      setAcknowledging(false);
    }
  };

  return (
    <section className="incidents-view">
      <div className="section-heading incident-heading">
        <div><span className="eyebrow">INCIDENT OPERATIONS</span><h2>{t("事件调查中心")}</h2><p>{t("从告警信号定位触发原因，并完整追踪恢复过程。")}</p></div>
        <div className="incident-sync"><span><i className={loading ? 'source-pulse source-pulse-loading' : 'source-pulse'} />{t("30 秒自动刷新")}</span><small>{t('事件保留 {{days}} 天', { days: retentionDays })} · {t("数据时间")} {formatFullTime(generatedAt)}</small><button className="secondary-button" onClick={() => void load()} disabled={loading}><RefreshCw size={14} className={loading ? 'spin' : ''} />{t("立即刷新")}</button></div>
      </div>
      {error && <div className="inline-error"><AlertTriangle size={16} />{error}<button onClick={() => void load()}>{t("重试")}</button></div>}

      <div className="incident-kpis">
        <button className="incident-kpi incident-kpi-danger" onClick={() => { setStatus('open'); setSeverity('critical'); }}><span><BellRing size={17} />{t("未恢复严重事件")}</span><strong>{summary.critical_open}</strong><small>{t("需要优先处置")}</small></button>
        <button className="incident-kpi incident-kpi-warning" onClick={() => { setStatus('open'); setSeverity('warning'); }}><span><AlertTriangle size={17} />{t("未恢复警告")}</span><strong>{summary.warning_open}</strong><small>{t("共")} {summary.open} {t("个活跃事件")}</small></button>
        <button className="incident-kpi incident-kpi-ok" onClick={() => { setStatus('resolved'); setSeverity('all'); }}><span><CheckCircle2 size={17} />{t("24H 已恢复")}</span><strong>{summary.resolved_24h}</strong><small>{t("筛选范围内共")} {summary.resolved}</small></button>
        <div className="incident-kpi"><span><TimerReset size={17} />{t("平均恢复时间")}</span><strong>{formatElapsed(summary.average_resolution_seconds)}</strong><small>{t("基于已恢复事件")}</small></div>
      </div>

      <div className="incident-command-bar">
        <label className="incident-search"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t("搜索标题、原因、恢复信息或事件标识")} aria-label={t("搜索事件")} />{search && <button onClick={() => setSearch('')} title={t("清空搜索")}><X size={14} /></button>}</label>
        <div className="segmented incident-status-filter" aria-label={t("事件状态")}>{([['all', t('全部')], ['open', t('未恢复')], ['resolved', t('已恢复')]] as const).map(([value, label]) => <button key={value} className={status === value ? 'active' : ''} onClick={() => setStatus(value)}>{label}</button>)}</div>
        <label><span>{t("级别")}</span><select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="all">{t("全部级别")}</option><option value="critical">{t("严重")}</option><option value="warning">{t("警告")}</option><option value="info">{t("信息")}</option></select></label>
        <label><span>{t("类型")}</span><select value={category} onChange={(event) => setCategory(event.target.value)}>{Object.entries(INCIDENT_CATEGORIES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <TimeRangeControl compact value={range} onChange={setRange} />
        <button className="incident-clear" onClick={clearFilters}><X size={14} />{t("重置")}</button>
      </div>

      <div className="incident-workspace">
        <div className="incident-list-panel">
          <div className="incident-panel-head"><div><span>{t("事件队列")}</span><strong>{total} {t("个匹配结果")}</strong></div>{loading && <RefreshCw size={14} className="spin" />}</div>
          <div className="incident-queue">
            {items.map((item) => (
              <button className={classNames('incident-row', `incident-row-${item.severity}`, selectedId === item.id && 'active')} key={item.id} onClick={() => setSelectedId(item.id)}>
                <span className="incident-row-icon">{item.status === 'resolved' ? <CheckCircle2 size={17} /> : item.severity === 'critical' ? <XCircle size={17} /> : <AlertTriangle size={17} />}</span>
                <span className="incident-row-main"><span className="incident-row-top"><b>{item.title}</b><em>{item.status === 'resolved' ? t('已恢复') : t('未恢复')}</em></span><small>{INCIDENT_CATEGORIES[item.category] || t('其他')} · {formatTime(item.started_at, true)}</small><span className="incident-row-snippet">{item.body || (item.legacy_cause_missing ? t('历史事件未保留原始触发原因') : item.resolution_body || t('暂无诊断详情'))}</span></span>
                <ChevronRight size={15} className="incident-row-arrow" />
              </button>
            ))}
            {!items.length && <div className="incident-empty"><Inbox size={28} /><strong>{t("没有匹配的事件")}</strong><span>{t("尝试放宽时间范围或重置筛选条件。")}</span><button onClick={clearFilters}>{t("重置筛选")}</button></div>}
          </div>
          {total > pageSize && <div className="incident-pagination"><button disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>{t("上一页")}</button><span>{page + 1} / {Math.ceil(total / pageSize)}</span><button disabled={(page + 1) * pageSize >= total} onClick={() => setPage((value) => value + 1)}>{t("下一页")}</button></div>}
        </div>

        <div className="incident-detail-panel">
          {selected ? <>
            <div className="incident-detail-head">
              <div className={`incident-detail-mark incident-detail-${selected.severity}`}>{selected.status === 'resolved' ? <CheckCircle2 /> : selected.severity === 'critical' ? <XCircle /> : <AlertTriangle />}</div>
              <div><div className="incident-detail-tags"><span>{INCIDENT_CATEGORIES[selected.category] || t('其他')}</span><span className={`severity-${selected.severity}`}>{selected.severity === 'critical' ? t('严重') : selected.severity === 'warning' ? t('警告') : t('信息')}</span><span className={selected.status === 'resolved' ? 'resolved' : 'open'}>{selected.status === 'resolved' ? t('已恢复') : t('处理中')}</span></div><h3>{selected.title}</h3><p>{selected.status === 'resolved' ? t('事件持续 {{duration}}，当前已恢复。', { duration: formatElapsed(selected.duration_seconds) }) : t('事件已持续 {{duration}}，等待指标恢复到安全范围。', { duration: formatElapsed(selected.duration_seconds) })}</p></div>
            </div>

            <div className="incident-timeline" aria-label={t("事件时间线")}>
              <div className="complete"><span><CircleDot size={15} /></span><div><strong>{t("事件触发")}</strong><small>{formatFullTime(selected.started_at)}</small></div></div>
              <div className="complete"><span><BellRing size={15} /></span><div><strong>{t("最后一次告警通知")}</strong><small>{formatFullTime(selected.last_notified_at)}</small></div></div>
              <div className={selected.status === 'resolved' ? 'complete' : ''}><span><CheckCircle2 size={15} /></span><div><strong>{selected.status === 'resolved' ? t('指标恢复') : t('等待恢复')}</strong><small>{selected.resolved_at ? formatFullTime(selected.resolved_at) : t('最后更新 {{time}}', { time: formatFullTime(selected.updated_at) })}</small></div></div>
            </div>

            <div className={classNames('incident-ack-card', Boolean(selected.acknowledged_at) && 'acknowledged')}><div className="incident-ack-head"><div><ShieldCheck size={16} /><span><strong>{selected.acknowledged_at ? t('事件已确认') : t('确认事件')}</strong><small>{selected.acknowledged_at ? `${selected.acknowledged_by} · ${formatFullTime(selected.acknowledged_at)}` : t('确认表示运维人员已经知晓并开始处理，不会改变健康状态。')}</small></span></div>{selected.acknowledged_at && <CheckCircle2 size={18} />}</div>{selected.acknowledged_at ? <p>{selected.acknowledgement_note || t('未填写处理备注')}</p> : <div className="incident-ack-form"><input value={acknowledgementNote} maxLength={1000} placeholder={t('可选：记录负责人、排查动作或工单编号')} onChange={(event) => setAcknowledgementNote(event.target.value)} /><button type="button" className="primary-button" disabled={acknowledging} onClick={() => void acknowledge(selected)}>{acknowledging ? <RefreshCw className="spin" size={14} /> : <ShieldCheck size={14} />}{t('确认并记录')}</button></div>}</div>

            {selected.metadata?.provider && <div className="provider-incident-context">
              <div className="provider-incident-context-head"><div><Cloud size={17} /><span><strong>{t('官方事件上下文')}</strong><small>{selected.metadata.provider.toUpperCase()} · {selected.metadata.impact || selected.severity} · {selected.metadata.phase || selected.status}</small></span></div>{selected.metadata.source_url && <a href={selected.metadata.source_url} target="_blank" rel="noreferrer">{t('查看官方状态页')}<ExternalLink size={13} /></a>}</div>
              {selected.metadata.timeline?.length ? <div className="provider-official-timeline">{selected.metadata.timeline.map((update, index) => <article key={`${update.id || index}-${update.created_at}`}><span><i /></span><div><div><strong>{update.status}</strong><time>{formatFullTime(update.created_at)}</time></div><p>{update.body || t('OpenAI 尚未提供进一步说明')}</p></div></article>)}</div> : <p className="provider-timeline-empty">{t('当前事件未附带官方更新记录。')}</p>}
            </div>}

            <div className="incident-explanation-grid">
              <article className="incident-explanation cause"><div><AlertTriangle size={17} /><span>{t("为什么发生")}</span></div>{selected.legacy_cause_missing ? <p className="incident-legacy-note">{t("该事件由旧版本记录，恢复时曾覆盖原始告警内容，因此无法可靠还原触发原因。新事件已完整保留告警与恢复上下文。")}</p> : <pre>{selected.body || t('事件源未提供额外诊断内容，请结合事件标识和对应监控指标排查。')}</pre>}</article>
              <article className="incident-explanation recovery"><div><CheckCircle2 size={17} /><span>{t("为什么恢复")}</span></div>{selected.status === 'resolved' ? <pre>{selected.resolution_body || t('监控指标已重新满足健康条件，但事件源未提供详细恢复说明。')}</pre> : <p>{t("尚未恢复。系统会持续采样；当指标回到恢复阈值并通过状态判定后，会在这里记录恢复依据和时间。")}</p>}</article>
            </div>

            <div className="incident-technical"><div className="incident-technical-head"><span>{t("技术上下文")}</span><small>{t("用于日志检索与二次排查")}</small></div><dl><div><dt>{t("事件标识")}</dt><dd>{selected.incident_key}</dd></div><div><dt>{t("事件类型")}</dt><dd>{selected.kind}</dd></div><div><dt>{t("事件编号")}</dt><dd>#{selected.id}</dd></div><div><dt>{t("最后更新")}</dt><dd>{formatFullTime(selected.updated_at)}</dd></div></dl></div>
          </> : <div className="incident-detail-empty"><CircleDot size={32} /><strong>{t("选择一个事件开始调查")}</strong><span>{t("右侧将展示触发原因、恢复依据与完整时间线。")}</span></div>}
        </div>
      </div>
    </section>
  );
}

export default function App() {
  const [authState, setAuthState] = useState<'loading' | 'setup' | 'guest' | 'ready'>('loading');
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [route, setRoute] = useState<AppRoute>(() => readRoute());
  const [summary, setSummary] = useState<Summary | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [overviewRange, setOverviewRange] = useState<TimeRange>(() => presetRange(7));
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [countdown, setCountdown] = useState(REFRESH_SECONDS);
  const coreRequestController = useRef<AbortController | null>(null);
  const refreshSeconds = Math.max(2, user?.dashboard_refresh_seconds || REFRESH_SECONDS);
  const tab: Tab = route.tab;
  const coreRefreshSeconds = tab === 'overview' ? refreshSeconds : Math.max(30, refreshSeconds);
  const elevated = canAccessMonitorModules(user?.role || '');
  const customerView = user?.role === 'viewer';
  const enabledConsolePageList = useMemo(
    () => user?.console_available ? enabledConsolePages(user.console_pages || {}) : [],
    [user?.console_available, user?.console_pages],
  );

  const navigate = useCallback((nextRoute: AppRoute, replace = false) => {
    const path = routePath(nextRoute);
    if (replace) window.history.replaceState(null, '', path);
    else window.history.pushState(null, '', path);
    setRoute(nextRoute);
    window.scrollTo({ top: 0, behavior: 'auto' });
  }, []);

  useEffect(() => {
    api<SetupStatus>('setup/status').then((status) => {
      setSetupStatus(status);
      if (status.required) {
        const base = window.location.pathname.startsWith('/monitor') ? '/monitor' : '';
        window.history.replaceState(null, '', `${base}/setup`);
        setAuthState('setup');
        return;
      }
      api<AuthUser>('auth/me').then((result) => { setUser(result); setCountdown(result.dashboard_refresh_seconds || REFRESH_SECONDS); setAuthState('ready'); }).catch(() => setAuthState('guest'));
    }).catch(() => setAuthState('guest'));
  }, []);
  useEffect(() => {
    const onPopState = () => setRoute(readRoute());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const loadCore = useCallback(async () => {
    coreRequestController.current?.abort();
    const controller = new AbortController();
    coreRequestController.current = controller;
    setRefreshing(true);
    try {
      const channelParameters = appendDateRange(new URLSearchParams(), overviewRange);
      const needsChannelDetails = tab === 'overview' || tab === 'logs';
      const [summaryPayload, channelPayload] = await Promise.all([
        api<Summary>('dashboard/summary', { signal: controller.signal }),
        needsChannelDetails
          ? api<{ items: Channel[] }>(`channels?${channelParameters.toString()}`, { signal: controller.signal })
          : Promise.resolve(null),
      ]);
      setSummary(summaryPayload);
      if (channelPayload) {
        const enabledChannels = channelPayload.items.filter((channel) => channel.enabled);
        setChannels(enabledChannels);
        setSelectedChannel((current) => current
          ? enabledChannels.find((channel) => channel.channel_id === current.channel_id) || null
          : null);
      }
      setError('');
      setCountdown(coreRefreshSeconds);
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === 'AbortError') return;
      if (requestError instanceof ApiError && requestError.status === 401) setAuthState('guest');
      else setError(requestError instanceof Error ? requestError.message : t('监控数据加载失败'));
    } finally {
      if (coreRequestController.current === controller) setRefreshing(false);
    }
  }, [coreRefreshSeconds, overviewRange, tab]);

  useEffect(() => {
    if (authState !== 'ready' || tab === 'console' || (!elevated && tab !== 'overview')) return;
    void loadCore();
    const timer = window.setInterval(() => void loadCore(), coreRefreshSeconds * 1000);
    return () => { window.clearInterval(timer); coreRequestController.current?.abort(); };
  }, [authState, coreRefreshSeconds, elevated, loadCore, tab]);
  useEffect(() => {
    if (authState !== 'ready') return;
    const timer = window.setInterval(() => setCountdown((value) => value <= 1 ? coreRefreshSeconds : value - 1), 1000);
    return () => window.clearInterval(timer);
  }, [authState, coreRefreshSeconds]);
  useEffect(() => {
    if (!user) return;
    const consoleAllowed = tab === 'console'
      && user.console_available
      && enabledConsolePageList.includes(route.consolePage);
    const monitorAllowed = tab === 'overview' || (elevated && (
      tab === 'providerStatus'
      || (tab === 'keyUsage' && user.key_usage_available)
      || ['logs', 'resources', 'incidents', 'channels'].includes(tab)
      || (tab === 'deliveries' && user.role === 'admin')
      || (tab === 'settings' && user.role === 'admin')
    ));
    if (!consoleAllowed && !monitorAllowed) {
      navigate(defaultAuthorizedRoute(user.role, user.console_pages || {}), true);
    }
  }, [elevated, enabledConsolePageList, navigate, route.consolePage, tab, user]);

  const overall = useMemo(() => {
    const providerIssue = summary?.provider_status?.include_in_overall
      && buildProviderStatusContext(summary.provider_status).state === 'relevant-issue';
    const health = overallHealth(summary, Boolean(providerIssue));
    const label = health.state === 'syncing' ? t('正在同步') : health.state === 'critical' ? t('存在异常') : health.state === 'warning' ? t('需要关注') : t('运行正常');
    const detail = health.reason === 'channel-sync-stale'
      ? t('渠道清单同步中断')
      : health.reason === 'failed-channels'
        ? t('{{count}} 个可见渠道异常', { count: summary?.channels.failed || 0 })
        : health.reason === 'critical-incidents'
          ? t('{{count}} 个严重事件未恢复', { count: summary?.incidents.critical || 0 })
        : health.reason === 'provider-issue'
            ? t('业务相关官方组件异常')
            : health.reason === 'delayed-channels'
              ? t('{{count}} 个渠道探测延迟', { count: summary?.channels.delayed || 0 })
            : health.reason === 'warning-incidents'
              ? t('{{count}} 个关注事件未恢复', { count: summary?.incidents.warning || 0 })
              : health.reason === 'unknown-channels'
                ? t('{{count}} 个渠道等待有效探测', { count: summary?.channels.unknown || 0 })
                : health.reason === 'log-collector-stale'
                  ? t('使用日志采集已过期')
                  : health.reason === 'resource-collector-stale'
                    ? t('机器资源采集已过期')
                : health.reason === 'healthy'
                  ? t('全部可见渠道状态明确')
                  : t('正在读取权威数据源');
    return { ...health, label, detail };
  }, [summary]);

  async function logout() { await api('auth/logout', { method: 'POST' }).catch(() => undefined); setAuthState('guest'); setUser(null); setSummary(null); }
  if (authState === 'loading') return <div className="boot-screen"><Activity className="spin" /><span>{t("正在建立安全会话")}</span></div>;
  if (authState === 'setup' && setupStatus) return <SetupView status={setupStatus} onComplete={() => { const base = window.location.pathname.startsWith('/monitor') ? '/monitor/' : '/'; window.history.replaceState(null, '', base); setAuthState('guest'); }} />;
  if (authState === 'guest') return <Login onSuccess={(authenticatedUser) => { setUser(authenticatedUser); setCountdown(authenticatedUser.dashboard_refresh_seconds || REFRESH_SECONDS); setAuthState('ready'); }} />;

  const enabledConsolePageSet = new Set<ConsolePage>(
    enabledConsolePageList,
  );
  const primaryNavItems = [
    { id: 'monitor-overview', label: t('监控总览'), Icon: Activity, route: { tab: 'overview', settingsPage: 'status', consolePage: 'overview' } as AppRoute, visible: true },
    { id: 'console-overview', label: t('概览'), Icon: LayoutDashboard, route: { tab: 'console', settingsPage: 'status', consolePage: 'overview' } as AppRoute, visible: enabledConsolePageSet.has('overview') },
    { id: 'console-analytics', label: t('数据看板'), Icon: BarChart3, route: { tab: 'console', settingsPage: 'status', consolePage: 'analytics' } as AppRoute, visible: enabledConsolePageSet.has('analytics') },
    { id: 'console-keys', label: t('API 密钥'), Icon: KeyRound, route: { tab: 'console', settingsPage: 'status', consolePage: 'keys' } as AppRoute, visible: enabledConsolePageSet.has('keys') },
    { id: 'console-logs', label: t('使用日志'), Icon: ScrollText, route: { tab: 'console', settingsPage: 'status', consolePage: 'logs' } as AppRoute, visible: enabledConsolePageSet.has('logs') },
  ].filter((item) => item.visible);
  const monitorNavItems = [
    { id: 'keyUsage', label: t('Key 查询'), Icon: KeyRound, route: { tab: 'keyUsage', settingsPage: 'status', consolePage: 'overview' } as AppRoute, visible: elevated && Boolean(user?.key_usage_available) },
    { id: 'logs', label: t('监控日志'), Icon: Clock3, route: { tab: 'logs', settingsPage: 'status', consolePage: 'overview' } as AppRoute, visible: elevated },
    { id: 'resources', label: t('机器资源'), Icon: Cpu, route: { tab: 'resources', settingsPage: 'status', consolePage: 'overview' } as AppRoute, visible: elevated },
    { id: 'incidents', label: t('事件'), Icon: AlertTriangle, route: { tab: 'incidents', settingsPage: 'status', consolePage: 'overview' } as AppRoute, visible: elevated },
    { id: 'deliveries', label: t('告警投递'), Icon: Mail, route: { tab: 'deliveries', settingsPage: 'status', consolePage: 'overview' } as AppRoute, visible: user?.role === 'admin' },
    { id: 'channels', label: t('渠道配置'), Icon: SlidersHorizontal, route: { tab: 'channels', settingsPage: 'status', consolePage: 'overview' } as AppRoute, visible: elevated },
    { id: 'providerStatus', label: t('官方状态'), Icon: Cloud, route: { tab: 'providerStatus', settingsPage: 'status', consolePage: 'overview' } as AppRoute, visible: elevated && Boolean(summary?.provider_status) },
    { id: 'settings', label: t('系统配置'), Icon: Settings, route: { tab: 'settings', settingsPage: route.settingsPage, consolePage: 'overview' } as AppRoute, visible: user?.role === 'admin' },
  ].filter((item) => item.visible);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><div className="brand-mark"><Activity size={21} /></div><div><span>{customerView ? 'API' : 'NEW API'}</span><strong>{customerView ? 'CONSOLE' : 'MONITOR'}</strong></div></div>
        <div className="top-actions"><ThemeSwitch compact /><LanguageSwitch compact />{tab !== 'console' && <div className="refresh-state"><RefreshCw className={refreshing ? 'spin' : ''} size={14} /><span>{countdown}s</span></div>}<span className="user-chip">{user?.display_name || user?.username}<small>{user?.role}</small></span><button className="icon-button" onClick={() => void logout()} title={t("退出登录")}><LogOut size={17} /></button></div>
      </header>
      <div className="app-layout">
        <aside className="app-sidebar">
          <nav aria-label={t('监控导航')}>
            {primaryNavItems.length > 0 && <section className="app-nav-section" aria-label={customerView ? t('服务中心') : 'New API'}>
              <div className="app-nav-section-title"><span>{customerView ? t('服务中心') : 'New API'}</span><small>{primaryNavItems.length}</small></div>
              {primaryNavItems.map(({ id, label, Icon, route: itemRoute }) => <button key={id} className={tab === itemRoute.tab && (itemRoute.tab !== 'console' || route.consolePage === itemRoute.consolePage) ? 'active' : ''} onClick={() => navigate(itemRoute)}><span className="nav-icon"><Icon size={17} /></span><span>{label}</span></button>)}
            </section>}
            {monitorNavItems.length > 0 && <section className="app-nav-section" aria-label={t('监控')}>
              <div className="app-nav-section-title"><span>{t('监控')}</span><small>{monitorNavItems.length}</small></div>
              {monitorNavItems.map(({ id, label, Icon, route: itemRoute }) => <button key={id} className={tab === itemRoute.tab ? 'active' : ''} onClick={() => navigate(itemRoute)}><span className="nav-icon"><Icon size={17} /></span><span>{label}</span></button>)}
            </section>}
          </nav>
        </aside>
        <div className="app-canvas">
          <main className="content">{tab === 'console' && user?.console_available ? <ConsoleShell page={route.consolePage} pages={user.console_pages || {}} globalScope={Boolean(user.console_global_scope)} customerView={customerView} onNavigate={(consolePage) => navigate({ tab: 'console', settingsPage: 'status', consolePage })} /> : <><section className="hero"><div><div className="eyebrow">OPERATIONS / REAL-TIME</div><h1>{t("服务运行态势")}</h1><p>{t("真实渠道探测、真实消费日志、主机与容器资源。")}</p></div><div className={`overall-status overall-${overall.tone}`} title={summary ? `${overall.detail} · ${formatFullTime(summary.generated_at)}` : overall.detail}><span className="status-beacon" /><div><small>OVERALL STATUS</small><strong>{overall.label}</strong></div><span>{overall.detail}</span></div></section>
            {error && <div className="inline-error"><AlertTriangle size={16} />{error}<button onClick={() => void loadCore()}>{t("重试")}</button></div>}
            {summary ? <>{tab === 'overview' && <Overview summary={summary} channels={channels} range={overviewRange} onRange={setOverviewRange} onChannel={setSelectedChannel} showProviderStatus={elevated} onProviderStatus={() => navigate({ tab: 'providerStatus', settingsPage: 'status', consolePage: 'overview' })} />}{tab === 'providerStatus' && summary.provider_status && <ProviderStatusView status={summary.provider_status} summary={summary} onOverview={() => navigate({ tab: 'overview', settingsPage: 'status', consolePage: 'overview' })} />}{tab === 'providerStatus' && !summary.provider_status && <div className="empty-state provider-unavailable"><Cloud size={28} /><strong>{t('官方状态当前不可见')}</strong><span>{t('该功能可能已关闭，或当前角色没有查看权限。')}</span><button className="secondary-button" type="button" onClick={() => navigate({ tab: 'overview', settingsPage: 'status', consolePage: 'overview' })}>{t('返回渠道总览')}</button></div>}{tab === 'keyUsage' && user?.key_usage_available && <KeyUsageView />}{tab === 'logs' && elevated && <LogsView channels={channels} />}{tab === 'resources' && elevated && <ResourcesView />}{tab === 'incidents' && elevated && <IncidentsView />}{tab === 'deliveries' && user?.role === 'admin' && <DeliveriesView />}{tab === 'channels' && elevated && <ChannelSettingsView />}{tab === 'settings' && user?.role === 'admin' && <SettingsView activePage={route.settingsPage} onActivePageChange={(settingsPage) => navigate({ tab: 'settings', settingsPage, consolePage: 'overview' })} />}</> : <div className="loading-panel"><RefreshCw className="spin" /><span>{t("正在读取第一批监控数据")}</span></div>}</>}
          </main>
          <footer><span>{customerView ? t('数据源：真实调用、渠道探测与资源采集') : <>{t("数据源：New API 管理接口 / 真实 Relay 请求 / Linux")} & Docker / OpenAI Status</>}</span><span>{t('告警策略：渠道连续 5 次失败或 10 次内失败 5 次；20 次内 15 次首字超过 15 秒')}</span></footer>
        </div>
      </div>
      {selectedChannel && <DetailDrawer channel={selectedChannel} customerView={customerView} onClose={() => setSelectedChannel(null)} />}
    </div>
  );
}
