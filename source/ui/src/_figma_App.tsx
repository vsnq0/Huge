import { useEffect, useRef, useState } from 'react'

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    play: <path d="m9 5 11 7-11 7Z" />, pause: <><path d="M8 5v14M16 5v14" /></>, folder: <path d="M3 7h7l2 2h9v11H3ZM3 7V4h7l2 3" />,
    plus: <path d="M12 5v14M5 12h14" />, arrow: <path d="M5 12h14m-5-5 5 5-5 5" />, check: <path d="m5 12 4 4L19 6" />,
    users: <><circle cx="9" cy="8" r="3" /><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 5" /></>, clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    link: <><path d="m10 14 4-4m-6 6-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 1 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" /></>,
    copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M15 8V4H4v11h4" /></>, settings: <><path d="m9 3-1 3-3 1v4l-2 2 2 3 3 1 1 4h5l1-4 3-1 3-3-2-2V7l-3-1-1-3Z" /><circle cx="12" cy="12" r="3" /></>,
    film: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" /></>, close: <path d="m6 6 12 12M6 18 18 6" />, exit: <><path d="M10 4H4v16h6M10 12h11m-4-4 4 4-4 4" /></>, crown: <path d="m3 6 5 5 4-7 4 7 5-5-2 13H5Z" />,
    rewind: <><path d="m11 5-8 7 8 7Zm10 0-8 7 8 7Z" /></>, forward: <><path d="m3 5 8 7-8 7Zm10 0 8 7-8 7Z" /></>, activity: <path d="M2 12h5l3-8 4 16 3-8h5" />,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.film}</svg>
}
const initialMembers = [ { name: 'vsnq', role: 'You · Host', image: 'photo-1535713875002-d1d0cf377fde', ready: false }, { name: 'Alex', role: 'Connected', image: 'photo-1500648767791-00dcc994a43e', ready: true }, { name: 'Sarah', role: 'Connected', image: 'photo-1534528741775-53994a69daeb', ready: true }, { name: 'Ryan', role: 'Connected', image: 'photo-1506794778202-cad84cf45f1d', ready: true } ]
const formatTime = (seconds: number) => [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map(value => String(value).padStart(2, '0')).join(':')

export default function App() {
  const [members, setMembers] = useState(initialMembers)
  const [lobby, setLobby] = useState('Friday movie night')
  const [host, setHost] = useState(true)
  const [playing, setPlaying] = useState(false)
  const [paused, setPaused] = useState(false)
  const [elapsed, setElapsed] = useState(754)
  const [position, setPosition] = useState(0)
  const [movie, setMovie] = useState('')
  const [path, setPath] = useState('C:\\Program Files\\DAUM\\PotPlayer\\PotPlayerMini64.exe')
  const [modal, setModal] = useState('')
  const [value, setValue] = useState('')
  const [address, setAddress] = useState('100.84.12.8:24816')
  const [copied, setCopied] = useState(false)
  const [profileImage, setProfileImage] = useState('/huge-logo.png')
  const [logs, setLogs] = useState([{ text: 'Sarah is ready to watch', time: '20:42:18', type: 'check' }, { text: 'Ryan is ready to watch', time: '20:42:12', type: 'check' }, { text: 'You selected Interstellar.2014.1080p.BluRay.mkv', time: '20:41:56', type: 'film' }, { text: 'Alex joined the lobby', time: '20:41:30', type: 'users' }, { text: 'You created Friday movie night', time: '20:40:02', type: 'plus' }])
  const fileRef = useRef<HTMLInputElement>(null)
  const profileRef = useRef<HTMLInputElement>(null)
  const allReady = members.length > 0 && members.every(member => member.ready)
  const log = (text: string, type = 'activity') => setLogs(previous => [{ text, type, time: new Date().toLocaleTimeString('en-GB') }, ...previous].slice(0, 50))
  const selectProfileImage = (file?: File) => {
    if (!file) return
    if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type) || file.size > 5 * 1024 * 1024) {
      log('Choose a PNG, JPG, WebP, or GIF profile picture under 5 MB', 'users')
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        setProfileImage(reader.result)
        log('You updated your profile picture', 'users')
      }
    }
    reader.onerror = () => log('Unable to read your profile picture. Please try again.', 'users')
    reader.readAsDataURL(file)
  }
  useEffect(() => { const timer = window.setInterval(() => { if (lobby) setElapsed(previous => previous + 1); if (playing) setPosition(previous => Math.min(10140, previous + 1)) }, 1000); return () => clearInterval(timer) }, [lobby, playing])
  const openModal = (type: string) => { setValue(type === 'path' ? path : ''); setModal(type) }
  const toggleReady = () => { if (!movie || !lobby) return; setMembers(previous => previous.map((member, index) => index === 0 ? { ...member, ready: !member.ready } : member)); log(members[0].ready ? 'You are no longer ready' : 'You are ready to watch', 'check') }
  const playback = () => { if (!lobby || !movie || !allReady) return; setPlaying(!playing); setPaused(playing); log(playing ? 'You paused the movie' : position ? 'You resumed the movie' : 'You started the movie', playing ? 'pause' : 'play') }
  const skip = (seconds: number) => { setPosition(previous => Math.max(0, Math.min(10140, previous + seconds))); log(`You skipped ${Math.abs(seconds)} seconds ${seconds > 0 ? 'forward' : 'backward'}`) }
  const submit = () => {
    if (!value.trim()) return
    if (modal === 'path') { setPath(value); log('PotPlayer path updated', 'settings') }
    else { setLobby(modal === 'create' ? value : "Friend's movie night"); setHost(modal === 'create'); if (modal === 'join') setAddress(value); setElapsed(0); setPlaying(false); setPosition(0); setMembers([{ ...initialMembers[0], role: modal === 'create' ? 'You · Host' : 'You', ready: false }]); log(modal === 'create' ? `You created ${value}` : `You joined ${value}`, 'users') }
    setModal('')
  }
  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="#"><img className="gothic-brand-icon" src="/huge-gothic-logo.svg" alt="Huge gothic sigil" /><span className="brand-name">Huge</span></a><div className="top-right"><span className="connection"><i /> Tailscale connected</span><button className="top-avatar profile-picker" aria-label="Select your profile picture" title="Change profile picture" onClick={() => profileRef.current?.click()}><img className={profileImage === '/huge-logo.png' ? 'logo-profile' : ''} src={profileImage} alt="Your profile picture" /></button><button className="button secondary profile-upload" onClick={() => profileRef.current?.click()}><Icon name="folder" size={15} />Upload photo</button><input hidden ref={profileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={event => { selectProfileImage(event.target.files?.[0]); event.target.value = '' }} /></div></header>
    <main>
      <div className="page-heading lobby-toolbar"><div className="lobby-actions"><button className="button secondary" onClick={() => openModal('join')}><Icon name="link" size={17} />Join lobby</button><button className="button primary" onClick={() => openModal('create')}><Icon name="plus" size={18} />Create lobby</button></div></div>
      <div className="dashboard">
        <div className="main-column">
          <section className="movie-card">
            <div className="cinematic-image" /><div className="movie-shade" />
            <div className="movie-top"><span className="pill"><span className="purple-dot" /> {playing ? 'NOW PLAYING' : paused ? 'PLAYBACK PAUSED' : 'TONIGHT’S FEATURE'}</span><span className="player-tag"><Icon name="film" size={14} /> PotPlayer</span></div>
            <div className="movie-content"><div className="movie-kicker">A JOURNEY BEYOND THE STARS</div><h2>{movie.startsWith('Interstellar') ? 'Interstellar' : movie ? movie.replace(/\.[^.]+$/, '').replaceAll('.', ' ') : 'Your next great movie'}</h2><div className="movie-meta">{movie.startsWith('Interstellar') ? <><span>2014</span><b>·</b><span>Sci-fi / Adventure</span><b>·</b><span>2h 49m</span><span className="quality">1080p</span></> : <span>{movie ? 'Local movie file' : 'Select a file to get started'}</span>}</div></div>
            <div className="file-strip"><div className="file-info"><span className="file-icon"><Icon name="film" size={18} /></span><div><span className="filename">{movie || 'No movie selected'}</span><span className="file-status"><i />{movie ? 'Local file selected · Ready for PotPlayer' : 'Waiting for a movie file'}</span></div></div><button className="button glass" onClick={() => fileRef.current?.click()}><Icon name="folder" size={16} />Select movie</button><input hidden type="file" accept="video/*,.mkv,.avi" ref={fileRef} onChange={event => { const file = event.target.files?.[0]; if (file) { setMovie(file.name); setPosition(0); setPlaying(false); setMembers(previous => previous.map(member => ({ ...member, ready: false }))); log(`You selected ${file.name}`, 'film') } }} /></div>
          </section>
          <section className="panel playback-panel"><div className="section-header"><h3><Icon name="play" size={17} />Playback</h3><span className="subtle-label"><span className="purple-dot" /> Synced for everyone</span></div>
            <div className="timeline"><span className="time">{formatTime(position)}</span><input aria-label="Movie timeline" type="range" min="0" max="10140" value={position} disabled={!movie || !lobby} onChange={event => setPosition(Number(event.target.value))} onPointerUp={() => log('You changed the playback position')} /><span className="time muted">02:49:00</span></div>
            <div className="transport"><button disabled={!lobby || !movie} onClick={() => skip(-30)}><Icon name="rewind" /><span>30s</span></button><button disabled={!lobby || !movie} onClick={() => skip(-5)}><span className="skip-small">↶</span><span>5s</span></button><button className="play-button" aria-label={playing ? 'Pause movie' : 'Play movie'} onClick={playback} disabled={!allReady || !lobby || !movie}><Icon name={playing ? 'pause' : 'play'} size={25} /></button><button disabled={!lobby || !movie} onClick={() => skip(5)}><span className="skip-small">↷</span><span>5s</span></button><button disabled={!lobby || !movie} onClick={() => skip(30)}><Icon name="forward" /><span>30s</span></button></div>
            <div className="ready-bar"><div><span className={`status-dot ${allReady ? 'green' : 'amber'}`} /><span>{allReady ? 'Everyone is ready. Let’s roll.' : `${members.filter(member => member.ready).length} of ${members.length} ready`}</span><small>{allReady ? 'Your movie night is one click away.' : 'The movie starts when everyone is ready.'}</small></div><button className={`button ${members[0]?.ready ? 'ready-button' : 'primary'}`} disabled={!movie || !lobby} onClick={toggleReady}><Icon name="check" size={17} />{members[0]?.ready ? 'You’re ready' : 'Ready up'}</button></div>
          </section>
          <section className="panel log-panel"><div className="section-header"><h3><Icon name="activity" size={18} />Live log<span className="live-badge">LIVE</span></h3><span className="subtle-label">{logs.length} events</span></div><div className="log-list">{logs.map((entry, index) => <div className="log-row" key={`${entry.time}-${index}`}><span className={`log-icon ${entry.type === 'check' ? 'green-text' : ''}`}><Icon name={entry.type} size={15} /></span><span>{entry.text}</span><time>{entry.time}</time></div>)}</div></section>
        </div>
        <aside>
          <section className="panel lobby-panel"><div className="section-header"><h3>THE LOBBY</h3><span className="pill tiny"><i />{lobby ? 'Connected' : 'Offline'}</span></div><h2>{lobby || 'No active lobby'}</h2><div className="host-line"><Icon name="crown" size={15} /><span>Hosted by <strong>{host ? 'vsnq' : 'your friend'}</strong></span>{host && <span className="you-badge">YOU</span>}</div><div className="address-box"><div><span>TAILSCALE ADDRESS</span><code>{lobby ? address : '—'}</code></div><button aria-label="Copy lobby address" disabled={!lobby} onClick={async () => { try { await navigator.clipboard.writeText(address); setCopied(true); setTimeout(() => setCopied(false), 2000) } catch { log('Copy address manually: ' + address) } }}><Icon name={copied ? 'check' : 'copy'} size={18} /></button></div><div className="lobby-timer"><span><Icon name="clock" size={16} />Time together</span><strong>{formatTime(elapsed)}</strong></div></section>
          <section className="panel members-panel"><div className="section-header"><h3><Icon name="users" size={18} />In this lobby <span className="count">{lobby ? members.length : 0}</span></h3><span className="subtle-label">{members.filter(member => member.ready).length} ready</span></div><div className="members-list">{lobby && members.map((member, index) => <div className="member" key={member.name}><div className={`avatar ${member.ready ? index === 0 && paused ? 'paused' : 'is-ready' : 'not-ready'}`}><img className={index === 0 && profileImage === '/huge-logo.png' ? 'logo-profile' : ''} src={index === 0 ? profileImage : `https://images.unsplash.com/${member.image}?w=100&h=100&fit=crop&crop=faces`} alt="" /><span>{member.ready ? <Icon name={index === 0 && paused ? 'pause' : 'check'} size={9} /> : '−'}</span></div><div className="member-info"><strong>{member.name}{index === 0 && <span> (you)</span>}</strong><small>{member.role}</small></div><span className={`member-status ${member.ready ? index === 0 && paused ? 'amber-text' : 'green-text' : 'red-text'}`}>{member.ready ? index === 0 && paused ? 'Paused' : 'Ready' : 'Not ready'}</span>{host && index > 0 && <button className="kick-button" title={`Kick ${member.name}`} aria-label={`Kick ${member.name}`} onClick={() => { setMembers(previous => previous.filter(person => person.name !== member.name)); log(`You removed ${member.name} from the lobby`, 'exit') }}><Icon name="close" size={13} /></button>}</div>)}</div><div className="member-legend"><span><i className="green" />Ready</span><span><i className="red" />Not ready</span><span><i className="amber" />Paused</span></div>{host && <button className="everyone-button" disabled={!movie || !lobby} onClick={() => { setMembers(previous => previous.map(member => ({ ...member, ready: true }))); log('Host marked everyone ready', 'check') }}><Icon name="check" size={15} />Ready everyone<span>Host control</span></button>}<button className="leave-button" disabled={!lobby} onClick={() => { log('You left the lobby', 'exit'); setLobby(''); setMembers([]); setPlaying(false); setElapsed(0) }}><Icon name="exit" size={15} />Leave lobby</button></section>
          <section className="panel potplayer-panel"><div className="section-header"><h3><span className="potplayer-logo"><Icon name="play" size={12} /></span>PotPlayer</h3><span className="green-text small"><i className="status-dot green" />Configured</span></div><p>Your player. Perfectly in sync.</p><div className="path-text" title={path}>{path}</div><button className="button secondary full" onClick={() => openModal('path')}><Icon name="folder" size={16} />Select PotPlayer path<Icon name="arrow" size={16} /></button><small>64-bit · Local player</small></section>
        </aside>
      </div>
      <footer><span>made by vsnq</span><span className="footer-note">"Why be ashamed of what you want?"</span></footer>
    </main>
    {modal && <div className="modal-backdrop" onClick={() => setModal('')}><form className="modal panel" onClick={event => event.stopPropagation()} onSubmit={event => { event.preventDefault(); submit() }}><button type="button" className="modal-close" aria-label="Close dialog" onClick={() => setModal('')}><Icon name="close" /></button><span className="modal-symbol"><Icon name={modal === 'path' ? 'folder' : 'users'} size={26} /></span><h2>{modal === 'create' ? 'Your night, your lobby.' : modal === 'join' ? 'Meet you at the movies.' : 'Connect your player.'}</h2><p>{modal === 'create' ? 'Give your lobby a name and bring your people together.' : modal === 'join' ? 'Enter your friend’s Tailscale address to join their lobby.' : 'Enter the full path to your PotPlayer executable. Browser previews cannot launch local applications.'}</p><label htmlFor="modal-input">{modal === 'create' ? 'Lobby name' : modal === 'join' ? 'Host address' : 'PotPlayer executable path'}</label><input id="modal-input" autoFocus required value={value} onChange={event => setValue(event.target.value)} placeholder={modal === 'create' ? 'Friday movie night' : modal === 'join' ? '100.84.12.8:24816' : 'C:\\Program Files\\DAUM\\PotPlayer\\PotPlayerMini64.exe'} /><button className="button primary full" type="submit">{modal === 'create' ? 'Create lobby' : modal === 'join' ? 'Join lobby' : 'Save path'}<Icon name="arrow" size={18} /></button></form></div>}
  </div>
}
