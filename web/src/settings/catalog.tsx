import { CircleGauge, Gauge, KeyRound, Network, RefreshCw, SlidersHorizontal, TerminalSquare } from 'lucide-react';
import type { ReactNode } from 'react';
import { t } from '../i18n';

export type SettingField = {
  key: string;
  label: string;
  type?: 'number' | 'text' | 'password' | 'boolean' | 'select';
  options?: Array<[string, string]>;
  hint?: string;
};

export type SettingSectionId = 'connection' | 'cpaQuota' | 'console' | 'keyUsage' | 'collection' | 'thresholds' | 'advanced';

export const SECRET_SETTING_KEYS = [
  'new_api_access_token',
  'relay_api_token',
  'smtp_password',
  'wecom_app_secret',
  'wecom_webhook_url',
  'feishu_app_secret',
  'feishu_webhook_url',
  'feishu_webhook_secret',
  'cpa_management_key',
];

export const SETTING_SECTIONS: Array<{
  id: SettingSectionId;
  title: string;
  short: string;
  description: string;
  icon: ReactNode;
  fields: SettingField[];
}> = [
  { id: 'connection', title: t('New API 连接'), short: t('连接与凭据'), icon: <Network size={18} />, description: t('管理接口只读同步与真实探测凭据。敏感字段不会回显。'), fields: [
    { key: 'new_api_base_url', label: t('New API 地址') }, { key: 'new_api_user_id', label: t('管理用户 ID'), type: 'number' }, { key: 'new_api_access_token', label: t('管理访问令牌'), type: 'password', hint: t('留空保持原值') }, { key: 'relay_api_token', label: t('真实探测令牌'), type: 'password', hint: t('留空保持原值') },
  ] },
  { id: 'cpaQuota', title: t('CPA 周配额'), short: t('独立容量监控'), icon: <Gauge size={18} />, description: t('独立读取 CLIProxyAPI / API Management 的周配额，不参与 New API 渠道总览。页面与接口仅管理员可见。'), fields: [
    { key: 'cpa_quota_enabled', label: t('启用 CPA 周配额监控'), type: 'boolean', hint: t('启用后按低频周期采集，关闭不会影响 CPA 或 New API') },
    { key: 'cpa_base_url', label: t('CPA 地址'), hint: t('推荐 http://host.docker.internal:8317') },
    { key: 'cpa_management_key', label: t('CPA 管理密钥'), type: 'password', hint: t('留空保持原值；只在服务端使用并加密保存') },
    { key: 'cpa_quota_activity_enabled', label: t('结合附近请求压力预测'), type: 'boolean', hint: t('读取 Token Usage Tracker 的近 1 小时与 6 小时数据，不改变 CPA 配置') },
    { key: 'cpa_quota_interval_seconds', label: t('配额采集间隔（秒）'), type: 'number', hint: t('默认 1800 秒；配额变化慢，无需高频请求') },
    { key: 'cpa_quota_timeout_seconds', label: t('单次请求超时（秒）'), type: 'number', hint: t('默认 30 秒，单账号失败不会阻断其他账号') },
    { key: 'cpa_quota_alert_remaining_percent', label: t('剩余配额告警阈值（%）'), type: 'number', hint: t('默认剩余 20% 时告警') },
    { key: 'cpa_quota_recovery_remaining_percent', label: t('恢复阈值（%）'), type: 'number', hint: t('默认 25%，通过回差避免阈值附近反复告警') },
    { key: 'cpa_quota_repeat_seconds', label: t('持续低配额重复提醒（秒）'), type: 'number', hint: t('默认 21600 秒，同一低配额事件最多每 6 小时提醒一次') },
  ] },
  { id: 'console', title: t('New API 功能页'), short: t('页面与访问策略'), icon: <TerminalSquare size={18} />, description: t('控制概览、数据看板、API 密钥和使用日志的可见范围与敏感操作频率。所有业务权限仍由 New API Session 最终校验。'), fields: [
    { key: 'console_enabled', label: t('启用 New API 功能页'), type: 'boolean', hint: t('关闭后入口和全部 BFF 接口同时停用') },
    { key: 'console_min_role', label: t('最低监控角色'), type: 'select', options: [['viewer', t('所有已登录用户')], ['operator', t('运维员及管理员')], ['admin', t('仅管理员')]], hint: t('监控角色只控制入口，不会提升 New API 原始权限') },
    { key: 'console_overview_enabled', label: t('显示账号概览'), type: 'boolean' },
    { key: 'console_analytics_enabled', label: t('显示数据看板'), type: 'boolean' },
    { key: 'console_keys_enabled', label: t('允许管理 API 密钥'), type: 'boolean', hint: t('密钥增删改始终使用当前用户的 New API 会话') },
    { key: 'console_logs_enabled', label: t('显示真实使用日志'), type: 'boolean' },
    { key: 'console_default_days', label: t('默认查询天数'), type: 'number', hint: t('1–30 天，普通用户单次最多查询 30 天') },
    { key: 'console_write_attempts_per_minute', label: t('每分钟写操作上限'), type: 'number', hint: t('限制密钥创建、编辑、停用与删除') },
    { key: 'console_reveal_attempts_per_minute', label: t('每分钟明文查看上限'), type: 'number', hint: t('建议保持较低，默认每分钟 6 次') },
  ] },
  { id: 'keyUsage', title: t('Key 用量查询'), short: t('权限与查询策略'), icon: <KeyRound size={18} />, description: t('按 Key 即时读取其额度与最近调用。Key 仅在当前请求中转发给 New API，不写入监控数据库、审计日志或浏览器地址。'), fields: [
    { key: 'key_usage_enabled', label: t('启用 Key 用量查询'), type: 'boolean', hint: t('关闭后入口和接口同时停用') },
    { key: 'key_usage_min_role', label: t('最低可用角色'), type: 'select', options: [['admin', t('仅管理员')], ['operator', t('运维员及管理员')]], hint: t('普通用户固定不可访问，避免 Key 信息泄露') },
    { key: 'key_usage_log_limit', label: t('单次返回调用数'), type: 'number', hint: t('10–500，New API 最多提供最近 1000 条') },
    { key: 'key_usage_attempts_per_minute', label: t('每用户每分钟查询次数'), type: 'number', hint: t('防止撞库、滥用与上游压力') },
    { key: 'key_usage_quota_per_unit', label: t('额度换算单位校验值'), type: 'number', hint: t('实际展示始终读取 New API /api/status；此值只用于发现配置漂移') },
  ] },
  { id: 'collection', title: t('采集频率'), short: t('同步与采样'), icon: <RefreshCw size={18} />, description: t('保存后监控工作线程将在数秒内热加载。'), fields: [
    { key: 'dashboard_refresh_seconds', label: t('页面刷新（秒）'), type: 'number' }, { key: 'channel_sync_interval_seconds', label: t('渠道同步（秒）'), type: 'number' }, { key: 'channel_interval_seconds', label: t('渠道探测（秒）'), type: 'number' }, { key: 'channel_probe_concurrency', label: t('渠道探测并发数'), type: 'number', hint: t('建议 2–3，避免探测阻塞采集器，同时限制上游瞬时压力') }, { key: 'log_interval_seconds', label: t('日志同步（秒）'), type: 'number' }, { key: 'resource_interval_seconds', label: t('资源采样（秒）'), type: 'number' }, { key: 'report_interval_seconds', label: t('周期报告（秒）'), type: 'number' }, { key: 'retention_days', label: t('原始采样保留（天）'), type: 'number', hint: t('延迟、渠道探测和资源原始数据的保留时间') }, { key: 'incident_retention_days', label: t('已恢复事件保留（天）'), type: 'number' }, { key: 'notification_retention_days', label: t('投递记录保留（天）'), type: 'number' },
  ] },
  { id: 'thresholds', title: t('耗时与资源阈值'), short: t('告警策略'), icon: <CircleGauge size={18} />, description: t('默认只外发真正影响体验的极端事件；其他指标继续记录在事件页，不主动打扰。'), fields: [
    { key: 'experience_alerts_only', label: t('仅外发体验严重告警'), type: 'boolean', hint: t('只发送渠道持续不可用、首字持续严重变慢及其恢复通知') }, { key: 'slow_request_seconds', label: t('慢请求展示阈值（秒）'), type: 'number', hint: t('仅用于页面标记和统计，不触发外部告警') }, { key: 'latency_first_response_seconds', label: t('首字严重延迟（秒）'), type: 'number' }, { key: 'latency_window_size', label: t('首字统计窗口（次）'), type: 'number' }, { key: 'latency_failure_threshold', label: t('窗口内严重延迟次数'), type: 'number', hint: t('默认最近 20 次中至少 15 次首字超过 15 秒') }, { key: 'latency_recovery_success_threshold', label: t('首字恢复连续正常次数'), type: 'number' }, { key: 'channel_consecutive_failure_threshold', label: t('渠道连续失败次数'), type: 'number', hint: t('默认连续 5 次全部失败才告警') }, { key: 'channel_failure_window_size', label: t('渠道失败统计窗口（次）'), type: 'number' }, { key: 'channel_failure_window_threshold', label: t('窗口内失败次数'), type: 'number', hint: t('默认最近 10 次中至少 5 次失败') }, { key: 'channel_recovery_success_threshold', label: t('渠道恢复连续成功次数'), type: 'number' }, { key: 'channel_slow_seconds', label: t('渠道慢探测展示（秒）'), type: 'number', hint: t('仅用于页面提示，不触发外部告警') }, { key: 'resource_sustain_seconds', label: t('资源持续时间（秒）'), type: 'number', hint: t('资源异常仍记录事件，但体验严重模式下不外发') }, { key: 'system_cpu_threshold', label: t('CPU 阈值（%）'), type: 'number' }, { key: 'system_memory_threshold', label: t('内存阈值（%）'), type: 'number' }, { key: 'system_disk_threshold', label: t('磁盘阈值（%）'), type: 'number' },
  ] },
  { id: 'advanced', title: t('高级采集'), short: t('范围与排除'), icon: <SlidersHorizontal size={18} />, description: t('日志重叠窗口、容器范围及排除项。'), fields: [
    { key: 'log_overlap_seconds', label: t('日志重叠窗口（秒）'), type: 'number' }, { key: 'log_initial_lookback_seconds', label: t('首次回溯（秒）'), type: 'number' }, { key: 'docker_container_names', label: t('容器名称（逗号分隔）') }, { key: 'disk_path', label: t('磁盘采集路径') }, { key: 'excluded_token_names', label: t('排除令牌名（逗号分隔）') }, { key: 'container_cpu_threshold', label: t('容器 CPU 阈值（%）'), type: 'number' }, { key: 'container_memory_threshold', label: t('容器内存阈值（%）'), type: 'number' }, { key: 'database_maintenance_interval_seconds', label: t('数据库维护间隔（秒）'), type: 'number' }, { key: 'database_max_mb', label: t('数据库容量告警（MB）'), type: 'number' }, { key: 'notification_max_attempts', label: t('告警投递最大尝试次数'), type: 'number', hint: t('失败后使用指数退避，达到上限后进入死信状态') },
  ] },
];
