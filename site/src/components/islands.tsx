import { useEffect, useMemo, useRef, useState } from 'react';

type SortDir = 'asc' | 'desc';

function order<T>(rows: T[], get: (r: T) => number | string, dir: SortDir): T[] {
  const mul = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = get(a), vb = get(b);
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * mul;
    return String(va ?? '').localeCompare(String(vb ?? '')) * mul;
  });
}

function SortTh({ label, active, dir, onClick, title }: {
  label: string; active: boolean; dir: SortDir; onClick: () => void; title?: string;
}) {
  return (
    <th onClick={onClick} title={title} className={'sortth' + (active ? ' on' : '')}>
      {label}{active ? (dir === 'asc' ? ' ↑' : ' ↓') : ''}
    </th>
  );
}

function toggleSort<K extends string>(cur: { key: K; dir: SortDir }, key: K, ascDefault: K[]) {
  if (cur.key === key) return { key, dir: cur.dir === 'asc' ? 'desc' : 'asc' as SortDir };
  return { key, dir: ascDefault.includes(key) ? 'asc' as SortDir : 'desc' as SortDir };
}

export type Team = { id: number; name: string; title: number; advance: number;
  elo?: number; fast?: number; group?: string;
  roster: { id: number; name: string; elo: number }[] };
export type Matchup = { a: number; b: number; p: number;
  factors: { f: string; v: number }[] };

export function Matchup({ teams, matchups }: { teams: Team[]; matchups: Matchup[] }) {
  const [a, setA] = useState(teams[0]?.id);
  const [b, setB] = useState(teams[1]?.id);
  const m = useMemo(() => matchups.find((x) => x.a === a && x.b === b), [a, b, matchups]);
  const na = (id: number) => teams.find((t) => t.id === id)?.name ?? id;
  return (
    <div>
      <div className="row">
        <select aria-label="Team A" value={a} onChange={(e) => setA(Number(e.target.value))}>
          {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <button type="button" className="swapbtn" aria-label="Swap teams" title="Swap teams"
          onClick={() => { setA(b); setB(a); }}>⇄</button>
        <span className="mut">vs</span>
        <select aria-label="Team B" value={b} onChange={(e) => setB(Number(e.target.value))}>
          {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </div>
      {m && a !== b && (
        <div className="matchup-meta">
          <div className="matchup-prob">{(m.p * 100).toFixed(1)}%</div>
          <div className="matchup-sub">{na(a)} beats {na(b)}</div>
          <div className="bar"><i style={{ width: `${m.p * 100}%` }} /></div>
          <ul className="mut factors">
            {m.factors.map((f) => (
              <li key={f.f}><span className="mono">{f.f.replace('_diff', '')}</span>: {f.v > 0 ? '+' : ''}{f.v.toFixed(2)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function OddsTable({ teams }: { teams: Team[] }) {
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<{ key: 'team' | 'title' | 'advance'; dir: SortDir }>({ key: 'title', dir: 'desc' });
  const rows = useMemo(() => {
    const f = teams.filter((t) => t.name.toLowerCase().includes(q.toLowerCase()));
    const get = (t: Team): number | string =>
      sort.key === 'team' ? t.name : sort.key === 'title' ? t.title : t.advance;
    return order(f, get, sort.dir);
  }, [teams, q, sort]);
  const toggle = (key: typeof sort.key) => setSort((s) => toggleSort(s, key, ['team']));
  return (
    <div>
      <div className="row island-filter">
        <input className="field" aria-label="Filter teams" placeholder="Filter teams"
          value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <table>
        <thead><tr><th>#</th>
          <SortTh label="Team" active={sort.key === 'team'} dir={sort.dir} onClick={() => toggle('team')} />
          <SortTh label="Title" active={sort.key === 'title'} dir={sort.dir} onClick={() => toggle('title')} />
          <SortTh label="Advance" active={sort.key === 'advance'} dir={sort.dir} onClick={() => toggle('advance')} />
          <th></th></tr></thead>
        <tbody>
          {rows.map((t, i) => (
            <tr key={t.id} id={'oddsteam-' + t.id}>
              <td className="num">{i + 1}</td><td>{t.name}</td>
              <td className="num">{(t.title * 100).toFixed(1)}%</td>
              <td className="num">{(t.advance * 100).toFixed(1)}%</td>
              <td style={{ minWidth: 90 }}><div className="bar"><i
                style={{ width: `${t.title * 100}%` }} /></div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export type PowerInput = { id: number; name: string; title: number };

const REGION_LABELS: Record<string, string> =
  { AM: 'Americas', EMEA: 'EMEA', PAC: 'Pacific', CN: 'China' };

export function PowerTable({ teams, matchups, regions }: {
  teams: PowerInput[]; matchups: Matchup[]; regions: Record<number, string>;
}) {
  const [region, setRegion] = useState('all');
  const [sort, setSort] = useState<{ key: 'team' | 'region' | 'power' | 'title'; dir: SortDir }>({ key: 'power', dir: 'desc' });
  const rows = useMemo(() => {
    const pmap = new Map<number, number>();
    for (const m of matchups) pmap.set(m.a * 1e7 + m.b, m.p);
    const f = teams
      .filter((t) => region === 'all' || regions[t.id] === region)
      .map((t) => {
        let s = 0, n = 0;
        for (const o of teams) {
          if (o.id === t.id) continue;
          const p = pmap.get(t.id * 1e7 + o.id);
          if (p !== undefined) { s += p; n++; }
        }
        return { ...t, region: regions[t.id] ?? '', power: n ? s / n : 0 };
      });
    const get = (t: typeof f[number]): number | string =>
      sort.key === 'team' ? t.name
      : sort.key === 'region' ? (REGION_LABELS[t.region] ?? t.region)
      : sort.key === 'power' ? t.power : t.title;
    return order(f, get, sort.dir);
  }, [teams, matchups, regions, region, sort]);
  const toggle = (key: typeof sort.key) => setSort((s) => toggleSort(s, key, ['team', 'region']));
  return (
    <div>
      <div className="row island-filter">
        <select aria-label="Region filter" value={region} onChange={(e) => setRegion(e.target.value)}>
          <option value="all">All regions</option>
          {Object.entries(REGION_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <span className="mut">{rows.length} teams</span>
      </div>
      <table>
        <thead><tr><th>#</th>
          <SortTh label="Team" active={sort.key === 'team'} dir={sort.dir} onClick={() => toggle('team')} />
          <SortTh label="Region" active={sort.key === 'region'} dir={sort.dir} onClick={() => toggle('region')} />
          <SortTh label="Power" active={sort.key === 'power'} dir={sort.dir} onClick={() => toggle('power')}
            title="Average model win probability vs the Champions field" />
          <SortTh label="Title" active={sort.key === 'title'} dir={sort.dir} onClick={() => toggle('title')} />
          <th></th></tr></thead>
        <tbody>
          {rows.map((t, i) => (
            <tr key={t.id} id={'eloteam-' + t.id}>
              <td className="num">{i + 1}</td><td>{t.name}</td>
              <td className="mut">{REGION_LABELS[t.region] ?? t.region}</td>
              <td className="num">{(t.power * 100).toFixed(1)}</td>
              <td className="num">{(t.title * 100).toFixed(1)}%</td>
              <td style={{ minWidth: 90 }}><div className="bar"><i
                style={{ width: `${t.power * 100}%` }} /></div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type SwingRow = { player_id: number; name: string; rating: number;
  role?: string; rounds: number; champs?: boolean; region?: string | null;
  team?: string };
export type SwingBoards = Record<string, SwingRow[]>;

const ROLE_LABELS: Record<string, string> =
  { D: 'Duelist', C: 'Controller', I: 'Initiator', S: 'Sentinel' };

const STAGES = [
  { id: 'all', label: 'All 2026' },
  { id: 'stage2', label: 'Stage 2' },
  { id: 'stage1', label: 'Stage 1' },
  { id: 'kickoff', label: 'Kickoff' },
];

export function SwingBoard({ boards, stages, hidePool }: {
  boards: SwingBoards; stages?: { id: string; label: string }[]; hidePool?: boolean }) {
  const stageOpts = stages ?? STAGES;
  const [champs, setChamps] = useState(!hidePool);
  const [stage, setStage] = useState(stageOpts[0].id);
  const [region, setRegion] = useState('all');
  const [role, setRole] = useState('all');
  const [pq, setPq] = useState('');
  const [sort, setSort] = useState<{ key: 'player' | 'rating' | 'role' | 'rounds'; dir: SortDir }>({ key: 'rating', dir: 'desc' });
  const list = useMemo(() => {
    const rows = boards[stage] ?? boards['all'] ?? [];
    const f = rows
      .filter((r) => !champs || r.champs)
      .filter((r) => region === 'all' || r.region === region)
      .filter((r) => role === 'all' || r.role === role)
      .filter((r) => r.name.toLowerCase().includes(pq.toLowerCase()));
    const get = (r: SwingRow): number | string =>
      sort.key === 'player' ? r.name
      : sort.key === 'rating' ? r.rating
      : sort.key === 'role' ? (r.role ?? '') : r.rounds;
    return order(f, get, sort.dir);
  }, [boards, stage, champs, region, role, pq, sort]);
  const toggle = (key: typeof sort.key) => setSort((s) => toggleSort(s, key, ['player', 'role']));
  const showTeam = useMemo(() =>
    Object.values(boards).some((rows) => rows.some((r) => r.team)), [boards]);
  // Original rank = position in the unfiltered stage board by rating.
  // Stays fixed when the user filters/searches or re-sorts.
  const rankOf = useMemo(() => {
    const rows = boards[stage] ?? boards['all'] ?? [];
    const sorted = [...rows].sort((a, b) => b.rating - a.rating);
    const m = new Map<number, number>();
    sorted.forEach((r, idx) => { if (!m.has(r.player_id)) m.set(r.player_id, idx + 1); });
    return m;
  }, [boards, stage]);
  return (
    <div>
      <div className="row island-filter">
        {stageOpts.length > 1 && (
        <select aria-label="Stage" value={stage} onChange={(e) => setStage(e.target.value)}>
          {stageOpts.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
        )}
        {!hidePool && (
        <select aria-label="Player pool" value={champs ? 'champs' : 'all'}
          onChange={(e) => setChamps(e.target.value === 'champs')}>
          <option value="champs">Champions players</option>
          <option value="all">All players</option>
        </select>
        )}
        <select aria-label="Region" value={region} onChange={(e) => setRegion(e.target.value)}>
          <option value="all">All regions</option>
          {Object.entries(REGION_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="all">All roles</option>
          {Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input className="field" aria-label="Filter players" placeholder="Filter players"
          value={pq} onChange={(e) => setPq(e.target.value)} />
        <span className="mut">{list.length} players</span>
      </div>
      <table>
        <thead><tr><th>#</th>
          <SortTh label="Player" active={sort.key === 'player'} dir={sort.dir} onClick={() => toggle('player')} />
          {showTeam && <th>Team</th>}
          <SortTh label="Round Swing" active={sort.key === 'rating'} dir={sort.dir} onClick={() => toggle('rating')} />
          <SortTh label="Role" active={sort.key === 'role'} dir={sort.dir} onClick={() => toggle('role')} />
          <SortTh label="Rounds" active={sort.key === 'rounds'} dir={sort.dir} onClick={() => toggle('rounds')} /></tr></thead>
        <tbody>
          {list.map((p, i) => (
            <tr key={p.player_id} id={'swingrow-' + p.player_id}>
              <td className="num">{rankOf.get(p.player_id) ?? i + 1}</td><td>{p.name}</td>
              {showTeam && <td>{p.team ?? '–'}</td>}
              <td className="num">{p.rating}</td><td>{p.role ? ROLE_LABELS[p.role] ?? p.role : '–'}</td><td className="num">{p.rounds}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export type CompAgent = { name: string; img: string };
export type CompTeam = { team: string; w: number; l: number };
export type MapComp = { map: string; map_img: string; agents: CompAgent[];
  edge: number; edge_rate: number; w: number; l: number; maps: number;
  expected: number; pick_rate: number; top_teams: CompTeam[] };
export type MapCompPool = { label: string; n_series: number; n_maps: number;
  min_maps: number; analyzed: number; maps: MapComp[] };
export type MapCompData = { as_of: string; pools: Record<string, MapCompPool> };

const POOL_TABS = [
  { id: 'champions', label: 'Champions' },
  { id: 'full', label: '2026' },
];

export function CompBoard({ mapComps }: { mapComps: MapCompData }) {
  const [pool, setPool] = useState(
    mapComps.pools['champions'] ? 'champions' : Object.keys(mapComps.pools)[0]);
  const p = mapComps.pools[pool];
  const [sel, setSel] = useState<string | undefined>(undefined);
  const comps = p?.maps ?? [];
  const c = comps.find((x) => x.map === sel) ?? comps[0];
  useEffect(() => { setSel(undefined); }, [pool]);
  if (!p || !c) return null;
  return (
    <div>
      <div className="row island-filter">
        <select aria-label="Comp pool" value={pool} onChange={(e) => setPool(e.target.value)}>
          {POOL_TABS.filter((t) => mapComps.pools[t.id]).map((t) => (
            <option key={t.id} value={t.id}>{t.label}</option>
          ))}
        </select>
      </div>
      <p className="sub">Best 5-agent comp per map, {p.label.toLowerCase()} pool through {mapComps.as_of}. Ranked by shrunk wins above Elo expectation; {p.min_maps}+ maps to qualify, mirrors excluded. {p.n_maps} maps, {p.analyzed} comps analyzed.</p>
      <div className="row maptabs">
        {comps.map((m) => (
          <button key={m.map} onClick={() => setSel(m.map)}
            className={'maptab' + (m.map === c.map ? ' on' : '')} aria-label={m.map}>
            <img src={m.map_img} alt={m.map} loading="lazy" />
            <span>{m.map}</span>
          </button>
        ))}
      </div>
      <div className="compbanner" style={{ backgroundImage: `url(${c.map_img})` }}>
        <span>{c.map}</span>
      </div>
      <div className="compagents">
        {c.agents.map((a) => (
          <div className="agent" key={a.name}>
            <img src={a.img} alt={a.name} loading="lazy" />
            <span>{a.name}</span>
          </div>
        ))}
      </div>
      <div className="row compstats">
        <div className="cstat"><b>{c.w}-{c.l}</b><span>record · {c.maps} maps</span></div>
        <div className="cstat"><b>+{c.edge.toFixed(1)}</b><span>wins above Elo expectation</span></div>
        <div className="cstat"><b>{(c.pick_rate * 100).toFixed(1)}%</b><span>pick rate on {c.map}</span></div>
      </div>
      <p className="mut compbest">Run best by {c.top_teams.map((t, i) => (
        <span key={t.team}>{i > 0 && ' · '}{t.team} <span className="num">{t.w}-{t.l}</span></span>
      ))}</p>
    </div>
  );
}

export type TeamMapRow = { map: string; w: number; l: number; n: number; off: number };
export type StrengthTeam = { id: number; name: string; maps: TeamMapRow[] };
export type StrengthPool = { label: string; n_series: number; n_maps: number;
  maps: string[]; teams: StrengthTeam[] };
export type StrengthData = { as_of: string; pools: Record<string, StrengthPool> };

export function TeamMapStrength({ strength, index }: {
  strength: StrengthData; index: SearchIndex }) {
  const [pool, setPool] = useState(
    strength.pools['champions'] ? 'champions' : Object.keys(strength.pools)[0]);
  const p = strength.pools[pool];
  const teams = useMemo(() =>
    [...index.teams].sort((a, b) => a.name.localeCompare(b.name)), [index]);
  const [teamId, setTeamId] = useState(teams[0]?.id);
  const t = p?.teams.find((x) => x.id === teamId);
  const rows = useMemo(() =>
    t ? [...t.maps].sort((a, b) => b.off - a.off || b.n - a.n) : [], [t]);
  if (!p) return null;
  return (
    <div>
      <div className="row island-filter">
        <select aria-label="Team" value={teamId} onChange={(e) => setTeamId(Number(e.target.value))}>
          {teams.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select>
        <select aria-label="Map pool" value={pool} onChange={(e) => setPool(e.target.value)}>
          {POOL_TABS.filter((x) => strength.pools[x.id]).map((x) => (
            <option key={x.id} value={x.id}>{x.label}</option>
          ))}
        </select>
      </div>
      <p className="sub">{t?.name ?? 'No team'} per-map record, {p.label.toLowerCase()} pool through {strength.as_of}. Strength shrunk toward 50% — thin samples sit near zero.</p>
      {!t || rows.every((r) => r.n === 0) ? (
        <p className="mut">No maps played in this pool.</p>
      ) : (
      <table>
        <thead><tr><th>Map</th><th>W-L</th><th>Strength</th><th></th></tr></thead>
        <tbody>
          {rows.map((r) => {
            const pct = Math.max(-100, Math.min(100, r.off * 200));
            return (
            <tr key={r.map}>
              <td>{r.map}</td>
              <td className="num">{r.n === 0 ? '–' : `${r.w}-${r.l}`}</td>
              <td className="num">{r.n === 0 ? '–' : (r.off > 0 ? '+' : '') + r.off.toFixed(2)}</td>
              <td style={{ minWidth: 120 }}>
                {r.n === 0 ? null : (
                <div style={{ position: 'relative', height: 3, background: 'var(--line)', borderRadius: 2, overflow: 'hidden' }}>
                  <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: 'rgba(0,0,0,0.3)' }} />
                  <div style={{
                    position: 'absolute', top: 0, bottom: 0, background: 'var(--accent)', opacity: 0.8,
                    ...(pct >= 0
                      ? { left: '50%', width: `${pct / 2}%` }
                      : { right: '50%', width: `${-pct / 2}%` }),
                  }} />
                </div>
                )}
              </td>
            </tr>
            );
          })}
        </tbody>
      </table>
      )}
    </div>
  );
}

export type SearchIndex = {
  teams: { id: number; name: string }[];
  players: { id: number; name: string; teamId: number | null }[];
};

type SearchHit = { kind: 'team' | 'player'; id: number; name: string; teamId: number | null };

export function GlobalSearch({ index }: { index: SearchIndex }) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const ql = q.trim().toLowerCase();
  const teamHits = ql ? index.teams.filter((t) => t.name.toLowerCase().includes(ql)).slice(0, 4) : [];
  const playerHits = ql ? index.players.filter((p) => p.name.toLowerCase().includes(ql)).slice(0, 4) : [];
  const teamName = (id: number | null) => index.teams.find((t) => t.id === id)?.name ?? '';
  const flat: SearchHit[] = [
    ...teamHits.map((t): SearchHit => ({ kind: 'team', id: t.id, name: t.name, teamId: null })),
    ...playerHits.map((p): SearchHit => ({ kind: 'player', id: p.id, name: p.name, teamId: p.teamId })),
  ];

  const go = (r: SearchHit) => {
    setOpen(false);
    setQ('');
    inputRef.current?.blur();
    let el: HTMLElement | null = null;
    if (r.kind === 'team') {
      el = document.getElementById(`teamcard-${r.id}`);
    } else {
      el = document.getElementById(`playerrow-${r.id}`) ?? document.getElementById(`swingrow-${r.id}`);
    }
    if (!el) el = document.getElementById(r.kind === 'team' ? 'sec-teams' : 'sec-swing');
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (flashTimer.current) clearTimeout(flashTimer.current);
    el.classList.add('flash');
    flashTimer.current = setTimeout(() => el.classList.remove('flash'), 2000);
  };

  const onInputKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(h + 1, flat.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
    else if (e.key === 'Enter' && flat[hi]) { go(flat[hi]); }
    else if (e.key === 'Escape') { setOpen(false); inputRef.current?.blur(); }
  };

  const item = (r: SearchHit, i: number) => (
    <button type="button" key={r.kind + r.id}
      className={'gsearch-item' + (hi === i ? ' on' : '')}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => go(r)}
      onMouseEnter={() => setHi(i)}>
      <span>{r.name}</span>
      {r.kind === 'player' && r.teamId != null && <span className="mut">{teamName(r.teamId)}</span>}
    </button>
  );

  return (
    <div className="gsearch"
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false); }}>
      <input ref={inputRef} className="field gsearch-input" aria-label="Search teams or players"
        placeholder="Search teams or players  ( / )"
        value={q} onFocus={() => setOpen(true)}
        onChange={(e) => { setQ(e.target.value); setHi(0); setOpen(true); }}
        onKeyDown={onInputKey} />
      {open && ql !== '' && (
        <div className="gsearch-drop" role="listbox">
          {flat.length === 0 && <div className="gsearch-empty mut">No matches</div>}
          {teamHits.length > 0 && <div className="gsearch-group">Teams</div>}
          {teamHits.map((t, i) => item({ kind: 'team', id: t.id, name: t.name, teamId: null }, i))}
          {playerHits.length > 0 && <div className="gsearch-group">Players</div>}
          {playerHits.map((p, j) => item({ kind: 'player', id: p.id, name: p.name, teamId: p.teamId }, teamHits.length + j))}
        </div>
      )}
    </div>
  );
}
