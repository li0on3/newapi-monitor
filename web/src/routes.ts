export type AppTab = 'overview' | 'console' | 'keyUsage' | 'logs' | 'resources' | 'incidents' | 'channels' | 'providerStatus' | 'settings';
export type ConsolePage = 'overview' | 'analytics' | 'keys' | 'logs';
export type SettingsPage = 'status' | 'overview' | 'notifications' | 'providers' | 'console' | 'connection' | 'keyUsage' | 'collection' | 'thresholds' | 'advanced' | 'access' | 'audit';

export type AppRoute = {
  tab: AppTab;
  settingsPage: SettingsPage;
  consolePage: ConsolePage;
};

const TAB_PATHS: Record<AppTab, string> = {
  overview: '',
  console: 'console',
  keyUsage: 'key-usage',
  logs: 'logs',
  resources: 'resources',
  incidents: 'incidents',
  channels: 'channels',
  providerStatus: 'upstream-status',
  settings: 'system',
};

const PATH_TABS = Object.fromEntries(Object.entries(TAB_PATHS).map(([tab, path]) => [path, tab])) as Record<string, AppTab>;
const SETTINGS_PATHS: Record<SettingsPage, string> = {
  status: 'status',
  overview: 'overview',
  notifications: 'notifications',
  providers: 'providers',
  console: 'console',
  connection: 'connection',
  keyUsage: 'key-usage',
  collection: 'collection',
  thresholds: 'thresholds',
  advanced: 'advanced',
  access: 'access',
  audit: 'audit',
};
const PATH_SETTINGS = Object.fromEntries(Object.entries(SETTINGS_PATHS).map(([page, path]) => [path, page])) as Record<string, SettingsPage>;
const CONSOLE_PATHS: Record<ConsolePage, string> = {
  overview: '',
  analytics: 'analytics',
  keys: 'keys',
  logs: 'logs',
};
const PATH_CONSOLE = Object.fromEntries(Object.entries(CONSOLE_PATHS).map(([page, path]) => [path, page])) as Record<string, ConsolePage>;

function routeSegments(pathname: string): string[] {
  const normalized = pathname.replace(/^\/monitor(?=\/|$)/, '').replace(/^\/+|\/+$/g, '');
  return normalized ? normalized.split('/') : [];
}

export function readRoute(pathname = window.location.pathname): AppRoute {
  const [page = '', detail] = routeSegments(pathname);
  const tab = PATH_TABS[page] || 'overview';
  const settingsPage = tab === 'settings' && detail && PATH_SETTINGS[detail]
    ? PATH_SETTINGS[detail]
    : 'status';
  const consolePage = tab === 'console' && detail && PATH_CONSOLE[detail]
    ? PATH_CONSOLE[detail]
    : 'overview';
  return { tab, settingsPage, consolePage };
}

export function routePath(route: AppRoute): string {
  const base = window.location.pathname === '/monitor' || window.location.pathname.startsWith('/monitor/') ? '/monitor' : '';
  const tabPath = TAB_PATHS[route.tab];
  if (route.tab === 'settings' && route.settingsPage !== 'status') return `${base}/${tabPath}/${SETTINGS_PATHS[route.settingsPage]}`;
  if (route.tab === 'console' && route.consolePage !== 'overview') return `${base}/${tabPath}/${CONSOLE_PATHS[route.consolePage]}`;
  return `${base}/${tabPath}` || '/';
}
