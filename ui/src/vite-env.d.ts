/// <reference types="vite/client" />

export type ImageEditPrepareResult = {
  ok?: boolean
  cancelled?: boolean
  error?: string
  mode?: 'banner' | 'avatar'
  path?: string
  preview_b64?: string
  preview_width?: number
  preview_height?: number
  source_width?: number
  source_height?: number
  mime?: string
}

export type ImageCropPayload = {
  path: string
  mode: 'banner' | 'avatar'
  x: number
  y: number
  width: number
  height: number
}

export type HugeMember = {
  id: string
  name: string
  ready?: boolean
  movie_loaded?: boolean
  host?: boolean
  avatar?: string
  paused?: boolean
}

export type HugeLog = {
  id?: number
  time: string
  actor: string
  message: string
}

export type UpdateInfo = {
  status: string
  current_version: string
  latest_version: string
  update_available: boolean
  release_name: string
  release_notes: string
  html_url: string
  asset_name: string
  progress: number
  bytes_done: number
  bytes_total: number
  error: string
  message: string
  sha256_verified: boolean
  sha256_skipped_reason: string
  banner_dismissed?: boolean
}

export type InstallInfo = {
  path: string
  frozen: boolean
  can_change: boolean
  default_path: string
  status: string
  message: string
  error: string
  progress: number
  diag_log?: string
}

export type HugeState = {
  app: string
  credit: string
  app_version?: string
  role: string | null
  user_id: string
  username: string
  avatar_b64: string
  banner_b64: string
  potplayer_path: string
  pot_ready: boolean
  pot_badge: string
  pot_badge_kind: string
  movie_path: string
  movie_name: string
  movie_loaded: boolean
  lobby_name: string
  host_name: string
  address: string
  users: HugeMember[]
  logs: HugeLog[]
  started: boolean
  ready: boolean
  elapsed: number
  elapsed_text: string
  connection_text: string
  connection_kind: string
  position: number
  duration: number
  playing: boolean
  paused: boolean
  all_ready: boolean
  member_count: number
  ready_count: number
  can_create: boolean
  can_join: boolean
  can_select_movie: boolean
  can_ready: boolean
  can_ready_all: boolean
  can_start: boolean
  can_playback: boolean
  can_skip: boolean
  host: boolean
  in_lobby: boolean
  last_error: string
  last_info: string
  lobby_name_draft: string
  join_address_draft: string
  tailscale: boolean
  closing: boolean
  quote: string
  update?: UpdateInfo
  install?: InstallInfo
  title_font?: string
  title_font_size?: number
  keyboard_shortcuts?: {
    play_pause?: string
    seek_back?: string
    seek_forward?: string
  }
  shortcut_warnings?: string[]
  shortcut_recording?: boolean
}

declare global {
  interface Window {
    pywebview?: {
      api: {
        get_state: () => Promise<HugeState>
        set_name: (value: string) => Promise<{ ok?: boolean } | HugeState>
        flush_name?: (value: string) => Promise<{ ok?: boolean; username?: string }>
        set_lobby_name: (value: string) => Promise<HugeState>
        set_join_address: (value: string) => Promise<HugeState>
        create_lobby: (name: string) => Promise<HugeState>
        join_lobby: (address: string) => Promise<HugeState>
        leave_lobby: () => Promise<HugeState>
        copy_address: () => Promise<HugeState & { ok?: boolean; address?: string }>
        choose_avatar: () => Promise<ImageEditPrepareResult>
        choose_banner: () => Promise<ImageEditPrepareResult>
        save_image_crop: (payload: ImageCropPayload) => Promise<HugeState & { ok?: boolean; error?: string }>
        choose_potplayer: () => Promise<HugeState>
        choose_movie: () => Promise<HugeState>
        toggle_ready: () => Promise<HugeState>
        ready_everyone: () => Promise<HugeState>
        start_movie: () => Promise<HugeState>
        play_pause: () => Promise<HugeState>
        skip: (ms: number) => Promise<HugeState>
        fullscreen: () => Promise<HugeState>
        kick: (userId: string) => Promise<HugeState>
        export_sync_logs?: () => Promise<{ ok?: boolean; path?: string; error?: string }>
        check_for_updates?: () => Promise<UpdateInfo & { ok?: boolean; error?: string }>
        get_update_state?: () => Promise<UpdateInfo>
        dismiss_update?: () => Promise<UpdateInfo & { ok?: boolean }>
        start_update?: () => Promise<UpdateInfo & { ok?: boolean; error?: string }>
        cancel_update_download?: () => Promise<UpdateInfo & { ok?: boolean }>
        get_keyboard_shortcuts?: () => Promise<{
          ok?: boolean
          shortcuts?: HugeState['keyboard_shortcuts']
          warnings?: string[]
          recording?: boolean
          actions?: string[]
        }>
        set_keyboard_shortcut?: (action: string, shortcut: string) => Promise<HugeState & { ok?: boolean; error?: string; warnings?: string[] }>
        clear_keyboard_shortcut?: (action: string) => Promise<HugeState & { ok?: boolean; error?: string }>
        reset_keyboard_shortcut?: (action?: string | null) => Promise<HugeState & { ok?: boolean; error?: string }>
        begin_record_shortcut?: (action: string) => Promise<HugeState & { ok?: boolean; error?: string; recording?: boolean }>
        cancel_record_shortcut?: () => Promise<HugeState & { ok?: boolean }>
        set_text_entry_active?: (active: boolean) => Promise<{ ok?: boolean }>
      }
    }
    __hugePush?: (state: HugeState) => void
    __HUGE_STATE_URL?: string
  }
}

export {}
