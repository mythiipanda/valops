import { useMemo, useState } from 'react';

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
            <tr key={t.id}>
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

export type AllTeam = { id: number; name: string; region: string; group?: string;
  elo: number; form: number };

const REGION_LABELS: Record<string, string> =
  { AM: 'Americas', EMEA: 'EMEA', PAC: 'Pacific', CN: 'China' };

export function EloTable({ teams }: { teams: AllTeam[] }) {
  const [region, setRegion] = useState('all');
  const [group, setGroup] = useState('all');
  const [sort, setSort] = useState<{ key: 'team' | 'region' | 'grp' | 'elo' | 'form' | 'delta'; dir: SortDir }>({ key: 'elo', dir: 'desc' });
  const rows = useMemo(() => {
    const f = teams
      .filter((t) => region === 'all' || t.region === region)
      .filter((t) => group === 'all' || t.group === group)
      .map((t) => ({ ...t, delta: t.form - t.elo }));
    const get = (t: typeof f[number]): number | string =>
      sort.key === 'team' ? t.name
      : sort.key === 'region' ? (REGION_LABELS[t.region] ?? t.region)
      : sort.key === 'grp' ? (t.group ?? '')
      : sort.key === 'elo' ? t.elo
      : sort.key === 'form' ? t.form : t.delta;
    return order(f, get, sort.dir);
  }, [teams, region, group, sort]);
  const toggle = (key: typeof sort.key) => setSort((s) => toggleSort(s, key, ['team', 'region', 'grp']));
  return (
    <div>
      <div className="row island-filter">
        <select aria-label="Region filter" value={region} onChange={(e) => setRegion(e.target.value)}>
          <option value="all">All regions</option>
          {Object.entries(REGION_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select aria-label="Group filter" value={group} onChange={(e) => setGroup(e.target.value)}>
          <option value="all">All groups</option>
          {['A', 'B', 'C', 'D'].map((g) => <option key={g} value={g}>Group {g}</option>)}
        </select>
        <span className="mut">{rows.length} teams</span>
      </div>
      <table>
        <thead><tr><th>#</th>
          <SortTh label="Team" active={sort.key === 'team'} dir={sort.dir} onClick={() => toggle('team')} />
          <SortTh label="Region" active={sort.key === 'region'} dir={sort.dir} onClick={() => toggle('region')} />
          <SortTh label="Grp" active={sort.key === 'grp'} dir={sort.dir} onClick={() => toggle('grp')} />
          <SortTh label="Elo" active={sort.key === 'elo'} dir={sort.dir} onClick={() => toggle('elo')} />
          <SortTh label="Form" active={sort.key === 'form'} dir={sort.dir} onClick={() => toggle('form')} />
          <SortTh label="&#916;" active={sort.key === 'delta'} dir={sort.dir} onClick={() => toggle('delta')}
            title="Stage-2 form minus full-year Elo" /></tr></thead>
        <tbody>
          {rows.map((t, i) => (
            <tr key={t.id}>
              <td className="num">{i + 1}</td><td>{t.name}</td>
              <td className="mut">{REGION_LABELS[t.region] ?? t.region}</td>
              <td className="mut">{t.group ?? '–'}</td>
              <td className="num">{Math.round(t.elo)}</td>
              <td className="num">{Math.round(t.form)}</td>
              <td className="num">{t.delta >= 0 ? '+' : ''}{Math.round(t.delta)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type SwingRow = { player_id: number; name: string; rating: number;
  role?: string; rounds: number; champs?: boolean; region?: string | null };
export type SwingBoards = Record<string, SwingRow[]>;

const STAGES = [
  { id: 'all', label: 'All 2026' },
  { id: 'stage2', label: 'Stage 2' },
  { id: 'stage1', label: 'Stage 1' },
  { id: 'kickoff', label: 'Kickoff' },
];

export function SwingBoard({ boards }: { boards: SwingBoards }) {
  const [champs, setChamps] = useState(true);
  const [stage, setStage] = useState('all');
  const [region, setRegion] = useState('all');
  const [sort, setSort] = useState<{ key: 'player' | 'rating' | 'role' | 'rounds'; dir: SortDir }>({ key: 'rating', dir: 'desc' });
  const list = useMemo(() => {
    const rows = boards[stage] ?? boards['all'] ?? [];
    const f = rows
      .filter((r) => !champs || r.champs)
      .filter((r) => region === 'all' || r.region === region);
    const get = (r: SwingRow): number | string =>
      sort.key === 'player' ? r.name
      : sort.key === 'rating' ? r.rating
      : sort.key === 'role' ? (r.role ?? '') : r.rounds;
    return order(f, get, sort.dir);
  }, [boards, stage, champs, region, sort]);
  const toggle = (key: typeof sort.key) => setSort((s) => toggleSort(s, key, ['player', 'role']));
  return (
    <div>
      <div className="row island-filter">
        <select aria-label="Stage" value={stage} onChange={(e) => setStage(e.target.value)}>
          {STAGES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
        <select aria-label="Player pool" value={champs ? 'champs' : 'all'}
          onChange={(e) => setChamps(e.target.value === 'champs')}>
          <option value="champs">Champions players</option>
          <option value="all">All players</option>
        </select>
        <select aria-label="Region" value={region} onChange={(e) => setRegion(e.target.value)}>
          <option value="all">All regions</option>
          {Object.entries(REGION_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <span className="mut">{list.length} players</span>
      </div>
      <table>
        <thead><tr><th>#</th>
          <SortTh label="Player" active={sort.key === 'player'} dir={sort.dir} onClick={() => toggle('player')} />
          <SortTh label="Round Swing" active={sort.key === 'rating'} dir={sort.dir} onClick={() => toggle('rating')} />
          <SortTh label="Role" active={sort.key === 'role'} dir={sort.dir} onClick={() => toggle('role')} />
          <SortTh label="Rounds" active={sort.key === 'rounds'} dir={sort.dir} onClick={() => toggle('rounds')} /></tr></thead>
        <tbody>
          {list.map((p, i) => (
            <tr key={p.player_id}>
              <td className="num">{i + 1}</td><td>{p.name}</td>
              <td className="num">{p.rating}</td><td>{p.role ?? '–'}</td><td className="num">{p.rounds}</td>
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
  edge: number; w: number; l: number; maps: number; pick_rate: number;
  top_teams: CompTeam[] };

export function CompBoard({ comps }: { comps: MapComp[] }) {
  const [sel, setSel] = useState(comps[0]?.map);
  const c = comps.find((x) => x.map === sel) ?? comps[0];
  if (!c) return null;
  return (
    <div>
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
        <div className="cstat"><b>+{(c.edge * 100).toFixed(1)}</b><span>pts above Elo expectation</span></div>
        <div className="cstat"><b>{(c.pick_rate * 100).toFixed(1)}%</b><span>pick rate on {c.map}</span></div>
      </div>
      <p className="mut compbest">Run best by {c.top_teams.map((t, i) => (
        <span key={t.team}>{i > 0 && ' · '}{t.team} <span className="num">{t.w}-{t.l}</span></span>
      ))}</p>
    </div>
  );
}
