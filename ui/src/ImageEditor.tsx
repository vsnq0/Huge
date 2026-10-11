import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

export type ImageEditSession = {
  mode: 'banner' | 'avatar'
  path: string
  previewUrl: string
  previewWidth: number
  previewHeight: number
  sourceWidth: number
  sourceHeight: number
}

export type ImageCropResult = {
  path: string
  mode: 'banner' | 'avatar'
  x: number
  y: number
  width: number
  height: number
}

type Props = {
  session: ImageEditSession
  onCancel: () => void
  onSave: (crop: ImageCropResult) => void
}

/** Matches the Huge movie-card banner plane (~2.72:1). */
const BANNER_ASPECT = 2.72
const MIN_ZOOM = 1
const MAX_ZOOM = 3

function clamp(n: number, min: number, max: number) {
  return Math.min(max, Math.max(min, n))
}

/**
 * Discord-style crop editor.
 * Frame (w×h) is the selection. Image is drawn with CSS cover+zoom.
 * Crop math maps the visible frame rect into source-image pixels.
 */
export default function ImageEditor({ session, onCancel, onSave }: Props) {
  const frameRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)
  const [frame, setFrame] = useState({ w: 0, h: 0 })
  const [zoom, setZoom] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [saving, setSaving] = useState(false)

  const isAvatar = session.mode === 'avatar'
  const iw = Math.max(1, session.previewWidth)
  const ih = Math.max(1, session.previewHeight)

  // Reset framing whenever a new picture is opened.
  useEffect(() => {
    setZoom(1)
    setOffset({ x: 0, y: 0 })
    setSaving(false)
  }, [session.path, session.mode, session.previewUrl])

  useEffect(() => {
    const el = frameRef.current
    if (!el) return
    const sync = () => {
      const rect = el.getBoundingClientRect()
      if (rect.width > 2 && rect.height > 2) {
        setFrame({ w: rect.width, h: rect.height })
      }
    }
    sync()
    const ro = new ResizeObserver(sync)
    ro.observe(el)
    return () => ro.disconnect()
  }, [session.mode, session.path])

  const coverScale = useMemo(() => {
    if (frame.w <= 0 || frame.h <= 0) return 1
    return Math.max(frame.w / iw, frame.h / ih)
  }, [frame.w, frame.h, iw, ih])

  const scale = coverScale * zoom
  const drawnW = iw * scale
  const drawnH = ih * scale
  const maxOffsetX = Math.max(0, (drawnW - frame.w) / 2)
  const maxOffsetY = Math.max(0, (drawnH - frame.h) / 2)

  const clampedOffset = useMemo(
    () => ({
      x: clamp(offset.x, -maxOffsetX, maxOffsetX),
      y: clamp(offset.y, -maxOffsetY, maxOffsetY),
    }),
    [offset.x, offset.y, maxOffsetX, maxOffsetY],
  )

  // Image top-left inside the frame (selection outline = the frame itself).
  const imgLeft = frame.w > 0 ? (frame.w - drawnW) / 2 + clampedOffset.x : 0
  const imgTop = frame.h > 0 ? (frame.h - drawnH) / 2 + clampedOffset.y : 0

  const setZoomPreservingCenter = useCallback(
    (nextZoom: number) => {
      const z = clamp(nextZoom, MIN_ZOOM, MAX_ZOOM)
      if (frame.w <= 0 || frame.h <= 0) {
        setZoom(z)
        return
      }
      const oldScale = coverScale * zoom
      const newScale = coverScale * z
      // Keep the image point currently under the frame center fixed.
      const centerX = (frame.w / 2 - imgLeft) / Math.max(oldScale, 1e-6)
      const centerY = (frame.h / 2 - imgTop) / Math.max(oldScale, 1e-6)
      const newDrawnW = iw * newScale
      const newDrawnH = ih * newScale
      const newImgLeft = frame.w / 2 - centerX * newScale
      const newImgTop = frame.h / 2 - centerY * newScale
      const newOx = newImgLeft - (frame.w - newDrawnW) / 2
      const newOy = newImgTop - (frame.h - newDrawnH) / 2
      const maxX = Math.max(0, (newDrawnW - frame.w) / 2)
      const maxY = Math.max(0, (newDrawnH - frame.h) / 2)
      setZoom(Number(z.toFixed(3)))
      setOffset({
        x: clamp(newOx, -maxX, maxX),
        y: clamp(newOy, -maxY, maxY),
      })
    },
    [coverScale, zoom, frame.w, frame.h, imgLeft, imgTop, iw, ih],
  )

  const onPointerDown = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = {
      x: e.clientX,
      y: e.clientY,
      ox: clampedOffset.x,
      oy: clampedOffset.y,
    }
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const drag = dragRef.current
    if (!drag) return
    setOffset({
      x: clamp(drag.ox + (e.clientX - drag.x), -maxOffsetX, maxOffsetX),
      y: clamp(drag.oy + (e.clientY - drag.y), -maxOffsetY, maxOffsetY),
    })
  }

  const onPointerUp = (e: React.PointerEvent) => {
    dragRef.current = null
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      /* ignore */
    }
  }

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    setZoomPreservingCenter(zoom + (e.deltaY < 0 ? 0.08 : -0.08))
  }

  const buildCrop = useCallback((): ImageCropResult => {
    const sx = session.sourceWidth / iw
    const sy = session.sourceHeight / ih
    // Frame (0,0)-(frame.w,frame.h) in preview pixels:
    let px = -imgLeft / Math.max(scale, 1e-6)
    let py = -imgTop / Math.max(scale, 1e-6)
    let pw = frame.w / Math.max(scale, 1e-6)
    let ph = frame.h / Math.max(scale, 1e-6)

    // Avatar selection is circular but crops the bounding square.
    if (isAvatar) {
      const side = Math.min(pw, ph)
      px += (pw - side) / 2
      py += (ph - side) / 2
      pw = side
      ph = side
    }

    // Clamp to preview bounds, then map to source pixels.
    px = clamp(px, 0, Math.max(0, iw - 1))
    py = clamp(py, 0, Math.max(0, ih - 1))
    pw = clamp(pw, 1, iw - px)
    ph = clamp(ph, 1, ih - py)

    let x = px * sx
    let y = py * sy
    let width = pw * sx
    let height = ph * sy

    x = clamp(x, 0, Math.max(0, session.sourceWidth - 1))
    y = clamp(y, 0, Math.max(0, session.sourceHeight - 1))
    width = clamp(width, 1, session.sourceWidth - x)
    height = clamp(height, 1, session.sourceHeight - y)

    if (isAvatar) {
      const side = Math.min(width, height)
      x += (width - side) / 2
      y += (height - side) / 2
      width = side
      height = side
    }

    return {
      path: session.path,
      mode: session.mode,
      x,
      y,
      width,
      height,
    }
  }, [session, iw, ih, imgLeft, imgTop, scale, frame.w, frame.h, isAvatar])

  const handleSave = () => {
    if (saving || frame.w <= 0) return
    setSaving(true)
    onSave(buildCrop())
  }

  const previewScale = isAvatar
    ? frame.w > 0
      ? 72 / frame.w
      : 0
    : frame.w > 0
      ? 220 / frame.w
      : 0

  return (
    <div className="image-editor-backdrop" role="dialog" aria-modal="true" aria-label={isAvatar ? 'Edit profile picture' : 'Edit banner'}>
      <div className={`image-editor-panel ${isAvatar ? 'is-avatar' : 'is-banner'}`}>
        <header className="image-editor-header">
          <div>
            <h2>{isAvatar ? 'Edit profile picture' : 'Edit banner'}</h2>
            <p>Drag to reposition · scroll or use the slider to zoom</p>
          </div>
          <button type="button" className="image-editor-close" onClick={onCancel} aria-label="Cancel">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 6 12 12M6 18 18 6" /></svg>
          </button>
        </header>

        <div className="image-editor-stage">
          <div className={`image-editor-frame-shell ${isAvatar ? 'circle' : 'banner'}`}>
            <div
              ref={frameRef}
              className={`image-editor-frame ${isAvatar ? 'circle' : 'banner'}`}
              style={isAvatar ? undefined : { aspectRatio: `${BANNER_ASPECT} / 1` }}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              onWheel={onWheel}
            >
              {frame.w > 0 && (
                <img
                  className="image-editor-photo"
                  src={session.previewUrl}
                  alt=""
                  draggable={false}
                  style={{
                    width: drawnW,
                    height: drawnH,
                    left: imgLeft,
                    top: imgTop,
                  }}
                />
              )}
              {isAvatar ? <div className="image-editor-circle-ring" aria-hidden /> : <div className="image-editor-banner-ring" aria-hidden />}
            </div>
          </div>

          {isAvatar ? (
            <div className="image-editor-live-preview" aria-hidden>
              <span>Preview</span>
              <div className="image-editor-live-circle">
                {frame.w > 0 && (
                  <img
                    src={session.previewUrl}
                    alt=""
                    draggable={false}
                    style={{
                      width: drawnW * previewScale,
                      height: drawnH * previewScale,
                      left: imgLeft * previewScale,
                      top: imgTop * previewScale,
                    }}
                  />
                )}
              </div>
            </div>
          ) : (
            <div className="image-editor-live-preview banner-preview" aria-hidden>
              <span>Preview</span>
              <div className="image-editor-live-banner" style={{ aspectRatio: `${BANNER_ASPECT} / 1` }}>
                {frame.w > 0 && (
                  <img
                    src={session.previewUrl}
                    alt=""
                    draggable={false}
                    style={{
                      width: drawnW * previewScale,
                      height: drawnH * previewScale,
                      left: imgLeft * previewScale,
                      top: imgTop * previewScale,
                    }}
                  />
                )}
              </div>
            </div>
          )}
        </div>

        <div className="image-editor-controls">
          <label className="image-editor-zoom">
            <span>Zoom</span>
            <input
              type="range"
              min={MIN_ZOOM}
              max={MAX_ZOOM}
              step={0.01}
              value={zoom}
              onChange={(e) => setZoomPreservingCenter(Number(e.target.value))}
            />
            <em>{Math.round(zoom * 100)}%</em>
          </label>
          <div className="image-editor-actions">
            <button type="button" className="button secondary" onClick={onCancel} disabled={saving}>Cancel</button>
            <button type="button" className="button primary" onClick={handleSave} disabled={saving || frame.w <= 0}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
