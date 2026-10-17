import { useEffect, useMemo, useRef, useState } from 'react'
import ImageEditor, { type ImageEditSession, type ImageCropResult } from './ImageEditor'
import type { HugeState, InstallInfo, UpdateInfo } from './vite-env'

const emptyUpdate: UpdateInfo = {
  status: 'idle',
  current_version: '',
  latest_version: '',
  update_available: false,
  release_name: '',
  release_notes: '',
  html_url: '',
  asset_name: '',
  progress: 0,
  bytes_done: 0,
  bytes_total: 0,
  error: '',
  message: '',
  sha256_verified: false,
  sha256_skipped_reason: '',
  banner_dismissed: false,
}

const emptyInstall: InstallInfo = {
  path: '',
  frozen: false,
  can_change: false,
  default_path: '',
  status: 'idle',
  message: '',
  error: '',
  progress: 0,
}

const empty: HugeState = {
  app: 'Huge',
  credit: 'by vsnq',
  app_version: '',
  role: null,
  user_id: 'host',
  username: '',
  avatar_b64: '',
  banner_b64: '',
  potplayer_path: '',
  pot_ready: false,
  pot_badge: 'WAITING FOR FILE',
  pot_badge_kind: 'warn',
  movie_path: '',
  movie_name: '',
  movie_loaded: false,
  lobby_name: '',
  host_name: '',
  address: '',
  users: [],
  logs: [],
  started: false,
  ready: false,
  elapsed: 0,
  elapsed_text: '00:00:00',
  connection_text: 'Starting Huge…',
  connection_kind: 'offline',
  position: -1,
  duration: -1,
  playing: false,
  paused: false,
  all_ready: false,
  member_count: 0,
  ready_count: 0,
  can_create: false,
  can_join: false,
  can_select_movie: false,
  can_ready: false,
  can_ready_all: false,
  can_start: false,
  can_playback: false,
  can_skip: false,
  host: false,
  in_lobby: false,
  last_error: '',
  last_info: '',
  lobby_name_draft: '',
  join_address_draft: '',
  tailscale: false,
  closing: false,
  quote: 'Why be ashamed of what you want?',
  update: emptyUpdate,
  install: emptyInstall,
  title_font: 'Outfit',
  title_font_size: 46,
  keyboard_shortcuts: {
    play_pause: 'Space',
    seek_back: '<',
    seek_forward: '>',
  },
  shortcut_warnings: [],
  shortcut_recording: false,
}

const SHORTCUT_ROWS: { action: 'play_pause' | 'seek_back' | 'seek_forward'; label: string; hint: string }[] = [
  { action: 'play_pause', label: 'Play / Pause', hint: 'Synchronized play or pause (Space)' },
  { action: 'seek_back', label: 'Seek Backward', hint: 'Skip back 5 seconds (<)' },
  { action: 'seek_forward', label: 'Seek Forward', hint: 'Skip forward 5 seconds (>)' },
]

function api() {
  return window.pywebview?.api
}

function stateUrl() {
  try {
    const query = new URLSearchParams(window.location.search).get('state')
    if (query) return query
    if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {
      return new URL('/state', window.location.origin).href
    }
    return window.__HUGE_STATE_URL || ''
  } catch {
    return ''
  }
}

function avatarSrc(b64?: string) {
  if (!b64) return ''
  if (b64.startsWith('data:')) return b64
  // Lobby thumbs may be JPEG/PNG/GIF — sniff magic from base64.
  const trimmed = b64.trim()
  if (trimmed.startsWith('R0lGOD')) {
    return `data:image/gif;base64,${trimmed}`
  }
  if (trimmed.startsWith('/9j/') || trimmed.startsWith('+/9k')) {
    return `data:image/jpeg;base64,${trimmed}`
  }
  return `data:image/png;base64,${trimmed}`
}

function formatMs(ms: number) {
  if (!Number.isFinite(ms) || ms < 0) return '00:00:00'
  const seconds = Math.floor(ms / 1000)
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return [h, m, s].map((n) => String(n).padStart(2, '0')).join(':')
}

function movieTitle(name: string) {
  if (!name) return ''
  return name.replace(/\.[^.]+$/, '').replaceAll('.', ' ')
}

function potExeName(path: string) {
  if (!path) return ''
  const parts = path.replace(/\\/g, '/').split('/')
  return parts[parts.length - 1] || path
}

function bannerSrc(b64?: string) {
  if (!b64) return ''
  if (b64.startsWith('data:')) return b64
  const trimmed = b64.trim()
  if (trimmed.startsWith('R0lGOD')) return `data:image/gif;base64,${trimmed}`
  if (trimmed.startsWith('iVBOR')) return `data:image/png;base64,${trimmed}`
  return `data:image/jpeg;base64,${trimmed}`
}

type LogPart = { t?: string; v?: string }

function sanitizeLogText(value?: string) {
  // Never interpret user-generated text as HTML.
  return String(value || '').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '')
}

function LogMessage({ actor, message, parts }: { actor?: string; message?: string; parts?: LogPart[] }) {
  const name = sanitizeLogText(actor).trim()
  let text = sanitizeLogText(message).trim()
  if (name) {
    const prefix = `${name} `
    while (text.startsWith(prefix)) text = text.slice(prefix.length).trimStart()
  }
  const spans = Array.isArray(parts) && parts.length
    ? parts
    : text
      ? [{ t: 'text', v: text }]
      : []
  return (
    <span className="log-message">
      {name ? <strong className="log-actor">{name}</strong> : null}
      {name && spans.length ? ' ' : null}
      {spans.map((part, index) => {
        const value = sanitizeLogText(part?.v)
        if (!value) return null
        if (part?.t === 'em' || part?.t === 'action' || part?.t === 'amount') {
          return <strong key={`${index}-${value.slice(0, 12)}`} className="log-em">{value}</strong>
        }
        return <span key={`${index}-${value.slice(0, 12)}`}>{value}</span>
      })}
    </span>
  )
}

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    play: <path d="m9 5 11 7-11 7Z" />,
    pause: <><path d="M8 5v14M16 5v14" /></>,
    folder: <path d="M3 7h7l2 2h9v11H3ZM3 7V4h7l2 3" />,
    plus: <path d="M12 5v14M5 12h14" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    check: <path d="m5 12 4 4L19 6" />,
    users: <><circle cx="9" cy="8" r="3" /><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 5" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    link: <><path d="m10 14 4-4m-6 6-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 1 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" /></>,
    copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M15 8V4H4v11h4" /></>,
    film: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" /></>,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    exit: <><path d="M10 4H4v16h6M10 12h11m-4-4 4 4-4 4" /></>,
    crown: <path d="m3 6 5 5 4-7 4 7 5-5-2 13H5Z" />,
    rewind: <><path d="m11 5-8 7 8 7Zm10 0-8 7 8 7Z" /></>,
    forward: <><path d="m3 5 8 7-8 7Zm10 0 8 7-8 7Z" /></>,
    activity: <path d="M2 12h5l3-8 4 16 3-8h5" />,
    expand: <path d="M4 10V4h6M20 14v6h-6M14 4h6v6M10 20H4v-6" />,
    settings: <><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" /></>,
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name] || paths.film}
    </svg>
  )
}

export default function App() {
  const [state, setState] = useState<HugeState>(empty)
  const [modal, setModal] = useState<'create' | 'join' | ''>('')
  const [value, setValue] = useState('')
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const [imageEdit, setImageEdit] = useState<ImageEditSession | null>(null)
  // Local username keeps typing instantaneous; backend sync is debounced.
  const [nameInput, setNameInput] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const [installConfirm, setInstallConfirm] = useState<{ dest: string; message: string; needsElevation?: boolean } | null>(null)
  const [installPickError, setInstallPickError] = useState('')
  const [titleFonts, setTitleFonts] = useState<string[]>([
    'Outfit', 'Segoe UI', 'Arial', 'Calibri', 'Verdana', 'Trebuchet MS',
    'Georgia', 'Times New Roman', 'Consolas', 'Cascadia Mono', 'Comic Sans MS', 'Impact',
  ])
  const nameFocused = useRef(false)
  const nameTimer = useRef(0)
  const logListRef = useRef<HTMLDivElement>(null)
  const stickLogBottom = useRef(true)
  const [recordingAction, setRecordingAction] = useState<string | null>(null)
  const [shortcutError, setShortcutError] = useState('')

  const setTextEntry = (active: boolean) => {
    void api()?.set_text_entry_active?.(active)
  }

  useEffect(() => {
    const isTextField = (el: EventTarget | null) => {
      if (!(el instanceof HTMLElement)) return false
      const tag = el.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
      return el.isContentEditable
    }
    const onFocusIn = (event: FocusEvent) => {
      if (isTextField(event.target)) setTextEntry(true)
    }
    const onFocusOut = (event: FocusEvent) => {
      if (isTextField(event.target)) {
        window.setTimeout(() => {
          const active = document.activeElement
          setTextEntry(isTextField(active))
        }, 0)
      }
    }
    document.addEventListener('focusin', onFocusIn)
    document.addEventListener('focusout', onFocusOut)
    return () => {
      document.removeEventListener('focusin', onFocusIn)
      document.removeEventListener('focusout', onFocusOut)
      setTextEntry(false)
    }
  }, [])

  useEffect(() => {
    if (!state.shortcut_recording) {
      setRecordingAction(null)
    }
  }, [state.shortcut_recording])

  const apply = (next: HugeState) => {
    setState(next)
    if (!nameFocused.current) {
      setNameInput(next.username || '')
    }
    if (next.last_error) setError(next.last_error)
  }

  const pushName = (raw: string, flush = false) => {
    const bridge = api()
    if (!bridge?.set_name) return
    if (nameTimer.current) window.clearTimeout(nameTimer.current)
    if (flush) {
      void (async () => {
        try {
          if (bridge.flush_name) await bridge.flush_name(raw)
          else await bridge.set_name(raw)
        } catch {
          /* ignore */
        }
      })()
      return
    }
    nameTimer.current = window.setTimeout(() => {
      void bridge.set_name(raw).catch(() => undefined)
    }, 300)
  }

  const onNameChange = (raw: string) => {
    setNameInput(raw)
    pushName(raw, false)
  }

  useEffect(() => {
    const onOpenEditor = (event: Event) => {
      const detail = (event as CustomEvent<ImageEditSession>).detail
      if (detail?.path && detail?.previewUrl) setImageEdit(detail)
    }
    window.addEventListener('huge-open-image-editor', onOpenEditor as EventListener)
    return () => window.removeEventListener('huge-open-image-editor', onOpenEditor as EventListener)
  }, [])

  useEffect(() => {
    let timer = 0
    let stopped = false
    window.__hugePush = (next) => apply(next)

    const tick = async () => {
      const bridge = api()
      if (!bridge?.get_state) return
      try {
        const next = await bridge.get_state()
        if (!stopped && next) apply(next)
      } catch {
        /* get_state is cheap and off the UI thread; a miss is fine */
      }
    }

    const start = () => {
      void tick()
      timer = window.setInterval(() => void tick(), 400)
    }

    if (window.pywebview?.api) start()
    else window.addEventListener('pywebviewready', start, { once: true })

    const loadFonts = async () => {
      try {
        const result = await api()?.list_title_fonts?.()
        if (result?.fonts?.length) setTitleFonts(result.fonts)
      } catch {
        /* keep defaults */
      }
    }
    if (window.pywebview?.api) void loadFonts()
    else window.addEventListener('pywebviewready', () => void loadFonts(), { once: true })

    return () => {
      stopped = true
      window.__hugePush = undefined
      window.removeEventListener('pywebviewready', start)
      if (timer) window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    const el = logListRef.current
    if (!el || !stickLogBottom.current) return
    el.scrollTop = el.scrollHeight
  }, [state.logs])

  const profile = useMemo(() => avatarSrc(state.avatar_b64), [state.avatar_b64])
  const banner = useMemo(() => bannerSrc(state.banner_b64), [state.banner_b64])
  const title = movieTitle(state.movie_name)
  const pos = state.position >= 0 ? state.position : 0
  const dur = state.duration > 0 ? state.duration : 1
  const connectionClass = state.connection_kind === 'online' ? '' : state.connection_kind === 'waiting' ? 'waiting-dot' : 'offline-dot'
  const showUpdateBanner = Boolean(
    state.update?.update_available
    && !state.update?.banner_dismissed
    && state.update.status !== 'checking'
    && state.update.status !== 'downloading'
    && state.update.status !== 'installing',
  )
  const updateSummary = useMemo(() => {
    const raw = (state.update?.release_notes || '').trim()
    if (!raw) return 'A new version is ready to install.'
    const line = raw.split(/\r?\n/).map((s) => s.trim()).find(Boolean) || raw
    return line.length > 220 ? `${line.slice(0, 217).trim()}…` : line
  }, [state.update?.release_notes])

  const beginChangeInstallLocation = async () => {
    setInstallPickError('')
    setInstallConfirm(null)
    const result = await api()?.browse_install_location?.()
    if (!result || result.cancelled) return
    if (result.same || result.ok === false) {
      setInstallPickError(result.error || 'Could not change installation location.')
      return
    }
    if (result.dest && result.confirm_message) {
      setInstallConfirm({
        dest: result.dest,
        message: result.confirm_message,
        needsElevation: Boolean(result.needs_elevation),
      })
    }
  }

  const confirmInstallMigration = async () => {
    if (!installConfirm?.dest) return
    setInstallPickError('')
    const result = await api()?.start_install_migration?.(installConfirm.dest)
    if (result && result.ok === false) {
      setInstallPickError(result.error || 'Could not start relocation.')
      return
    }
    setInstallConfirm(null)
  }

  const exportSyncLogs = async () => {
    const result = await api()?.export_sync_logs?.()
    if (result && result.ok === false) {
      window.alert(result.error || 'Could not export sync logs.')
    } else if (result?.path) {
      window.alert(`Sync log saved to your Desktop:\n${result.path}\n\nSend this file when reporting a multiplayer test.`)
    }
    return result
  }

  const connectionLabel = state.tailscale
    ? 'Tailscale connected'
    : state.connection_kind === 'online'
      ? 'Connected'
      : state.connection_kind === 'waiting'
        ? 'Waiting'
        : 'Offline'

  const call = async (fn: () => Promise<unknown> | undefined) => {
    try {
      await fn()
    } catch {
      /* click handler should never freeze the window */
    }
    const url = stateUrl()
    if (!url) return
    try {
      const response = await fetch(url, { cache: 'no-store' })
      if (response.ok) apply(await response.json())
    } catch {
      /* poll will catch up */
    }
  }

  const openImageChooser = async (kind: 'banner' | 'avatar') => {
    const bridge = api()
    if (!bridge) return
    try {
      const result = kind === 'banner' ? await bridge.choose_banner() : await bridge.choose_avatar()
      if (!result || result.cancelled || !result.ok) {
        if (result?.error) setError(result.error)
        return
      }
      if (!result.path || !result.preview_b64) {
        setError(result.error || 'Could not open that picture.')
        return
      }
      setImageEdit({
        mode: result.mode === 'avatar' ? 'avatar' : 'banner',
        path: result.path,
        previewUrl: `data:${result.mime || 'image/jpeg'};base64,${result.preview_b64}`,
        previewWidth: Number(result.preview_width) || 1,
        previewHeight: Number(result.preview_height) || 1,
        sourceWidth: Number(result.source_width) || Number(result.preview_width) || 1,
        sourceHeight: Number(result.source_height) || Number(result.preview_height) || 1,
      })
    } catch {
      setError('Could not open that picture.')
    }
  }

  const saveImageEdit = async (crop: ImageCropResult) => {
    const bridge = api()
    if (!bridge?.save_image_crop) {
      setImageEdit(null)
      return
    }
    try {
      const next = await bridge.save_image_crop(crop)
      if (next?.ok === false) {
        setError(next.error || 'Could not save that picture.')
        setImageEdit(null)
        return
      }
      if (next && typeof next === 'object' && 'avatar_b64' in next) {
        apply(next as HugeState)
      } else {
        await call(async () => next)
      }
      setImageEdit(null)
    } catch {
      setError('Could not save that picture.')
      setImageEdit(null)
    }
  }

  const openCreate = () => {
    setValue(state.lobby_name_draft || '')
    setModal('create')
  }
  const openJoin = () => {
    setValue(state.join_address_draft || state.address || '')
    setModal('join')
  }

  const submitModal = async () => {
    if (!value.trim()) return
    const bridge = api()
    if (!bridge) return
    if (modal === 'create') await call(() => bridge.create_lobby(value.trim()))
    if (modal === 'join') await call(() => bridge.join_lobby(value.trim()))
    setModal('')
  }

  const copyAddress = async () => {
    const bridge = api()
    if (!bridge) return
    const next = await bridge.copy_address()
    const address = (next && next.address) || state.address
    if (address) {
      try { await navigator.clipboard.writeText(address) } catch { /* ignore */ }
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    }
    await call(async () => next)
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#/" onClick={(e) => e.preventDefault()}>
          <img className="gothic-brand-icon" src="./huge-gothic-logo.svg" alt="Huge gothic sigil" />
          <span className="brand-name">Huge</span>
        </a>
        <div className="top-right">
          <span className="connection"><i className={connectionClass} /> {connectionLabel}</span>
          <input
            className="name-field"
            placeholder="Your name"
            value={nameInput}
            disabled={state.in_lobby}
            maxLength={32}
            onFocus={() => { nameFocused.current = true; setTextEntry(true) }}
            onChange={(e) => onNameChange(e.target.value)}
            onBlur={() => {
              nameFocused.current = false
              setTextEntry(false)
              pushName(nameInput, true)
            }}
          />
          <button className="top-avatar profile-picker" aria-label="Select your profile picture" title="Change profile picture" disabled={state.in_lobby} onClick={() => void openImageChooser('avatar')}>
            {profile ? <img src={profile} alt="Your profile picture" /> : <img className="logo-profile" src="./huge-gothic-logo.svg" alt="Huge" />}
          </button>
          <button className="button secondary profile-upload" disabled={state.in_lobby} onClick={() => void openImageChooser('avatar')}>
            <Icon name="folder" size={15} />Upload photo
          </button>
          <button className="button secondary settings-button" type="button" aria-label="Open settings" title="Settings" onClick={() => setShowSettings(true)}>
            <Icon name="settings" size={17} />
          </button>
        </div>
      </header>
      {showUpdateBanner && (
        <div className="update-banner" role="status">
          <div className="update-banner-text">
            <strong>Huge v{state.update?.latest_version || '—'} is available</strong>
            <p>{updateSummary}</p>
          </div>
          <div className="update-banner-actions">
            <button className="button primary" type="button" onClick={() => void call(() => api()?.start_update())}>Update Now</button>
            <button className="button secondary" type="button" onClick={() => void call(() => api()?.dismiss_update())}>Later</button>
          </div>
        </div>
      )}
      <main>
        <div className="page-heading lobby-toolbar">
          <div className="lobby-actions">
            <button className="button secondary" disabled={state.in_lobby} onClick={openJoin}><Icon name="link" size={17} />Join lobby</button>
            <button className="button primary" disabled={state.in_lobby} onClick={openCreate}><Icon name="plus" size={18} />Create lobby</button>
          </div>
        </div>
        <div className="dashboard">
          <div className="main-column">
            <section className="movie-card">
              <div className={`cinematic-image${banner ? ' has-banner' : ''}`} style={banner ? { backgroundImage: `url(${banner})` } : undefined} />
              <div className="movie-shade" />
              <div className="movie-top">
                {(state.playing || state.paused) ? (
                  <span className="pill"><span className="purple-dot" /> {state.playing ? 'NOW PLAYING' : 'PLAYBACK PAUSED'}</span>
                ) : <span />}
                <div className="movie-top-actions">
                  <button className="button glass banner-pick" onClick={() => void openImageChooser('banner')}>
                    <Icon name="folder" size={14} />Choose banner
                  </button>
                </div>
              </div>
              {title && (
                <div className="movie-content">
                  <h2
                    className="movie-title"
                    style={{
                      fontFamily: `'${(state.title_font || 'Outfit').replace(/'/g, '')}', Outfit, sans-serif`,
                      fontSize: `${Math.max(22, Math.min(72, Number(state.title_font_size) || 46))}px`,
                    }}
                  >
                    {title}
                  </h2>
                </div>
              )}
              <div className="file-strip">
                <div className="file-info">
                  <span className="file-icon"><Icon name="film" size={18} /></span>
                  <div>
                    <span className="filename">{state.movie_name || 'No movie selected'}</span>
                    <span className="file-status"><i />{state.movie_loaded ? 'Ready for PotPlayer' : 'Waiting for a movie file'}</span>
                  </div>
                </div>
                <button className="button glass" disabled={!state.can_select_movie} onClick={() => void call(() => api()?.choose_movie())}>
                  <Icon name="folder" size={16} />Select movie
                </button>
              </div>
            </section>

            <section className="panel playback-panel">
              <div className="section-header">
                <h3><Icon name="play" size={17} />Playback</h3>
              </div>
              <div className="timeline">
                <span className="time">{formatMs(pos)}</span>
                <input aria-label="Movie timeline" type="range" min="0" max={dur} value={Math.min(pos, dur)} disabled readOnly />
                <span className="time muted">{formatMs(state.duration)}</span>
              </div>
              <div className="transport">
                <button disabled={!state.can_skip} onClick={() => void call(() => api()?.skip(-30000))}><Icon name="rewind" /><span>30s</span></button>
                <button disabled={!state.can_skip} onClick={() => void call(() => api()?.skip(-5000))}><span className="skip-small">↶</span><span>5s</span></button>
                <button className="play-button" aria-label={state.playing ? 'Pause movie' : 'Play movie'} disabled={!state.can_playback} onClick={() => void call(() => api()?.play_pause())}>
                  <Icon name={state.playing ? 'pause' : 'play'} size={25} />
                </button>
                <button disabled={!state.can_skip} onClick={() => void call(() => api()?.skip(5000))}><span className="skip-small">↷</span><span>5s</span></button>
                <button disabled={!state.can_skip} onClick={() => void call(() => api()?.skip(30000))}><Icon name="forward" /><span>30s</span></button>
                <button className="full-button" disabled={!state.in_lobby} title="Fullscreen" onClick={() => void call(() => api()?.fullscreen())}><Icon name="expand" /></button>
              </div>
              <div className="ready-bar">
                <div>
                  <span className={`status-dot ${state.all_ready ? 'green' : 'amber'}`} />
                  <span>{state.all_ready ? 'Everyone is ready. Let’s roll.' : `${state.ready_count} of ${state.member_count || 0} ready`}</span>
                  <small>{state.all_ready ? 'Press Start Movie when you are ready to play.' : 'The movie starts when everyone is ready.'}</small>
                </div>
                <div className="ready-actions">
                  {state.host && (
                    <button className="button start-button" disabled={!state.can_start} onClick={() => void call(() => api()?.start_movie())}>
                      ▶ Start Movie
                    </button>
                  )}
                  <button className={`button ${state.ready ? 'ready-button' : 'primary'}`} disabled={!state.can_ready} onClick={() => void call(() => api()?.toggle_ready())}>
                    <Icon name="check" size={17} />{state.ready ? "You’re ready" : 'Ready up'}
                  </button>
                </div>
              </div>
            </section>

            <section className="panel log-panel">
              <div className="section-header">
                <h3><Icon name="activity" size={18} />Live log<span className="live-badge">LIVE</span></h3>
                <span className="subtle-label">{state.logs.length} events</span>
              </div>
              <div
                className="log-list"
                ref={logListRef}
                onScroll={(event) => {
                  const el = event.currentTarget
                  stickLogBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
                }}
              >
                {state.logs.map((entry) => (
                  <div className="log-row" key={entry.id ?? `${entry.time}-${entry.actor}-${entry.message}`}>
                    <span className="log-icon"><Icon name="activity" size={15} /></span>
                    <LogMessage actor={entry.actor} message={entry.message} parts={entry.parts} />
                    <time>{entry.time}</time>
                  </div>
                ))}
                {!state.logs.length && <div className="log-row"><span>Waiting for lobby events…</span></div>}
              </div>
            </section>
          </div>

          <aside>
            <section className="panel lobby-panel">
              <div className="section-header">
                <h3>THE LOBBY</h3>
                <span className="pill tiny"><i className={state.in_lobby ? '' : 'offline-dot'} />{state.in_lobby ? 'Connected' : 'Offline'}</span>
              </div>
              <h2>{state.lobby_name || 'No active lobby'}</h2>
              <div className="host-line">
                <Icon name="crown" size={15} />
                <span>Hosted by <strong>{state.host_name || '—'}</strong></span>
                {state.host && <span className="you-badge">YOU</span>}
              </div>
              <div className="address-box">
                <div>
                  <span>TAILSCALE ADDRESS</span>
                  <code>{state.in_lobby ? (state.address || '—') : '—'}</code>
                </div>
                <button aria-label="Copy lobby address" disabled={!state.in_lobby} onClick={() => void copyAddress()}>
                  <Icon name={copied ? 'check' : 'copy'} size={18} />
                </button>
              </div>
              <div className="lobby-timer">
                <span><Icon name="clock" size={16} />Time together</span>
                <strong>{state.elapsed_text}</strong>
              </div>
            </section>

            <section className="panel members-panel">
              <div className="section-header">
                <h3><Icon name="users" size={18} />In this lobby <span className="count">{state.in_lobby ? state.member_count : 0}</span></h3>
                <span className="subtle-label">{state.ready_count} ready</span>
              </div>
              <div className="members-list">
                {state.in_lobby && state.users.map((member) => {
                  const mine = member.id === state.user_id || (state.host && member.host)
                  const ring = member.paused ? 'paused' : member.ready ? 'is-ready' : 'not-ready'
                  const status = member.paused ? 'Paused' : member.ready ? 'Ready' : 'Not ready'
                  const src = avatarSrc(member.avatar)
                  return (
                    <div className="member" key={member.id}>
                      <div className={`avatar ${ring}`}>
                        {src ? <img src={src} alt="" /> : <img className="logo-profile" src="./huge-gothic-logo.svg" alt="" />}
                        <span>{member.ready ? <Icon name={member.paused ? 'pause' : 'check'} size={9} /> : '−'}</span>
                      </div>
                      <div className="member-info">
                        <strong>{member.name}{mine && <span> (you)</span>}</strong>
                        <small>{member.host ? 'You · Host' : 'Connected'}</small>
                      </div>
                      <span className={`member-status ${member.paused ? 'amber-text' : member.ready ? 'green-text' : 'red-text'}`}>{status}</span>
                      {state.host && !member.host && (
                        <button className="kick-button" title={`Kick ${member.name}`} onClick={() => void call(() => api()?.kick(member.id))}>
                          <Icon name="close" size={13} />
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
              <div className="member-legend">
                <span><i className="green" />Ready</span>
                <span><i className="red" />Not ready</span>
                <span><i className="amber" />Paused</span>
              </div>
              {state.host && (
                <button className="everyone-button" disabled={!state.can_ready_all || !state.movie_loaded} onClick={() => void call(() => api()?.ready_everyone())}>
                  <Icon name="check" size={15} />Ready everyone<span>Host control</span>
                </button>
              )}
              <button className="leave-button" disabled={!state.in_lobby} onClick={() => void call(() => api()?.leave_lobby())}>
                <Icon name="exit" size={15} />Leave lobby
              </button>
            </section>

            <section className="panel potplayer-panel">
              <div className="section-header">
                <h3><span className="potplayer-logo"><Icon name="play" size={12} /></span>PotPlayer</h3>
                <span className={`small ${state.pot_ready ? 'green-text' : 'red-text'}`}>
                  <i className={`status-dot ${state.pot_ready ? 'green' : 'red'}`} />{state.pot_ready ? '64-bit · Ready' : '64-bit · Not found'}
                </span>
              </div>
              <div className="pot-path-block" title={state.potplayer_path || undefined}>
                <span className="pot-path-label">Path</span>
                <div className="path-text">{state.potplayer_path ? potExeName(state.potplayer_path) : 'Not selected'}</div>
              </div>
              <button className="button secondary full" onClick={() => void call(() => api()?.choose_potplayer())}>
                <Icon name="folder" size={16} />Select PotPlayer path<Icon name="arrow" size={16} />
              </button>
            </section>

          </aside>
        </div>
        <footer>
          <span>by vsnq · v{state.app_version || '—'}</span>
        </footer>
      </main>

      {modal && (
        <div className="modal-backdrop" onClick={() => setModal('')}>
          <form className="modal panel" onClick={(event) => event.stopPropagation()} onSubmit={(event) => { event.preventDefault(); void submitModal() }}>
            <button type="button" className="modal-close" aria-label="Close dialog" onClick={() => setModal('')}><Icon name="close" /></button>
            <span className="modal-symbol"><Icon name={modal === 'create' ? 'users' : 'link'} size={26} /></span>
            <h2>{modal === 'create' ? 'Your night, your lobby.' : 'Meet you at the movies.'}</h2>
            <p>{modal === 'create' ? 'Give your lobby a name and bring your people together.' : 'Enter your friend’s Tailscale address to join their lobby.'}</p>
            <label htmlFor="modal-input">{modal === 'create' ? 'Lobby name' : 'Host address'}</label>
            <input id="modal-input" autoFocus required value={value} onChange={(event) => setValue(event.target.value)} placeholder={modal === 'create' ? 'Friday movie night' : '100.84.12.8:24816'} />
            <button className="button primary full" type="submit">{modal === 'create' ? 'Create lobby' : 'Join lobby'}<Icon name="arrow" size={18} /></button>
          </form>
        </div>
      )}

      {error && (
        <button className="error-banner" onClick={() => setError('')}>{error}</button>
      )}

      {imageEdit && (
        <ImageEditor
          session={imageEdit}
          onCancel={() => setImageEdit(null)}
          onSave={(crop) => void saveImageEdit(crop)}
        />
      )}

      {showSettings && (
        <div className="modal-backdrop" onClick={() => {
          if (recordingAction) void api()?.cancel_record_shortcut?.()
          setRecordingAction(null)
          setShowSettings(false)
        }}>
          <div className="settings-modal panel" onClick={(event) => event.stopPropagation()} role="dialog" aria-labelledby="settings-title">
            <button type="button" className="modal-close" aria-label="Close settings" onClick={() => {
              if (recordingAction) void api()?.cancel_record_shortcut?.()
              setRecordingAction(null)
              setShowSettings(false)
            }}><Icon name="close" /></button>
            <h2 id="settings-title">Settings</h2>
            <section className="settings-section">
              <div className="section-header">
                <h3>Appearance</h3>
                <span className="subtle-label">Movie Title</span>
              </div>
              <label className="settings-field">
                <span>Font family</span>
                <select
                  value={state.title_font || 'Outfit'}
                  onChange={(e) => void call(() => api()?.set_title_font?.(e.target.value))}
                >
                  {titleFonts.map((font) => (
                    <option key={font} value={font}>{font}</option>
                  ))}
                </select>
              </label>
              <label className="settings-field">
                <span>Font size — {Math.max(22, Math.min(72, Number(state.title_font_size) || 46))}px</span>
                <input
                  type="range"
                  min={22}
                  max={72}
                  step={1}
                  value={Math.max(22, Math.min(72, Number(state.title_font_size) || 46))}
                  onChange={(e) => void call(() => api()?.set_title_font_size?.(Number(e.target.value)))}
                />
              </label>
              <div
                className="title-font-preview"
                style={{
                  fontFamily: `'${(state.title_font || 'Outfit').replace(/'/g, '')}', Outfit, sans-serif`,
                  fontSize: `${Math.max(18, Math.min(40, (Number(state.title_font_size) || 46) * 0.55))}px`,
                }}
              >
                {title || 'Movie title preview'}
              </div>
            </section>
            <section className="settings-section">
              <div className="section-header">
                <h3>Keyboard Shortcuts</h3>
                <span className="subtle-label">Playback</span>
              </div>
              <p className="settings-help">
                Shortcuts work while Huge or PotPlayer is focused. When PotPlayer is active, Huge consumes only these playback keys so PotPlayer does not also handle them; every other PotPlayer shortcut stays available. Ignored while typing in Huge.
              </p>
              <div className="keybind-list">
                {SHORTCUT_ROWS.map((row) => {
                  const current = state.keyboard_shortcuts?.[row.action] || ''
                  const isRecording = recordingAction === row.action || (state.shortcut_recording && recordingAction === row.action)
                  return (
                    <div className="keybind-row" key={row.action}>
                      <div className="keybind-meta">
                        <strong>{row.label}</strong>
                        <span>{row.hint}</span>
                      </div>
                      <div className="keybind-keys" aria-label={`${row.label} shortcut`}>
                        {isRecording ? <em className="keybind-recording">Press a key…</em> : (current || <span className="keybind-empty">None</span>)}
                      </div>
                      <div className="keybind-actions">
                        <button
                          className="button secondary"
                          type="button"
                          disabled={Boolean(recordingAction) && !isRecording}
                          onClick={() => {
                            setShortcutError('')
                            if (isRecording) {
                              void call(async () => {
                                await api()?.cancel_record_shortcut?.()
                                setRecordingAction(null)
                              })
                              return
                            }
                            setRecordingAction(row.action)
                            void call(async () => {
                              const result = await api()?.begin_record_shortcut?.(row.action)
                              if (result && result.ok === false) {
                                setShortcutError(String(result.error || 'Could not start recording.'))
                                setRecordingAction(null)
                              }
                            })
                          }}
                        >
                          {isRecording ? 'Cancel' : 'Record Keybind'}
                        </button>
                        <button
                          className="button secondary"
                          type="button"
                          disabled={Boolean(recordingAction)}
                          title="Clear shortcut"
                          onClick={() => {
                            setShortcutError('')
                            void call(() => api()?.clear_keyboard_shortcut?.(row.action))
                          }}
                        >
                          Clear
                        </button>
                        <button
                          className="button secondary"
                          type="button"
                          disabled={Boolean(recordingAction)}
                          title="Reset to default"
                          onClick={() => {
                            setShortcutError('')
                            void call(() => api()?.reset_keyboard_shortcut?.(row.action))
                          }}
                        >
                          Reset
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
              {(shortcutError || (state.shortcut_warnings && state.shortcut_warnings.length > 0)) && (
                <p className="update-error">
                  {shortcutError || state.shortcut_warnings?.join(' · ')}
                </p>
              )}
            </section>
            <section className="settings-section">
              <div className="section-header">
                <h3>Installation</h3>
                <span className="subtle-label">General</span>
              </div>
              <p className="settings-help">Current location</p>
              <code className="install-path">{state.install?.path || '—'}</code>
              {state.install?.diag_log ? (
                <p className="settings-help">Install diagnostic log: {state.install.diag_log}</p>
              ) : null}
              {!state.install?.frozen && (
                <p className="settings-help">Running from source — change location is available after installing with Setup.exe.</p>
              )}
              {(state.install?.status === 'downloading' || state.install?.status === 'installing') && (
                <div className="update-progress">
                  <div className="update-progress-bar"><i style={{ width: `${Math.round((state.install.progress || 0) * 100)}%` }} /></div>
                  <span>{state.install.message || (state.install.status === 'installing' ? 'Relocating…' : 'Downloading installer…')}</span>
                  {state.install.status === 'downloading' && (
                    <button className="button secondary" type="button" onClick={() => void call(() => api()?.cancel_install_migration?.())}>Cancel</button>
                  )}
                </div>
              )}
              {(installPickError || state.install?.error) && (
                <p className="update-error">{installPickError || state.install?.error}</p>
              )}
              {installConfirm ? (
                <div className="install-confirm">
                  <p className="update-notes">{installConfirm.message}</p>
                  {installConfirm.needsElevation ? (
                    <p className="settings-help">Setup will request administrator permission only for this folder, then reopen Huge. Cancelling UAC leaves your current install unchanged.</p>
                  ) : null}
                  <div className="update-actions">
                    <button className="button primary" type="button" onClick={() => void confirmInstallMigration()}>
                      {installConfirm.needsElevation ? 'Continue (may ask for admin)' : 'Confirm'}
                    </button>
                    <button className="button secondary" type="button" onClick={() => setInstallConfirm(null)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <button
                  className="button secondary full"
                  type="button"
                  disabled={!state.install?.can_change || state.install?.status === 'downloading' || state.install?.status === 'installing' || state.update?.status === 'downloading' || state.update?.status === 'installing'}
                  onClick={() => void beginChangeInstallLocation()}
                >
                  Change installation location
                </button>
              )}
            </section>
            <section className="settings-section">
              <div className="section-header">
                <h3>Updates</h3>
                <span className="subtle-label">v{state.app_version || state.update?.current_version || '—'}</span>
              </div>
              <div className="update-body">
                {state.update?.update_available && state.update.status !== 'downloading' && state.update.status !== 'installing' ? (
                  <div className="update-offer">
                    <strong>Huge v{state.update.latest_version} is available</strong>
                    <p className="update-notes">{state.update.release_notes || 'A newer stable release is ready.'}</p>
                    <div className="update-actions">
                      <button className="button primary" type="button" onClick={() => void call(() => api()?.start_update())}>Update Now</button>
                      <button className="button secondary" type="button" onClick={() => void call(() => api()?.dismiss_update())}>Later</button>
                    </div>
                  </div>
                ) : (
                  <p className="update-status-text">
                    {state.update?.status === 'checking'
                      ? 'Checking for updates…'
                      : state.update?.status === 'up_to_date'
                        ? (state.update?.message || `Huge is up to date (v${state.app_version || state.update?.current_version || '—'}).`)
                        : state.update?.message || 'Huge can check GitHub for new releases.'}
                  </p>
                )}
                {(state.update?.status === 'downloading' || state.update?.status === 'installing') && (
                  <div className="update-progress">
                    <div className="update-progress-bar"><i style={{ width: `${Math.round((state.update.progress || 0) * 100)}%` }} /></div>
                    <span>
                      {state.update.status === 'installing'
                        ? 'Starting installer…'
                        : `Downloading… ${Math.round((state.update.progress || 0) * 100)}%`}
                    </span>
                    {state.update.status === 'downloading' && (
                      <button className="button secondary" type="button" onClick={() => void call(() => api()?.cancel_update_download())}>Cancel</button>
                    )}
                  </div>
                )}
                {state.update?.error && (
                  <div className="update-error-block">
                    <p className="update-error">{state.update.error}</p>
                    <p className="update-note">Your current Huge install was left as-is. You can retry safely.</p>
                  </div>
                )}
              </div>
              <button
                className="button secondary full"
                type="button"
                disabled={state.update?.status === 'checking' || state.update?.status === 'downloading' || state.update?.status === 'installing'}
                onClick={() => void call(() => api()?.check_for_updates())}
              >
                {state.update?.status === 'error' ? 'Retry update check' : 'Check for Updates'}
              </button>
            </section>
            <section className="settings-section">
              <div className="section-header">
                <h3>Troubleshooting</h3>
              </div>
              <p className="settings-help">Export a ZIP of playback and sync diagnostics (play/pause, skips, START MOVIE, lobby networking) to your Desktop.</p>
              <button className="button secondary full" type="button" onClick={() => void call(() => exportSyncLogs())}>
                Export Sync Logs
              </button>
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
