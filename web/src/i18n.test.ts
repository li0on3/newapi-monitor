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
})
