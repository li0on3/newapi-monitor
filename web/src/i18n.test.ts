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

  test('translates the customer console and its security boundary', () => {
    expect(translate('客户控制台', 'en')).toBe('Customer console')
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
    expect(translate('第 {{page}}/{{pages}} 页 · {{total}} 条记录', 'en', { page: 2, pages: 5, total: 88 })).toBe(
      'Page 2 of 5 · 88 records',
    )
  })
})
