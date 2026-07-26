import { describe, expect, test } from 'bun:test'
import { detectLanguage, translate } from './i18n'

describe('language detection', () => {
  test('prefers an explicit saved language', () => {
    expect(detectLanguage(['en-US'], 'zh-CN')).toBe('zh-CN')
  })

  test('uses Chinese for Chinese browser locales and English otherwise', () => {
    expect(detectLanguage(['zh-Hans-CN', 'en-US'])).toBe('zh-CN')
    expect(detectLanguage(['en-US', 'zh-CN'])).toBe('en')
  })
})

describe('translations', () => {
  test('translates primary navigation and interpolated copy', () => {
    expect(translate('总览', 'en')).toBe('Overview')
    expect(translate('共 {{count}} 个活跃事件', 'en', { count: 3 })).toBe('3 active incidents')
  })

  test('keeps Chinese source copy in Chinese mode', () => {
    expect(translate('机器资源', 'zh-CN')).toBe('机器资源')
  })

  test('translates OpenAI provider status controls', () => {
    expect(translate('OpenAI 官方状态', 'en')).toBe('OpenAI official status')
    expect(translate('测试官方连接', 'en')).toBe('Test official connection')
    expect(translate('上游官方状态不会自动修改或禁用 New API 渠道。', 'en')).toBe(
      'Upstream status never modifies or disables New API channels automatically.',
    )
    expect(translate('官方状态仅作参考', 'en')).toBe('Official status is contextual only')
    expect(translate('查看官方状态详情', 'en')).toBe('View official status details')
    expect(translate('业务相关组件', 'en')).toBe('Workload-relevant components')
    expect(translate('官方状态页可见范围', 'en')).toBe('Official status page visibility')
  })

  test('translates the New API pages and their security boundary', () => {
    expect(translate('New API 功能页', 'en')).toBe('New API pages')
    expect(translate('监控总览', 'en')).toBe('Monitor overview')
    expect(translate('监控日志', 'en')).toBe('Monitor logs')
    expect(translate('API 密钥', 'en')).toBe('API keys')
    expect(translate('数据看板', 'en')).toBe('Analytics')
    expect(translate('业务数据不落监控库；每次请求由 New API Session 再鉴权。', 'en')).toBe(
      'Business data is not stored in the monitor database; every request is re-authorized by New API Session.',
    )
    expect(translate('错误', 'en')).toBe('Error')
    expect(translate('全部', 'en')).toBe('All')
    expect(translate('次调用', 'en')).toBe('calls')
    expect(translate('首字', 'en')).toBe('First token')
    expect(translate('调用渠道', 'en')).toBe('Channel')
    expect(translate('账号、额度与快捷入口', 'en')).toBe('Account, quota, and quick actions')
    expect(translate('退出监控仅终止监控会话，不会退出或修改 New API。', 'en')).toBe(
      'Signing out only ends the monitor session. It does not sign out of or modify New API.',
    )
    expect(translate('个人 New API 功能页', 'en')).toBe('Personal New API pages')
    expect(translate('用户无需预先同步，登录时实时识别。普通 New API 用户只能使用个人 New API 功能页，Admin 与 Root 自动成为管理员；这里可以对指定用户覆盖。', 'en')).toContain(
      'identified live at sign-in',
    )
    expect(translate('第 {{page}}/{{pages}} 页 · {{total}} 条记录', 'en', { page: 2, pages: 5, total: 88 })).toBe(
      'Page 2 of 5 · 88 records',
    )
  })

  test('distinguishes monitor key groups from New API routing groups', () => {
    expect(translate('密钥分组', 'en')).toBe('Key groups')
    expect(translate('New API 路由/计费分组', 'en')).toBe('New API routing/billing group')
    expect(translate('{{days}} 天用量', 'en', { days: 7 })).toBe('7-day usage')
    expect(translate('自定义分组只用于监控平台的组织和统计，不会改变 New API 的路由、计费或密钥权限。', 'en')).toBe(
      'Custom groups organize monitor statistics only; they do not change New API routing, billing, or key permissions.',
    )
  })

  test('translates the neutral customer experience and multi-group workflow', () => {
    expect(translate('API 服务中心', 'en')).toBe('API Service Center')
    expect(translate('账号登录', 'en')).toBe('Sign in')
    expect(translate('监控总览', 'en')).toBe('Monitor overview')
    expect(translate('一个密钥可以同时属于多个分组。', 'en')).toBe('A key can belong to multiple groups at the same time.')
    expect(translate('保存成员', 'en')).toBe('Save members')
    expect(translate('趋势数据表', 'en')).toBe('Trend data table')
    expect(translate('其他模型', 'en')).toBe('Other models')
  })

  test('translates theme controls', () => {
    expect(translate('主题', 'en')).toBe('Theme')
    expect(translate('跟随系统', 'en')).toBe('System')
    expect(translate('浅色', 'en')).toBe('Light')
    expect(translate('深色', 'en')).toBe('Dark')
  })

  test('translates alert delivery operations and maintenance policies', () => {
    expect(translate('告警投递中心', 'en')).toBe('Alert delivery center')
    expect(translate('恢复死信并重投', 'en')).toBe('Recover dead letter and redeliver')
    expect(translate('启用静默时段', 'en')).toBe('Enable quiet hours')
    expect(translate('计划维护窗口', 'en')).toBe('Scheduled maintenance window')
  })
})
