/**
 * 外观 / 对话背景管理。
 *
 * 设计目标：让「切换对话背景图」成为一个一等能力——
 * 外观配置（纯色渐变 / 内置壁纸 / 自定义图片 / 模糊 / 压暗）统一存 localStorage，
 * 通过 CSS 变量 --chat-bg / --chat-bg-blur / --chat-bg-dim 作用到对话区背景层，
 * 因此换背景不需要改动任何组件代码。
 */

const KEY = 'huanzhen_appearance'

export const PRESETS = [
  { id: 'none', name: '默认深空', type: 'css', value: 'radial-gradient(1200px 600px at 15% -10%, rgba(77,107,254,.10), transparent 60%)' },
  { id: 'aurora', name: '极光', type: 'css', value: 'linear-gradient(135deg,#0B0F17 0%,#122036 40%,#0E2A2A 100%)' },
  { id: 'nebula', name: '星云', type: 'css', value: 'radial-gradient(900px 500px at 20% 10%, rgba(146,84,255,.22), transparent 60%), radial-gradient(800px 500px at 90% 90%, rgba(77,107,254,.20), transparent 60%), #0B0F17' },
  { id: 'ember', name: '余烬', type: 'css', value: 'radial-gradient(900px 520px at 80% 0%, rgba(245,165,36,.16), transparent 60%), #0B0F17' },
  { id: 'mint', name: '薄荷', type: 'css', value: 'radial-gradient(900px 520px at 10% 90%, rgba(52,211,153,.16), transparent 60%), #0B0F17' },
]

export const FONT_SCALES = [
  { id: 1, name: '标准' },
  { id: 1.1, name: '较大' },
  { id: 1.22, name: '大' },
  { id: 1.35, name: '特大' },
]

const DEFAULT = { mode: 'dark', preset: 'none', image: '', blur: 0, dim: 0, fontScale: 1.1 }

export function loadAppearance() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '{}')
    return { ...DEFAULT, ...raw }
  } catch {
    return { ...DEFAULT }
  }
}

export function saveAppearance(cfg) {
  localStorage.setItem(KEY, JSON.stringify(cfg))
}

/** 把外观配置写进 CSS 变量 */
export function applyAppearance(cfg) {
  const root = document.documentElement
  let bg = 'transparent'
  if (cfg.image) {
    bg = `url(${cfg.image})`
  } else {
    const p = PRESETS.find((x) => x.id === cfg.preset)
    if (p && p.id !== 'none') bg = p.value
  }
  root.style.setProperty('--chat-bg', bg)
  root.style.setProperty('--chat-bg-blur', (cfg.blur || 0) + 'px')
  root.style.setProperty('--chat-bg-dim', String(cfg.dim || 0))
  // 白天 / 夜间模式
  root.dataset.mode = cfg.mode === 'light' ? 'light' : 'dark'
  root.style.colorScheme = cfg.mode === 'light' ? 'light' : 'dark'
  // 字号缩放（全局生效，解决"字太小太瘦"的问题）
  root.style.setProperty('--fs-scale', String(cfg.fontScale || 1))
  // 有背景图时给文字加一点投影，避免糊在风景图里读不清
  root.dataset.hasBg = cfg.image ? '1' : '0'
}

/** 读取图片文件为 dataURL（限制体积，避免塞爆 localStorage） */
export function readImageFile(file, maxBytes = 3 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith('image/')) return reject(new Error('请选择图片文件'))
    if (file.size > maxBytes) return reject(new Error('图片过大（建议 < 3MB）'))
    const fr = new FileReader()
    fr.onload = () => resolve(String(fr.result))
    fr.onerror = () => reject(new Error('图片读取失败'))
    fr.readAsDataURL(file)
  })
}
