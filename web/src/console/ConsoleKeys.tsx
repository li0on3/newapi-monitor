import {
  Activity,
  Check,
  ChevronRight,
  Coins,
  Copy,
  Eye,
  FolderCog,
  FolderOpen,
  KeyRound,
  Layers3,
  MoveRight,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldOff,
  Trash2,
  UserPlus,
  X,
} from 'lucide-react'
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { getLanguage, t } from '../i18n'
import { TimeRangeControl } from '../TimeRangeControl'
import { dateRangeQuery, presetRange, type TimeRange } from '../time-range'
import { consoleApi } from './api'
import { ConsoleBadge, ConsoleEmpty, ConsoleError, ConsoleLoading } from './ConsoleCommon'
import type {
  ConsoleKeyGroup,
  ConsoleKeyGroupColor,
  ConsoleKeyGroupWorkspace,
  ConsoleToken,
  ConsoleTokenDraft,
  ConsoleTokenPage,
} from './types'
import { quotaText } from './utils'

const EMPTY_DRAFT: ConsoleTokenDraft = {
  name: '', remain_quota: 500000, expired_time: -1, unlimited_quota: false,
  model_limits_enabled: false, model_limits: '', allow_ips: '', group: 'default',
  cross_group_retry: false,
}

const GROUP_COLORS: ConsoleKeyGroupColor[] = ['slate', 'emerald', 'blue', 'amber', 'violet', 'rose']

type GroupDraft = { id: number | null; name: string; color: ConsoleKeyGroupColor }
type GroupMemberEditor = { group: ConsoleKeyGroup; tokenIds: Set<number>; keyword: string }

function keyStatus(status: number) {
  if (status === 1) return { label: t('启用'), tone: 'green' as const }
  if (status === 2) return { label: t('停用'), tone: 'neutral' as const }
  if (status === 3) return { label: t('已过期'), tone: 'red' as const }
  if (status === 4) return { label: t('额度耗尽'), tone: 'amber' as const }
  return { label: t('未知'), tone: 'neutral' as const }
}

function dateTime(timestamp: number) {
  if (!timestamp) return t('从未使用')
  return new Intl.DateTimeFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(timestamp * 1000))
}

function expirationInput(timestamp: number) {
  if (timestamp < 0) return ''
  const date = new Date(timestamp * 1000)
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 16)
}

function integer(value: number) {
  return new Intl.NumberFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN').format(value || 0)
}

export function ConsoleKeys() {
  const [data, setData] = useState<ConsoleTokenPage | null>(null)
  const [workspace, setWorkspace] = useState<ConsoleKeyGroupWorkspace | null>(null)
  const [page, setPage] = useState(1)
  const [usageRange, setUsageRange] = useState<TimeRange>(() => presetRange(7))
  const [keyword, setKeyword] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [editor, setEditor] = useState<{ id: number | null; draft: ConsoleTokenDraft } | null>(null)
  const [options, setOptions] = useState<{ models: string[]; groups: string[]; quota_per_unit: number }>({ models: [], groups: [], quota_per_unit: 500000 })
  const [saving, setSaving] = useState(false)
  const [actionId, setActionId] = useState<number | null>(null)
  const [revealed, setRevealed] = useState<{ name: string; key: string } | null>(null)
  const [groupManager, setGroupManager] = useState(false)
  const [groupDraft, setGroupDraft] = useState<GroupDraft>({ id: null, name: '', color: 'blue' })
  const [assigning, setAssigning] = useState<{ tokenIds: number[]; groupIds: number[] } | null>(null)
  const [groupMembers, setGroupMembers] = useState<GroupMemberEditor | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    const [keyResult, groupResult] = await Promise.allSettled([
      consoleApi.keys({ page, page_size: 20, keyword: query }),
      consoleApi.keyGroups(dateRangeQuery(usageRange)),
    ])
    if (keyResult.status === 'fulfilled') {
      setData(keyResult.value)
      setSelected(new Set())
    }
    if (groupResult.status === 'fulfilled') setWorkspace(groupResult.value)
    const quotaPerUnit = groupResult.status === 'fulfilled'
      ? groupResult.value.quota_per_unit
      : keyResult.status === 'fulfilled'
        ? keyResult.value.quota_per_unit
        : 0
    if (quotaPerUnit) setOptions((current) => ({ ...current, quota_per_unit: quotaPerUnit }))
    const keyFailure = keyResult.status === 'rejected'
      ? keyResult.reason instanceof Error ? keyResult.reason.message : t('未知错误')
      : ''
    const groupFailure = groupResult.status === 'rejected'
      ? groupResult.reason instanceof Error ? groupResult.reason.message : t('未知错误')
      : ''
    if (keyFailure && groupFailure) setError(keyFailure)
    else if (keyFailure) setError(t('密钥列表加载失败：{{message}}', { message: keyFailure }))
    else if (groupFailure) setError(t('密钥列表可用，但分组用量加载失败：{{message}}', { message: groupFailure }))
    setLoading(false)
  }, [page, query, usageRange])

  useEffect(() => { void load() }, [load])
  const pageCount = Math.max(1, Math.ceil((data?.total || 0) / (data?.page_size || 20)))
  const allSelected = Boolean(data?.items.length) && data!.items.every((item) => selected.has(item.id))
  const quotaUnit = workspace?.quota_per_unit || options.quota_per_unit || 500000
  const modelSuggestions = useMemo(() => options.models.slice(0, 500), [options.models])
  const workspaceTokens = useMemo(
    () => Object.values(workspace?.token_usage || {}).sort((left, right) => left.token_name.localeCompare(right.token_name)),
    [workspace],
  )

  const openEditor = async (item?: ConsoleToken) => {
    setError('')
    try {
      const loadedOptions = !options.models.length && !options.groups.length
        ? await consoleApi.keyOptions()
        : options
      if (loadedOptions !== options) setOptions(loadedOptions)
      setEditor({
        id: item?.id ?? null,
        draft: item ? {
          name: item.name,
          remain_quota: item.remain_quota,
          expired_time: item.expired_time,
          unlimited_quota: item.unlimited_quota,
          model_limits_enabled: item.model_limits_enabled,
          model_limits: item.model_limits,
          allow_ips: item.allow_ips,
          group: item.group,
          cross_group_retry: item.cross_group_retry,
        } : { ...EMPTY_DRAFT, remain_quota: loadedOptions.quota_per_unit || 500000 },
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('读取密钥选项失败'))
    }
  }

  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!editor) return
    setSaving(true)
    setError('')
    try {
      if (editor.id == null) await consoleApi.createKey(editor.draft)
      else await consoleApi.updateKey(editor.id, editor.draft)
      setEditor(null)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('保存失败'))
    } finally {
      setSaving(false)
    }
  }

  const saveGroup = async (event: FormEvent) => {
    event.preventDefault()
    if (!groupDraft.name.trim()) return
    setSaving(true)
    setError('')
    try {
      const payload = { name: groupDraft.name.trim(), color: groupDraft.color }
      if (groupDraft.id == null) await consoleApi.createKeyGroup(payload)
      else await consoleApi.updateKeyGroup(groupDraft.id, payload)
      setGroupDraft({ id: null, name: '', color: 'blue' })
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('分组保存失败'))
    } finally {
      setSaving(false)
    }
  }

  const removeGroup = async (group: ConsoleKeyGroup) => {
    if (!window.confirm(t('删除分组“{{name}}”？密钥不会被删除，其他分组关系会保留。', { name: group.name }))) return
    setSaving(true)
    setError('')
    try {
      await consoleApi.deleteKeyGroup(group.id)
      if (groupDraft.id === group.id) setGroupDraft({ id: null, name: '', color: 'blue' })
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('分组删除失败'))
    } finally {
      setSaving(false)
    }
  }

  const assignGroup = async () => {
    if (!assigning) return
    setSaving(true)
    setError('')
    try {
      await consoleApi.assignKeyGroups(assigning.tokenIds, assigning.groupIds)
      setAssigning(null)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('密钥分组更新失败'))
    } finally {
      setSaving(false)
    }
  }

  const saveGroupMembers = async () => {
    if (!groupMembers) return
    setSaving(true)
    setError('')
    try {
      await consoleApi.updateKeyGroupMembers(groupMembers.group.id, [...groupMembers.tokenIds])
      setGroupMembers(null)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('分组成员更新失败'))
    } finally {
      setSaving(false)
    }
  }

  const updateStatus = async (item: ConsoleToken) => {
    setActionId(item.id)
    try {
      await consoleApi.updateKeyStatus(item.id, item.status === 1 ? 2 : 1)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('状态更新失败'))
    } finally {
      setActionId(null)
    }
  }

  const remove = async (item: ConsoleToken) => {
    if (!window.confirm(t('确定删除这个 API 密钥吗？此操作不可撤销。'))) return
    setActionId(item.id)
    try {
      await consoleApi.deleteKey(item.id)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('删除失败'))
    } finally {
      setActionId(null)
    }
  }

  const batchRemove = async () => {
    if (!selected.size || !window.confirm(t('确定删除选中的 API 密钥吗？此操作不可撤销。'))) return
    setSaving(true)
    try {
      await consoleApi.batchDeleteKeys([...selected])
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('批量删除失败'))
    } finally {
      setSaving(false)
    }
  }

  const reveal = async (item: ConsoleToken) => {
    setActionId(item.id)
    try {
      const result = await consoleApi.revealKey(item.id)
      setRevealed({ name: item.name, key: result.key })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('密钥读取失败'))
    } finally {
      setActionId(null)
    }
  }

  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    setPage(1)
    setQuery(keyword.trim())
  }

  return <div className="console-page console-keys-page">
    {workspace && <>
      <section className="console-metric-grid key-usage-metrics">
        <article className="console-metric console-metric-green"><span className="console-metric-icon"><KeyRound size={18} /></span><div><small>{t('个人密钥')}</small><strong>{integer(workspace.summary.keys)}</strong><p>{t('当前账号')}</p></div></article>
        <article className="console-metric console-metric-blue"><span className="console-metric-icon"><Activity size={18} /></span><div><small>{workspace.all_time ? t('当前密钥累计请求') : t('当前密钥 {{days}} 天请求', { days: workspace.days })}</small><strong>{integer(workspace.summary.requests)}</strong><p>{t('{{models}} 个调用模型', { models: workspace.summary.models })}</p></div></article>
        <article className="console-metric console-metric-amber"><span className="console-metric-icon"><Coins size={18} /></span><div><small>{workspace.all_time ? t('当前密钥累计用量') : t('当前密钥 {{days}} 天用量', { days: workspace.days })}</small><strong>{quotaText(workspace.summary.quota, quotaUnit)}</strong><p>{t('{{tokens}} Tokens', { tokens: integer(workspace.summary.tokens) })}</p></div></article>
        <article className="console-metric"><span className="console-metric-icon"><Layers3 size={18} /></span><div><small>{t('密钥分组')}</small><strong>{integer(workspace.summary.groups)}</strong><p>{t('{{count}} 个密钥未分组', { count: workspace.ungrouped.key_count })}</p></div></article>
      </section>

      <section className="console-panel key-group-overview">
        <div className="console-panel-head key-group-overview-head">
          <div><span className="eyebrow">KEY USAGE GROUPS</span><h3>{t('分组密钥用量')}</h3><p>{t('点击任意分组即可添加或移除密钥；一个密钥可以同时属于多个分组。')}</p></div>
          <div className="key-group-head-actions"><TimeRangeControl compact value={usageRange} onChange={setUsageRange} /><button type="button" onClick={() => setGroupManager(true)}><FolderCog size={14} />{t('管理分组')}</button></div>
        </div>
        <div className="key-group-card-grid">
          <article className="key-group-card key-group-card-slate"><div className="key-group-card-title"><span className="key-group-color-dot" /><div><strong>{t('未分组')}</strong><small>{t('个人或尚未归类的密钥')}</small></div><b>{workspace.ungrouped.key_count}</b></div><div className="key-group-card-usage"><span><small>{t('请求')}</small><strong>{integer(workspace.ungrouped.usage.requests)}</strong></span><span><small>{t('用量')}</small><strong>{quotaText(workspace.ungrouped.usage.quota, quotaUnit)}</strong></span></div></article>
          {workspace.groups.map((group) => <button className={`key-group-card key-group-card-action key-group-card-${group.color}`} type="button" key={group.id} onClick={() => setGroupMembers({ group, tokenIds: new Set(workspaceTokens.filter((token) => token.key_group_ids.includes(group.id)).map((token) => token.token_id)), keyword: '' })}><div className="key-group-card-title"><span className="key-group-color-dot" /><div><strong>{group.name}</strong><small>{t('{{count}} 个密钥 · 点击管理成员', { count: group.key_count })}</small></div><b>{group.key_count}</b></div><div className="key-group-card-usage"><span><small>{t('请求')}</small><strong>{integer(group.usage.requests)}</strong></span><span><small>{t('用量')}</small><strong>{quotaText(group.usage.quota, quotaUnit)}</strong></span></div><span className="key-group-card-cta"><UserPlus size={13} />{t('添加密钥')}<ChevronRight size={13} /></span></button>)}
        </div>
        <div className="key-group-attribution"><Activity size={13} /><span>{t('用量来自 New API 小时归集；最新请求可能稍后出现。分组按当前成员关系计算，同一密钥属于多个分组时会分别计入，因此分组之间不可直接相加。')}{workspace.excluded_deleted_key_usage.requests > 0 ? ` ${t('另有 {{count}} 条已删除密钥的历史请求未计入当前密钥总计。', { count: workspace.excluded_deleted_key_usage.requests })}` : ''}</span></div>
      </section>
    </>}

    <section className="console-toolbar">
      <form className="console-search" onSubmit={submitSearch}><Search size={16} /><input value={keyword} maxLength={128} placeholder={t('按密钥名称搜索')} onChange={(event) => setKeyword(event.target.value)} /><button type="submit">{t('搜索')}</button></form>
      <div className="console-toolbar-actions">
        {selected.size > 0 && <button className="secondary-button" type="button" disabled={saving} onClick={() => setAssigning({ tokenIds: [...selected], groupIds: [] })}><MoveRight size={15} />{t('设置分组')} ({selected.size})</button>}
        {selected.size > 0 && <button className="secondary-button danger" type="button" disabled={saving} onClick={() => void batchRemove()}><Trash2 size={15} />{t('删除选中')} ({selected.size})</button>}
        <button className="primary-button" type="button" onClick={() => void openEditor()}><Plus size={16} />{t('创建 API 密钥')}</button>
      </div>
    </section>
    {error && <div className="console-inline-warning">{error}<button type="button" onClick={() => setError('')}><X size={14} /></button></div>}
    {loading && !data ? <ConsoleLoading /> : !data ? <ConsoleError message={error} retry={() => void load()} /> : <section className="console-panel console-table-panel">
      <div className="console-panel-head"><div><span className="eyebrow">API CREDENTIALS</span><h3>{t('个人密钥与用量')}</h3><p>{t('密钥操作即时生效；本平台只保存自定义分组关系，不保存密钥明文。')}</p></div><ConsoleBadge tone="blue">{data.total}</ConsoleBadge></div>
      {data.items.length ? <div className="console-table-scroll"><table className="console-table console-key-usage-table"><thead><tr><th><input aria-label={t('全选')} type="checkbox" checked={allSelected} onChange={(event) => setSelected(event.target.checked ? new Set(data.items.map((item) => item.id)) : new Set())} /></th><th>{t('名称与密钥')}</th><th>{t('状态')}</th><th>{t('密钥分组')}</th><th>{workspace?.all_time ? t('累计/历史总量') : t('{{days}} 天用量', { days: workspace?.days || 7 })}</th><th>{t('额度')}</th><th>{t('模型与路由')}</th><th>{t('最后使用')}</th><th>{t('操作')}</th></tr></thead><tbody>{data.items.map((item) => {
        const status = keyStatus(item.status)
        const usage = workspace?.token_usage[String(item.id)]
        return <tr key={item.id}>
          <td><input aria-label={`${t('选择')} ${item.name}`} type="checkbox" checked={selected.has(item.id)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(item.id); else next.delete(item.id); return next })} /></td>
          <td><strong>{item.name}</strong><code>{item.masked_key || t('密钥已隐藏')}</code></td>
          <td><ConsoleBadge tone={status.tone}>{status.label}</ConsoleBadge>{item.expired_time > 0 && <small>{dateTime(item.expired_time)}</small>}</td>
          <td><button className="key-group-pill-list" type="button" onClick={() => setAssigning({ tokenIds: [item.id], groupIds: usage?.key_group_ids || [] })}>{usage?.key_groups.length ? usage.key_groups.slice(0, 3).map((group) => <span className={`key-group-pill key-group-pill-${group.color}`} key={group.id}><i />{group.name}</span>) : <span className="key-group-pill key-group-pill-slate"><i />{t('未分组')}</span>}{(usage?.key_groups.length || 0) > 3 && <b>+{usage!.key_groups.length - 3}</b>}<Pencil size={11} /></button></td>
          <td><strong>{integer(usage?.requests || 0)} {t('次请求')}</strong><small>{quotaText(usage?.quota || 0, quotaUnit)} · {integer(usage?.tokens || 0)} Tokens</small></td>
          <td><strong>{item.unlimited_quota ? t('不限额') : quotaText(item.remain_quota, quotaUnit)}</strong><small>{item.unlimited_quota ? t('不限制可用额度') : `${t('累计已用')} ${quotaText(item.used_quota, quotaUnit)}`}</small></td>
          <td><strong>{item.model_limits_enabled ? `${item.model_limits.split(',').filter(Boolean).length} ${t('个模型')}` : t('全部模型')}</strong><small>{t('路由/计费分组')}: {item.group || t('默认分组')}</small></td>
          <td><strong>{dateTime(item.accessed_time)}</strong><small>{t('创建于')} {dateTime(item.created_time)}</small></td>
          <td><div className="console-row-actions"><button type="button" title={t('查看密钥')} disabled={actionId === item.id} onClick={() => void reveal(item)}><Eye size={15} /></button><button type="button" title={t('编辑')} onClick={() => void openEditor(item)}><Pencil size={15} /></button><button type="button" title={item.status === 1 ? t('停用') : t('启用')} disabled={actionId === item.id || item.status > 2} onClick={() => void updateStatus(item)}><ShieldOff size={15} /></button><button className="danger" type="button" title={t('删除')} disabled={actionId === item.id} onClick={() => void remove(item)}><Trash2 size={15} /></button></div></td>
        </tr>
      })}</tbody></table></div> : <ConsoleEmpty title={query ? t('没有匹配的 API 密钥') : t('暂无 API 密钥')} detail={query ? t('换一个名称关键词再试。') : t('创建第一个密钥，为客户端分配独立凭据。')} />}
      <div className="console-pagination"><span>{t('第 {{page}}/{{pages}} 页 · {{total}} 条记录', { page: data.page, pages: pageCount, total: data.total })}</span><div><button type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}>{t('上一页')}</button><button type="button" disabled={page >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>{t('下一页')}</button></div></div>
    </section>}

    {groupManager && <div className="console-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setGroupManager(false) }}><section className="console-modal key-group-manager" role="dialog" aria-modal="true"><div className="console-modal-head"><div><span className="eyebrow">KEY GROUP MANAGER</span><h3>{t('管理密钥分组')}</h3><p>{t('分组数据仅保存在本平台，并按当前账号隔离。')}</p></div><button type="button" disabled={saving} onClick={() => setGroupManager(false)}><X size={18} /></button></div><div className="key-group-manager-body"><form className="key-group-form" onSubmit={saveGroup}><label><span>{groupDraft.id == null ? t('新建分组') : t('编辑分组')}</span><input autoFocus maxLength={48} required value={groupDraft.name} placeholder={t('例如：客户项目、内部工具')} onChange={(event) => setGroupDraft({ ...groupDraft, name: event.target.value })} /></label><div className="key-group-color-picker" aria-label={t('分组颜色')}>{GROUP_COLORS.map((color) => <button aria-label={color} className={`${color} ${groupDraft.color === color ? 'active' : ''}`} key={color} type="button" onClick={() => setGroupDraft({ ...groupDraft, color })}><span /></button>)}</div><div className="key-group-form-actions">{groupDraft.id != null && <button className="secondary-button" type="button" onClick={() => setGroupDraft({ id: null, name: '', color: 'blue' })}>{t('取消编辑')}</button>}<button className="primary-button" disabled={saving || !groupDraft.name.trim()} type="submit">{saving ? <RefreshCw className="spin" size={14} /> : <Plus size={14} />}{groupDraft.id == null ? t('创建分组') : t('保存分组')}</button></div></form><div className="key-group-manager-list">{workspace?.groups.length ? workspace.groups.map((group) => <article key={group.id}><span className={`key-group-manager-color ${group.color}`} /><div><strong>{group.name}</strong><small>{t('{{count}} 个密钥 · {{requests}} 次请求', { count: group.key_count, requests: integer(group.usage.requests) })}</small></div><button type="button" title={t('编辑')} onClick={() => setGroupDraft({ id: group.id, name: group.name, color: group.color })}><Pencil size={14} /></button><button className="danger" type="button" title={t('删除')} onClick={() => void removeGroup(group)}><Trash2 size={14} /></button></article>) : <div className="key-group-manager-empty"><FolderOpen size={24} /><span>{t('还没有自定义分组')}</span></div>}</div><div className="key-group-manager-note">{t('删除分组不会删除任何 API 密钥；密钥仍会保留在其他分组中。')}</div></div></section></div>}

    {assigning && <div className="console-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setAssigning(null) }}><section className="console-modal key-group-assign-modal" role="dialog" aria-modal="true"><div className="console-modal-head"><div><span className="eyebrow">ASSIGN KEY GROUPS</span><h3>{t('设置密钥分组')}</h3><p>{t('为 {{count}} 个密钥选择一个或多个统计分组；不会改变路由、计费或密钥权限。', { count: assigning.tokenIds.length })}</p></div><button type="button" disabled={saving} onClick={() => setAssigning(null)}><X size={18} /></button></div><div className="key-group-assignment-toolbar"><span>{t('已选择 {{count}} 个分组', { count: assigning.groupIds.length })}</span><button type="button" onClick={() => setAssigning({ ...assigning, groupIds: [] })}>{t('清空选择')}</button></div><div className="key-group-assignment-list">{workspace?.groups.map((group) => { const active = assigning.groupIds.includes(group.id); return <button className={`${group.color} ${active ? 'active' : ''}`} key={group.id} type="button" onClick={() => setAssigning({ ...assigning, groupIds: active ? assigning.groupIds.filter((id) => id !== group.id) : [...assigning.groupIds, group.id] })}><span /><div><strong>{group.name}</strong><small>{t('当前 {{count}} 个密钥', { count: group.key_count })}</small></div>{active && <Check size={15} />}</button> })}</div><div className="console-modal-actions"><button className="secondary-button" type="button" disabled={saving} onClick={() => setAssigning(null)}>{t('取消')}</button><button className="primary-button" type="button" disabled={saving} onClick={() => void assignGroup()}>{saving ? <RefreshCw className="spin" size={15} /> : <Check size={15} />}{t('保存分组设置')}</button></div></section></div>}

    {groupMembers && <div className="console-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setGroupMembers(null) }}><section className="console-modal key-group-members-modal" role="dialog" aria-modal="true"><div className="console-modal-head"><div><span className="eyebrow">GROUP MEMBERS</span><h3>{groupMembers.group.name}</h3><p>{t('点击密钥即可加入或移出该分组；其他分组关系不会受影响。')}</p></div><button type="button" disabled={saving} onClick={() => setGroupMembers(null)}><X size={18} /></button></div><div className="key-group-member-toolbar"><label><Search size={15} /><input value={groupMembers.keyword} placeholder={t('搜索密钥名称')} onChange={(event) => setGroupMembers({ ...groupMembers, keyword: event.target.value })} /></label><span>{t('已选择 {{count}}/{{total}}', { count: groupMembers.tokenIds.size, total: workspaceTokens.length })}</span></div><div className="key-group-member-list">{workspaceTokens.filter((token) => token.token_name.toLowerCase().includes(groupMembers.keyword.trim().toLowerCase())).map((token) => { const active = groupMembers.tokenIds.has(token.token_id); return <button className={active ? 'active' : ''} type="button" key={token.token_id} onClick={() => setGroupMembers((current) => { if (!current) return current; const tokenIds = new Set(current.tokenIds); if (tokenIds.has(token.token_id)) tokenIds.delete(token.token_id); else tokenIds.add(token.token_id); return { ...current, tokenIds } })}><span className="key-member-check">{active && <Check size={13} />}</span><span><strong>{token.token_name || `#${token.token_id}`}</strong><small>{integer(token.requests)} {t('次请求')} · {quotaText(token.quota, quotaUnit)}</small></span><em>{token.key_groups.map((group) => group.name).join(' · ') || t('未分组')}</em></button> })}</div><div className="console-modal-actions"><button className="secondary-button" type="button" disabled={saving} onClick={() => setGroupMembers(null)}>{t('取消')}</button><button className="primary-button" type="button" disabled={saving} onClick={() => void saveGroupMembers()}>{saving ? <RefreshCw className="spin" size={15} /> : <Check size={15} />}{t('保存成员')}</button></div></section></div>}

    {editor && <div className="console-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setEditor(null) }}><form className="console-modal console-key-editor" onSubmit={save}><div className="console-modal-head"><div><span className="eyebrow">{editor.id == null ? 'CREATE CREDENTIAL' : 'EDIT CREDENTIAL'}</span><h3>{editor.id == null ? t('创建 API 密钥') : t('编辑 API 密钥')}</h3><p>{t('保存后立即同步到账户服务。')}</p></div><button type="button" disabled={saving} onClick={() => setEditor(null)}><X size={18} /></button></div><div className="console-form-grid">
      <label className="console-form-wide"><span>{t('密钥名称')}</span><input required maxLength={50} value={editor.draft.name} placeholder={t('例如：客户 A - Codex')} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, name: event.target.value } })} /></label>
      <label><span>{t('可用额度')}</span><div className="console-input-prefix"><b>$</b><input type="number" min="0" step="0.01" disabled={editor.draft.unlimited_quota} value={(editor.draft.remain_quota / quotaUnit).toString()} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, remain_quota: Math.round(Number(event.target.value || 0) * quotaUnit) } })} /></div></label>
      <label><span>{t('过期时间')}</span><input type="datetime-local" value={expirationInput(editor.draft.expired_time)} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, expired_time: event.target.value ? Math.floor(new Date(event.target.value).getTime() / 1000) : -1 } })} /><small>{t('留空表示永不过期')}</small></label>
      <label><span>{t('路由/计费分组')}</span><input list="console-key-groups" maxLength={128} value={editor.draft.group} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, group: event.target.value } })} /><datalist id="console-key-groups">{options.groups.map((group) => <option key={group} value={group} />)}</datalist><small>{t('此分组会影响路由或计费，与上方自定义统计分组相互独立。')}</small></label>
      <label className="console-form-wide"><span>{t('模型限制')}</span><input list="console-key-models" maxLength={8192} disabled={!editor.draft.model_limits_enabled} value={editor.draft.model_limits} placeholder={t('多个模型用英文逗号分隔')} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, model_limits: event.target.value } })} /><datalist id="console-key-models">{modelSuggestions.map((model) => <option key={model} value={model} />)}</datalist></label>
      <label className="console-form-wide"><span>{t('允许的 IP')}</span><textarea maxLength={4096} rows={3} value={editor.draft.allow_ips} placeholder={t('每行一个 IP；留空不限制')} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, allow_ips: event.target.value } })} /></label>
    </div><div className="console-toggle-grid"><label><input type="checkbox" checked={editor.draft.unlimited_quota} onChange={(event) => { const unlimited = event.target.checked; setEditor({ ...editor, draft: { ...editor.draft, unlimited_quota: unlimited, remain_quota: !unlimited && editor.draft.remain_quota < 0 ? quotaUnit : editor.draft.remain_quota } }) }} /><span><strong>{t('不限额')}</strong><small>{t('不限制该密钥可用额度')}</small></span></label><label><input type="checkbox" checked={editor.draft.model_limits_enabled} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, model_limits_enabled: event.target.checked } })} /><span><strong>{t('启用模型限制')}</strong><small>{t('只允许上方列出的模型')}</small></span></label><label><input type="checkbox" checked={editor.draft.cross_group_retry} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, cross_group_retry: event.target.checked } })} /><span><strong>{t('跨分组重试')}</strong><small>{t('仅 auto 分组生效')}</small></span></label></div><div className="console-modal-actions"><button className="secondary-button" type="button" disabled={saving} onClick={() => setEditor(null)}>{t('取消')}</button><button className="primary-button" type="submit" disabled={saving || !editor.draft.name.trim()}>{saving ? <RefreshCw className="spin" size={15} /> : <KeyRound size={15} />}{saving ? t('正在保存') : t('保存密钥')}</button></div></form></div>}

    {revealed && <div className="console-modal-backdrop"><section className="console-modal console-reveal-modal" role="dialog" aria-modal="true"><div className="console-modal-head"><div><span className="eyebrow">ONE-TIME REVEAL</span><h3>{t('密钥明文')}</h3><p>{t('仅在当前窗口临时显示，关闭后监控平台不会保留。')}</p></div><button type="button" onClick={() => setRevealed(null)}><X size={18} /></button></div><div className="console-secret-box"><span>{revealed.name}</span><code>{revealed.key}</code><button type="button" onClick={() => void navigator.clipboard.writeText(revealed.key)}><Copy size={15} />{t('复制密钥')}</button></div><div className="console-inline-warning">{t('请立即保存到安全位置，不要通过聊天或截图分享。')}</div><div className="console-modal-actions"><button className="primary-button" type="button" onClick={() => setRevealed(null)}>{t('我已安全保存')}</button></div></section></div>}
  </div>
}
