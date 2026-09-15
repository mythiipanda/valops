import { useMemo, useState } from 'react';

export type Team = { id: number; name: string; title: number; advance: number;
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
  const rows = teams.filter((t) => t.name.toLowerCase().includes(q.toLowerCase()));
  return (
    <div>
      <div className="row island-filter">
        <input className="field" aria-label="Filter teams" placeholder="Filter teams"
          value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <table>
        <thead><tr><th>#</th><th>Team</th><th>Title</th><th>Advance</th><th></th></tr></thead>
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

export type SwingRow = { name: string; ar100: number; rounds: number; champs: boolean; space?: number };

export function SwingBoard({ rows }: { rows: SwingRow[] }) {
  const [champs, setChamps] = useState(true);
  const list = rows.filter((r) => !champs || r.champs);
  return (
    <div>
      <div className="row island-filter">
        <select aria-label="Player pool" value={champs ? 'champs' : 'all'}
          onChange={(e) => setChamps(e.target.value === 'champs')}>
          <option value="champs">Champions players</option>
          <option value="all">All 2026</option>
        </select>
      </div>
      <table>
        <thead><tr><th>#</th><th>Player</th><th>SWING-AR</th><th>Space</th><th>Rounds</th></tr></thead>
        <tbody>
          {list.map((p, i) => (
            <tr key={p.name}>
              <td className="num">{i + 1}</td><td>{p.name}</td>
              <td className="num">{p.ar100}</td><td className="num">{p.space ?? '–'}</td><td className="num">{p.rounds}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
