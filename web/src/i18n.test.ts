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
})
