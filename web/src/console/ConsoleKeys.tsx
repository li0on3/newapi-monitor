import { Copy, Eye, KeyRound, Pencil, Plus, RefreshCw, Search, ShieldOff, Trash2, X } from 'lucide-react'
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { getLanguage, t } from '../i18n'
import { consoleApi } from './api'
import { ConsoleBadge, ConsoleEmpty, ConsoleError, ConsoleLoading } from './ConsoleCommon'
import type { ConsoleToken, ConsoleTokenDraft, ConsoleTokenPage } from './types'
import { quotaText } from './utils'

const EMPTY_DRAFT: ConsoleTokenDraft = {
  name: '', remain_quota: 500000, expired_time: -1, unlimited_quota: false,
  model_limits_enabled: false, model_limits: '', allow_ips: '', group: 'default',
  cross_group_retry: false,
}

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

export function ConsoleKeys() {
  const [data, setData] = useState<ConsoleTokenPage | null>(null)
  const [page, setPage] = useState(1)
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

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await consoleApi.keys({ page, page_size: 20, keyword: query })
      setData(result)
      if (result.quota_per_unit) setOptions((current) => ({ ...current, quota_per_unit: result.quota_per_unit! }))
      setSelected(new Set())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('未知错误'))
    } finally {
      setLoading(false)
    }
  }, [page, query])

  useEffect(() => { void load() }, [load])
  const pageCount = Math.max(1, Math.ceil((data?.total || 0) / (data?.page_size || 20)))
  const allSelected = Boolean(data?.items.length) && data!.items.every((item) => selected.has(item.id))

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

  const submitSearch = (event: FormEvent) => { event.preventDefault(); setPage(1); setQuery(keyword.trim()) }
  const quotaUnit = options.quota_per_unit || 500000
  const modelSuggestions = useMemo(() => options.models.slice(0, 500), [options.models])

  return <div className="console-page console-keys-page">
    <section className="console-toolbar">
      <form className="console-search" onSubmit={submitSearch}><Search size={16} /><input value={keyword} maxLength={128} placeholder={t('按密钥名称搜索')} onChange={(event) => setKeyword(event.target.value)} /><button type="submit">{t('搜索')}</button></form>
      <div className="console-toolbar-actions">{selected.size > 0 && <button className="secondary-button danger" type="button" disabled={saving} onClick={() => void batchRemove()}><Trash2 size={15} />{t('删除选中')} ({selected.size})</button>}<button className="primary-button" type="button" onClick={() => void openEditor()}><Plus size={16} />{t('创建 API 密钥')}</button></div>
    </section>
    {error && <div className="console-inline-warning">{error}<button type="button" onClick={() => setError('')}><X size={14} /></button></div>}
    {loading && !data ? <ConsoleLoading /> : !data ? <ConsoleError message={error} retry={() => void load()} /> : <section className="console-panel console-table-panel">
      <div className="console-panel-head"><div><span className="eyebrow">API CREDENTIALS</span><h3>{t('API 密钥')}</h3><p>{t('密钥操作实时写入 New API；监控平台不保存密钥明文。')}</p></div><ConsoleBadge tone="blue">{data.total}</ConsoleBadge></div>
      {data.items.length ? <div className="console-table-scroll"><table className="console-table"><thead><tr><th><input aria-label={t('全选')} type="checkbox" checked={allSelected} onChange={(event) => setSelected(event.target.checked ? new Set(data.items.map((item) => item.id)) : new Set())} /></th><th>{t('名称与密钥')}</th><th>{t('状态')}</th><th>{t('额度')}</th><th>{t('模型与分组')}</th><th>{t('最后使用')}</th><th>{t('操作')}</th></tr></thead><tbody>{data.items.map((item) => { const status = keyStatus(item.status); return <tr key={item.id}><td><input aria-label={`${t('选择')} ${item.name}`} type="checkbox" checked={selected.has(item.id)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(item.id); else next.delete(item.id); return next })} /></td><td><strong>{item.name}</strong><code>{item.masked_key || t('密钥已隐藏')}</code></td><td><ConsoleBadge tone={status.tone}>{status.label}</ConsoleBadge>{item.expired_time > 0 && <small>{dateTime(item.expired_time)}</small>}</td><td><strong>{item.unlimited_quota ? t('不限额') : quotaText(item.remain_quota, quotaUnit)}</strong><small>{item.unlimited_quota ? t('不限制可用额度') : `${t('已使用')} ${quotaText(item.used_quota, quotaUnit)}`}</small></td><td><strong>{item.model_limits_enabled ? `${item.model_limits.split(',').filter(Boolean).length} ${t('个模型')}` : t('全部模型')}</strong><small>{item.group || t('默认分组')}</small></td><td><strong>{dateTime(item.accessed_time)}</strong><small>{t('创建于')} {dateTime(item.created_time)}</small></td><td><div className="console-row-actions"><button type="button" title={t('查看密钥')} disabled={actionId === item.id} onClick={() => void reveal(item)}><Eye size={15} /></button><button type="button" title={t('编辑')} onClick={() => void openEditor(item)}><Pencil size={15} /></button><button type="button" title={item.status === 1 ? t('停用') : t('启用')} disabled={actionId === item.id || item.status > 2} onClick={() => void updateStatus(item)}><ShieldOff size={15} /></button><button className="danger" type="button" title={t('删除')} disabled={actionId === item.id} onClick={() => void remove(item)}><Trash2 size={15} /></button></div></td></tr>})}</tbody></table></div> : <ConsoleEmpty title={query ? t('没有匹配的 API 密钥') : t('暂无 API 密钥')} detail={query ? t('换一个名称关键词再试。') : t('创建第一个密钥，为客户端分配独立凭据。')} />}
      <div className="console-pagination"><span>{t('第 {{page}}/{{pages}} 页 · {{total}} 条记录', { page: data.page, pages: pageCount, total: data.total })}</span><div><button type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}>{t('上一页')}</button><button type="button" disabled={page >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>{t('下一页')}</button></div></div>
    </section>}

    {editor && <div className="console-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setEditor(null) }}><form className="console-modal console-key-editor" onSubmit={save}><div className="console-modal-head"><div><span className="eyebrow">{editor.id == null ? 'CREATE CREDENTIAL' : 'EDIT CREDENTIAL'}</span><h3>{editor.id == null ? t('创建 API 密钥') : t('编辑 API 密钥')}</h3><p>{t('保存后立即同步到 New API，不会修改 New API 程序代码。')}</p></div><button type="button" disabled={saving} onClick={() => setEditor(null)}><X size={18} /></button></div><div className="console-form-grid">
      <label className="console-form-wide"><span>{t('密钥名称')}</span><input required maxLength={50} value={editor.draft.name} placeholder={t('例如：客户 A - Codex')} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, name: event.target.value } })} /></label>
      <label><span>{t('可用额度')}</span><div className="console-input-prefix"><b>$</b><input type="number" min="0" step="0.01" disabled={editor.draft.unlimited_quota} value={(editor.draft.remain_quota / quotaUnit).toString()} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, remain_quota: Math.round(Number(event.target.value || 0) * quotaUnit) } })} /></div></label>
      <label><span>{t('过期时间')}</span><input type="datetime-local" value={expirationInput(editor.draft.expired_time)} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, expired_time: event.target.value ? Math.floor(new Date(event.target.value).getTime() / 1000) : -1 } })} /><small>{t('留空表示永不过期')}</small></label>
      <label><span>{t('分组')}</span><input list="console-key-groups" maxLength={128} value={editor.draft.group} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, group: event.target.value } })} /><datalist id="console-key-groups">{options.groups.map((group) => <option key={group} value={group} />)}</datalist></label>
      <label className="console-form-wide"><span>{t('模型限制')}</span><input list="console-key-models" maxLength={8192} disabled={!editor.draft.model_limits_enabled} value={editor.draft.model_limits} placeholder={t('多个模型用英文逗号分隔')} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, model_limits: event.target.value } })} /><datalist id="console-key-models">{modelSuggestions.map((model) => <option key={model} value={model} />)}</datalist></label>
      <label className="console-form-wide"><span>{t('允许的 IP')}</span><textarea maxLength={4096} rows={3} value={editor.draft.allow_ips} placeholder={t('每行一个 IP；留空不限制')} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, allow_ips: event.target.value } })} /></label>
    </div><div className="console-toggle-grid"><label><input type="checkbox" checked={editor.draft.unlimited_quota} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, unlimited_quota: event.target.checked } })} /><span><strong>{t('不限额')}</strong><small>{t('不限制该密钥可用额度')}</small></span></label><label><input type="checkbox" checked={editor.draft.model_limits_enabled} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, model_limits_enabled: event.target.checked } })} /><span><strong>{t('启用模型限制')}</strong><small>{t('只允许上方列出的模型')}</small></span></label><label><input type="checkbox" checked={editor.draft.cross_group_retry} onChange={(event) => setEditor({ ...editor, draft: { ...editor.draft, cross_group_retry: event.target.checked } })} /><span><strong>{t('跨分组重试')}</strong><small>{t('仅 auto 分组生效')}</small></span></label></div><div className="console-modal-actions"><button className="secondary-button" type="button" disabled={saving} onClick={() => setEditor(null)}>{t('取消')}</button><button className="primary-button" type="submit" disabled={saving || !editor.draft.name.trim()}>{saving ? <RefreshCw className="spin" size={15} /> : <KeyRound size={15} />}{saving ? t('正在保存') : t('保存到 New API')}</button></div></form></div>}

    {revealed && <div className="console-modal-backdrop"><section className="console-modal console-reveal-modal" role="dialog" aria-modal="true"><div className="console-modal-head"><div><span className="eyebrow">ONE-TIME REVEAL</span><h3>{t('密钥明文')}</h3><p>{t('仅在当前窗口临时显示，关闭后监控平台不会保留。')}</p></div><button type="button" onClick={() => setRevealed(null)}><X size={18} /></button></div><div className="console-secret-box"><span>{revealed.name}</span><code>{revealed.key}</code><button type="button" onClick={() => void navigator.clipboard.writeText(revealed.key)}><Copy size={15} />{t('复制密钥')}</button></div><div className="console-inline-warning">{t('请立即保存到安全位置，不要通过聊天或截图分享。')}</div><div className="console-modal-actions"><button className="primary-button" type="button" onClick={() => setRevealed(null)}>{t('我已安全保存')}</button></div></section></div>}
  </div>
}
